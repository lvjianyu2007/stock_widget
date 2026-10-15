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
