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


if __name__ == "__main__":
    unittest.main()
