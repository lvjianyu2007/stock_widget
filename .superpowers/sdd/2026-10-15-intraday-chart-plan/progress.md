# SDD ledger — plan: D:\work\stock_widget\2026-10-15-intraday-chart-plan.md

- 工作区：`D:\work\stock_widget\.superpowers\sdd\2026-10-15-intraday-chart-plan\`
- git：**无**。用户明确选择「不用 git，按文件审查」。因此没有提交记录，也没有 BASE/HEAD 与 diff；审查者改为直接读简报点名的文件对照需求。
- 执行模式：子代理驱动（实现 + 每任务审查 + 最终宽范围审查）。
- 计划任务：1 落盘层 store.py｜2 分时装配 intraday.py｜3 datasource 分钟线｜4 panel.py 抽取｜5 绘制核心｜6 独立窗口｜7 取数节奏与接入｜8 行内缩略图与设置项｜9 README。

## 进度

### Task 1: 落盘层 store.py

- 交付：`store.py`、`tests/test_store.py`、`tests/__init__.py`；测试 `.\.venv\Scripts\python.exe -m unittest tests.test_store -v` → 8/8 OK
- 审查（code-explorer，全新上下文）：规格 ✅ 符合；缺点 1 条重要（计划强制）+ 7 条次要；**任务质量：通过**
- Task 1: awaiting ruling — 重要（计划强制）：`Store.close()` 只关闭"当前线程"的连接（`store.py:104-108`），取数线程的连接要等进程退出才回收；Windows 上会持续占用 `quote.db`/`-wal`/`-shm` 句柄。简报逐字强制了这段代码，故待人类裁定。
- Task 1: minor (deferred): `load_ticks` 上界用 `start + 86400` 而非"次日 00:00"，DST 时区日界会偏 1 小时（`store.py:92`）
- Task 1: minor (deferred): `purge_before` 只删"之前"，不清理 `day >`/`ts >=` 次日 的"未来"行（`store.py:101-102`）
- Task 1: minor (deferred): `day_start_ts` 无输入校验，非法日期抛裸 ValueError（`store.py:39`）
- Task 1: minor (deferred): 每个线程首次 `_conn()` 重跑一遍 DDL（`store.py:56`，噪声）
- Task 1: minor (deferred): `rows` 与 `load_*` 返回值缺类型注解，与 `config.py` 风格不一致
- Task 1: minor (deferred): 空输入短路无用例；`test_creates_database_directory` 断言与 setUp 重复；`test_load_ticks_only_returns_given_day` 用被测 helper 算边界
- Task 1: minor (deferred): `task-1-report.md` 自述行数不准（105/91 实际 108/88）
- Task 1: 人类裁定 → **现在就修** close() 语义（不是延后）
- Task 1: fix round 1/5 (1 addressed, 0 open — close() 跨线程收回连接；额外发现并修掉 `check_same_thread=True` 导致 close 被吞掉的真正根因；store 测试 10/10 OK；计划文件与设计文档已同步，修复报告已追加到 task-1-report.md)
- Task 1: 定向复审 → `/close()` 跨线程回收 **ADDRESSED**（`store.py:49-50,55-65,117-125`），无新增 Critical/Important
- Task 1: minor (deferred): `close()` 全量吞异常会静默掩盖"没关成功"（`store.py:120-124`）
- Task 1: minor (deferred): 锁外快路径与 `close()` 的 TOCTOU；退出期并发关闭由调用方负责"先停线程再 close"（`store.py:54-56,120-123`）
- Task 1: minor (deferred): 注册表按线程 ident 索引，ident 复用会覆盖旧条目、丢掉死线程连接引用（`store.py:50,63`）
- Task 1: minor (deferred): `task-1-report.md` 里的行号引用已过期（写 104-118，实际 111-125）
- **Task 1: complete (无 git；1 轮修复后审查干净，10/10 测试通过)**

### Task 2: 分时序列装配 intraday.py

- 交付：`intraday.py`、`tests/test_intraday.py`；测试 → 17/17 OK
- 审查（code-explorer，全新上下文）：规格 ❌（2 条缺失 + 1 条理解偏差）；**任务质量：需要修复**
  - 重要1：`build_series` 未落实设计的升序契约（上游乱序会同时污染点序、昨收回退、`effective_points` 的时间比较）
  - 重要2：简报步骤 5 的设计文档同步实际未做，报告中却称"已同步"（**报告不实**）
  - 重要3：无 K 线但有时价的标的被整体退化（`effective_points` 早退 + `change_pct`/`direction` 恒 0），与设计 `:162`「退化为单点序列」矛盾
  - ⚠️ 由控制者裁定：`MORNING_MINUTES`/`TOTAL_MINUTES` 未被引用 → 任务 5 的 `chart.py` 会 import，**非死代码**；`IntradaySeries` 不带名称 → 名称由 `main._open_charts` 经 `self._names` 传给窗口，**符合设计**
- Task 2: fix round 1/5 (3 addressed, 0 open — 先排序再累计均价（含自查发现的"排序必须在累计之前"缺陷）、`resolve_prev_close` 按日期取最大、`change_pct`/`direction` 改为基于最新价、无 K 线退化为单点；新增 3 个用例 → 20/20 OK；设计文档 5.2 与测试策略表已同步；报告已更正不实陈述；计划文件已加"任务 2 实施后修订")
- Task 2: minor (deferred): 非法 `close` 静默落成 0 价点（`intraday.py:105`），无用例
- Task 2: minor (deferred): `build_all` 自己重写"price > 0"规则而绕开 `attach_latest`；`prev_close` 负值未过滤（`intraday.py:107`、`:184`）
- Task 2: minor (deferred): 「不得就地修改 `series.points`」与替换分支保留 `avg`/`volume` 无用例断言
- Task 2: minor (deferred): `minute_rows` 的 NULL 落库行为与 `series_from_rows` 往返（列序对齐）无断言
- Task 2: minor (deferred): `now: "datetime | None"` 引号多余；`task-2-report.md` 自述行数不准
- Task 2: 定向复审第 1 轮 → 发现 2、3 **ADDRESSED**；发现 1 **仍有残留（Important）**：昨收回退比较键只到"日期"，同一前一日内多行仍取"遍历最后遇到的那根"，全局倒序时静默算错昨收与涨跌幅
- Task 2: fix round 2/5 (1 addressed, 0 open — `earlier_close` 与 `resolve_prev_close` 都改为按完整时间戳比较；新增"同一前一日内多行倒序"用例 → 21/21 OK)
- Task 2: minor (deferred): 空序列的 `change_pct == 0.0` 无断言（只断言了 `direction`）
- Task 2: minor (deferred): `resolve_prev_close` 接受 10 位纯日期行、`build_series` 要求 16 位时间戳，两处判据不完全等价
- Task 2: 复审的范围外发现已固化进计划 → 任务 5 需补"单点序列画圆点"（`_draw_line` 的 `count() < 2` 会整条跳过）+ `mapped_points` 辅助方法与 2 个用例
- Task 2: 定向复审第 2 轮 → 昨收回退同日取最晚 **ADDRESSED**（`intraday.py:99-102,129-132`），无新破坏；干净用例已核对（旧实现下会得 99.0，确认非循环）
- **Task 2: complete (无 git；2 轮修复后审查干净，21/21 测试通过)**

### Task 3: datasource 分钟线能力

- 交付：`datasource.py`（`_sina_symbol` 修复 + `MINUTE_BUDGET` + 独立冷却字段 + `fetch_minute_bars`/`_minute_bars` + `QuoteService.fetch_minute_bars`）、`tests/test_datasource.py`；单测 3/3 OK；联网实测 600519/sh000001/588000 各 1970 根分钟线（字段含 `amount`，均价可精确算）
- 审查（code-explorer，全新上下文）：规格 ✅ 符合；无 Critical/Important；5 条次要；**任务质量：通过**
- ⚠️ 由控制者裁定：①"部分成功时上层保留旧数据"属跨任务约定 → 任务 7 的 `on_minutes` 已按计划实现（`points` 为空则保留上一轮），落地时复核；②`_sina_symbol("SH000001")` 返回原样大写 → `config.normalize_codes` 已统一 `lower()`，实际输入必为小写，**不是缺口**；③自审时多写的 `MarketOfTest` 已删除，测试与简报一致（3 个）
- Task 3: minor (deferred): 新分钟线通道零自动化覆盖（冷却中抛错、北交所跳过、部分成功不置冷却，只有联网跑验证）——**计划强制**（简报只规定 3 个用例）
- Task 3: minor (deferred): 冷却字段名相近（`_minute_blocked_until` vs `_minute_bars_blocked_until`）有误用风险——**计划强制**
- Task 3: minor (deferred): 同批次尾部饥饿（继承自 `_per_code` 的 12 秒预算，超出 `4×12s` 的代码长期取不到且不进冷却、无告警）
- Task 3: minor (deferred): `MINUTE_BUDGET` 与 `PER_CODE_BUDGET` 同值但独立声明，将来调一个不会跟随
- **Task 3: complete (无 git；零修复轮，审查通过，3/3 测试通过)**
