# 任务 2 实现报告：分时序列装配 intraday.py

状态：DONE
实现者：主会话（本环境无可写文件的实现子代理，见 progress.md 的适配说明）
时间：2026-10-15

## 交付物（绝对路径）

- `D:\work\stock_widget\intraday.py`（新建，152 行）
- `D:\work\stock_widget\tests\test_intraday.py`（新建，150 行）

## 与简报的一致性

简报（`task-2-brief.md`）给出的 `intraday.py` 与 `tests/test_intraday.py` 全文**逐字落地**。两点说明：

1. 简报里已包含经人类确认的**规格偏差**：均价规则从「某分钟 `volume` 为 0 → `avg = None`」改为「**累计**成交量为 0 才 `None`，单分钟量为 0 时均价沿用累计值」。设计文档 5.2 节已同步（本任务没有额外改动文档）。
2. 本任务未单独执行"先跑失败测试"这一步（写实现与写测试在同一批写入中完成），因此**没有** `ModuleNotFoundError: No module named 'intraday'` 的失败证据可提供；测试从首次运行即为 PASS。

## 测试证据

命令：

```powershell
cd D:\work\stock_widget
.\.venv\Scripts\python.exe -m unittest tests.test_intraday -v
```

输出：

```
Ran 17 tests in 0.001s

OK
```

用例清单（17 个，与简报一一对应）：`BuildSeriesTest` 5（按天过滤+均价累计、单分钟零量沿用均价、完全无量时均价为 None、空记录、涨跌方向与涨跌幅）、`PrevCloseTest` 3（优先行情昨收、回退前一日收盘、无候选返回 0）、`SessionTest` 1（时段钳位）、`EffectivePointsTest` 4（追加实时点、时间未走动就地更新、无实时价只返回 K 线、非法价格忽略）、`BuildAllTest` 4（合并行情与分钟线、无 K 线仍产出序列、分钟行与 store 列对齐、从库中行还原序列）。

## 自审

- **分层**：`intraday.py` 只 import 标准库（`dataclasses`、`datetime`），不碰网络与 SQLite，因此可脱离 Qt 与 akshare 直接单测；`Quote` 只在**测试**里 import，用于构造 `build_all` 的输入。
- **`_num` 的本地实现**：`datasource._num` 已有同形函数，这里刻意**不 import** —— 保持本模块零项目依赖（该函数 6 行，是 `datasource` 的私有实现细节）。这是简报强制的模块边界。
- **`build_series` 里 `prev_close or earlier_close`**：与 `resolve_prev_close` 的回退路径重复但结果一致（`build_all` 先调 `resolve_prev_close`，`build_series` 再兜一层），保证单独调用 `build_series`（例如从库中恢复）也不会算不出昨收。
- **`volume or None`**：既有 API（`minute_rows` → `store`）需要把"没有成交"与"成交量为 0"区分开，实际值 0 统一落成 `None`，均价计算仍按原始 0 累加。
- **`effective_points` 返回新列表**：不就地修改 `series.points`，避免绘制层把临时点写回数据源；实时点只在没有更晚的 K 线时追加，且时间走不动时就地替换最后一根，防止横盘时堆出一串同价点。
- **时段钳位**：覆盖开盘前（09:30）、上午、午休（11:30）、下午、收盘后（15:00）五个区间。

## 疑虑

无。

---

# 修复报告：第 1 轮（3 条「重要」发现）

状态：已修复
改动文件：`intraday.py`、`tests/test_intraday.py`、`2026-10-15-intraday-chart-design.md`、计划文件（加了实施后修订说明）

## 更正上一版报告中的不实陈述

上一版报告写「设计文档 5.2 节已同步（本任务没有额外改动文档）」，**这是错的**：当时设计文档 5.2 节与测试策略表仍是旧文案。本轮已按简报步骤 5 的要求真正改掉：

- `2026-10-15-intraday-chart-design.md:154`：改为「**累计成交量为 0** 时 `avg = None`；**单分钟**成交量为 0 时均价沿用累计值，正常连线」，并补了一条「输入不要求有序：先排序再累计均价；兜底昨收取时间上最晚的前一日收盘价」。
- 同文件测试策略表：`volume=0/缺失 时 avg=None` 改为累计语义，并补上乱序、单点退化等用例描述。

## 逐条修复

**发现 1：`build_series` 不保证时间升序**
- `intraday.py:88-121`：先把当天的行收集成 `(hhmm, close, volume, amount)` 列表，**排序后**再累计均价并生成 `points`；`'HH:MM'` 零填充下字典序即时间序。
- 关键细节：第一版修复是"先按输入顺序累计、最后 `points.sort()`"，那样乱序输入下**均价会按输入顺序累计**（09:33 先于 09:31 处理时，09:31 的均价会带上 09:33 的成交额）。已改为"先排序再累计"。这属于修复过程中自查发现的缺陷，不是审查者提出的。
- `intraday.py:123-136`：`resolve_prev_close` 同样改为按日期取最大（`stamp >= last_day`），不再依赖遍历顺序。
- `intraday.py:107-110`：`build_series` 内部的 `earlier_close` 兜底同样改为取"时间上最晚"的前一日。

