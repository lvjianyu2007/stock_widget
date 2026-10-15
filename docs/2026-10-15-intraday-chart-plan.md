# 当天实时分时图 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 把每天的行情数据（分钟级 + 10 秒原始快照）暂存到本地 SQLite，并画出当天 09:30–15:00 的实时分时图（含均价线与昨收基准线），提供「独立窗口」与「行内缩略图」两种呈现。

**架构：** 新增 `store.py`（落盘）与 `intraday.py`（K 线记录 → 分时序列的纯转换）两层；`datasource` 增加逐只拉分钟线的能力；`QuoteFetcher` 在同一线程里跑两个节奏（行情 10 秒、分钟线 60 秒），落盘后通过 `minutes_ready` 信号交给 UI；`chart.py` 的 `IntradayPainter` 同时服务行内缩略图与独立窗口，`panel.py` 提供两者共用的面板绘制。

**技术栈：** Python 3.11+、PySide6 6.6+、akshare 1.12+、标准库 `sqlite3` / `unittest`（**不引入 pytest**）。

**规格来源：** `D:\work\stock_widget\2026-10-15-intraday-chart-design.md`

---

## 前置说明

- **本项目未纳入 git 管理**，因此计划中**没有 commit 步骤**；每个任务以「运行测试 + 目视验证」收尾。
- 所有命令都在项目目录 `D:\work\stock_widget` 下执行，使用项目自带的虚拟环境：
  - 跑全部测试：`.\.venv\Scripts\python.exe -m unittest discover -s tests -t . -v`
  - 跑单个用例：`.\.venv\Scripts\python.exe -m unittest tests.test_store -v`
  - 跑程序：`.\.venv\Scripts\python.exe main.py`
- 代码换行约定：单行不超过 160 字符；未超限**不要**主动换行，参数不要一个一行。
- mysql 无、网络访问 akshare 由项目已有的 `socket.setdefaulttimeout(12.0)` 兜底。

## 文件结构

| 文件 | 状态 | 职责 |
|---|---|---|
| `store.py` | 新建 | SQLite 落盘：`tick`（10 秒快照）+ `minute`（分钟级），只保留当天 |
| `intraday.py` | 新建 | 分钟线原始记录 → 当天分时序列（纯函数，无网络/无数据库） |
| `panel.py` | 新建 | 面板几何常量 + 圆角/投影/把手绘制，主窗口与分时窗口共用 |
| `chart.py` | 新建 | `IntradayPainter`（坐标映射与线条）+ `SparklineWidget` + `IntradayChartWindow` |
| `tests/__init__.py` | 新建 | 空文件，让 `tests` 成为包 |
| `tests/test_store.py` | 新建 | 落盘层的读写与清理 |
| `tests/test_intraday.py` | 新建 | 分时序列装配的纯逻辑 |
| `tests/test_datasource.py` | 新建 | `_sina_symbol` 指数前缀修复 |
| `tests/test_chart.py` | 新建 | 坐标映射与取值范围（不依赖 QApplication） |
| `datasource.py` | 修改 | 修 `_sina_symbol`；新增 `fetch_minute_bars`；`QuoteService` 转发 |
| `config.py` | 修改 | `DEFAULTS` 新增 `show_sparkline` |
| `settings_dialog.py` | 修改 | 新增「显示行内分时缩略图」勾选框 |
| `main.py` | 修改 | 面板常量改为导入；取数节奏、落盘、`minutes_ready`、缩略图与窗口接入 |
| `README.md` | 修改 | 功能、配置表、文件结构、数据通道、版本记录 |

依赖方向（无环）：`main → chart → {intraday, panel, config, datasource}`，`main → {store, intraday, datasource}`，`store → config`。

---

## 任务 1：落盘层 `store.py`

**文件：**
- 创建：`store.py`
- 创建：`tests/__init__.py`
- 测试：`tests/test_store.py`

- [ ] **步骤 1：创建空的 `tests/__init__.py`**

```python
```
（内容为空，只是为了 `python -m unittest discover` 能把 `tests` 当包导入。）

- [ ] **步骤 2：编写失败的测试**

写入 `tests/test_store.py`：

```python
"""本地暂存层：读写、覆盖、跨线程、按天清理。"""
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from store import Store, day_start_ts


class StoreTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self._tmp.name) / "data" / "quote.db")

    def tearDown(self):
        self.store.close()
        self._tmp.cleanup()

    def test_save_then_load_minutes_in_time_order(self):
        self.store.save_minutes([
            ("600519", "2026-10-15", "09:32", 1501.0, 1500.2, 1200.0),
            ("600519", "2026-10-15", "09:31", 1500.0, 1499.5, 800.0),
        ])
        rows = self.store.load_minutes("600519", "2026-10-15")
        self.assertEqual([row[0] for row in rows], ["09:31", "09:32"])
        self.assertEqual(rows[0][1], 1500.0)
        self.assertEqual(rows[0][2], 1499.5)
        self.assertEqual(rows[0][3], 800.0)

    def test_save_minutes_overwrites_same_key(self):
        self.store.save_minutes([("600519", "2026-10-15", "09:31", 1500.0, None, None)])
        self.store.save_minutes([("600519", "2026-10-15", "09:31", 1502.0, 1501.0, 900.0)])
        rows = self.store.load_minutes("600519", "2026-10-15")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], 1502.0)

    def test_load_minutes_ignores_other_day_and_code(self):
        self.store.save_minutes([
            ("600519", "2026-10-14", "09:31", 1400.0, None, None),
            ("000001", "2026-10-15", "09:31", 11.0, None, None),
        ])
        self.assertEqual(self.store.load_minutes("600519", "2026-10-15"), [])

    def test_load_ticks_only_returns_given_day(self):
        day = "2026-10-15"
        start = day_start_ts(day)
        self.store.save_ticks([
            ("600519", start - 10, 1490.0),
            ("600519", start + 10, 1500.0),
            ("600519", start + 86400 + 5, 1510.0),
        ])
        rows = self.store.load_ticks("600519", day)
        self.assertEqual([row[1] for row in rows], [1500.0])

    def test_save_ticks_overwrites_same_timestamp(self):
        day = "2026-10-15"
        ts = day_start_ts(day) + 10
        self.store.save_ticks([("600519", ts, 1500.0), ("600519", ts, 1501.0)])
        self.assertEqual(len(self.store.load_ticks("600519", day)), 1)

    def test_purge_before_keeps_only_target_day(self):
        self.store.save_minutes([
            ("600519", "2026-10-14", "09:31", 1400.0, None, None),
            ("600519", "2026-10-15", "09:31", 1500.0, None, None),
        ])
        start = day_start_ts("2026-10-15")
        self.store.save_ticks([("600519", start - 1, 1400.0), ("600519", start + 1, 1500.0)])

        self.store.purge_before("2026-10-15")

        self.assertEqual(self.store.load_minutes("600519", "2026-10-14"), [])
        self.assertEqual(len(self.store.load_minutes("600519", "2026-10-15")), 1)
        self.assertEqual(len(self.store.load_ticks("600519", "2026-10-15")), 1)

    def test_other_thread_can_write_and_main_thread_read(self):
        def worker():
            store = Store(self.store.path)
            store.save_minutes([("600519", "2026-10-15", "09:31", 1500.0, None, None)])
            store.close()

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()
        self.assertEqual(len(self.store.load_minutes("600519", "2026-10-15")), 1)

    def test_close_releases_other_thread_connections(self):
        """close() 要能收回取数线程的连接，否则 Windows 上 data 目录一直被占用。

        取数线程与 UI 线程共用同一个 Store 实例（各自持有自己的连接）。
        """
        store = Store(self.store.path)
        captured = {}

        def worker():
            captured["conn"] = store._conn()
            store.save_minutes([("600519", "2026-10-15", "09:31", 1500.0, None, None)])

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()

        store.close()

        with self.assertRaises(sqlite3.ProgrammingError):
            captured["conn"].execute("SELECT 1")
        self.assertEqual(len(store._connections), 0)

    def test_store_can_be_used_again_after_close(self):
        self.store.save_minutes([("600519", "2026-10-15", "09:31", 1500.0, None, None)])
        self.store.close()
        self.store.save_minutes([("600519", "2026-10-15", "09:32", 1501.0, None, None)])
        self.assertEqual(len(self.store.load_minutes("600519", "2026-10-15")), 2)

    def test_creates_database_directory(self):
        self.assertTrue(self.store.path.exists())
```

