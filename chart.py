"""分时图：坐标映射 / 配色（IntradayPainter）+ 行内缩略图 + 独立窗口。"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QEvent, QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QApplication, QMenu, QWidget

from config import DEFAULTS, NUM_FONT, UI_FONT
from intraday import MORNING_MINUTES, SESSIONS, TOTAL_MINUTES, IntradaySeries, effective_points
from panel import GRIP, SHADOW, draw_background, draw_grip, draw_shadow, panel_rect

AVG_COLOR = "#F5C542"        # 均价线
GRID_COLOR = "#5A6472"       # 刻度线
MIN_DEV_PCT = 0.5            # Y 轴最小幅度（%），避免横盘被放大成噪声
POINT_RADIUS = 2.5           # 只有 1 个可绘制点时画的圆点半径


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

    def mapped_points(self, points, baseline: float, max_dev: float, rect: QRectF, *, use_avg: bool = False) -> list[QPointF]:
        """把点序列换算成坐标（跳过无坐标或均价为 None 的点），供折线与单点绘制共用。"""
        coords = []
        for point in points:
            index = minutes_of(point.time)
            value = point.avg if use_avg else point.price
            if index is None or value is None:
                continue
            coords.append(QPointF(self.map_x(index, rect), self.map_y(value, baseline, max_dev, rect)))
        return coords

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
        color = self.line_color(series)
        coords = self.mapped_points(points, baseline, max_dev, rect)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        self._draw_baseline(painter, rect, baseline, max_dev)
        if any(point.avg is not None for point in points):
            self._draw_line(painter, rect, points, baseline, max_dev, QColor(AVG_COLOR), use_avg=True)
        if len(coords) == 1:
            # 只有行情、还没有分钟线（冷启动 / 接口失败）：画个点，别让图上只剩基准线
            painter.setPen(Qt.NoPen)
            painter.setBrush(self._faded(color))
            painter.drawEllipse(coords[0], POINT_RADIUS, POINT_RADIUS)
        elif len(coords) > 1:
            self._stroke(painter, coords, color)
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
        coords = self.mapped_points(points, baseline, max_dev, rect, use_avg=use_avg)
        if len(coords) < 2:
            return
        self._stroke(painter, coords, color, 0.9 if use_avg else 1.0)

    def _stroke(self, painter: QPainter, coords, color: QColor, alpha: float = 1.0) -> None:
        line = QPolygonF()
        for coord in coords:
            line.append(coord)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(self._faded(color, alpha), 1.4))
        painter.drawPolyline(line)


class IntradayChartWindow(QWidget):
    """独立分时图窗口：无边框、可拖动、右下角可缩放、可置顶。"""

    MIN_SIZE = (360, 240)
    DEFAULT_SIZE = (640, 360)
    AXIS_WIDTH = 66              # 右侧价格刻度宽度
    TIME_AXIS_HEIGHT = 20        # 底部时间刻度高度
    HEADER_HEIGHT = 26
    MIN_OPACITY = 0.35           # 图表需要底衬，背景不透明度不跟随面板设到 0
    CLOSE_SIZE = 18              # 右上角关闭按钮边长
    FOOTER_HEIGHT = 14           # 底部状态行高度（时间刻度下面单独一行）

    def __init__(self, code: str, name: str = "", parent=None):
        super().__init__(parent)
        self.code = code
        self._name = name or code
        self._series: IntradaySeries | None = None
        self._source = ""
        self._updated = ""
        self._warning = ""
        self._style = dict(DEFAULTS)
        self._brush = IntradayPainter(DEFAULTS["up_color"], DEFAULTS["down_color"], DEFAULTS["flat_color"], DEFAULTS["font_color"])
        self._drag_offset = None
        self._resizing = False
        self._resize_origin = None
        self._resize_geom = None
        self._close_hover = False
        self._close_pressed = False
        self._suppress_close = False  # 右键菜单 / 切换置顶引发的那次失活不算数

        self.setWindowTitle(f"分时图 · {self._name}")
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        # 必须显式带 Qt.Window：只写 FramelessWindowHint 会把窗口类型清成 Qt.Widget，
        # 这个窗口又带着父对象（为了右键「立即刷新」能转发给主窗口），于是被当成子控件嵌进主窗口里
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setMouseTracking(True)
        self.setMinimumSize(self.MIN_SIZE[0] + 2 * SHADOW, self.MIN_SIZE[1] + 2 * SHADOW)
        self.resize(self.DEFAULT_SIZE[0] + 2 * SHADOW, self.DEFAULT_SIZE[1] + 2 * SHADOW)

    # ---------- 数据与样式 ----------

    def set_series(self, series: IntradaySeries | None, source: str = "", updated: str = "", warning: str = "") -> None:
        self._series = series
        self._source = source
        self._updated = updated
        self._warning = warning
        self.update()

    def apply_style(self, values: dict) -> None:
        """样式取自 config；只认识 DEFAULTS 里有的键，缺的用默认值。"""
        self._style = {**DEFAULTS, **{key: value for key, value in (values or {}).items() if key in DEFAULTS}}
        alpha = max(0.0, min(1.0, float(self._style["font_opacity"])))
        self._brush.set_colors(self._style["up_color"], self._style["down_color"], self._style["flat_color"], self._style["font_color"], alpha)
        self.update()

    # ---------- 绘制 ----------

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        panel = panel_rect(self.width(), self.height())
        opacity = max(self.MIN_OPACITY, min(1.0, float(self._style["opacity"])))
        draw_shadow(painter, panel, opacity)
        draw_background(painter, panel, self._style["bg_color"], opacity)
        draw_grip(painter, panel, opacity)

        self._draw_header(painter, panel)
        self._draw_close(painter, panel)
        chart = self._chart_rect(panel)
        if not self._has_data():
            painter.setFont(QFont(UI_FONT, 11))
            painter.setPen(QPen(self._brush.text_color()))
            painter.drawText(chart, Qt.AlignCenter, "暂无分时数据")
            painter.end()
            return
        self._draw_axes(painter, chart)
        self._brush.draw(painter, chart, self._series)
        painter.end()

    def _has_data(self) -> bool:
        """有分钟线，或只有实时价（冷启动第一秒）：都算有东西可画。"""
        return self._series is not None and (bool(self._series.points) or self._series.latest_price is not None)

    def _chart_rect(self, panel: QRectF) -> QRectF:
        return QRectF(
            panel.left() + 12,
            panel.top() + self.HEADER_HEIGHT + 10,
            max(1.0, panel.width() - 24 - self.AXIS_WIDTH),
            max(1.0, panel.height() - self.HEADER_HEIGHT - self.TIME_AXIS_HEIGHT - self.FOOTER_HEIGHT - 16),
        )

    def _draw_header(self, painter: QPainter, panel: QRectF) -> None:
        # 右边给关闭按钮留出位置，价格文字不许压到它上面
        header = QRectF(panel.left() + 12, panel.top() + 6, panel.width() - 24 - self.CLOSE_SIZE - 10, self.HEADER_HEIGHT)
        size = max(9, int(self._style["font_size"]) - 2)
        painter.setFont(QFont(UI_FONT, size))
        painter.setPen(QPen(self._brush.text_color()))
        painter.drawText(header, Qt.AlignLeft | Qt.AlignVCenter, f"{self._name}  {self.code}")
        series = self._series
        if series is None or series.last_price <= 0:
            return
        color = QColor(self._style["flat_color"])
        if series.direction > 0:
            color = QColor(self._style["up_color"])
        elif series.direction < 0:
            color = QColor(self._style["down_color"])
        color.setAlphaF(max(0.0, min(1.0, float(self._style["font_opacity"]))))
        painter.setFont(QFont(NUM_FONT, size))
        painter.setPen(QPen(color))
        decimals = 3 if self._is_fund(self.code) else 2
        text = f"{series.last_price:.{decimals}f}  {series.change_pct:+.2f}%"
        painter.drawText(header, Qt.AlignRight | Qt.AlignVCenter, text)

    def _draw_axes(self, painter: QPainter, chart: QRectF) -> None:
        series = self._series
        points = effective_points(series)
        baseline = self._brush.baseline_of(series, points)
        max_dev = self._brush.max_deviation(points, baseline)
        decimals = 3 if self._is_fund(self.code) else 2
        painter.setFont(QFont(NUM_FONT, max(8, int(self._style["font_size"]) - 4)))
        grid = QColor(GRID_COLOR)
        grid.setAlphaF(0.35)
        for price in (baseline + max_dev, baseline, baseline - max_dev):
            y = self._brush.map_y(price, baseline, max_dev, chart)
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(grid, 1, Qt.DotLine))
            painter.drawLine(QPointF(chart.left(), y), QPointF(chart.right(), y))
            painter.setPen(QPen(self._brush.text_color()))
            label = QRectF(chart.right() + 4, y - 8, self.AXIS_WIDTH - 8, 16)
            painter.drawText(label, Qt.AlignLeft | Qt.AlignVCenter, f"{price:.{decimals}f}")
        self._draw_time_axis(painter, chart)
        self._draw_footer(painter, chart)

    def _draw_time_axis(self, painter: QPainter, chart: QRectF) -> None:
        """底部时间刻度：自己占一行，两端标签贴住图区边线，不再溢出面板。"""
        ticks = ((0, "09:30", Qt.AlignLeft), (60, "10:30", Qt.AlignHCenter), (120, "11:30/13:00", Qt.AlignHCenter),
                 (180, "14:00", Qt.AlignHCenter), (240, "15:00", Qt.AlignRight))
        painter.setPen(QPen(self._brush.text_color()))
        for index, text, align in ticks:
            x = self._brush.map_x(index, chart)
            half = 46.0 if index == 120 else 30.0        # 中午那个标签最长，单独放宽，免得被压成 "1:30/13:0"
            left = x if align == Qt.AlignLeft else x - half
            right = x if align == Qt.AlignRight else x + half
            painter.drawText(QRectF(left, chart.bottom() + 2, right - left, self.TIME_AXIS_HEIGHT - 2), align | Qt.AlignVCenter, text)

    def _draw_footer(self, painter: QPainter, chart: QRectF) -> None:
        """状态行排到时间刻度下面单独一行，不再和时间刻度挤在一起。"""
        if not self._updated:
            return
        painter.setFont(QFont(UI_FONT, max(8, int(self._style["font_size"]) - 5)))
        painter.setPen(QPen(self._brush.text_color()))
        footer = f"{self._source} · {self._updated} 更新"
        if self._warning:
            footer += f"（{self._warning}）"
        rect = QRectF(chart.left(), chart.bottom() + self.TIME_AXIS_HEIGHT + 1, chart.width() + self.AXIS_WIDTH, self.FOOTER_HEIGHT)
        painter.drawText(rect, Qt.AlignLeft | Qt.AlignVCenter, footer)

    def _close_rect(self, panel: QRectF | None = None) -> QRectF:
        """右上角关闭按钮的位置；跟着面板走，缩放后仍然贴着角。"""
        panel = panel if panel is not None else panel_rect(self.width(), self.height())
        return QRectF(panel.right() - self.CLOSE_SIZE - 8, panel.top() + 6, self.CLOSE_SIZE, self.CLOSE_SIZE)

    def _draw_close(self, painter: QPainter, panel: QRectF) -> None:
        rect = self._close_rect(panel)
        if self._close_hover:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(232, 17, 35, 210))
            painter.drawRoundedRect(rect, 4, 4)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(255, 255, 255) if self._close_hover else self._brush.text_color(), 1.4))
        inset = 5.0
        painter.drawLine(QPointF(rect.left() + inset, rect.top() + inset), QPointF(rect.right() - inset, rect.bottom() - inset))
        painter.drawLine(QPointF(rect.right() - inset, rect.top() + inset), QPointF(rect.left() + inset, rect.bottom() - inset))

    @staticmethod
    def _is_fund(code: str) -> bool:
        text = str(code).strip()
        return text[:1] == "5" or text[:2] in ("15", "16", "18")

    # ---------- 交互 ----------

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)
        panel = panel_rect(self.width(), self.height()).toRect()
        pos = event.position().toPoint()
        if self._close_rect().contains(event.position()):
            self._close_pressed = True      # 按下与松开都落在按钮上才算点击，拖出去就取消
            event.accept()
            return
        if 0 <= panel.right() - pos.x() <= GRIP and 0 <= panel.bottom() - pos.y() <= GRIP:
            self._resizing = True
            self._resize_origin = event.globalPosition().toPoint()
            self._resize_geom = QRect(self.geometry())
        else:
            self._drag_offset = event.globalPosition().toPoint() - self.pos()
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if not (event.buttons() & Qt.LeftButton):
            hover = self._close_rect().contains(event.position())
            if hover != self._close_hover:
                self._close_hover = hover
                self.update()
            self.setCursor(Qt.PointingHandCursor if hover else Qt.ArrowCursor)
            return super().mouseMoveEvent(event)
        if self._resizing and self._resize_geom is not None:
            delta = event.globalPosition().toPoint() - self._resize_origin
            rect = QRect(self._resize_geom)
            rect.setRight(rect.right() + delta.x())
            rect.setBottom(rect.bottom() + delta.y())
            width = max(self.minimumWidth(), rect.width())
            height = max(self.minimumHeight(), rect.height())
            self.resize(width, height)
        elif self._drag_offset is not None:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._close_pressed:
            self._close_pressed = False
            if self._close_rect().contains(event.position()):
                self.close()
                event.accept()
                return
        self._drag_offset = None
        self._resizing = False
        self._resize_origin = None
        self._resize_geom = None
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event) -> None:
        if self._close_hover:
            self._close_hover = False
            self.update()
        super().leaveEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.close()
            event.accept()
            return
        super().keyPressEvent(event)

    def _auto_close_suppressed(self) -> bool:
        """右键菜单/弹窗开着、或正在切换窗口标志时，这一次失活不能当成「点别处了」。"""
        if self._suppress_close:
            return True
        return QApplication.activePopupWidget() is not None or QApplication.activeModalWidget() is not None

    def event(self, event):
        """点别的窗口（**含其他软件**）会走这里。

        跨应用切换时 Qt 发的是 WindowDeactivate，而不是 focusOutEvent——
        只盯 focusOutEvent 的话，同应用内点主窗口能关，点别的软件却没反应。
        """
        if event.type() == QEvent.WindowDeactivate and not self._auto_close_suppressed():
            self.close()
        return super().event(event)

    def focusOutEvent(self, event) -> None:
        """同一应用内的焦点转移（比如点了主窗口）也会走到这里。"""
        if not self._auto_close_suppressed():
            self.close()
        super().focusOutEvent(event)

    def contextMenuEvent(self, event) -> None:
        menu = QMenu(self)
        menu.addAction("立即刷新", self._refresh)
        on_top = menu.addAction("窗口置顶")
        on_top.setCheckable(True)
        on_top.setChecked(bool(self.windowFlags() & Qt.WindowStaysOnTopHint))
        on_top.triggered.connect(self._toggle_on_top)
        menu.addSeparator()
        menu.addAction("关闭", self.close)
        self._suppress_close = True
        try:
            menu.exec(event.globalPos())
        finally:
            self._suppress_close = False

    def _refresh(self) -> None:
        """转交给主窗口：分时数据由取数线程统一提供。"""
        parent = self.parent()
        if parent is not None and hasattr(parent, "request_refresh"):
            parent.request_refresh()

    def _toggle_on_top(self, checked: bool) -> None:
        position = self.pos()
        self._suppress_close = True      # 换窗口标志会诱发一次失活，别顺手把窗口关了
        try:
            self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | (Qt.WindowStaysOnTopHint if checked else Qt.WindowType(0)))
            self.show()
            self.move(position)
        finally:
            self._suppress_close = False


class SparklineWidget(QWidget):
    """行内迷你分时：无坐标轴无文字。

    自己不接收鼠标事件（主窗口统一做命中判断），这样主窗口的拖动逻辑完全不受影响。
    """

    WIDTH = 72
    HEIGHT = 22

    def __init__(self, code: str, parent=None):
        super().__init__(parent)
        self.code = code
        self._series: IntradaySeries | None = None
        self._brush = IntradayPainter(DEFAULTS["up_color"], DEFAULTS["down_color"], DEFAULTS["flat_color"], DEFAULTS["font_color"])
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self.setToolTip("点击查看分时图")

    def apply_style(self, values: dict) -> None:
        style = {**DEFAULTS, **{key: value for key, value in (values or {}).items() if key in DEFAULTS}}
        alpha = max(0.0, min(1.0, float(style["font_opacity"])))
        self._brush.set_colors(style["up_color"], style["down_color"], style["flat_color"], style["font_color"], alpha)
        self.update()

    def set_series(self, series: IntradaySeries | None) -> None:
        self._series = series
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
        if self._series is None or not self._series.points:
            # 没有数据：一条居中的灰线占位，避免行高变化
            painter.setPen(QPen(QColor(128, 128, 128, 120), 1))
            painter.drawLine(QPointF(rect.left(), rect.center().y()), QPointF(rect.right(), rect.center().y()))
            painter.end()
            return
        self._brush.draw(painter, rect, self._series)
        painter.end()
