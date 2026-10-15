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
        self._conn()                      # 立刻建库建表，启动阶段就能暴露磁盘问题

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self.path), timeout=5.0)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)
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
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None
```

- [ ] **步骤 5：运行测试验证通过**

运行：`.\.venv\Scripts\python.exe -m unittest tests.test_store -v`
预期：8 个用例全部 PASS

---

