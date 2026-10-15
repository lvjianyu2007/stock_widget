## 任务 2：分时序列装配 `intraday.py`

> **规格偏差（已确认）：** 规格里写的「`volume` 为 0 或缺失时 `avg = None`」改为「**累计成交量为 0 时** `avg = None`」。原因：均价线 = 累计成交额 ÷ 累计成交量，某一分钟量为 0 时均价并未改变，仍应连线；只有源头完全不提供成交量（累计量为 0）时才无法算均价。已同步更新设计文档 5.2 节。

**文件：**
- 创建：`intraday.py`
- 测试：`tests/test_intraday.py`

- [ ] **步骤 1：编写失败的测试**

写入 `tests/test_intraday.py`：

```python
"""分时序列装配：按天过滤、均价累计、昨收回退、实时点拼接。"""
import unittest
from datetime import datetime

from datasource import Quote
from intraday import (
    attach_latest,
    build_all,
    build_series,
    clamp_to_session,
    effective_points,
    minute_rows,
    resolve_prev_close,
    series_from_rows,
)


def _records():
    return [
        {"day": "2026-10-14 15:00:00", "close": "100.0", "volume": "500", "amount": "50000"},
        {"day": "2026-10-15 09:31:00", "close": "102.0", "volume": "1000", "amount": "102000"},
        {"day": "2026-10-15 09:32:00", "close": "104.0", "volume": "0", "amount": "0"},
        {"day": "2026-10-15 09:33:00", "close": "103.0", "volume": "1000", "amount": "103000"},
    ]


class BuildSeriesTest(unittest.TestCase):
    def test_filters_other_days_and_accumulates_avg(self):
        series = build_series("600519", _records(), "2026-10-15", prev_close=101.0)
        self.assertEqual([point.time for point in series.points], ["09:31", "09:32", "09:33"])
        self.assertAlmostEqual(series.points[0].avg, 102.0)
        self.assertAlmostEqual(series.points[2].avg, (102000 + 103000) / 2000)

    def test_zero_volume_minute_keeps_previous_avg(self):
        series = build_series("600519", _records(), "2026-10-15", prev_close=101.0)
        self.assertAlmostEqual(series.points[1].avg, 102.0)
        self.assertEqual(series.points[1].volume, None)

    def test_avg_is_none_without_any_volume(self):
        records = [{"day": "2026-10-15 09:31:00", "close": "102.0", "volume": "0", "amount": "0"}]
        series = build_series("600519", records, "2026-10-15", prev_close=101.0)
        self.assertIsNone(series.points[0].avg)

    def test_empty_records_gives_empty_series(self):
        series = build_series("600519", [], "2026-10-15", prev_close=101.0)
        self.assertEqual(series.points, [])
        self.assertEqual(series.last_price, 0.0)
        self.assertEqual(series.direction, 0)

    def test_direction_and_change_pct_follow_last_price(self):
        series = build_series("600519", _records(), "2026-10-15", prev_close=100.0)
        attach_latest(series, 105.0)
        self.assertEqual(series.direction, 1)
        self.assertAlmostEqual(series.change_pct, 5.0)
        attach_latest(series, 95.0)
        self.assertEqual(series.direction, -1)
        attach_latest(series, 100.0)
        self.assertEqual(series.direction, 0)


class PrevCloseTest(unittest.TestCase):
    def test_prefers_quote_prev_close(self):
        self.assertEqual(resolve_prev_close(101.5, _records(), "2026-10-15"), 101.5)

    def test_falls_back_to_previous_day_last_close(self):
        self.assertEqual(resolve_prev_close(0.0, _records(), "2026-10-15"), 100.0)

    def test_returns_zero_without_any_candidate(self):
        self.assertEqual(resolve_prev_close(0.0, [], "2026-10-15"), 0.0)


class SessionTest(unittest.TestCase):
    def test_clamp_to_session(self):
        self.assertEqual(clamp_to_session(datetime(2026, 10, 15, 8, 0)), "09:30")
        self.assertEqual(clamp_to_session(datetime(2026, 10, 15, 10, 5)), "10:05")
        self.assertEqual(clamp_to_session(datetime(2026, 10, 15, 12, 0)), "11:30")
        self.assertEqual(clamp_to_session(datetime(2026, 10, 15, 14, 3)), "14:03")
        self.assertEqual(clamp_to_session(datetime(2026, 10, 15, 16, 0)), "15:00")


class EffectivePointsTest(unittest.TestCase):
    def test_appends_live_point_after_last_bar(self):
        series = build_series("600519", _records(), "2026-10-15", 101.0)
        attach_latest(series, 105.0)
        points = effective_points(series, now=datetime(2026, 10, 15, 14, 30))
        self.assertEqual(len(points), 4)
        self.assertEqual(points[-1].time, "14:30")
        self.assertEqual(points[-1].price, 105.0)
        self.assertAlmostEqual(points[-1].avg, series.points[-1].avg)

    def test_updates_last_bar_when_time_has_not_moved(self):
        series = build_series("600519", _records(), "2026-10-15", 101.0)
        attach_latest(series, 105.0)
        points = effective_points(series, now=datetime(2026, 10, 15, 9, 33))
        self.assertEqual(len(points), 3)
        self.assertEqual(points[-1].price, 105.0)

    def test_no_live_price_returns_bars_only(self):
        series = build_series("600519", _records(), "2026-10-15", 101.0)
        self.assertEqual(len(effective_points(series)), 3)

    def test_invalid_price_is_ignored(self):
        series = build_series("600519", _records(), "2026-10-15", 101.0)
        attach_latest(series, 0.0)
        self.assertIsNone(series.latest_price)


class BuildAllTest(unittest.TestCase):
    def test_uses_quote_price_and_prev_close(self):
        quotes = [Quote(code="600519", name="贵州茅台", price=105.0, prev_close=101.0)]
        series = build_all(quotes, {"600519": _records()}, "2026-10-15")
        self.assertEqual(series["600519"].latest_price, 105.0)
        self.assertEqual(series["600519"].prev_close, 101.0)
        self.assertEqual(len(series["600519"].points), 3)

    def test_code_without_bars_still_gets_a_series(self):
        quotes = [Quote(code="600519", price=105.0, prev_close=101.0)]
        series = build_all(quotes, {}, "2026-10-15")
        self.assertEqual(series["600519"].points, [])
        self.assertEqual(series["600519"].last_price, 105.0)

    def test_minute_rows_match_store_columns(self):
        quotes = [Quote(code="600519", price=105.0, prev_close=101.0)]
        series = build_all(quotes, {"600519": _records()}, "2026-10-15")
        rows = minute_rows(series, "2026-10-15")
        self.assertEqual(rows[0][:3], ("600519", "2026-10-15", "09:31"))
        self.assertEqual(len(rows), 3)

    def test_series_from_rows_restores_stored_points(self):
        rows = [("09:31", 1500.0, 1499.5, 800.0), ("09:32", 1501.0, 1500.2, 1200.0)]
        series = series_from_rows("600519", "2026-10-15", rows)
        self.assertEqual([point.time for point in series.points], ["09:31", "09:32"])
        self.assertEqual(series.points[1].avg, 1500.2)
        self.assertEqual(series.points[1].volume, 1200.0)
```

