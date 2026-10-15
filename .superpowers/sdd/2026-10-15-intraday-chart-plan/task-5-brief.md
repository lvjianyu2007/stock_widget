## 任务 5：`chart.py` 绘制核心 `IntradayPainter`

**文件：**
- 创建：`chart.py`
- 测试：`tests/test_chart.py`

- [ ] **步骤 1：编写失败的测试**

写入 `tests/test_chart.py`：

```python
"""分时图坐标映射与取值范围（纯计算，不需要 QApplication）。"""
import unittest
from datetime import datetime

from PySide6.QtCore import QRectF

from chart import IntradayPainter, minutes_of
from intraday import attach_latest, build_series

RECT = QRectF(0.0, 0.0, 480.0, 200.0)


def _series(prev_close=100.0):
    records = [
        {"day": "2026-10-15 09:31:00", "close": "102.0", "volume": "1000", "amount": "102000"},
        {"day": "2026-10-15 11:30:00", "close": "103.0", "volume": "1000", "amount": "103000"},
        {"day": "2026-10-15 14:00:00", "close": "98.0", "volume": "1000", "amount": "98000"},
    ]
    return build_series("600519", records, "2026-10-15", prev_close)


class MinutesOfTest(unittest.TestCase):
    def test_session_boundaries(self):
        self.assertEqual(minutes_of("09:30"), 0)
        self.assertEqual(minutes_of("11:30"), 120)
        self.assertEqual(minutes_of("13:00"), 120)
        self.assertEqual(minutes_of("15:00"), 240)

    def test_out_of_session_returns_none(self):
        self.assertIsNone(minutes_of("09:29"))
        self.assertIsNone(minutes_of("12:00"))
        self.assertIsNone(minutes_of("15:01"))
        self.assertIsNone(minutes_of("bad"))


class MappingTest(unittest.TestCase):
    def test_map_x_spans_full_width(self):
        self.assertEqual(IntradayPainter.map_x(0, RECT), 0.0)
        self.assertEqual(IntradayPainter.map_x(240, RECT), 480.0)
        self.assertEqual(IntradayPainter.map_x(120, RECT), 240.0)

    def test_map_y_is_symmetric_around_baseline(self):
        center = 100.0
        self.assertEqual(IntradayPainter.map_y(100.0, 100.0, 5.0, RECT), center)
        self.assertEqual(IntradayPainter.map_y(105.0, 100.0, 5.0, RECT), 0.0)
        self.assertEqual(IntradayPainter.map_y(95.0, 100.0, 5.0, RECT), 200.0)

    def test_map_y_clamps_out_of_range_value(self):
        self.assertEqual(IntradayPainter.map_y(200.0, 100.0, 5.0, RECT), 0.0)

    def test_map_y_without_deviation_sits_on_center(self):
        self.assertEqual(IntradayPainter.map_y(100.0, 100.0, 0.0, RECT), 100.0)


class RangeTest(unittest.TestCase):
    def test_baseline_prefers_prev_close(self):
        series = _series(prev_close=100.0)
        self.assertEqual(IntradayPainter.baseline_of(series, series.points), 100.0)

    def test_baseline_falls_back_to_first_point(self):
        series = _series(prev_close=0.0)
        self.assertEqual(IntradayPainter.baseline_of(series, series.points), 102.0)

    def test_max_deviation_uses_avg_values_too(self):
        series = _series(prev_close=100.0)
        deviation = IntradayPainter.max_deviation(series.points, 100.0)
        self.assertAlmostEqual(deviation, 3.0)      # 均价最高 102.5 也在这条线内

    def test_max_deviation_has_minimum_range(self):
        records = [{"day": "2026-10-15 09:31:00", "close": "100.0", "volume": "1", "amount": "100"}]
        series = build_series("600519", records, "2026-10-15", prev_close=100.0)
        deviation = IntradayPainter.max_deviation(series.points, 100.0)
        self.assertAlmostEqual(deviation, 0.5)      # 0.5% 兜底，避免横盘被放大


class ColorTest(unittest.TestCase):
    def test_line_color_follows_direction(self):
        painter = IntradayPainter("#FF0000", "#00FF00", "#FFFFFF", "#FFFFFF")
        rising = _series(100.0)
        attach_latest(rising, 105.0)
        self.assertEqual(painter.line_color(rising).name(), "#ff0000")
        falling = _series(100.0)
        attach_latest(falling, 95.0)
        self.assertEqual(painter.line_color(falling).name(), "#00ff00")
        flat = _series(100.0)
        attach_latest(flat, 100.0)
        self.assertEqual(painter.line_color(flat).name(), "#ffffff")

    def test_line_color_without_points_is_flat(self):
        empty = build_series("600519", [], "2026-10-15", 0.0)
        painter = IntradayPainter("#FF0000", "#00FF00", "#FFFFFF", "#FFFFFF")
        self.assertEqual(painter.line_color(empty).name(), "#ffffff")
```

- [ ] **步骤 2：运行测试验证失败**

运行：`.\.venv\Scripts\python.exe -m unittest tests.test_chart -v`
预期：FAIL，报错 `ModuleNotFoundError: No module named 'chart'`

- [ ] **步骤 3：实现 `chart.py` 的绘制核心**

写入 `chart.py`（本任务只写前半部分，任务 6 会继续往同一文件追加窗口类）：