- [ ] **步骤 3：运行测试验证失败**

运行：`.\.venv\Scripts\python.exe -m unittest discover -s tests -t . -v`
预期：FAIL，报错 `ModuleNotFoundError: No module named 'store'`

- [ ] **步骤 4：实现 `store.py`**

写入 `store.py`：

```python
"""本地暂存：行情快照与分钟线落盘到 SQLite，只保留当天数据。

写入发生在取数线程、读取发生在 UI 线程，而 sqlite3 连接不能跨线程共用，
所以每个线程各自持有一个连接（threading.local）；WAL 模式下读写互不阻塞。
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from config import BASE_DIR

DB_PATH = BASE_DIR / "data" / "quote.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tick (
    code  TEXT    NOT NULL,
    ts    INTEGER NOT NULL,
    price REAL    NOT NULL,
    PRIMARY KEY (code, ts)
);
CREATE TABLE IF NOT EXISTS minute (
    code   TEXT NOT NULL,
    day    TEXT NOT NULL,
    hhmm   TEXT NOT NULL,
    price  REAL NOT NULL,
    avg    REAL,
    volume REAL,
    PRIMARY KEY (code, day, hhmm)
);
CREATE INDEX IF NOT EXISTS idx_minute_day ON minute (day);
"""


def day_start_ts(day: str) -> int:
    """某天 00:00 的 unix 秒（本地时区）；tick 表按它查询与清理。"""
    return int(datetime.strptime(str(day), "%Y-%m-%d").timestamp())


class Store:
    """一天一清的本地行情库。"""

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path is not None else DB_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._lock = threading.Lock()
        self._connections: dict[int, sqlite3.Connection] = {}   # 线程 ident -> 连接；close() 要能全部收回
        self._conn()                      # 立刻建库建表，启动阶段就能暴露磁盘问题

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is not None and self._connections.get(threading.get_ident()) is conn:
            return conn
        with self._lock:
            # check_same_thread=False：每个连接只被创建它的线程使用，但 close() 要从
            # UI 线程回收取数线程的连接（sqlite3 默认会拒绝跨线程操作）
            conn = sqlite3.connect(str(self.path), timeout=5.0, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)
            self._connections[threading.get_ident()] = conn
        self._local.conn = conn
        return conn

    def save_ticks(self, rows) -> None:
        """rows = [(code, ts, price)]；同一 (code, ts) 覆盖写入。"""
        if not rows:
            return
        conn = self._conn()
        with conn:
            conn.executemany("INSERT OR REPLACE INTO tick (code, ts, price) VALUES (?, ?, ?)", rows)

    def save_minutes(self, rows) -> None:
        """rows = [(code, day, hhmm, price, avg, volume)]；同一分钟覆盖写入。"""
        if not rows:
            return
        conn = self._conn()
        with conn:
            conn.executemany(
                "INSERT OR REPLACE INTO minute (code, day, hhmm, price, avg, volume) VALUES (?, ?, ?, ?, ?, ?)",
                rows,
            )

    def load_minutes(self, code: str, day: str):
        """返回 [(hhmm, price, avg, volume)]，按时间升序。"""
        cursor = self._conn().execute(
            "SELECT hhmm, price, avg, volume FROM minute WHERE code = ? AND day = ? ORDER BY hhmm",
            (str(code), str(day)),
        )
        return list(cursor.fetchall())

    def load_ticks(self, code: str, day: str):
        """返回 [(ts, price)]，按时间升序。"""
        start = day_start_ts(day)
        cursor = self._conn().execute(
            "SELECT ts, price FROM tick WHERE code = ? AND ts >= ? AND ts < ? ORDER BY ts",
            (str(code), start, start + 86400),
        )
        return list(cursor.fetchall())

    def purge_before(self, day: str) -> None:
        """删掉指定日期之前的分钟线与快照（只保留当天）。"""
        start = day_start_ts(day)
        conn = self._conn()
        with conn:
            conn.execute("DELETE FROM minute WHERE day < ?", (str(day),))
            conn.execute("DELETE FROM tick WHERE ts < ?", (start,))

    def close(self) -> None:
        """关闭**所有线程**持有的连接（含取数线程），释放 db / -wal / -shm 的文件句柄。

        Windows 上句柄不释放会让 data 目录无法移动或删除，只关当前线程的连接不够用，
        所以这里按注册表逐一关闭。close() 之后再次使用会重新建立连接。
        """
        with self._lock:
            connections = list(self._connections.values())
            self._connections.clear()
        for conn in connections:
            try:
                conn.close()
            except Exception:
                pass          # 关闭失败不能让退出流程崩掉：句柄随进程结束回收
        self._local.conn = None
```

- [ ] **步骤 5：运行测试验证通过**

运行：`.\.venv\Scripts\python.exe -m unittest tests.test_store -v`
预期：10 个用例全部 PASS

---

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
预期：20 个用例全部 PASS

- [ ] **步骤 5：同步更新设计文档 5.2 节的均价规则**

把 `2026-10-15-intraday-chart-design.md` 中「`volume` 为 0 或缺失时 `avg = None`（绘制时跳过该点，不画断线误导）」改为：

```markdown
  - 逐行累计 `amount` 与 `volume` 得到 `avg`；**累计成交量为 0**（源头完全不提供量）时 `avg = None`，绘制时跳过该点；单分钟量为 0 时均价沿用累计值，正常连线；
```

---

### 任务 2 实施后修订（2026-10-15，审查后修复）

审查发现 3 条「重要」问题，下面这些是本任务最终落地的代码与上面代码块的差异，**再跑本任务时以这里的为准**：

