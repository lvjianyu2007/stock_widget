# 当天实时分时图 —— 设计说明

- 日期：2026-10-15
- 状态：设计已确认，待编写实现计划
- 适用范围：`stock_widget`（D:\work\stock_widget）

---

## 1. 目标与非目标

### 目标

1. 落盘暂存：把每天的行情数据存到本地，**分钟级**（每只每天 240 点）与 **10 秒原始快照**都保留。
2. 当天实时分时：像行情软件那样，开盘后边走边画当天 09:30–15:00 的分时线，含**均价线**与**昨收基准线**，随刷新更新。
3. 两种呈现方式同时做原型，最后二选一保留：
   - A：点击缩略图弹出**独立分时图窗口**（无边框、可拖动、可置顶，可同时开多只）；
   - C：列表行内**迷你缩略图**。

### 非目标（本次不做）

- 历史某日分时回看、多日/多周走势折线（数据表结构已预留 `day` 字段，后续不用改表）。
- 十字光标、悬浮 tooltip、鼠标缩放/拖动、成交量副图、多指标叠加。
- 分钟线走 pytdx 备用通道（pytdx 分钟线覆盖有限，本次只走 akshare 主通道）。
- 分时图窗口的位置/尺寸持久化（每次按主窗口位置打开）。
- 10 秒原始快照的任何展示形态（只落盘，读取接口先备着）。

---

## 2. 需求确认结论

| 议题 | 结论 |
|---|---|
| 图形形态 | 当天实时分时（含均价线、昨收基准线） |
| 数据来源 | **以接口分钟线为主**：一次拿全天，程序没开也能补齐；本地 SQLite 兼作缓存与历史留档 |
| 展示方式 | A（独立窗口）与 C（行内缩略图）**都做原型**，共用同一数据层与绘制核心，最后决定保留哪个 |
| 存储粒度 | **分钟级 + 10 秒原始快照**都存 |
| 保留期限 | **只保留当天**，次日开盘前清理旧数据 |
| 打开方式 | 点击缩略图打开该只分时图；若与主窗口拖动事件冲突难以处理，退化为**双击整行**打开 |
| 文档位置 | 项目根目录（本文件），**不提交 git**（本项目未纳入 git 管理） |

---

## 3. 关键实测事实（2026-10-15 实测，akshare 1.19.1）

1. **一个接口覆盖全部品种**：`ak.stock_zh_a_minute(symbol=..., period="1", adjust="")` 对指数（`sh000001`）、ETF（`sh588000`）、股票（`sz000799`）都返回分钟线。
2. **字段齐全**：`day / open / high / low / close / volume / amount`，因此均价线可**精确计算**为「累计 `amount` ÷ 累计 `volume`」，不需要估算。
3. **一次返回约 8 个交易日的分钟线**（实测 1970 根），按 `day` 字段过滤当天即可；顺带具备补最近几天的能力。
4. 昨收基准线沿用现有 `_minute_row` 的算法：取今天之前最后一根 K 线的 `close`。
5. **需要修一个既有隐患**：`_sina_symbol("sh000001")` 会拼成 `szsh000001`。指数代码本身已带 `sh`/`sz` 前缀，必须直接返回原值。

---

## 4. 架构总览

```
                    ┌───────────────────────────────────────────┐
                    │ QuoteFetcher（QThread，后台取数线程）        │
                    │  行情 10 秒  ─┬─> quotes  ──> UI 刷新        │
                    │                └─> store.save_ticks()      │
                    │  分钟线 60 秒 ─┬─> provider.fetch_minute_bars()
                    │                ├─> intraday.build_series()  │
                    │                └─> store.save_minutes()    │
                    └───────────────┬───────────────────────────┘
                                    │ Signal: ready / minutes_ready
                    ┌───────────────▼───────────────────────────┐
                    │ StockWidget（UI 线程）                     │
                    │  ├─ IntradayChartWindow（A: 独立窗口，按需创建）│
                    │  └─ StockRow + SparklineWidget（C: 行内缩略图）│
                    │            └─ 二者共用 IntradayPainter      │
                    └───────────────────────────────────────────┘
```

