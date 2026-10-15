## 任务 7：`main.py` 取数节奏、落盘与窗口接入

**文件：**
- 修改：`main.py`（`StockWidget.__init__`、`QuoteFetcher`、`_open_chart`、`contextMenuEvent`、`closeEvent`）
- 修改：`config.py`（`DEFAULTS` 增加 `show_sparkline`）

- [ ] **步骤 1：`config.py` 的 `DEFAULTS` 增加配置项**

在 `"show_source": True,` 之后插入一行：

```python
    "show_sparkline": True,    # 行内迷你分时缩略图（关掉就只留独立分时窗口）
```

- [ ] **步骤 2：`main.py` 导入补充**

导入区加入：

```python
import time

from chart import IntradayChartWindow
import intraday
from store import Store
```

并把 `QtCore` 那行改为（`QPoint` 是 `_chart_position` 需要的）：

```python
from PySide6.QtCore import QPoint, QRect, Qt, QThread, Signal
```

（`time`、`QPoint`、`intraday`、`Store` 原先都没有；`datetime` 已有。）

- [ ] **步骤 3：`QuoteFetcher` 增加落盘与分钟线节奏**

3a. 类属性与信号：

```python
    MINUTE_INTERVAL = 60.0           # 分钟线刷新间隔（秒）

    minutes_ready = Signal(dict)     # code -> IntradaySeries
```

3b. `__init__` 增加参数与状态（签名改为 `def __init__(self, service: QuoteService, store=None, parent=None):`）：

```python
        self._store = store
        self._minute_since = 0.0
        self._closed_state = ""
        self.store_failed = ""
```

3c. `run()` 整体替换为下面这版（原有逻辑全部保留，只多了开头的"库里当天数据"恢复、行情落盘、以及分钟线节奏）：

```python
    def run(self) -> None:
        self._restore_series()
        while not self._stop.is_set():
            self._wake.clear()
            codes = list(self._codes)
            state = market_state()
            if state != self._state:
                self._state = state
                self.state_changed.emit(state)
            live = state == "交易中"
            force = self._force
            self._force = False
            done = self._closed_done or self._closed_fails >= self.CLOSED_RETRY

            # 休盘：已经成功取过一次（或失败次数用完了）就不再发请求，只等开盘
            if not live and not force and done:
                self._wake.wait(self.CLOSED_POLL)
                continue

            fetched_ok = False
            quotes: list = []
            if codes:
                self.started_fetch.emit()
                try:
                    quotes, source, warning = self._service.fetch(codes)
                    self._save_ticks(quotes)
                    self.ready.emit(quotes, source, warning)
                    fetched_ok = bool(source)
                except Exception as exc:
                    self.ready.emit([], "", str(exc))

            # 分钟线：交易中每分钟一次；状态刚切到休盘时补一次（收盘那次拿到全天数据）
            if quotes and self._minute_due(state):
                self._fetch_minutes(quotes)

            if live:
                self._closed_done = False
                self._closed_fails = 0
            else:
                # 休盘那次没取到（网络抖了）就隔一会儿再试，最多 CLOSED_RETRY 次；
                # 成功了就安静下来，直到开盘前不再打扰接口
                self._closed_done = fetched_ok
                self._closed_fails = 0 if fetched_ok else self._closed_fails + 1
            self._wake.wait(self._interval if live else self.CLOSED_POLL)
```

3d. 在 `run()` 之后追加这几个方法：

```python
    def _minute_due(self, state: str) -> bool:
        """交易中每 MINUTE_INTERVAL 秒拉一次；休盘只在状态刚切换时补一次（收盘那次拿全天数据）。"""
        if state != "交易中":
            if self._closed_state == state:
                return False
            self._closed_state = state
            return True
        self._closed_state = ""
        now = time.monotonic()
        if now - self._minute_since < self.MINUTE_INTERVAL:
            return False
        self._minute_since = now
        return True

    def _fetch_minutes(self, quotes: list) -> None:
        """拉每只当天分钟线，落盘后交给界面画分时图。"""
        day = datetime.now().strftime("%Y-%m-%d")
        codes = [quote.code for quote in quotes]
        try:
            bars = self._service.fetch_minute_bars(codes)
        except Exception as exc:
            self.minute_warning = str(exc)
            return
        self.minute_warning = ""
        series = intraday.build_all(quotes, bars, day)
        self._save_minutes(series, day)
        self.minutes_ready.emit(series)

    def _restore_series(self) -> None:
        """启动时先把库里当天的分钟点读出来，重启后分时图不至于空白。"""
        if self._store is None or not self._codes:
            return
        day = datetime.now().strftime("%Y-%m-%d")
        restored = {}
        for code in self._codes:
            try:
                rows = self._store.load_minutes(code, day)
            except Exception:
                continue
            if rows:
                restored[code] = intraday.series_from_rows(code, day, rows)
        if restored:
            self.minutes_ready.emit(restored)

    def _save_ticks(self, quotes: list) -> None:
        """10 秒原始快照落盘；失败就禁用落盘，不拖累取数。"""
        if self._store is None:
            return
        ts = int(time.time())
        rows = [(quote.code, ts, quote.price) for quote in quotes if quote.price > 0]
        try:
            self._store.save_ticks(rows)
        except Exception as exc:
            self._store = None
            self.store_failed = f"本地暂存已禁用：{exc}"

    def _save_minutes(self, series_by_code: dict, day: str) -> None:
        if self._store is None:
            return
        try:
            self._store.save_minutes(intraday.minute_rows(series_by_code, day))
        except Exception as exc:
            self._store = None
            self.store_failed = f"本地暂存已禁用：{exc}"
```