1. `build_series`：先把当天的行收集成 `(hhmm, close, volume, amount)`，**排序后再累计均价**（不能先累计再排序，否则乱序输入下均价会按输入顺序累计）；`earlier_close` 取"时间上最晚"的前一日。
2. `resolve_prev_close`：改为按日期取最大（`stamp >= last_day`），不依赖输入顺序。
3. `IntradaySeries.change_pct` / `direction`：判断条件从「`not self.points`」改为「`self.last_price <= 0`」，无 K 线但有实时价时也要算得出涨跌幅。
4. `effective_points`：早退条件只判 `latest_price is None`；没有分钟线但有实时价时返回**单点序列**（时间用钳位后的当前时刻），避免冷启动/接口失败时整片空白。
5. 新增 3 个用例：乱序输入的升序与均价、乱序昨收、无 K 线退化为单点。测试数 17 → **20**。
6. 设计文档 5.2 节的均价规则与测试策略表已同步修订版本。

## 任务 3：`datasource.py` 分钟线能力

**文件：**
- 修改：`datasource.py`（`_sina_symbol` 约 123-125 行；`AkshareProvider.__init__` 约 250-258 行；`AkshareProvider` 末尾新增方法；`QuoteService` 约 770 行起）
- 测试：`tests/test_datasource.py`

- [ ] **步骤 1：编写失败的测试**

写入 `tests/test_datasource.py`：

```python
"""新浪代码拼装：指数带前缀时不能重复拼前缀。"""
import unittest

from datasource import _is_index_code, _sina_symbol


class SinaSymbolTest(unittest.TestCase):
    def test_stock_and_fund_prefixes(self):
        self.assertEqual(_sina_symbol("600519"), "sh600519")
        self.assertEqual(_sina_symbol("000001"), "sz000001")
        self.assertEqual(_sina_symbol("300750"), "sz300750")
        self.assertEqual(_sina_symbol("588000"), "sh588000")
        self.assertEqual(_sina_symbol("159915"), "sz159915")

    def test_index_code_keeps_its_prefix(self):
        self.assertEqual(_sina_symbol("sh000001"), "sh000001")
        self.assertEqual(_sina_symbol("sz399006"), "sz399006")

    def test_is_index_code(self):
        self.assertTrue(_is_index_code("sh000001"))
        self.assertTrue(_is_index_code("sz399001"))
        self.assertFalse(_is_index_code("000001"))
        self.assertFalse(_is_index_code("600519"))
```

- [ ] **步骤 2：运行测试验证失败**

运行：`.\.venv\Scripts\python.exe -m unittest tests.test_datasource -v`
预期：FAIL，`test_index_code_keeps_its_prefix` 报 `'szsh000001' != 'sh000001'`

- [ ] **步骤 3：修 `_sina_symbol`**

把 `datasource.py` 中的 `_sina_symbol` 整体替换为：

```python
def _sina_symbol(code: str) -> str:
    """新浪接口用的代码：指数（sh000001）已自带前缀，原样返回；其余按沪市 sh、深市 sz 拼。"""
    text = str(code).strip()
    if _is_index_code(text):
        return text
    return ("sh" if text[:1] in ("5", "6", "9") else "sz") + text
```

- [ ] **步骤 4：运行测试验证通过**

运行：`.\.venv\Scripts\python.exe -m unittest tests.test_datasource -v`
预期：3 个用例全部 PASS

- [ ] **步骤 5：给 `AkshareProvider` 加分钟线接口**

在 `AkshareProvider.__init__` 里，紧跟 `self._index_blocked_until = 0.0` 之后补一行：

```python
        self._minute_bars_blocked_until = 0.0   # 分钟线通道的冷却截止时间
```

在 `AkshareProvider` 的 `_fetch_indexes` 方法**之前**插入以下三个成员（`MINUTE_BUDGET` 常量紧跟类已有的 `ROUND_BUDGET` 之后声明）：

```python
    MINUTE_BUDGET = 12.0     # 逐只拉分钟线的总时长上限（秒）
```

```python
    def fetch_minute_bars(self, codes: list[str]) -> dict:
        """逐只拉当天分钟线，返回 代码 -> 原始 bar 记录（一次约 8 个交易日，按天过滤交给上层）。

        与行情通道一样是"一只一个请求"，复用同一套并发与时间预算；
        一只都没拿到就进冷却，避免被限流时每轮都空跑一遍。
        """
        import akshare as ak

        if time.monotonic() < self._minute_bars_blocked_until:
            raise RuntimeError("分钟线通道冷却中")
        targets = [code for code in codes if not _is_bj_code(code)]
        if not targets:
            return {}
        rows, errors = self._per_code(targets, lambda code: self._minute_bars(ak, code), self.MINUTE_BUDGET)
        if not rows:
            self._minute_bars_blocked_until = time.monotonic() + self.COOLDOWN
            raise RuntimeError("；".join(errors[:2]) or "分钟线没有取到数据")
        return rows

    @staticmethod
    def _minute_bars(ak, code: str) -> list[dict] | None:
        frame = ak.stock_zh_a_minute(symbol=_sina_symbol(code), period="1", adjust="")
        if frame is None or len(frame) == 0:
            return None
        return frame.to_dict("records")
```

在 `QuoteService` 的 `fetch` 方法**之后**插入：

```python
    def fetch_minute_bars(self, codes: list[str]) -> dict:
        """分钟线只走 akshare 主通道：pytdx 不提供分钟线，失败由上层冷却并保留旧数据。"""
        return self._providers["akshare"].fetch_minute_bars(codes)
```

- [ ] **步骤 6：手工验证分钟线能真的取到数据**

运行（需要联网，交易时段或收盘后均可）：

```powershell
.\.venv\Scripts\python.exe -c "from datasource import QuoteService; s = QuoteService('akshare', '', {}); bars = s.fetch_minute_bars(['600519', 'sh000001', '588000']); print({code: len(rows) for code, rows in bars.items()})"
```

预期：打印形如 `{'600519': 1970, 'sh000001': 1970, '588000': 1970}`（数量级 1000+ 即正常，说明股票/指数/ETF 都覆盖）
若打印 `RuntimeError: 分钟线通道冷却中`，等待 5 分钟后重试；若报 `没有取到数据`，检查网络。

---

## 任务 4：抽出 `panel.py` 并让主窗口复用

**文件：**
- 创建：`panel.py`
- 修改：`main.py`（常量声明约 32-38 行；`paintEvent` 约 375-418 行；`_panel_rect` 约 366-373 行；`_hit_region` 约 505-519 行）

> 这是**纯搬移**：绘制结果与交互行为必须完全不变。改动完成后按步骤 4 目视回归。

- [ ] **步骤 1：创建 `panel.py`**

写入 `panel.py`：