模块职责与依赖方向（无环）：

| 模块 | 职责 | 依赖 |
|---|---|---|
| `store.py`（新） | SQLite 读写、当天数据清理 | 仅标准库 `sqlite3` |
| `intraday.py`（新） | K 线记录 → 分时序列的纯转换 | 仅标准库 |
| `datasource.py`（改） | 新增"逐只拉分钟线"的网络能力 | 现有 akshare 逻辑 |
| `chart.py`（新） | 绘制核心 + 两个控件 | PySide6、`intraday`、`panel` |
| `panel.py`（新） | 面板几何常量与背景/投影绘制 | PySide6 |
| `main.py`（改） | 取数节奏编排、UI 接入 | 以上全部 |

数据流：`akshare 分钟线` → `datasource.fetch_minute_bars()` → `intraday.build_series()` → 同时进入 `store`（落盘）与 `minutes_ready` 信号（绘制）。

---

## 5. 详细设计

### 5.1 `store.py` —— 本地暂存

- 数据库：`BASE_DIR/data/quote.db`（复用 `config.BASE_DIR`，打包成 exe 后仍跟随 exe 目录，与 `config.json` 一致）；目录不存在时自动创建。

```sql
CREATE TABLE IF NOT EXISTS tick (
    code  TEXT    NOT NULL,
    ts    INTEGER NOT NULL,   -- unix 秒
    price REAL    NOT NULL,
    PRIMARY KEY (code, ts)
);

CREATE TABLE IF NOT EXISTS minute (
    code   TEXT NOT NULL,
    day    TEXT NOT NULL,     -- 'YYYY-MM-DD'
    hhmm   TEXT NOT NULL,     -- 'HH:MM'
    price  REAL NOT NULL,
    avg    REAL,              -- 均价，可能为 NULL
    volume REAL,              -- 该分钟成交量，可能为 NULL
    PRIMARY KEY (code, day, hhmm)
);

CREATE INDEX IF NOT EXISTS idx_minute_day ON minute(day);
```

- 线程模型：SQLite 连接不能跨线程共用，写入发生在取数线程、读取发生在 UI 线程，因此 `Store` 内部用 `threading.local()` 为每个线程持有独立连接，并统一开启 `PRAGMA journal_mode=WAL`。
- 接口：

| 方法 | 说明 |
|---|---|
| `save_ticks(rows)` | `rows = [(code, ts, price)]`，`INSERT OR REPLACE`，单事务批量提交 |
| `save_minutes(rows)` | `rows = [(code, day, hhmm, price, avg, volume)]`，同上 |
| `load_minutes(code, day)` | 返回按 `hhmm` 升序的 `[(hhmm, price, avg, volume)]`，返回原始元组以避免 `store` 反向依赖 `intraday` |
| `load_ticks(code, day)` | 返回当天 `[(ts, price)]`（暂不展示，作为原始数据的读取出口） |
| `purge_before(day)` | `minute` 按 `day` 删除；`tick` 用 Python 算出当天 00:00 的 unix 秒后按 `ts <` 删除 |
| `close()` | 关闭**所有线程**持有的连接（含取数线程），释放 db / -wal / -shm 句柄；close() 之后再次使用会重新建连 |

- 清理时机：程序启动时调一次 `purge_before(今天)`；长期运行跨日时，在"当天第一次拿到行情"时再调一次。
- 写入频率与体量：10 秒排快照约 4 只 × 4 小时 × 6 次/分 ≈ 5760 行/天；分钟级 4 只 × 240 点 = 960 行/天。均为可忽略量级。
- 落盘失败（磁盘只读、库损坏等）不参与主流程：初始化与写入均 `try/except`，失败时把 `store` 置为 `None`（本次运行禁用落盘），并在底部状态栏提示一次，取数照常。

### 5.2 `intraday.py` —— 分时序列装配

纯转换层，不碰网络、不碰 SQLite，便于单测。