- [ ] **步骤 2：运行测试验证失败**

运行：`.\.venv\Scripts\python.exe -m unittest tests.test_intraday -v`
预期：FAIL，报错 `ModuleNotFoundError: No module named 'intraday'`

- [ ] **步骤 3：实现 `intraday.py`**

写入 `intraday.py`：

```python
"""分时序列装配：把分钟线原始记录转成可绘制的当天分时序列。

纯转换层：不碰网络、不碰 SQLite，便于直接单测。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

SESSIONS = ((9 * 60 + 30, 11 * 60 + 30), (13 * 60, 15 * 60))
MORNING_MINUTES = SESSIONS[0][1] - SESSIONS[0][0]     # 上午 120 分钟
TOTAL_MINUTES = 240                                   # 全天分钟数（午休不占宽度）


def _num(value, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return default if result != result else result


def _hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


@dataclass
class MinutePoint:
    time: str                     # 'HH:MM'
    price: float
    avg: float | None = None      # 累计成交额 / 累计成交量
    volume: float | None = None   # 该分钟成交量


@dataclass
class IntradaySeries:
    code: str
    day: str = ""
    prev_close: float = 0.0
    points: list[MinutePoint] = field(default_factory=list)
    latest_price: float | None = None      # 10 秒轮询的最新价

    @property
    def last_price(self) -> float:
        """画线用的最新价：实时价优先，其次最后一根 K 线。"""
        if self.latest_price is not None:
            return self.latest_price
        return self.points[-1].price if self.points else 0.0

    @property
    def change_pct(self) -> float:
        if self.prev_close <= 0 or not self.points:
            return 0.0
        return (self.last_price - self.prev_close) / self.prev_close * 100.0

    @property
    def direction(self) -> int:
        if self.prev_close <= 0 or not self.points:
            return 0
        diff = self.last_price - self.prev_close
        return 1 if diff > 0 else -1 if diff < 0 else 0


def clamp_to_session(now: datetime) -> str:
    """把时刻钳到最近的交易时段内：开盘前停在 09:30，午休停在 11:30，收盘后停在 15:00。"""
    minutes = now.hour * 60 + now.minute
    morning_start, morning_end = SESSIONS[0]
    afternoon_start, afternoon_end = SESSIONS[1]
    if minutes < morning_start:
        return _hhmm(morning_start)
    if minutes <= morning_end:
        return _hhmm(minutes)
    if minutes < afternoon_start:
        return _hhmm(morning_end)
    if minutes <= afternoon_end:
        return _hhmm(minutes)
    return _hhmm(afternoon_end)


def build_series(code: str, records, day: str, prev_close: float = 0.0) -> IntradaySeries:
    """把分钟线原始记录（含多个交易日）转成当天的分时序列。"""
    points: list[MinutePoint] = []
    earlier_close = 0.0
    total_amount = 0.0
    total_volume = 0.0
    for row in records or []:
        stamp = str(row.get("day", ""))[:16]        # 'YYYY-MM-DD HH:MM'
        if len(stamp) < 16:
            continue
        row_day, hhmm = stamp[:10], stamp[11:16]
        close = _num(row.get("close"))
        if row_day < day:
            earlier_close = close                   # 用来兜底算昨收
            continue
        if row_day != day:
            continue
        volume = _num(row.get("volume"))
        total_volume += volume
        total_amount += _num(row.get("amount"))
        avg = total_amount / total_volume if total_volume > 0 else None
        points.append(MinutePoint(time=hhmm, price=close, avg=avg, volume=volume or None))
    return IntradaySeries(code=code, day=day, prev_close=prev_close or earlier_close, points=points)


def resolve_prev_close(quote_prev_close: float, records, day: str) -> float:
    """昨收优先用行情通道给的值，缺失时回退到分钟线里今天之前的最后一根收盘价。"""
    if quote_prev_close > 0:
        return quote_prev_close
    last = 0.0
    for row in records or []:
        stamp = str(row.get("day", ""))[:10]
        if stamp and stamp < day:
            last = _num(row.get("close"))
    return last


def attach_latest(series: IntradaySeries, price: float) -> IntradaySeries:
    """接上 10 秒轮询的最新价；价格无效时保留原值。"""
    if price > 0:
        series.latest_price = price
    return series


def effective_points(series: IntradaySeries, now: "datetime | None" = None) -> list[MinutePoint]:
    """画图用的点序列：分钟线 + 一个"当前时刻"的实时点（时间没往前走时就地更新最后一根）。"""
    points = list(series.points)
    if series.latest_price is None or not points:
        return points
    last = points[-1]
    stamp = clamp_to_session(now or datetime.now())
    if stamp <= last.time:
        points[-1] = MinutePoint(time=last.time, price=series.latest_price, avg=last.avg, volume=last.volume)
        return points
    points.append(MinutePoint(time=stamp, price=series.latest_price, avg=last.avg))
    return points


def series_from_rows(code: str, day: str, rows) -> IntradaySeries:
    """从本地库读出的 [(hhmm, price, avg, volume)] 还原成序列。"""
    points = [
        MinutePoint(time=hhmm, price=price, avg=avg, volume=volume)
        for hhmm, price, avg, volume in rows
    ]
    return IntradaySeries(code=code, day=day, points=points)


def minute_rows(series_by_code: dict, day: str) -> list[tuple]:
    """转成 store.save_minutes 需要的行为 [(code, day, hhmm, price, avg, volume)]。"""
    rows = []
    for code, series in series_by_code.items():
        for point in series.points:
            rows.append((code, day, point.time, point.price, point.avg, point.volume))
    return rows


def build_all(quotes, bars_by_code: dict, day: str) -> dict:
    """把行情快照与分钟线合成每只的分时序列（含实时价）。"""
    result = {}
    for quote in quotes:
        records = bars_by_code.get(quote.code) or []
        prev_close = resolve_prev_close(quote.prev_close, records, day)
        series = build_series(quote.code, records, day, prev_close)
        if quote.price > 0:
            series.latest_price = quote.price
        result[quote.code] = series
    return result
```

- [ ] **步骤 4：运行测试验证通过**

运行：`.\.venv\Scripts\python.exe -m unittest tests.test_intraday -v`
预期：17 个用例全部 PASS

- [ ] **步骤 5：同步更新设计文档 5.2 节的均价规则**

把 `2026-10-15-intraday-chart-design.md` 中「`volume` 为 0 或缺失时 `avg = None`（绘制时跳过该点，不画断线误导）」改为：

```markdown
  - 逐行累计 `amount` 与 `volume` 得到 `avg`；**累计成交量为 0**（源头完全不提供量）时 `avg = None`，绘制时跳过该点；单分钟量为 0 时均价沿用累计值，正常连线；
```

---