`__init__` 里同时补一个初始值：`self.minute_warning = ""`。

- [ ] **步骤 4：`StockWidget` 接入落盘、分时信号与窗口**

4a. `__init__` 中 `self._names` 之后补：

```python
        self._store, store_error = self._open_store()
        self._series: dict = {}
        self._charts: dict = {}
        self._press_pos = None
```

并把 `_last_warning` 的初始化改为带上落盘错误：`self._last_warning = store_error`；`_fetcher` 的创建改为 `QuoteFetcher(self._service, self._store, self)`，并连接新信号：

```python
        self._fetcher.minutes_ready.connect(self.on_minutes)
```

4b. 追加两个方法（放在 `on_quotes` 之前）：

```python
    @staticmethod
    def _open_store():
        """落盘失败（磁盘只读、库损坏）不该拖垮小组件：返回 None 表示本次运行禁用暂存。"""
        try:
            store = Store()
            store.purge_before(datetime.now().strftime("%Y-%m-%d"))
            return store, ""
        except Exception as exc:
            return None, f"本地暂存不可用：{exc}"

    def on_minutes(self, series_by_code: dict) -> None:
        """分钟线到达：更新已打开的分时窗口（无数据的那些保留上一轮，不清空）。"""
        for code, series in series_by_code.items():
            if not series.points and code in self._series:
                continue
            self._series[code] = series
            window = self._charts.get(code)
            if window is not None:
                window.set_series(series, self._service.source_label, self._last_update, self._last_warning)

    def _open_chart(self, code: str) -> None:
        """打开分时图窗口；已经开着就复用并提到前面。"""
        window = self._charts.get(code)
        if window is None:
            window = IntradayChartWindow(code, self._names.get(code, ""), self)
            window.apply_style(self.config.as_dict())
            window.destroyed.connect(lambda _=None, c=code: self._charts.pop(c, None))
            self._charts[code] = window
            window.move(self._chart_position())
        series = self._series.get(code)
        if series is not None:
            window.set_series(series, self._service.source_label, self._last_update, self._last_warning)
        window.show()
        window.raise_()

    def _chart_position(self):
        """首次打开时贴着主窗口右侧；放不下就回退到屏幕内的左上角。"""
        screen = QApplication.primaryScreen().availableGeometry()
        x = self.x() + self.width() - SHADOW + 8
        y = self.y()
        if x + 640 > screen.right():
            x = max(screen.left(), screen.right() - 660)
        return QPoint(x, max(screen.top(), min(y, screen.bottom() - 380)))

    def request_refresh(self) -> None:
        """分时窗口右键「立即刷新」转过来（窗口的父对象就是主窗口）。"""
        self._fetcher.wake()
```

4c. `on_quotes` 尾部追加一句，把落盘失败暴露到状态栏：

```python
        if self._fetcher.store_failed:
            self._last_warning = self._last_warning or self._fetcher.store_failed
```

4d. `contextMenuEvent` 在「设置…」之后加入分时图入口：

```python
        chart_menu = menu.addMenu("分时图")
        for code in self.config["codes"]:
            name = self._names.get(code, code)
            chart_menu.addAction(f"{name}  {code}", lambda _=False, c=code: self._open_chart(c))
```

4e. `closeEvent` 里关闭全部分时窗口并关闭数据库：

```python
        for window in list(self._charts.values()):
            window.close()
        if self._store is not None:
            self._store.close()
```

- [ ] **步骤 5：静态检查与启动验证**

运行：`.\.venv\Scripts\python.exe -m py_compile main.py config.py`
预期：无输出
运行：`.\.venv\Scripts\python.exe -c "import main"`
预期：无输出、无异常

- [ ] **步骤 6：目视验证**

运行：`.\.venv\Scripts\python.exe main.py`，逐项确认：

- [ ] 程序正常启动，主窗口行情照旧刷新（底部显示 `akshare·新浪分钟 · HH:MM:SS 更新`）
- [ ] 首次启动后 `D:\work\stock_widget\data\quote.db` 被创建
- [ ] 右键 → 「分时图」→ 点任意一只，弹出分时图窗口，能看到当天分时线；再点同一只不会重复开窗
- [ ] 等 60–90 秒，分时线向右生长；右键主窗口「立即刷新」后分时窗口数据也更新
- [ ] 关闭程序再启动，分时窗口里的当天已有点位立刻就有（不是空白）
- [ ] 若当前是休盘时段：等一轮后 `minute` 表里应写入全天分钟点，可用下面的命令确认

```powershell
.\.venv\Scripts\python.exe -c "from store import Store; s = Store(); print(len(s.load_minutes('600519', __import__('datetime').date.today().isoformat())), len(s.load_ticks('600519', __import__('datetime').date.today().isoformat())))"
```

预期：分钟点数量在 200 上下（或 0 表示今天还没开盘数据），tick 数量随运行时间增长

---