```python
@dataclass
class MinutePoint:
    time: str            # 'HH:MM'
    price: float
    avg: float | None    # 累计成交额 / 累计成交量
    volume: float | None

@dataclass
class IntradaySeries:
    code: str
    day: str             # 'YYYY-MM-DD'
    prev_close: float    # 昨收，画基准线用
    points: list[MinutePoint]
    latest_price: float | None   # 10 秒轮询的最新价，拼"正在走"的尾巴
```

- `build_series(code, records, day, prev_close) -> IntradaySeries`
  - 过滤 `day` 字段前 10 位等于当天的记录；
  - 逐行累计 `amount` 与 `volume` 得到 `avg`；**累计成交量为 0**（源头完全不提供量）时 `avg = None`，绘制时跳过该点；**单分钟**成交量为 0 时均价沿用累计值，正常连线；
  - 输入**不要求有序**：按时间排序后再累计均价，输出保证升序；兜底昨收取"时间上最晚"的前一日收盘价；
  - 时间统一成 `'HH:MM'`；按时间升序。
- `attach_latest(series, price) -> IntradaySeries`
  - 若当日最后一根 K 线时间早于当前时间，则追加一个**临时点**：时间取当前 `'HH:MM'`（落在 09:30–11:30 / 13:00–15:00 之外则钳到最近的有效刻度），价格取最新价，均价沿用最后一根的 `avg` —— 这样 10 秒刷新也能看到线在动。
  - 最新价与最后一根价格相同时不追加，避免多余的点。
- `resolve_prev_close(quote_prev_close, records, day) -> float`
  - 优先用行情通道给出的 `Quote.prev_close`（最准）；为 0 或缺失时回退到记录里 `day < 当天` 的最后一根 `close`。
- `build_all(quotes, bars_by_code, day) -> dict[code, IntradaySeries]`
  - 把上一轮的行情快照与分钟线合并成最终序列；某只没有分钟线但有行情时，退化为"单点序列"（图上只有一个点加实时价）。
- 交易时段外的过滤：不额外裁剪 K 线（新浪分钟线本身从 09:30 开始），只按"当天"过滤。

### 5.3 `datasource.py` 变更

1. **修 `_sina_symbol`**：指数代码（`_is_index_code` 为真）直接返回原值，不再拼接前缀。这是数据正确性的前置修复。
2. 新增 `AkshareProvider.fetch_minute_bars(codes) -> dict[str, list[dict]]`：
   - 逐只请求 `stock_zh_a_minute(symbol=_sina_symbol(code), period="1", adjust="")`，返回 `frame.to_dict("records")`（原始记录，按天过滤交给 `intraday`）；
   - **复用现有并发与预算框架** `_per_code(targets, worker, budget)`（4 并发、单轮 12 秒上限）；
   - 目标集合为"非北交所"的全部自选代码（股票、ETF、指数都支持；北交所新浪分钟线不支持，跳过）；
   - 新增独立冷却 `_minute_bars_blocked_until`（沿用 `COOLDOWN = 300` 秒）：一轮一只都没拿到就冷却，避免被限流时反复空跑；
   - 请求量与现有行情轮询相当（分钟线 60 秒一次，4 只 ≈ 4 请求/分钟）。
3. `QuoteService` 增加转发方法 `fetch_minute_bars(codes)`：分钟线**只走 akshare 主通道**，不参与主备切换（pytdx 本次不支持分钟线）；主通道异常时直接抛错，由上层冷却并保留旧数据。

### 5.4 `main.py` 变更 —— 取数节奏与接入

`QuoteFetcher` 在同一个线程里跑两个节奏：

| 节奏 | 常量 | 行为 |
|---|---|---|
| 行情 | 现有 `refresh_seconds`（默认 10 秒） | 不变；拿到 `quotes` 后 `store.save_ticks()` |
| 分钟线 | `MINUTE_INTERVAL = 60.0` 秒 | 按"距上次刷新是否到点"判断；`fetch_minute_bars` → `intraday.build_all` → `store.save_minutes` → `emit minutes_ready` |