```python
"""面板外观：几何常量与圆角/投影/把手绘制，主窗口与分时图窗口共用。"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPen

SHADOW = 14                      # 面板四周留给投影的空白
PANEL_RADIUS = 12                # 面板圆角
BORDER = 6                       # 四条边上的缩放热区
GRIP = 18                        # 右下角缩放热区边长
MIN_PANEL = (180, 96)            # 面板最小尺寸（不含投影）


def panel_rect(width: int, height: int) -> QRectF:
    """面板区域，四周留出 SHADOW 做投影。"""
    return QRectF(SHADOW, SHADOW, max(1.0, width - 2 * SHADOW), max(1.0, height - 2 * SHADOW))


def draw_shadow(painter: QPainter, panel: QRectF, opacity: float) -> None:
    """由外向内叠若干层圆角矩形，越靠里越深，观感接近柔和阴影。"""
    painter.setPen(Qt.NoPen)
    for step in range(SHADOW, 0, -1):
        ratio = 1.0 - step / SHADOW
        alpha = int(36 * ratio * ratio * opacity)
        if alpha <= 0:
            continue
        painter.setBrush(QColor(0, 0, 0, alpha))
        radius = PANEL_RADIUS + step * 0.8
        painter.drawRoundedRect(panel.adjusted(-step, -step + 3, step, step + 3), radius, radius)


def draw_background(painter: QPainter, panel: QRectF, bg_color: str, opacity: float) -> None:
    """圆角背景 + 一圈很淡的高光描边。"""
    background = QColor(bg_color)
    background.setAlphaF(opacity)
    painter.setBrush(QBrush(background))
    painter.setPen(QPen(QColor(255, 255, 255, int(46 * opacity)), 1))
    painter.drawRoundedRect(panel, PANEL_RADIUS, PANEL_RADIUS)


def draw_grip(painter: QPainter, panel: QRectF, opacity: float) -> None:
    """右下角的缩放把手提示（两道斜线）。"""
    right, bottom = int(panel.right()), int(panel.bottom())
    painter.setPen(QPen(QColor(255, 255, 255, int(95 * opacity)), 1.5))
    for offset in (6, 10):
        painter.drawLine(right - offset, bottom - 6, right - 6, bottom - offset)


def draw_hover_outline(painter: QPainter, panel: QRectF, region: str) -> None:
    """面板全透明时的边缘虚线提示，否则鼠标根本找不到缩放位置。"""
    painter.setBrush(Qt.NoBrush)
    painter.setPen(QPen(QColor(255, 255, 255, 110), 1, Qt.DashLine))
    painter.drawRoundedRect(panel, PANEL_RADIUS, PANEL_RADIUS)
    if region == "br":
        right, bottom = int(panel.right()), int(panel.bottom())
        painter.setPen(QPen(QColor(255, 255, 255, 150), 1.5))
        for offset in (6, 10):
            painter.drawLine(right - offset, bottom - 6, right - 6, bottom - offset)
```

- [ ] **步骤 2：`main.py` 改为复用 `panel.py`**

2a. 删除 `main.py` 里这 6 行常量声明（`WARN_COLOR` 保留）：

```python
SHADOW = 14                      # 面板四周留给投影的空白
PANEL_RADIUS = 12                # 面板圆角
BORDER = 6                       # 四条边上的缩放热区
GRIP = 18                        # 右下角缩放热区边长
MIN_PANEL = (180, 96)            # 面板最小尺寸（不含投影）
```

2b. 导入区改为：

```python
from PySide6.QtCore import QRect, Qt, QThread, Signal
from PySide6.QtGui import QAction, QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMenu,
    QVBoxLayout,
    QWidget,
)

from config import NUM_FONT, UI_FONT, Config
from datasource import Quote, QuoteService, market_state
from panel import BORDER, GRIP, MIN_PANEL, SHADOW, draw_background, draw_grip, draw_hover_outline, draw_shadow, panel_rect
from settings_dialog import SettingsDialog
```

（`QRectF` / `QBrush` / `QPen` 在搬移后不再被 `main.py` 使用，一并从导入里去掉。）

2c. 删掉 `_panel_rect` 方法，把 `paintEvent` 整体替换为：

```python
    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        panel = panel_rect(self.width(), self.height())
        opacity = max(0.0, min(1.0, float(self.config["opacity"])))
        if opacity > 0.0:
            draw_shadow(painter, panel, opacity)
            draw_background(painter, panel, self.config["bg_color"], opacity)
            draw_grip(painter, panel, opacity)
            painter.end()
            return
        # 面板全透明时，鼠标贴到边缘给个淡淡的方向线，否则根本找不到缩放位置
        if self._hover_region and self._hover_region != "move":
            draw_hover_outline(painter, panel, self._hover_region)
        painter.end()
```

2d. `_hit_region` 第一行改为：

```python
        panel = panel_rect(self.width(), self.height()).toRect()
```

- [ ] **步骤 3：静态检查**

运行：`.\.venv\Scripts\python.exe -m py_compile main.py panel.py`
预期：无输出（编译通过）
再运行：`.\.venv\Scripts\python.exe -c "import main"`
预期：无输出、无异常（说明没有残留的未定义名字）

- [ ] **步骤 4：目视回归（行为必须与改动前一致）**

运行：`.\.venv\Scripts\python.exe main.py`，逐项确认：

- [ ] 圆角背景、投影深浅、右下角两道斜线把手与改动前一致
- [ ] 左键按住任意位置能拖动窗口
- [ ] 四边、四角鼠标指针形状正确、能缩放；右下角把手能缩放
- [ ] 设置里把「背景不透明度」拖到 0% 后，鼠标贴边出现虚线提示
- [ ] 关闭程序后 `config.json` 里的 `pos_x/pos_y/width/height` 正常更新

---

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

### 任务 5 补充要求（任务 2 复审时发现，实施时必须带上）

任务 2 让 `effective_points` 在"只有行情、没有分钟线"时退化为**单点序列**（真实场景：冷启动第一秒、分钟线接口失败、当天无成交）。而任务 5 的 `_draw_line` 有 `if line.count() < 2: return`，单点会被整条跳过，图上只剩基准线与刻度。设计 5.2 要求这种情况下"图上只有一个点加实时价"。

因此任务 5 需要额外做两件事：

1. `IntradayPainter` 增加一个可测的坐标换算辅助方法，并在只有 1 个可绘制点时画一个圆点（不要画折线）：

```python
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
```

   `_draw_line` 改为先取 `mapped_points`，少于 2 个点就直接返回；`draw()` 里在价格折线少于 2 个点时用线色画一个半径 2.5 的圆点（`painter.setBrush(color); painter.drawEllipse(coord, 2.5, 2.5)`）。

2. 补两个用例：`mapped_points` 对单点返回长度为 1 的坐标；`mapped_points` 会跳过均价为 `None` 的点（`use_avg=True` 时）。

## 任务 6：独立分时图窗口 `IntradayChartWindow`（原型 A）

**文件：**
- 修改：`chart.py`（在文件末尾追加）
- 修改：`tests/test_chart.py`（追加缩略图尺寸断言由任务 8 负责，本任务不加测试）

- [ ] **步骤 1：追加窗口类到 `chart.py` 末尾**

