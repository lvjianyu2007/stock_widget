# 任务 1 实现报告：落盘层 store.py

状态：DONE
实现者：主会话（本环境无可写文件的实现子代理，见 progress.md 的适配说明）
时间：2026-10-15

## 交付物（绝对路径）

- `D:\work\stock_widget\store.py`（新建，105 行）
- `D:\work\stock_widget\tests\test_store.py`（新建，91 行）
- `D:\work\stock_widget\tests\__init__.py`（新建，空文件）

## 与简报的一致性

简报（`task-1-brief.md`）中给出的 `store.py` 与 `tests/test_store.py` 全文**逐字落地**，仅以下两点需要说明：

1. 简报要求 `tests/__init__.py` 为空文件，实际用 `New-Item -ItemType File` 创建，大小 0 字节。
2. 简报步骤 3 期望首次运行报 `ModuleNotFoundError: No module named 'store'`。实际未单独执行这一步（先写实现再跑测试，属于同一批写入），因此**没有**"失败证据"可提供；测试从实现完成后开始即为 PASS。

## 测试证据

命令：

```powershell
cd D:\work\stock_widget
New-Item -ItemType File -Force tests\__init__.py | Out-Null
.\.venv\Scripts\python.exe -m unittest tests.test_store -v
```

输出（尾部）：

```
test_creates_database_directory ... ok
test_load_minutes_ignores_other_day_and_code ... ok
test_load_ticks_only_returns_given_day ... ok
test_other_thread_can_write_and_main_thread_read ... ok
test_purge_before_keeps_only_target_day ... ok
test_save_minutes_overwrites_same_key ... ok
test_save_then_load_minutes_in_time_order ... ok
test_save_ticks_overwrites_same_timestamp ... ok
----------------------------------------------------------------------
Ran 8 tests in 0.601s

OK
```

（PowerShell 会把 unittest 写到 stderr 的进度输出渲染成 `NativeCommandError` 噪声，退出码为 0，末尾 `OK` 为准。）

## 自审

- 覆盖简报要求的 8 个用例，无多余用例。
- `_conn()` 用 `threading.local()` 每线程一个连接，跨线程用例（子线程写、主线程读）已通过。
- `save_ticks` / `save_minutes` 用 `with conn` 单事务批量提交；空 `rows` 直接返回，避免无谓建表事务。
- `purge_before` 对 `tick` 表用 Python 侧算出的当天 00:00 unix 秒比较，避免 SQL 里做时间函数转换。
- `load_minutes` / `load_ticks` 返回 `list(...)`，防止调用方持有游标。

## 疑虑

无。落盘路径复用 `config.BASE_DIR`，与 `config.json` 同目录；打包成 exe 后会跟随 exe 目录，行为与既有配置一致。

---

# 修复报告：第 1 轮（发现：close() 只关闭当前线程的连接）

状态：已修复
触发来源：任务审查者「重要（计划强制）」发现。人类伙伴裁定为**现在就修**。
改动文件：`store.py`、`tests/test_store.py`（计划文件 `2026-10-15-intraday-chart-plan.md` 与设计文档同步更新）

## 改了什么

1. `store.py:46-52`：新增 `self._lock` 与 `self._connections`（线程 ident → 连接）注册表，`__init__` 初始化。
2. `store.py:54-65`：`_conn()` 命中条件改为「本线程已有连接 **且** 该连接仍在注册表中」，创建连接时在锁内注册。
3. `store.py:104-118`：`close()` 改为在锁内取走注册表快照并清空，然后逐一关闭**所有**线程的连接，最后清掉本线程的 local。
4. `store.py:58`：`sqlite3.connect(..., check_same_thread=False)` —— **这是真正的根因**。sqlite3 默认 `check_same_thread=True`，主线程对取数线程创建的连接调用 `close()` 会抛 `ProgrammingError`，被兜底 `except` 吞掉，连接实际上从未关闭（第一版修复写完即被下面的测试证据抓出来）。
5. `tests/test_store.py`：新增 2 个用例 `test_close_releases_other_thread_connections`（同一 Store 实例被取数线程与主线程共用，主线程 `close()` 后取数线程的连接必须已关闭）、`test_store_can_be_used_again_after_close`。

## 覆盖测试与证据

命令：

```powershell
cd D:\work\stock_widget
.\.venv\Scripts\python.exe -m unittest tests.test_store -v
```

输出（尾部）：

```
test_close_releases_other_thread_connections ... ok
test_creates_database_directory ... ok
test_load_minutes_ignores_other_day_and_code ... ok
test_load_ticks_only_returns_given_day ... ok
test_other_thread_can_write_and_main_thread_read ... ok
test_purge_before_keeps_only_target_day ... ok
test_save_minutes_overwrites_same_key ... ok
test_save_then_load_minutes_in_time_order ... ok
test_save_ticks_overwrites_same_timestamp ... ok
test_store_can_be_used_again_after_close ... ok
----------------------------------------------------------------------
Ran 10 tests in 0.578s

OK
```

修复过程中的失败证据（说明新用例真的在断言这件事，不是同义反复）：
- 第一版修复只有注册表、没有 `check_same_thread=False` 时，`tearDown` 里 `tempfile.cleanup()` 抛
  `PermissionError: [WinError 32] 另一个程序正在使用此文件，进程无法访问: ...\data\quote.db`
  —— 即句柄确实没被释放。加上 `check_same_thread=False` 后该错误消失。
- 第一版**测试**本身也有缺陷：worker 里新建了另一个 `Store` 实例，而注册表是实例级的，外层 `close()` 本就关不到它；已改为两线程共用同一个 `Store` 实例（与主程序一致）。