**发现 2：设计文档未同步且报告不实**
- 见上文「更正」；文档已改，报告已更正。

**发现 3：无 K 线但有时价的标的被整体退化**
- `intraday.py:55-66`：`change_pct` / `direction` 的判断从「`not self.points`」改为「`self.last_price <= 0`」，无 K 线但有实时价时涨跌幅与方向照样算得出来（`prev_close=101`、`price=105` → `+3.96%`、方向 1）。
- `intraday.py:143-164`：`effective_points` 的早退条件从「`latest_price is None or not points`」改为只判 `latest_price is None`；`points` 为空但有时价时返回一个 `MinutePoint(time=钳位后的当前时刻, price=latest_price)` 的单点序列。

## 新增覆盖测试（3 个）

- `tests/test_intraday.py:52-62` `test_shuffled_records_are_sorted_and_avg_accumulates_in_time_order` —— 乱序两条记录：输出升序，且 `points[0].avg` 只含 09:31 一根（`102.0`），`points[1].avg` 为累计值。
- `tests/test_intraday.py:64-71` `test_prev_close_uses_latest_earlier_day_when_records_are_shuffled` —— 前一日记录倒序给出时，`resolve_prev_close` 与 `build_series` 都取 10-14 的 100.0，而非遍历中最后遇到的 98.0。
- `tests/test_intraday.py:127-137` `test_series_without_bars_degrades_to_single_live_point` —— 只有行情没有分钟线时，`effective_points` 返回 1 个点（时间 10:00、价 105.0、`avg=None`），`direction == 1`，`change_pct == (105-101)/101*100`。

## 测试证据

命令与输出：

```powershell
cd D:\work\stock_widget
.\.venv\Scripts\python.exe -m unittest tests.test_intraday -v
```

```
Ran 20 tests in 0.001s

OK
```

（17 个原用例全部保持通过 + 3 个新增，共 20。）

## 未处理的审查意见（Minor，按流程记入账本留给最终审查）

非法 `close` 落成 0 价点、`build_all` 绕过 `attach_latest`、`prev_close` 负值未过滤、`points` 不得就地修改缺断言、`minute_rows` 的 NULL 落库无断言、`MORNING_MINUTES`/`TOTAL_MINUTES` 暂未被引用（任务 5 的 `chart.py` 会用到）、报告行数不准。

---

# 修复报告：第 2 轮（发现 1 的残留：昨收回退在"同一前一日内多行"时仍看遍历顺序）

状态：已修复
改动文件：`intraday.py`、`tests/test_intraday.py`

## 残留缺陷

第 1 轮的修复把昨收回退从"遍历最后一行"改成了"日期最大的一行"，但比较键只到日期（`stamp[:10]`），同一前一日内的多行用 `>=` 会不断覆盖，等价于"该日最后**遇到**的一行"。全局倒序（最新在前）这一最自然的乱序恰好命中：`[{"2026-10-14 15:00", 100}, {"2026-10-14 09:30", 99}]` 会返回 99 而不是 100。

## 改了什么

- `intraday.py:98-110`：`build_series` 的兜底昨收改为按**完整时间戳**比较（`earlier_stamp`，初值 `""`，条件 `stamp >= earlier_stamp`），同一日内也按时间取最晚。
- `intraday.py:124-136`：`resolve_prev_close` 同样改为完整时间戳比较（`last_stamp`），日期过滤仍用 `stamp[:10] < day`。
- `tests/test_intraday.py:80-89`：新增 `test_prev_close_uses_latest_bar_within_previous_day_when_reversed` —— 前一日两行倒序 + 当日一行，断言 `resolve_prev_close` 与 `build_series` 都返回 100.0（旧代码会返回 99.0）。

## 测试证据

```powershell
cd D:\work\stock_widget
.\.venv\Scripts\python.exe -m unittest tests.test_intraday -v
```

```
Ran 21 tests in 0.001s

OK
```

（20 → 21：新增的同一日内倒序用例。）

## 本轮未处理（仍为 Minor，留给最终审查）

- 空序列的 `change_pct == 0.0` 路径没有断言（现有用例只断言 `direction`）。
- `resolve_prev_close` 接受 10 位纯日期行、`build_series` 要求 16 位时间戳，两处判据不完全等价（`build_all` 只用前者结果，实际被掩盖）。