- 新增信号 `minutes_ready = Signal(dict)`（`code -> IntradaySeries`），UI 只负责绘制。
- 休盘行为：
  - 休盘期间不按间隔拉分钟线（与现有"休盘只取一次"一致）；
  - 状态从「交易中」变为「已收盘」时**强制补拉一次**，拿到全天权威分钟线并落盘；
  - 右键「立即刷新」同时强制刷新分钟线。
- 启动恢复：程序启动、首轮取数前，先从 `store.load_minutes(code, 今天)` 读当天已有点位并 `emit` 一次，这样重启后分时图不会空白。
- UI 接入：
  - `StockRow` 增加 `SparklineWidget`（`show_sparkline` 为真时显示），位置在**名称与价格之间**（固定宽约 72px）。放中间而非最右，是为了避开右下角缩放热区；
  - `StockRow` 新增回调/信号：缩略图被点击 → `StockWidget._open_chart(code)`；
  - `_open_chart(code)`：`self._charts: dict[str, IntradayChartWindow]` 中已存在则 `show() + raise_()` 复用，否则新建并登记，窗口关闭时从字典移除；
  - 右键菜单新增「分时图」子菜单，列出当前自选代码（作为缩略图方案被关掉时的入口）；
  - 分时窗口与缩略图都接收 `minutes_ready` 的最新序列；窗口未打开时不创建任何绘制对象（省开销）。
- 事件冲突处理：行内 `name_label / price_label / pct_label` 保持 `WA_TransparentForMouseEvents`，只有缩略图控件接收鼠标事件，主窗口的拖动逻辑不变。**若实测点击缩略图影响了拖动体验，则退化为双击整行打开**（`StockRow.mouseDoubleClickEvent` 转发），缩略图仍保留为纯展示。

### 5.5 `chart.py` —— 绘制

共用绘制核心 `IntradayPainter`（坐标映射、配色、基准线只写一份，最后砍 A 或 C 只删控件）：

| 方法 | 说明 |
|---|---|
| `map_x(hhmm, rect)` | 时间 → X。X 轴固定 **09:30–11:30、13:00–15:00 共 240 分钟**，午休不占宽度；未开盘部分留空，线随时间向右生长 |
| `map_y(price, prev_close, max_dev, rect)` | 价格 → Y。**以昨收为中线上下对称**（分时图惯例），`max_dev` 取当天最大涨跌幅，最小按 0.5% 兜底，避免横盘时曲线被放大成噪声 |
| `draw_baseline` | 昨收虚线（平盘色，半透明） |
| `draw_price_line` | 价格折线，颜色按最新价 vs 昨收取涨/跌/平色 |
| `draw_avg_line` | 均价线（黄色系，半透明），`avg` 为 `None` 的点跳过 |
| `draw_axes` | 右侧价格刻度（含涨跌幅）、底部时间刻度（09:30 / 10:30 / 11:30 / 13:00 / 14:00 / 15:00） |

- `SparklineWidget`：固定高约 22px，**无坐标轴、无文字**，只画价格折线 + 昨收虚线；`set_series()` 后 `update()`；`mouseReleaseEvent` 发 `clicked` 信号；无数据时画一条居中的灰色水平线占位。
- `IntradayChartWindow`：独立无边框窗口，默认 640×360，首次打开在主窗口右侧（越界则回退到屏幕内）；可拖动、右下角可缩放；左上角显示"名称 + 最新价 + 涨跌幅"，右上角显示数据源与更新时间；右键菜单：立即刷新 / 窗口置顶 / 关闭；无数据时居中显示"暂无分时数据"。
- 配色与透明度全部取自 `config`（`up_color` / `down_color` / `flat_color` / `font_color` / `font_opacity` / `bg_color` / `opacity`），与主窗口风格一致，改设置即时生效。

### 5.6 `panel.py` —— 抽公共面板绘制

主窗口现有的面板绘制（`SHADOW` / `PANEL_RADIUS` / `BORDER` / `GRIP` / `MIN_PANEL` 常量 + 圆角背景 + 分层投影 + 右下角把手）会被分时窗口复用。做法：把这些常量与绘制逻辑抽到 `panel.py`，主窗口改为调用，分时窗口直接复用。

