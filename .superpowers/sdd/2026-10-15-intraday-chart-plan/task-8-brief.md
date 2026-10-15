## 任务 8：行内缩略图 `SparklineWidget`（原型 C）与点击打开

**文件：**
- 修改：`chart.py`（追加 `SparklineWidget`）
- 修改：`main.py`（`StockRow`、鼠标事件、`apply_style`）
- 修改：`settings_dialog.py`（勾选框）

- [ ] **步骤 1：追加 `SparklineWidget` 到 `chart.py` 末尾**

```python
class SparklineWidget(QWidget):
    """行内迷你分时：无坐标轴无文字。

    自己不接收鼠标事件（主窗口统一做命中判断），这样主窗口的拖动逻辑完全不受影响。
    """

    WIDTH = 72
    HEIGHT = 22

    def __init__(self, code: str, parent=None):
        super().__init__(parent)
        self.code = code
        self._series: IntradaySeries | None = None
        self._brush = IntradayPainter(DEFAULTS["up_color"], DEFAULTS["down_color"], DEFAULTS["flat_color"], DEFAULTS["font_color"])
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self.setToolTip("点击查看分时图")

    def apply_style(self, values: dict) -> None:
        style = {**DEFAULTS, **{key: value for key, value in (values or {}).items() if key in DEFAULTS}}
        alpha = max(0.0, min(1.0, float(style["font_opacity"])))
        self._brush.set_colors(style["up_color"], style["down_color"], style["flat_color"], style["font_color"], alpha)
        self.update()

    def set_series(self, series: IntradaySeries | None) -> None:
        self._series = series
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
        if self._series is None or not self._series.points:
            # 没有数据：一条居中的灰线占位，避免行高变化
            painter.setPen(QPen(QColor(128, 128, 128, 120), 1))
            painter.drawLine(QPointF(rect.left(), rect.center().y()), QPointF(rect.right(), rect.center().y()))
            painter.end()
            return
        self._brush.draw(painter, rect, self._series)
        painter.end()
```

- [ ] **步骤 2：`StockRow` 加入缩略图**

2a. `__init__` 中，把三个标签的构建改为带上缩略图（`from chart import IntradayChartWindow, SparklineWidget`）：

```python
        self.name_label = QLabel(code)
        self.sparkline = SparklineWidget(code, self)
        self.price_label = QLabel("--")
        self.pct_label = QLabel("--")
        self.price_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.pct_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.name_label.setToolTip(code)
        self.name_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.sparkline.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        for label, stretch in ((self.name_label, 5), (self.sparkline, 0), (self.price_label, 3), (self.pct_label, 3)):
            label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            layout.addWidget(label, stretch)
```

2b. `apply_style` 增加两个参数并转发样式（签名末尾补 `show_sparkline: bool = True`）：

```python
        self.sparkline.setVisible(show_sparkline)
        self.sparkline.apply_style({
            "up_color": up_color,
            "down_color": down_color,
            "flat_color": flat_color,
            "font_color": font_color,
            "font_opacity": alpha,
        })
```

2c. 追加一个转发方法：

```python
    def set_series(self, series) -> None:
        self.sparkline.set_series(series)
```

- [ ] **步骤 3：主窗口接入点击命中与样式透传**

3a. 类常量补一个点击容差：`CLICK_SLOP = 4      # 位移小于这个像素数才算"点击"而不是拖窗口"`

3b. `apply_style` 里 `row.apply_style(...)` 调用追加参数：

```python
            row.apply_style(
                size,
                cfg["font_color"],
                cfg["up_color"],
                cfg["down_color"],
                cfg["flat_color"],
                alpha,
                bool(cfg["show_sparkline"]),
            )
```

3c. 追加命中判断与鼠标处理（`mousePressEvent` / `mouseReleaseEvent` / `mouseMoveEvent` 三处改动）：

```python
    def _sparkline_code_at(self, pos) -> str:
        """落点在某行的缩略图上就返回那只代码（缩略图自己不接收鼠标事件）。"""
        for code, row in self._rows.items():
            if not row.sparkline.isVisible():
                continue
            if row.sparkline.geometry().translated(row.pos()).contains(pos):
                return code
        return ""
```

`mouseMoveEvent` 中"未按键"分支改为：先把落点换算成缩略图代码，命中则用 `Qt.PointingHandCursor`：