```python
"""分时图：坐标映射 / 配色（IntradayPainter）+ 行内缩略图 + 独立窗口。"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPolygonF, QPainter, QPen
from PySide6.QtWidgets import QMenu, QWidget

from config import DEFAULTS, NUM_FONT, UI_FONT
from intraday import (
    MORNING_MINUTES,
    SESSIONS,
    TOTAL_MINUTES,
    IntradaySeries,
    effective_points,
)
from panel import SHADOW, draw_background, draw_grip, draw_shadow, panel_rect

AVG_COLOR = "#F5C542"        # 均价线
GRID_COLOR = "#5A6472"       # 刻度线
MIN_DEV_PCT = 0.5            # Y 轴最小幅度（%），避免横盘被放大成噪声


def minutes_of(hhmm: str) -> int | None:
    """'09:31' -> 自 09:30 起算的分钟序号（午休不占宽度）；不在交易时段返回 None。"""
    try:
        hour, minute = (int(part) for part in str(hhmm).split(":"))
    except ValueError:
        return None
    total = hour * 60 + minute
    morning_start, morning_end = SESSIONS[0]
    if morning_start <= total <= morning_end:
        return total - morning_start
    afternoon_start, afternoon_end = SESSIONS[1]
    if afternoon_start <= total <= afternoon_end:
        return MORNING_MINUTES + (total - afternoon_start)
    return None


class IntradayPainter:
    """把分时序列画进给定矩形；缩略图与完整窗口共用，保证风格一致。"""

    def __init__(self, up_color: str, down_color: str, flat_color: str, font_color: str, alpha: float = 1.0):
        self.set_colors(up_color, down_color, flat_color, font_color, alpha)

    def set_colors(self, up_color: str, down_color: str, flat_color: str, font_color: str, alpha: float = 1.0) -> None:
        self._up = QColor(up_color)
        self._down = QColor(down_color)
        self._flat = QColor(flat_color)
        self._font = QColor(font_color)
        self._alpha = max(0.0, min(1.0, float(alpha)))

    # ---------- 坐标映射 ----------

    @staticmethod
    def map_x(index: int, rect: QRectF) -> float:
        """分钟序号 -> X 坐标；X 轴固定 240 分钟宽，未开盘部分留空。"""
        return rect.left() + rect.width() * index / TOTAL_MINUTES

    @staticmethod
    def map_y(price: float, baseline: float, max_dev: float, rect: QRectF) -> float:
        """价格 -> Y 坐标，以基准价（昨收）为中线上下对称。"""
        if max_dev <= 0:
            return rect.center().y()
        ratio = max(-1.0, min(1.0, (price - baseline) / max_dev))
        return rect.center().y() - ratio * rect.height() / 2.0

    @staticmethod
    def baseline_of(series: IntradaySeries, points) -> float:
        """基准线价格：优先昨收，拿不到就用第一根的价格兜底。"""
        if series.prev_close > 0:
            return series.prev_close
        return points[0].price if points else 0.0

    @staticmethod
    def max_deviation(points, baseline: float) -> float:
        """上下对称的幅度：价格与均价偏离基准的最大绝对值，至少留 0.5%。"""
        if baseline <= 0 or not points:
            return 0.0
        values = [point.price for point in points] + [point.avg for point in points if point.avg is not None]
        deviation = max(abs(value - baseline) for value in values)
        return max(deviation, baseline * MIN_DEV_PCT / 100.0)

    # ---------- 绘制 ----------

    def line_color(self, series: IntradaySeries) -> QColor:
        """按涨跌取线色：红涨、绿跌、白平。"""
        if series.direction > 0:
            return QColor(self._up)
        if series.direction < 0:
            return QColor(self._down)
        return QColor(self._flat)

    def text_color(self) -> QColor:
        return self._faded(self._font)

    def baseline_color(self) -> QColor:
        return self._faded(self._flat, 0.45)

    def draw(self, painter: QPainter, rect: QRectF, series: IntradaySeries, now: "datetime | None" = None) -> None:
        """画昨收基准线、价格折线与均价线（不含刻度文字，刻度由窗口自己画）。"""
        points = effective_points(series, now)
        if not points or rect.width() <= 1 or rect.height() <= 1:
            return
        baseline = self.baseline_of(series, points)
        max_dev = self.max_deviation(points, baseline)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        self._draw_baseline(painter, rect, baseline, max_dev)
        if any(point.avg is not None for point in points):
            self._draw_line(painter, rect, points, baseline, max_dev, QColor(AVG_COLOR), use_avg=True)
        self._draw_line(painter, rect, points, baseline, max_dev, self.line_color(series), use_avg=False)
        painter.restore()

    def _faded(self, color: QColor, alpha: float = 1.0) -> QColor:
        faded = QColor(color)
        faded.setAlphaF(self._alpha * alpha)
        return faded

    def _draw_baseline(self, painter: QPainter, rect: QRectF, baseline: float, max_dev: float) -> None:
        painter.setPen(QPen(self.baseline_color(), 1, Qt.DashLine))
        y = self.map_y(baseline, baseline, max_dev, rect)
        painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))

    def _draw_line(self, painter: QPainter, rect: QRectF, points, baseline: float, max_dev: float, color: QColor, *, use_avg: bool) -> None:
        line = QPolygonF()
        for point in points:
            index = minutes_of(point.time)
            value = point.avg if use_avg else point.price
            if index is None or value is None:
                continue
            line.append(QPointF(self.map_x(index, rect), self.map_y(value, baseline, max_dev, rect)))
        if line.count() < 2:
            return
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(self._faded(color, 0.9 if use_avg else 1.0), 1.4))
        painter.drawPolyline(line)
```

- [ ] **步骤 4：运行测试验证通过**

运行：`.\.venv\Scripts\python.exe -m unittest tests.test_chart -v`
预期：12 个用例全部 PASS

---