- 这是**纯搬移**，不改变任何绘制结果与交互行为；
- 收益：分时窗口不必复制约 20 行投影逻辑，两处风格不会各自漂移；
- 回归方式：主窗口目视对比改动前后（位置、投影、圆角、把手、拖动、四边四角缩放全部保持）。

### 5.7 配置与设置面板

| 变更 | 内容 |
|---|---|
| `config.DEFAULTS` 新增 | `show_sparkline: True` |
| `config.json` | 首次运行自动补上该键（现有 `Config` 逻辑对缺失键取默认值，无需迁移代码） |
| 设置面板 | 新增勾选框「显示行内分时缩略图」，与 `show_source` 同级 |

---

## 6. 错误处理

| 场景 | 行为 |
|---|---|
| 分钟线拉取失败（限流/断网/接口变更） | 通道进 5 分钟冷却；**保留上一次的分时线继续显示**；底部沿用现有 footer 警告机制提示原因（完整报错在悬浮提示里） |
| 分钟线返回空 | 同上处理；不把已有分时线清空 |
| 落盘失败 | 禁用落盘并提示一次，取数与绘制不受影响 |
| 指数/ETF 代码 | 走同一分钟线接口；北交所代码跳过分钟线（图上显示"暂无分时数据"） |
| 非交易日/未开盘打开分时图 | 显示"暂无分时数据"占位，不报错 |
| 分时窗口在数据尚未到达时创建 | 先显示占位，收到 `minutes_ready` 后自动绘制 |

---

## 7. 测试策略

优先覆盖纯逻辑，UI 靠人工目视（不做 UI 自动化）。测试用标准库 `unittest`（**不引入新依赖**），新增 `tests/` 目录。

| 测试 | 覆盖点 |
|---|---|
| `tests/test_intraday.py` | 按天过滤（隔日数据被剔除）；均价随累计量正确递增；**累计**成交量为 0 时 `avg=None`、单分钟 0 量沿用累计值；乱序输入下的升序输出与按时间累计；`resolve_prev_close` 的优先与回退（含乱序）；`effective_points` 的追加/就地更新；无分钟线时退化为单点且涨跌幅可算；空记录时返回空序列 |
| `tests/test_store.py` | 临时目录建库；`save_minutes` → `load_minutes` 往返一致且按时间升序；`save_ticks` 重复时间戳覆盖而非重复插入；`purge_before` 只删旧数据；跨线程读写（模拟取数线程写、UI 线程读） |
| 手工验证 | 交易时段打开分时图，观察：线随刷新向右生长、均价线在昨日基准上下波动、涨跌配色正确、缩放/置顶正常、`show_sparkline` 开关生效、程序重启后当天已有点位立即恢复 |

---

## 8. 交付顺序

1. **阶段一**：`store.py` + `intraday.py`（含单测）→ `datasource` 分钟线能力 → `main` 取数节奏与落盘。
2. **阶段二**：`panel.py` 抽取 + `chart.py` 绘制核心 + `IntradayChartWindow`（A）。
3. **阶段三**：`SparklineWidget` 行内缩略图（C）+ `show_sparkline` 配置与设置项。
4. **阶段四**：你对比 A / C 的实际效果，决定保留哪个（砍掉其中一个只删除对应控件，数据层与绘制核心不动）。

---

## 9. 风险与已知限制

1. **新浪限流**：分钟线 60 秒一次、4 只约 4 请求/分钟，与现有行情轮询同量级，风险可控；被打断时靠冷却 + 保留旧线兜底。
2. **1 分钟粒度决定线的精细度**：10 秒的快照只用来落 `tick` 表和拼"正在走的尾巴"，线的形状是分钟级。
3. **均价线在成交量缺失时不可用**：实测主接口带 `volume`/`amount`，属兜底场景。
4. **只保留当天**：跨日清理后昨天数据不可回看，这是本期的明确取舍；表结构含 `day` 字段，后续要做历史回看只需去掉清理策略。
5. **`panel.py` 抽取主窗口绘制**：属既有代码的小改进，需目视回归确认拖动/缩放/投影无变化。
