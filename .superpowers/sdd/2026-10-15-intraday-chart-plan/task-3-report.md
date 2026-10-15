# 任务 3 实现报告：datasource 分钟线能力

状态：DONE
实现者：主会话（本环境无可写文件的实现子代理，见 progress.md 的适配说明）
时间：2026-10-15

## 交付物（绝对路径）

- `D:\work\stock_widget\datasource.py`（修改）
  - `_sina_symbol`（约 123-128 行）：指数代码原样返回
  - `AkshareProvider.MINUTE_BUDGET = 12.0`（约 264 行）
  - `AkshareProvider.__init__`：新增 `self._minute_bars_blocked_until = 0.0`（约 272 行）
  - `AkshareProvider.fetch_minute_bars` / `_minute_bars`（新增，约 562-587 行）
  - `QuoteService.fetch_minute_bars`（新增，约 886 行）
- `D:\work\stock_widget\tests\test_datasource.py`（新建，3 个用例）

## 与简报的一致性

简报给出的 `_sina_symbol`、`MINUTE_BUDGET`、`fetch_minute_bars`、`_minute_bars`、`QuoteService.fetch_minute_bars` 五处改动与 `tests/test_datasource.py` 全文**逐字落地**。

自我更正：第一次写测试文件时我多写了一个 `MarketOfTest`（断言 `market_of` 对指数的市场号）。`market_of` 的指数行为属于更早的指数支持改动、不在本任务范围内，是为了"顺手多测一点"，已删除，最终测试与简报完全一致（3 个用例）。

## 测试证据

### 单元测试

```powershell
cd D:\work\stock_widget
.\.venv\Scripts\python.exe -m unittest tests.test_datasource -v
```

```
Ran 3 tests in 0.000s

OK
```

### 联网验证（简报步骤 6）

```powershell
.\.venv\Scripts\python.exe -c "from datasource import QuoteService; s = QuoteService('akshare', '', {}); bars = s.fetch_minute_bars(['600519', 'sh000001', '588000']); print({code: len(rows) for code, rows in bars.items()}); print('sample:', bars['sh000001'][-1])"
```

```
{'600519': 1970, 'sh000001': 1970, '588000': 1970}
sample: {'day': '2026-10-15 14:42:00', 'open': '3709.760', 'high': '3709.884', 'low': '3708.372', 'close': '3709.884', 'volume': '81692900', 'amount': '1425473536.0000'}
```

结论：股票（600519）、指数（sh000001）、ETF（588000）三类都能取到当天分钟线，字段为 `day/open/high/low/close/volume/amount`，`amount` 存在 → 均价可精确计算。指数走的是原样前缀（`sh000001`），说明 `_sina_symbol` 的修复生效（修复前会拼成 `szsh000001` 而取不到数据）。

## 自审

- **修复的是既有隐患**：`_sina_symbol` 此前对所有代码都拼前缀，指数 `sh000001` 会变成 `szsh000001`，`fetch` 的 minute/daily 两条轻量线路对指数必然取不到数据（这正是本轮分时图要用的接口）。
- **冷却独立**：新加的 `_minute_bars_blocked_until` 与行情用的 `_minute_blocked_until` 是两个字段，避免分时图的分钟线失败把行情主线路也拖进冷却（两者请求频率与用途不同）。
- **复用既有并发框架**：`fetch_minute_bars` 直接调 `_per_code`（4 并发、单轮 12 秒预算、超时任务后台自行结束），没有另写一套线程池。
- **只拿原始记录**：本层只做网络与"records 列表"，按天过滤、均价计算交给 `intraday`（任务 2），保持网络/转换分离。
- **主备语义**：`QuoteService.fetch_minute_bars` 不走 `_order()`，pytdx 不参与，失败直接抛给上层冷却，不做静默降级。

## 疑虑

`fetch_minute_bars` 目前对"部分成功"没有特殊处理：若 4 只里只有 1 只拿到，返回的 dict 里只有那一只，其余由上层（任务 7 的 `on_minutes`）保留上一轮数据、不清空。这是设计 5.4 约定的行为，本任务不做额外兜底。