```python
class IntradayChartWindow(QWidget):
    """独立分时图窗口：无边框、可拖动、右下角可缩放、可置顶。"""

    MIN_SIZE = (360, 240)
    DEFAULT_SIZE = (640, 360)
    AXIS_WIDTH = 66              # 右侧价格刻度宽度
    TIME_AXIS_HEIGHT = 20        # 底部时间刻度高度
    HEADER_HEIGHT = 26
    MIN_OPACITY = 0.35           # 图表需要底衬，背景不透明度不跟随面板设到 0

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

        self.setWindowTitle(f"分时图 · {self._name}")
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
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
        chart = self._chart_rect(panel)
        if self._series is None or not self._series.points:
            painter.setFont(QFont(UI_FONT, 11))
            painter.setPen(QPen(self._brush.text_color()))
            painter.drawText(chart, Qt.AlignCenter, "暂无分时数据")
            painter.end()
            return
        self._draw_axes(painter, chart)
        self._brush.draw(painter, chart, self._series)
        painter.end()

    def _chart_rect(self, panel: QRectF) -> QRectF:
        return QRectF(
            panel.left() + 12,
            panel.top() + self.HEADER_HEIGHT + 10,
            max(1.0, panel.width() - 24 - self.AXIS_WIDTH),
            max(1.0, panel.height() - self.HEADER_HEIGHT - self.TIME_AXIS_HEIGHT - 18),
        )

    def _draw_header(self, painter: QPainter, panel: QRectF) -> None:
        header = QRectF(panel.left() + 12, panel.top() + 6, panel.width() - 24, self.HEADER_HEIGHT)
        size = max(9, int(self._style["font_size"]) - 2)
        painter.setFont(QFont(UI_FONT, size))
        painter.setPen(QPen(self._brush.text_color()))
        painter.drawText(header, Qt.AlignLeft | Qt.AlignVCenter, f"{self._name}  {self.code}")
        series = self._series
        if series is None or not series.points:
            return
        if series.direction > 0:
            color = QColor(self._style["up_color"])
        elif series.direction < 0:
            color = QColor(self._style["down_color"])
        else:
            color = QColor(self._style["flat_color"])
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
        for index, text in ((0, "09:30"), (60, "10:30"), (120, "11:30/13:00"), (180, "14:00"), (240, "15:00")):
            x = self._brush.map_x(index, chart)
            painter.setPen(QPen(self._brush.text_color()))
            painter.drawText(QRectF(x - 28, chart.bottom() + 2, 56, self.TIME_AXIS_HEIGHT - 2), Qt.AlignCenter, text)
        if self._updated:
            painter.setFont(QFont(UI_FONT, max(8, int(self._style["font_size"]) - 5)))
            painter.setPen(QPen(self._brush.text_color()))
            footer = f"{self._source} · {self._updated} 更新"
            if self._warning:
                footer += f"（{self._warning}）"
            painter.drawText(QRectF(chart.left(), chart.bottom() + 2, chart.width(), self.TIME_AXIS_HEIGHT - 2), Qt.AlignLeft | Qt.AlignVCenter, footer)

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
        if 0 <= panel.right() - pos.x() <= GRIP and 0 <= panel.bottom() - pos.y() <= GRIP:
            self._resizing = True
            self._resize_origin = event.globalPosition().toPoint()
            self._resize_geom = QRect(self.geometry())
        else:
            self._drag_offset = event.globalPosition().toPoint() - self.pos()
        event.accept()

    def mouseMoveEvent(self, event) -> None:
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
        self._drag_offset = None
        self._resizing = False
        self._resize_origin = None
        self._resize_geom = None
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event) -> None:
        menu = QMenu(self)
        menu.addAction("立即刷新", self._refresh)
        on_top = menu.addAction("窗口置顶")
        on_top.setCheckable(True)
        on_top.setChecked(bool(self.windowFlags() & Qt.WindowStaysOnTopHint))
        on_top.triggered.connect(self._toggle_on_top)
        menu.addSeparator()
        menu.addAction("关闭", self.close)
        menu.exec(event.globalPos())

    def _refresh(self) -> None:
        """转交给主窗口：分时数据由取数线程统一提供。"""
        parent = self.parent()
        if parent is not None and hasattr(parent, "request_refresh"):
            parent.request_refresh()

    def _toggle_on_top(self, checked: bool) -> None:
        position = self.pos()
        self.setWindowFlags(Qt.FramelessWindowHint | (Qt.WindowStaysOnTopHint if checked else Qt.WindowType(0)))
        self.show()
        self.move(position)
```

- [ ] **步骤 2：静态检查**

运行：`.\.venv\Scripts\python.exe -m py_compile chart.py`
预期：无输出
运行：`.\.venv\Scripts\python.exe -m unittest tests.test_chart -v`
预期：12 个用例仍全部 PASS

- [ ] **步骤 3：目视验证（先造一段假数据，避免等开盘）**

运行：

```powershell
.\.venv\Scripts\python.exe -c "import sys; from PySide6.QtWidgets import QApplication; from chart import IntradayChartWindow; from intraday import MinutePoint, IntradaySeries; app = QApplication(sys.argv); w = IntradayChartWindow('600519', '贵州茅台'); s = IntradaySeries(code='600519', day='2026-10-15', prev_close=100.0); s.points = [MinutePoint(time=f'{9 + (30 + i) // 60:02d}:{(30 + i) % 60:02d}', price=100 + ((i % 40) - 20) * 0.08, avg=100 + ((i % 30) - 15) * 0.05) for i in range(120)]; s.latest_price = 101.2; w.apply_style({'opacity': 0.9, 'font_size': 14, 'font_opacity': 1.0, 'bg_color': '#101722', 'up_color': '#F05A4F', 'down_color': '#2BC48A', 'flat_color': '#FFFFFF', 'font_color': '#E8EEF7'}); w.set_series(s, 'akshare·新浪分钟', '14:03:11', ''); w.show(); app.exec()"
```

逐项确认：

- [ ] 窗口无系统标题栏，圆角 + 投影与主窗口风格一致
- [ ] 左上角显示「贵州茅台 600519」，右上角价格与涨跌幅为红色（涨）
- [ ] 画出了价格折线（红）、黄色均价线、中间一条昨收虚线
- [ ] 右侧有三档价格刻度、底边有 09:30 / 10:30 / 11:30|13:00 / 14:00 / 15:00 时间刻度
- [ ] 左键拖动可移动窗口；右下角可缩放
- [ ] 右键菜单三项都在：「立即刷新 / 窗口置顶 / 关闭」，置顶可切换

---

## 任务 7：`main.py` 取数节奏、落盘与窗口接入

**文件：**
- 修改：`main.py`（`StockWidget.__init__`、`QuoteFetcher`、`_open_chart`、`contextMenuEvent`、`closeEvent`）
- 修改：`config.py`（`DEFAULTS` 增加 `show_sparkline`）

- [ ] **步骤 1：`config.py` 的 `DEFAULTS` 增加配置项**

在 `"show_source": True,` 之后插入一行：

```python
    "show_sparkline": True,    # 行内迷你分时缩略图（关掉就只留独立分时窗口）
```

