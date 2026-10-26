"""新浪代码拼装：指数带前缀时不能重复拼前缀。"""
import unittest
from unittest.mock import MagicMock, patch

from datasource import AkshareProvider, _is_index_code, _parse_sina_hq, _sina_symbol


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


class SinaHqParseTest(unittest.TestCase):
    """新浪单只行情接口的返回解析：1 个请求顶掉原来的 8 页全量快照。"""

    LINE = ('var hq_str_sh000001="上证指数,3671.0038,3690.4753,3666.7145,3681.6631,3664.8209,'
            '0,0,206148047,368310172698,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,2026-10-26,10:39:00";')

    def test_parses_index_line(self):
        quote = _parse_sina_hq(self.LINE)
        self.assertIsNotNone(quote)
        self.assertEqual(quote.code, "sh000001")
        self.assertEqual(quote.name, "上证指数")
        self.assertAlmostEqual(quote.price, 3666.7145)
        self.assertAlmostEqual(quote.prev_close, 3690.4753)
        self.assertLess(quote.change, 0)
        self.assertAlmostEqual(quote.change_pct, (3666.7145 - 3690.4753) / 3690.4753 * 100.0)
        self.assertFalse(quote.missing)

    def test_empty_payload_is_treated_as_missing(self):
        self.assertIsNone(_parse_sina_hq('var hq_str_sh000001="";'))

    def test_zero_price_is_ignored(self):
        """现价 0（停牌/无成交）要当成"没取到"，别把界面价格改成 0。"""
        self.assertIsNone(_parse_sina_hq('var hq_str_sh000001="上证指数,0,3690.47,0,0,0";'))

    def test_garbage_is_ignored(self):
        self.assertIsNone(_parse_sina_hq(""))
        self.assertIsNone(_parse_sina_hq("<html>404</html>"))


class BjSymbolTest(unittest.TestCase):
    def test_beijing_exchange_gets_bj_prefix(self):
        """北交所的 4xx/8xx/920xx 要拼 bj，早先会被错拼成 sz。"""
        self.assertEqual(_sina_symbol("430047"), "bj430047")
        self.assertEqual(_sina_symbol("830799"), "bj830799")
        self.assertEqual(_sina_symbol("920002"), "bj920002")


class HqRowsTest(unittest.TestCase):
    """新浪行情接口：一个请求拿全部，代码要映射回 config 里的写法。"""

    BODY = (
        'var hq_str_sz000799="酒鬼酒,34.900,34.920,35.000,35.100,34.800,0,0,100,3400,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,2026-10-26,10:39:00";\n'
        'var hq_str_sh000001="上证指数,3671.003,3690.475,3666.714,3681.663,3664.820,0,0,1,2,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,2026-10-26,10:39:00";\n'
        'var hq_str_sh600000="";\n'
    )

    def _quotes(self, codes):
        provider = AkshareProvider()
        resp = MagicMock()
        resp.text = self.BODY
        with patch("requests.get", return_value=resp):
            return provider._hq_rows(codes)

    def test_maps_back_to_config_code(self):
        quotes = self._quotes(["000799", "sh000001"])
        self.assertEqual([quote.code for quote in quotes], ["000799", "sh000001"])
        self.assertEqual(quotes[0].name, "酒鬼酒")
        self.assertAlmostEqual(quotes[0].price, 35.0)
        self.assertAlmostEqual(quotes[0].prev_close, 34.92)
        self.assertFalse(quotes[0].missing)

    def test_code_not_returned_is_marked_missing(self):
        """接口没返回的代码标成 missing，交给后面的兜底去补。"""
        quotes = self._quotes(["600000"])
        self.assertTrue(quotes[0].missing)

    def test_order_puts_hq_first(self):
        self.assertEqual(AkshareProvider()._order(["600519"])[0], "hq")


if __name__ == "__main__":
    unittest.main()
