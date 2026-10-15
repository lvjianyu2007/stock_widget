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

    def test_shuffled_records_are_sorted_and_avg_accumulates_in_time_order(self):
        """接口返回顺序不做保证：输出必须按时间升序，且均价按时间累计。"""
        records = [
            {"day": "2026-10-15 09:33:00", "close": "103.0", "volume": "1000", "amount": "103000"},
            {"day": "2026-10-15 09:31:00", "close": "102.0", "volume": "1000", "amount": "102000"},
        ]
        series = build_series("600519", records, "2026-10-15", prev_close=101.0)
        self.assertEqual([point.time for point in series.points], ["09:31", "09:33"])
        self.assertAlmostEqual(series.points[0].avg, 102.0)
        self.assertAlmostEqual(series.points[1].avg, (102000 + 103000) / 2000)

    def test_prev_close_uses_latest_earlier_day_when_records_are_shuffled(self):
        records = [
            {"day": "2026-10-14 15:00:00", "close": "100.0", "volume": "1", "amount": "1"},
            {"day": "2026-10-13 15:00:00", "close": "98.0", "volume": "1", "amount": "1"},
            {"day": "2026-10-15 09:31:00", "close": "102.0", "volume": "1", "amount": "1"},
        ]
        self.assertEqual(resolve_prev_close(0.0, records, "2026-10-15"), 100.0)
        self.assertEqual(build_series("600519", records, "2026-10-15").prev_close, 100.0)

    def test_prev_close_uses_latest_bar_within_previous_day_when_reversed(self):
        """同一前一日内多行、且全局倒序时，昨收要取该日时间最晚的一根。"""
        records = [
            {"day": "2026-10-14 15:00:00", "close": "100.0", "volume": "1", "amount": "1"},
            {"day": "2026-10-14 09:30:00", "close": "99.0", "volume": "1", "amount": "1"},
            {"day": "2026-10-15 09:31:00", "close": "102.0", "volume": "1", "amount": "1"},
        ]
        self.assertEqual(resolve_prev_close(0.0, records, "2026-10-15"), 100.0)
        self.assertEqual(build_series("600519", records, "2026-10-15").prev_close, 100.0)


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

    def test_series_without_bars_degrades_to_single_live_point(self):
        """只有行情、没有分钟线时（冷启动/接口失败）要退化成单点，而不是空白 + 平盘。"""
        quotes = [Quote(code="600519", price=105.0, prev_close=101.0)]
        series = build_all(quotes, {}, "2026-10-15")["600519"]
        points = effective_points(series, now=datetime(2026, 10, 15, 10, 0))
        self.assertEqual(len(points), 1)
        self.assertEqual(points[0].time, "10:00")
        self.assertEqual(points[0].price, 105.0)
        self.assertIsNone(points[0].avg)
        self.assertEqual(series.direction, 1)
        self.assertAlmostEqual(series.change_pct, (105.0 - 101.0) / 101.0 * 100.0)

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


if __name__ == "__main__":
    unittest.main()