- [ ] **步骤 2：`main.py` 导入补充**

导入区加入：

```python
import time

from chart import IntradayChartWindow
import intraday
from store import Store
```

并把 `QtCore` 那行改为（`QPoint` 是 `_chart_position` 需要的）：

```python
from PySide6.QtCore import QPoint, QRect, Qt, QThread, Signal
```

（`time`、`QPoint`、`intraday`、`Store` 原先都没有；`datetime` 已有。）

- [ ] **步骤 3：`QuoteFetcher` 增加落盘与分钟线节奏**

3a. 类属性与信号：

```python
    MINUTE_INTERVAL = 60.0           # 分钟线刷新间隔（秒）

    minutes_ready = Signal(dict)     # code -> IntradaySeries
```

3b. `__init__` 增加参数与状态（签名改为 `def __init__(self, service: QuoteService, store=None, parent=None):`）：

```python
        self._store = store
        self._minute_since = 0.0
        self._closed_state = ""
        self.store_failed = ""
```

3c. `run()` 整体替换为下面这版（原有逻辑全部保留，只多了开头的"库里当天数据"恢复、行情落盘、以及分钟线节奏）：

```python
    def run(self) -> None:
        self._restore_series()
        while not self._stop.is_set():
            self._wake.clear()
            codes = list(self._codes)
            state = market_state()
            if state != self._state:
                self._state = state
                self.state_changed.emit(state)
            live = state == "交易中"
            force = self._force
            self._force = False
            done = self._closed_done or self._closed_fails >= self.CLOSED_RETRY

            # 休盘：已经成功取过一次（或失败次数用完了）就不再发请求，只等开盘
            if not live and not force and done:
                self._wake.wait(self.CLOSED_POLL)
                continue

            fetched_ok = False
            quotes: list = []
            if codes:
                self.started_fetch.emit()
                try:
                    quotes, source, warning = self._service.fetch(codes)
                    self._save_ticks(quotes)
                    self.ready.emit(quotes, source, warning)
                    fetched_ok = bool(source)
                except Exception as exc:
                    self.ready.emit([], "", str(exc))

            # 分钟线：交易中每分钟一次；状态刚切到休盘时补一次（收盘那次拿到全天数据）
            if quotes and self._minute_due(state):
                self._fetch_minutes(quotes)

            if live:
                self._closed_done = False
                self._closed_fails = 0
            else:
                # 休盘那次没取到（网络抖了）就隔一会儿再试，最多 CLOSED_RETRY 次；
                # 成功了就安静下来，直到开盘前不再打扰接口
                self._closed_done = fetched_ok
                self._closed_fails = 0 if fetched_ok else self._closed_fails + 1
            self._wake.wait(self._interval if live else self.CLOSED_POLL)
```

3d. 在 `run()` 之后追加这几个方法：

```python
    def _minute_due(self, state: str) -> bool:
        """交易中每 MINUTE_INTERVAL 秒拉一次；休盘只在状态刚切换时补一次（收盘那次拿全天数据）。"""
        if state != "交易中":
            if self._closed_state == state:
                return False
            self._closed_state = state
            return True
        self._closed_state = ""
        now = time.monotonic()
        if now - self._minute_since < self.MINUTE_INTERVAL:
            return False
        self._minute_since = now
        return True

    def _fetch_minutes(self, quotes: list) -> None:
        """拉每只当天分钟线，落盘后交给界面画分时图。"""
        day = datetime.now().strftime("%Y-%m-%d")
        codes = [quote.code for quote in quotes]
        try:
            bars = self._service.fetch_minute_bars(codes)
        except Exception as exc:
            self.minute_warning = str(exc)
            return
        self.minute_warning = ""
        series = intraday.build_all(quotes, bars, day)
        self._save_minutes(series, day)
        self.minutes_ready.emit(series)

    def _restore_series(self) -> None:
        """启动时先把库里当天的分钟点读出来，重启后分时图不至于空白。"""
        if self._store is None or not self._codes:
            return
        day = datetime.now().strftime("%Y-%m-%d")
        restored = {}
        for code in self._codes:
            try:
                rows = self._store.load_minutes(code, day)
            except Exception:
                continue
            if rows:
                restored[code] = intraday.series_from_rows(code, day, rows)
        if restored:
            self.minutes_ready.emit(restored)

    def _save_ticks(self, quotes: list) -> None:
        """10 秒原始快照落盘；失败就禁用落盘，不拖累取数。"""
        if self._store is None:
            return
        ts = int(time.time())
        rows = [(quote.code, ts, quote.price) for quote in quotes if quote.price > 0]
        try:
            self._store.save_ticks(rows)
        except Exception as exc:
            self._store = None
            self.store_failed = f"本地暂存已禁用：{exc}"

    def _save_minutes(self, series_by_code: dict, day: str) -> None:
        if self._store is None:
            return
        try:
            self._store.save_minutes(intraday.minute_rows(series_by_code, day))
        except Exception as exc:
            self._store = None
            self.store_failed = f"本地暂存已禁用：{exc}"
```

`__init__` 里同时补一个初始值：`self.minute_warning = ""`。

- [ ] **步骤 4：`StockWidget` 接入落盘、分时信号与窗口**

4a. `__init__` 中 `self._names` 之后补：

```python
        self._store, store_error = self._open_store()
        self._series: dict = {}
        self._charts: dict = {}
        self._press_pos = None
```

并把 `_last_warning` 的初始化改为带上落盘错误：`self._last_warning = store_error`；`_fetcher` 的创建改为 `QuoteFetcher(self._service, self._store, self)`，并连接新信号：

```python
        self._fetcher.minutes_ready.connect(self.on_minutes)
```

4b. 追加两个方法（放在 `on_quotes` 之前）：

```python
    @staticmethod
    def _open_store():
        """落盘失败（磁盘只读、库损坏）不该拖垮小组件：返回 None 表示本次运行禁用暂存。"""
        try:
            store = Store()
            store.purge_before(datetime.now().strftime("%Y-%m-%d"))
            return store, ""
        except Exception as exc:
            return None, f"本地暂存不可用：{exc}"

    def on_minutes(self, series_by_code: dict) -> None:
        """分钟线到达：更新已打开的分时窗口（无数据的那些保留上一轮，不清空）。"""
        for code, series in series_by_code.items():
            if not series.points and code in self._series:
                continue
            self._series[code] = series
            window = self._charts.get(code)
            if window is not None:
                window.set_series(series, self._service.source_label, self._last_update, self._last_warning)

    def _open_chart(self, code: str) -> None:
        """打开分时图窗口；已经开着就复用并提到前面。"""
        window = self._charts.get(code)
        if window is None:
            window = IntradayChartWindow(code, self._names.get(code, ""), self)
            window.apply_style(self.config.as_dict())
            window.destroyed.connect(lambda _=None, c=code: self._charts.pop(c, None))
            self._charts[code] = window
            window.move(self._chart_position())
        series = self._series.get(code)
        if series is not None:
            window.set_series(series, self._service.source_label, self._last_update, self._last_warning)
        window.show()
        window.raise_()

    def _chart_position(self):
        """首次打开时贴着主窗口右侧；放不下就回退到屏幕内的左上角。"""
        screen = QApplication.primaryScreen().availableGeometry()
        x = self.x() + self.width() - SHADOW + 8
        y = self.y()
        if x + 640 > screen.right():
            x = max(screen.left(), screen.right() - 660)
        return QPoint(x, max(screen.top(), min(y, screen.bottom() - 380)))

    def request_refresh(self) -> None:
        """分时窗口右键「立即刷新」转过来（窗口的父对象就是主窗口）。"""
        self._fetcher.wake()
```

