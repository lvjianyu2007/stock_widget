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
