"""主窗口的行状态：这一轮取不到数据时，不要把已经显示的价格清掉。

指数快照（新浪）内部要发 8 个请求，偶发失败是常态；失败后的冷却期内每轮都拿不到，
如果每次都把界面翻成「无数据」，看起来就是「指数间断性没数据」。
"""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from datasource import Quote
from intraday import IntradaySeries
from main import StockRow, _quote_from_series


class StockRowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_has_price_after_a_valid_quote(self):
        row = StockRow("sh000001")
        self.assertFalse(row.has_price())
        row.set_quote(Quote(code="sh000001", name="上证指数", price=3723.98, prev_close=3707.48, change_pct=0.45))
        self.assertTrue(row.has_price())
        self.assertEqual(row.price_label.text(), "3723.98")
        self.assertEqual(row.pct_label.text(), "+0.45%")

    def test_missing_quote_keeps_the_previous_price(self):
        row = StockRow("sh000001")
        row.set_quote(Quote(code="sh000001", price=3723.98, prev_close=3707.48))
        missing = Quote(code="sh000001", source="akshare", found=False)
        self.assertTrue(missing.missing)
        self.assertTrue(row.has_price(), "有旧值时 on_quotes 应当跳过这次覆盖，价格不能被清掉")

    def test_row_never_loaded_shows_no_data(self):
        row = StockRow("510300")
        self.assertFalse(row.has_price())
        row.set_quote(Quote(code="510300", source="akshare", found=False))
        self.assertEqual(row.pct_label.text(), "无数据")
        self.assertFalse(row.has_price())


class QuoteFallbackTest(unittest.TestCase):
    """指数行情缺失时，用分时数据的最后一根 K 线兜底（不额外发请求）。"""

    def test_uses_series_last_price(self):
        series = IntradaySeries(code="sh000001", prev_close=3690.48)
        series.latest_price = 3666.71
        quote = _quote_from_series(Quote(code="sh000001", found=False), series)
        self.assertFalse(quote.missing)
        self.assertAlmostEqual(quote.price, 3666.71)
        self.assertLess(quote.change, 0)

    def test_without_prev_close_keeps_missing(self):
        series = IntradaySeries(code="sh000001")        # 没有昨收，算不出涨跌
        series.latest_price = 3666.71
        self.assertTrue(_quote_from_series(Quote(code="sh000001", found=False), series).missing)

    def test_without_series_keeps_missing(self):
        self.assertTrue(_quote_from_series(Quote(code="sh000001", found=False), None).missing)


if __name__ == "__main__":
    unittest.main()