4c. `on_quotes` 尾部追加一句，把落盘失败暴露到状态栏：

```python
        if self._fetcher.store_failed:
            self._last_warning = self._last_warning or self._fetcher.store_failed
```

4d. `contextMenuEvent` 在「设置…」之后加入分时图入口：

```python
        chart_menu = menu.addMenu("分时图")
        for code in self.config["codes"]:
            name = self._names.get(code, code)
            chart_menu.addAction(f"{name}  {code}", lambda _=False, c=code: self._open_chart(c))
```

4e. `closeEvent` 里关闭全部分时窗口并关闭数据库：

```python
        for window in list(self._charts.values()):
            window.close()
        if self._store is not None:
            self._store.close()
```

- [ ] **步骤 5：静态检查与启动验证**

运行：`.\.venv\Scripts\python.exe -m py_compile main.py config.py`
预期：无输出
运行：`.\.venv\Scripts\python.exe -c "import main"`
预期：无输出、无异常

- [ ] **步骤 6：目视验证**

运行：`.\.venv\Scripts\python.exe main.py`，逐项确认：

- [ ] 程序正常启动，主窗口行情照旧刷新（底部显示 `akshare·新浪分钟 · HH:MM:SS 更新`）
- [ ] 首次启动后 `D:\work\stock_widget\data\quote.db` 被创建
- [ ] 右键 → 「分时图」→ 点任意一只，弹出分时图窗口，能看到当天分时线；再点同一只不会重复开窗
- [ ] 等 60–90 秒，分时线向右生长；右键主窗口「立即刷新」后分时窗口数据也更新
- [ ] 关闭程序再启动，分时窗口里的当天已有点位立刻就有（不是空白）
- [ ] 若当前是休盘时段：等一轮后 `minute` 表里应写入全天分钟点，可用下面的命令确认

```powershell
.\.venv\Scripts\python.exe -c "from store import Store; s = Store(); print(len(s.load_minutes('600519', __import__('datetime').date.today().isoformat())), len(s.load_ticks('600519', __import__('datetime').date.today().isoformat())))"
```

预期：分钟点数量在 200 上下（或 0 表示今天还没开盘数据），tick 数量随运行时间增长

---

## 任务 8：行内缩略图 `SparklineWidget`（原型 C）与点击打开

**文件：**
- 修改：`chart.py`（追加 `SparklineWidget`）
- 修改：`main.py`（`StockRow`、鼠标事件、`apply_style`）
- 修改：`settings_dialog.py`（勾选框）

- [ ] **步骤 1：追加 `SparklineWidget` 到 `chart.py` 末尾**

```python
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
```

- [ ] **步骤 2：`StockRow` 加入缩略图**

2a. `__init__` 中，把三个标签的构建改为带上缩略图（`from chart import IntradayChartWindow, SparklineWidget`）：

```python
        self.name_label = QLabel(code)
        self.sparkline = SparklineWidget(code, self)
        self.price_label = QLabel("--")
        self.pct_label = QLabel("--")
        self.price_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.pct_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.name_label.setToolTip(code)
        self.name_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.sparkline.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        for label, stretch in ((self.name_label, 5), (self.sparkline, 0), (self.price_label, 3), (self.pct_label, 3)):
            label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            layout.addWidget(label, stretch)
```

2b. `apply_style` 增加两个参数并转发样式（签名末尾补 `show_sparkline: bool = True`）：

```python
        self.sparkline.setVisible(show_sparkline)
        self.sparkline.apply_style({
            "up_color": up_color,
            "down_color": down_color,
            "flat_color": flat_color,
            "font_color": font_color,
            "font_opacity": alpha,
        })
```

2c. 追加一个转发方法：

```python
    def set_series(self, series) -> None:
        self.sparkline.set_series(series)
```

- [ ] **步骤 3：主窗口接入点击命中与样式透传**

3a. 类常量补一个点击容差：`CLICK_SLOP = 4      # 位移小于这个像素数才算"点击"而不是拖窗口"`

3b. `apply_style` 里 `row.apply_style(...)` 调用追加参数：

```python
            row.apply_style(
                size,
                cfg["font_color"],
                cfg["up_color"],
                cfg["down_color"],
                cfg["flat_color"],
                alpha,
                bool(cfg["show_sparkline"]),
            )
```

3c. 追加命中判断与鼠标处理（`mousePressEvent` / `mouseReleaseEvent` / `mouseMoveEvent` 三处改动）：

```python
    def _sparkline_code_at(self, pos) -> str:
        """落点在某行的缩略图上就返回那只代码（缩略图自己不接收鼠标事件）。"""
        for code, row in self._rows.items():
            if not row.sparkline.isVisible():
                continue
            if row.sparkline.geometry().translated(row.pos()).contains(pos):
                return code
        return ""
```

`mouseMoveEvent` 中"未按键"分支改为：先把落点换算成缩略图代码，命中则用 `Qt.PointingHandCursor`：

```python
        if not (event.buttons() & Qt.LeftButton):
            pos = event.position().toPoint()
            if self._sparkline_code_at(pos):
                self.setCursor(Qt.PointingHandCursor)
            else:
                self.setCursor(CURSOR_FOR.get(self._hit_region(pos), Qt.OpenHandCursor))
            region = self._hit_region(pos)
            if region != self._hover_region:
                self._hover_region = region
                if float(self.config["opacity"]) <= 0.0:
                    self.update()
            return super().mouseMoveEvent(event)
```

`mousePressEvent` 里 `event.accept()` 之前补一句记录按下位置：

```python
        self._press_pos = event.globalPosition().toPoint()
```

`mouseReleaseEvent` 开头补"判定为点击则打开分时图"：

```python
    def mouseReleaseEvent(self, event) -> None:
        moved = 0
        if self._press_pos is not None:
            moved = (event.globalPosition().toPoint() - self._press_pos).manhattanLength()
        if moved <= self.CLICK_SLOP and self._resize_geom is None and self._drag_offset is not None:
            code = self._sparkline_code_at(event.position().toPoint())
            if code:
                self._open_chart(code)
        self._press_pos = None
        ...
```

（其余原有逻辑保持不变。）

3d. `on_minutes` 里把序列也分给缩略图，在 `self._series[code] = series` 之后补：

```python
            row = self._rows.get(code)
            if row is not None:
                row.set_series(series)
```

