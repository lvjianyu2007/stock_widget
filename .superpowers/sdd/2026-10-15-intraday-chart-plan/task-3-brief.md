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