```python
        if not (event.buttons() & Qt.LeftButton):
            pos = event.position().toPoint()
            if self._sparkline_code_at(pos):
                self.setCursor(Qt.PointingHandCursor)
            else:
                self.setCursor(CURSOR_FOR.get(self._hit_region(pos), Qt.OpenHandCursor))
            region = self._hit_region(pos)
            if region != self._hover_region:
                self._hover_region = region
                if float(self.config["opacity"]) <= 0.0:
                    self.update()
            return super().mouseMoveEvent(event)
```

`mousePressEvent` 里 `event.accept()` 之前补一句记录按下位置：

```python
        self._press_pos = event.globalPosition().toPoint()
```

`mouseReleaseEvent` 开头补"判定为点击则打开分时图"：

```python
    def mouseReleaseEvent(self, event) -> None:
        moved = 0
        if self._press_pos is not None:
            moved = (event.globalPosition().toPoint() - self._press_pos).manhattanLength()
        if moved <= self.CLICK_SLOP and self._resize_geom is None and self._drag_offset is not None:
            code = self._sparkline_code_at(event.position().toPoint())
            if code:
                self._open_chart(code)
        self._press_pos = None
        ...
```

（其余原有逻辑保持不变。）

3d. `on_minutes` 里把序列也分给缩略图，在 `self._series[code] = series` 之后补：

```python
            row = self._rows.get(code)
            if row is not None:
                row.set_series(series)
```

3e. `_rebuild_rows` 里新建的行需要立刻应用一次样式与序列，在 `row = StockRow(code)` 之后补：

```python
            row.apply_style(
                int(self.config["font_size"]),
                self.config["font_color"],
                self.config["up_color"],
                self.config["down_color"],
                self.config["flat_color"],
                self._text_alpha,
                bool(self.config["show_sparkline"]),
            )
            if code in self._series:
                row.set_series(self._series[code])
```

- [ ] **步骤 4：设置面板加勾选框**

4a. `__init__` 里 `show_source_check` 之后加：

```python
        self.sparkline_check = QCheckBox("显示行内分时缩略图")
        self.sparkline_check.setChecked(bool(config["show_sparkline"]))
        self.sparkline_check.setToolTip("关掉后列表里不再显示迷你分时图，分时图仍可右键菜单打开")
```

4b. 表单里加一行（紧跟 `form.addRow("", self.show_source_check)` 之后）：

```python
        form.addRow("", self.sparkline_check)
```

4c. `values()` 增加一项：

```python
            "show_sparkline": self.sparkline_check.isChecked(),
```

4d. `_restore_defaults` 增加一行：

```python
        self.sparkline_check.setChecked(bool(DEFAULTS["show_sparkline"]))
```

- [ ] **步骤 5：静态检查**

运行：`.\.venv\Scripts\python.exe -m py_compile main.py chart.py settings_dialog.py`
预期：无输出
运行：`.\.venv\Scripts\python.exe -m unittest discover -s tests -t . -v`
预期：全部用例 PASS（40 个）

- [ ] **步骤 6：目视验证**

运行：`.\.venv\Scripts\python.exe main.py`，逐项确认：

- [ ] 每一行名称与价格之间出现约 72×22 的迷你分时线，颜色随涨跌
- [ ] 鼠标移到缩略图上指针变成小手
- [ ] 点击缩略图弹出该只的分时图窗口；**在同一位置按住拖动窗口仍然生效**（这是当初最担心的事件冲突点）
- [ ] 在缩略图以外区域点击不会误弹窗
- [ ] 设置里取消勾选「显示行内分时缩略图」→ 列表回到纯文字，缩略图消失，仍可用右键菜单打开分时图
- [ ] 重启程序后该勾选状态被记住

- [ ] **步骤 7（仅在步骤 6 出现手感问题时执行）：退化为双击整行打开**

本计划采用的单击实现是「主窗口在 `mouseReleaseEvent` 里判断位移 ≤ `CLICK_SLOP` 且落点在缩略图矩形内」，因此不需要子控件接收事件、拖动不受影响。若实测仍觉得别扭（例如手抖导致点不开），按规格 5.4 的约定退化为**双击整行**：

在 `StockWidget` 追加：

```python
    def mouseDoubleClickEvent(self, event) -> None:
        """双击任意行打开该只分时图；缩略图方案手感不好时的退路。"""
        if event.button() != Qt.LeftButton:
            return super().mouseDoubleClickEvent(event)
        for code, row in self._rows.items():
            top = row.pos().y()
            if top <= event.position().toPoint().y() <= top + row.height():
                self._open_chart(code)
                break
        event.accept()
```

同时把 `mouseReleaseEvent` 里那段"命中缩略图就打开"的逻辑删掉，缩略图保留为纯展示（`setToolTip("双击整行查看分时图")`）。

---