3e. `_rebuild_rows` 里新建的行需要立刻应用一次样式与序列，在 `row = StockRow(code)` 之后补：

```python
            row.apply_style(
                int(self.config["font_size"]),
                self.config["font_color"],
                self.config["up_color"],
                self.config["down_color"],
                self.config["flat_color"],
                self._text_alpha,
                bool(self.config["show_sparkline"]),
            )
            if code in self._series:
                row.set_series(self._series[code])
```

- [ ] **步骤 4：设置面板加勾选框**

4a. `__init__` 里 `show_source_check` 之后加：

```python
        self.sparkline_check = QCheckBox("显示行内分时缩略图")
        self.sparkline_check.setChecked(bool(config["show_sparkline"]))
        self.sparkline_check.setToolTip("关掉后列表里不再显示迷你分时图，分时图仍可右键菜单打开")
```

4b. 表单里加一行（紧跟 `form.addRow("", self.show_source_check)` 之后）：

```python
        form.addRow("", self.sparkline_check)
```

4c. `values()` 增加一项：

```python
            "show_sparkline": self.sparkline_check.isChecked(),
```

4d. `_restore_defaults` 增加一行：

```python
        self.sparkline_check.setChecked(bool(DEFAULTS["show_sparkline"]))
```

- [ ] **步骤 5：静态检查**

运行：`.\.venv\Scripts\python.exe -m py_compile main.py chart.py settings_dialog.py`
预期：无输出
运行：`.\.venv\Scripts\python.exe -m unittest discover -s tests -t . -v`
预期：全部用例 PASS（40 个）

- [ ] **步骤 6：目视验证**

运行：`.\.venv\Scripts\python.exe main.py`，逐项确认：

- [ ] 每一行名称与价格之间出现约 72×22 的迷你分时线，颜色随涨跌
- [ ] 鼠标移到缩略图上指针变成小手
- [ ] 点击缩略图弹出该只的分时图窗口；**在同一位置按住拖动窗口仍然生效**（这是当初最担心的事件冲突点）
- [ ] 在缩略图以外区域点击不会误弹窗
- [ ] 设置里取消勾选「显示行内分时缩略图」→ 列表回到纯文字，缩略图消失，仍可用右键菜单打开分时图
- [ ] 重启程序后该勾选状态被记住

- [ ] **步骤 7（仅在步骤 6 出现手感问题时执行）：退化为双击整行打开**

本计划采用的单击实现是「主窗口在 `mouseReleaseEvent` 里判断位移 ≤ `CLICK_SLOP` 且落点在缩略图矩形内」，因此不需要子控件接收事件、拖动不受影响。若实测仍觉得别扭（例如手抖导致点不开），按规格 5.4 的约定退化为**双击整行**：

在 `StockWidget` 追加：

```python
    def mouseDoubleClickEvent(self, event) -> None:
        """双击任意行打开该只分时图；缩略图方案手感不好时的退路。"""
        if event.button() != Qt.LeftButton:
            return super().mouseDoubleClickEvent(event)
        for code, row in self._rows.items():
            top = row.pos().y()
            if top <= event.position().toPoint().y() <= top + row.height():
                self._open_chart(code)
                break
        event.accept()
```

同时把 `mouseReleaseEvent` 里那段"命中缩略图就打开"的逻辑删掉，缩略图保留为纯展示（`setToolTip("双击整行查看分时图")`）。

---

## 任务 9：更新 `README.md`

**文件：**
- 修改：`README.md`（功能、操作表、config 表、数据通道、文件结构、版本记录、已知限制）

- [ ] **步骤 1：功能列表加两条**

在「休盘不空转」之后插入：

```markdown
- **本地暂存**：行情 10 秒快照与分钟线写入 `data/quote.db`（SQLite），**只保留当天**，每天首次启动自动清理旧数据；落盘失败不影响取数
- **当天实时分时**：列表每行的迷你分时缩略图（可关），点击即弹出该只的独立分时图窗口（价格线 + 均价线 + 昨收基准线 + 价格/时间刻度，无边框可拖动可缩放）
```

- [ ] **步骤 2：操作表补一行**

| 操作 | 说明 |
|---|---|
| 点击行内迷你分时图 | 弹出该只的独立分时图窗口（再次点击不会重复开窗） |
| 分时图窗口右键 | 立即刷新 / 窗口置顶 / 关闭 |

- [ ] **步骤 3：config 表补一行**

| 键 | 默认值 | 说明 |
|---|---|---|
| `show_sparkline` | `true` | 是否显示行内迷你分时缩略图；关掉后仍可用右键菜单打开独立分时图 |

- [ ] **步骤 4：文件结构表补充新文件**

```markdown
├── store.py             # 本地暂存：SQLite 落盘（tick 快照 + 分钟线），只保留当天
├── intraday.py          # 分时序列装配：分钟线记录 -> 当天分时点（纯转换，可单测）
├── chart.py             # 分时图：坐标映射与配色 + 行内缩略图 + 独立窗口
├── panel.py             # 面板外观：几何常量与圆角/投影绘制（主窗口与分时窗口共用）
├── tests/               # unittest 用例（store / intraday / datasource / chart）
└── data/quote.db        # 运行时生成的本地行情库
```

- [ ] **步骤 5：版本记录加一条**

| 版本 | 日期 | 主要内容 |
|---|---|---|
| v0.10.0 | 2026-10-15 | 本地暂存（SQLite，只留当天）+ 当天实时分时图（均价线/昨收基准线），行内缩略图与独立窗口两种呈现 |

并在文件末尾按现有格式补上 `### v0.10.0 — 2026-10-15` 的详细条目，内容包括：新增四个模块与职责、分钟线 60 秒节奏与 10 秒快照的分工、均价线算法（累计 amount ÷ 累计 volume）、`_sina_symbol` 指数前缀修复、`panel.py` 抽取、`show_sparkline` 配置项。

- [ ] **步骤 6：已知限制补两条**

```markdown
6. **分时线形状是分钟级**：10 秒快照只用于原始留档与拼接"正在走的尾巴"，线的精细度取决于 1 分钟 K 线
7. **只保留当天数据**：跨日清理后不能回看昨天（表结构已含 `day` 字段，后续扩展历史回看不需要改表）
```

---

## 完成标准

- [ ] `.\.venv\Scripts\python.exe -m unittest discover -s tests -t . -v` 全部通过（45 个用例）
- [ ] `.\.venv\Scripts\python.exe main.py` 能正常启动并刷新行情
- [ ] `data/quote.db` 中 `minute` 表有当天分钟点、`tick` 表有 10 秒快照，且旧日期的数据被清理
- [ ] 点击行内缩略图弹出分时图窗口，图上可见价格线、均价线、昨收虚线、价格与时间刻度
- [ ] 主窗口拖动/缩放行为与本期改动前完全一致（含面板 0% 透明度时的虚线提示）
- [ ] 设置里关闭「显示行内分时缩略图」后列表回归纯文字，右键菜单仍能打开分时图
- [ ] `2026-10-15-intraday-chart-design.md` 与 `README.md` 均已反映最终实现
