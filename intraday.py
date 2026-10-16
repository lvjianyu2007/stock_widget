"""分时序列装配：把分钟线原始记录转成可绘制的当天分时序列。

纯转换层：不碰网络、不碰 SQLite（只依赖标准库），便于直接单测。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

SESSIONS = ((9 * 60 + 30, 11 * 60 + 30), (13 * 60, 15 * 60))
MORNING_MINUTES = SESSIONS[0][1] - SESSIONS[0][0]     # 上午 120 分钟
TOTAL_MINUTES = 240                                   # 全天分钟数（午休不占宽度）

AVG_MIN_RATIO = 0.5      # 均价 / 价格 的合理区间；越界说明该品种的量额口径不同
AVG_MAX_RATIO = 2.0      # （指数就是这样：成交额÷成交量算出来是十几，不是点位）


def _num(value, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return default if result != result else result


def _hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _sane_average(avg: float | None, price: float) -> float | None:
    """均价必须与价格同量级，越界就丢掉。

    指数的成交量/成交额口径和股票不同，成交额÷成交量算出来是十几而不是点位；
    这种均价留着不只是画歪一条线 —— 纵轴会被它撑到几千点，价格线直接压成直线。
    """
    if avg is None or price <= 0:
        return avg
    return avg if AVG_MIN_RATIO <= avg / price <= AVG_MAX_RATIO else None


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
        """按最新价算涨跌幅：只有行情、还没有分钟线时也要算得出来。"""
        if self.prev_close <= 0 or self.last_price <= 0:
            return 0.0
        return (self.last_price - self.prev_close) / self.prev_close * 100.0

    @property
    def direction(self) -> int:
        if self.prev_close <= 0 or self.last_price <= 0:
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
    """把分钟线原始记录（含多个交易日）转成当天的分时序列。

    均价 = 累计成交额 ÷ 累计成交量；**累计**量为 0 时均价为 None（源头不给量），
    某一分钟量为 0 时均价沿用累计值，正常连线。

    输入**不要求有序**（接口返回顺序不做保证）：输出按时间升序，兜底昨收取
    "时间上最晚"的那根前一日收盘价，而不是遍历顺序上的最后一根。
    """
    rows = []
    earlier_stamp = ""
    earlier_close = 0.0
    for row in records or []:
        stamp = str(row.get("day", ""))[:16]        # 'YYYY-MM-DD HH:MM'
        if len(stamp) < 16:
            continue
        row_day, hhmm = stamp[:10], stamp[11:16]
        close = _num(row.get("close"))
        if row_day < day:
            if stamp >= earlier_stamp:              # 取"时间上最晚"的前一日那根，同一日内也按时间比较
                earlier_stamp, earlier_close = stamp, close
            continue
        if row_day != day:
            continue
        rows.append((hhmm, close, _num(row.get("volume")), _num(row.get("amount"))))
    rows.sort(key=lambda item: item[0])             # 'HH:MM' 零填充，字典序即时间序；均价必须按时间累计，所以先排序再累计

    points: list[MinutePoint] = []
    total_amount = 0.0
    total_volume = 0.0
    for hhmm, close, volume, amount in rows:
        total_volume += volume
        total_amount += amount
        avg = total_amount / total_volume if total_volume > 0 else None
        points.append(MinutePoint(time=hhmm, price=close, avg=_sane_average(avg, close), volume=volume or None))
    return IntradaySeries(code=code, day=day, prev_close=prev_close or earlier_close, points=points)


def resolve_prev_close(quote_prev_close: float, records, day: str) -> float:
    """昨收优先用行情通道给的值，缺失时回退到分钟线里今天之前的最后一根收盘价。

    同样不依赖输入顺序：按完整时间戳取最晚的那根（同一前一日内的多行也按时间比），
    而不是遍历中最后遇到的那根。
    """
    if quote_prev_close > 0:
        return quote_prev_close
    last_stamp = ""
    last_close = 0.0
    for row in records or []:
        stamp = str(row.get("day", ""))[:16]
        if stamp and stamp[:10] < day and stamp >= last_stamp:
            last_stamp, last_close = stamp, _num(row.get("close"))
    return last_close


def attach_latest(series: IntradaySeries, price: float) -> IntradaySeries:
    """接上 10 秒轮询的最新价；价格无效时保留原值。"""
    if price > 0:
        series.latest_price = price
    return series


def effective_points(series: IntradaySeries, now: "datetime | None" = None) -> list[MinutePoint]:
    """画图用的点序列：分钟线 + 一个"当前时刻"的实时点。

    - 没有实时价：原样返回分钟线，不就地修改 `series.points`；
    - 还没有分钟线但有实时价（冷启动、分钟线接口失败）：退化成"单点序列"，
      否则这类标的会整片显示"暂无数据"；
    - 实时点时间晚于最后一根：追加一个临时点；时间没往前走：就地更新最后一根。
    """
    points = list(series.points)
    if series.latest_price is None:
        return points
    stamp = clamp_to_session(now or datetime.now())
    if not points:
        return [MinutePoint(time=stamp, price=series.latest_price)]
    last = points[-1]
    if stamp <= last.time:
        points[-1] = MinutePoint(time=last.time, price=series.latest_price, avg=last.avg, volume=last.volume)
        return points
    points.append(MinutePoint(time=stamp, price=series.latest_price, avg=last.avg))
    return points


def series_from_rows(code: str, day: str, rows) -> IntradaySeries:
    """从本地库读出的 [(hhmm, price, avg, volume)] 还原成序列。

    库里可能存着早期算歪的均价（指数那种口径），这里再过一遍，免得旧数据把图弄坏
    —— 重启后先显示的就是这批数据。
    """
    points = [
        MinutePoint(time=hhmm, price=price, avg=_sane_average(avg, price), volume=volume)
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
    """把行情快照与分钟线合成每只的分时序列（含实时价）。

    只有分钟线、没有行情快照的代码也要出序列（指数通道掉线、某只行情偶发失败时就是
    这种情况）：昨收从分钟线里推、最新价取最后一根 K 线。否则这只的分时图会一直停在
    旧数据上 —— 没有旧数据时缩略图就退化成一条灰色的占位横线。
    """
    result = {}
    for quote in quotes:
        records = bars_by_code.get(quote.code) or []
        prev_close = resolve_prev_close(quote.prev_close, records, day)
        series = build_series(quote.code, records, day, prev_close)
        if quote.price > 0:
            series.latest_price = quote.price
        result[quote.code] = series
    for code, records in (bars_by_code or {}).items():
        if code in result or not records:
            continue
        series = build_series(code, records, day, resolve_prev_close(0.0, records, day))
        if series.points:
            series.latest_price = series.points[-1].price     # 没有行情快照，就用最后一根 K 线
        result[code] = series
    return result
