## 任务 6：独立分时图窗口 `IntradayChartWindow`（原型 A）

**文件：**
- 修改：`chart.py`（在文件末尾追加）
- 修改：`tests/test_chart.py`（追加缩略图尺寸断言由任务 8 负责，本任务不加测试）

- [ ] **步骤 1：追加窗口类到 `chart.py` 末尾**

```python
class IntradayChartWindow(QWidget):
    """独立分时图窗口：无边框、可拖动、右下角可缩放、可置顶。"""

    MIN_SIZE = (360, 240)
    DEFAULT_SIZE = (640, 360)
    AXIS_WIDTH = 66              # 右侧价格刻度宽度
    TIME_AXIS_HEIGHT = 20        # 底部时间刻度高度
    HEADER_HEIGHT = 26
    MIN_OPACITY = 0.35           # 图表需要底衬，背景不透明度不跟随面板设到 0

    def __init__(self, code: str, name: str = "", parent=None):
        super().__init__(parent)
        self.code = code
        self._name = name or code
        self._series: IntradaySeries | None = None
        self._source = ""
        self._updated = ""
        self._warning = ""
        self._style = dict(DEFAULTS)
        self._brush = IntradayPainter(DEFAULTS["up_color"], DEFAULTS["down_color"], DEFAULTS["flat_color"], DEFAULTS["font_color"])
        self._drag_offset = None
        self._resizing = False
        self._resize_origin = None
        self._resize_geom = None

        self.setWindowTitle(f"分时图 · {self._name}")
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setMouseTracking(True)
        self.setMinimumSize(self.MIN_SIZE[0] + 2 * SHADOW, self.MIN_SIZE[1] + 2 * SHADOW)
        self.resize(self.DEFAULT_SIZE[0] + 2 * SHADOW, self.DEFAULT_SIZE[1] + 2 * SHADOW)

    # ---------- 数据与样式 ----------

    def set_series(self, series: IntradaySeries | None, source: str = "", updated: str = "", warning: str = "") -> None:
        self._series = series
        self._source = source
        self._updated = updated
        self._warning = warning
        self.update()

    def apply_style(self, values: dict) -> None:
        """样式取自 config；只认识 DEFAULTS 里有的键，缺的用默认值。"""
        self._style = {**DEFAULTS, **{key: value for key, value in (values or {}).items() if key in DEFAULTS}}
        alpha = max(0.0, min(1.0, float(self._style["font_opacity"])))
        self._brush.set_colors(self._style["up_color"], self._style["down_color"], self._style["flat_color"], self._style["font_color"], alpha)
        self.update()

    # ---------- 绘制 ----------

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        panel = panel_rect(self.width(), self.height())
        opacity = max(self.MIN_OPACITY, min(1.0, float(self._style["opacity"])))
        draw_shadow(painter, panel, opacity)
        draw_background(painter, panel, self._style["bg_color"], opacity)
        draw_grip(painter, panel, opacity)

        self._draw_header(painter, panel)
        chart = self._chart_rect(panel)
        if self._series is None or not self._series.points:
            painter.setFont(QFont(UI_FONT, 11))
            painter.setPen(QPen(self._brush.text_color()))
            painter.drawText(chart, Qt.AlignCenter, "暂无分时数据")
            painter.end()
            return
        self._draw_axes(painter, chart)
        self._brush.draw(painter, chart, self._series)
        painter.end()

    def _chart_rect(self, panel: QRectF) -> QRectF:
        return QRectF(
            panel.left() + 12,
            panel.top() + self.HEADER_HEIGHT + 10,
            max(1.0, panel.width() - 24 - self.AXIS_WIDTH),
            max(1.0, panel.height() - self.HEADER_HEIGHT - self.TIME_AXIS_HEIGHT - 18),
        )

    def _draw_header(self, painter: QPainter, panel: QRectF) -> None:
        header = QRectF(panel.left() + 12, panel.top() + 6, panel.width() - 24, self.HEADER_HEIGHT)
        size = max(9, int(self._style["font_size"]) - 2)
        painter.setFont(QFont(UI_FONT, size))
        painter.setPen(QPen(self._brush.text_color()))
        painter.drawText(header, Qt.AlignLeft | Qt.AlignVCenter, f"{self._name}  {self.code}")
        series = self._series
        if series is None or not series.points:
            return
        if series.direction > 0:
            color = QColor(self._style["up_color"])
        elif series.direction < 0:
            color = QColor(self._style["down_color"])
        else:
            color = QColor(self._style["flat_color"])
        color.setAlphaF(max(0.0, min(1.0, float(self._style["font_opacity"]))))
        painter.setFont(QFont(NUM_FONT, size))
        painter.setPen(QPen(color))
        decimals = 3 if self._is_fund(self.code) else 2
        text = f"{series.last_price:.{decimals}f}  {series.change_pct:+.2f}%"
        painter.drawText(header, Qt.AlignRight | Qt.AlignVCenter, text)

    def _draw_axes(self, painter: QPainter, chart: QRectF) -> None:
        series = self._series
        points = effective_points(series)
        baseline = self._brush.baseline_of(series, points)
        max_dev = self._brush.max_deviation(points, baseline)
        decimals = 3 if self._is_fund(self.code) else 2
        painter.setFont(QFont(NUM_FONT, max(8, int(self._style["font_size"]) - 4)))
        grid = QColor(GRID_COLOR)
        grid.setAlphaF(0.35)
        for price in (baseline + max_dev, baseline, baseline - max_dev):
            y = self._brush.map_y(price, baseline, max_dev, chart)
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(grid, 1, Qt.DotLine))
            painter.drawLine(QPointF(chart.left(), y), QPointF(chart.right(), y))
            painter.setPen(QPen(self._brush.text_color()))
            label = QRectF(chart.right() + 4, y - 8, self.AXIS_WIDTH - 8, 16)
            painter.drawText(label, Qt.AlignLeft | Qt.AlignVCenter, f"{price:.{decimals}f}")
        for index, text in ((0, "09:30"), (60, "10:30"), (120, "11:30/13:00"), (180, "14:00"), (240, "15:00")):
            x = self._brush.map_x(index, chart)
            painter.setPen(QPen(self._brush.text_color()))
            painter.drawText(QRectF(x - 28, chart.bottom() + 2, 56, self.TIME_AXIS_HEIGHT - 2), Qt.AlignCenter, text)
        if self._updated:
            painter.setFont(QFont(UI_FONT, max(8, int(self._style["font_size"]) - 5)))
            painter.setPen(QPen(self._brush.text_color()))
            footer = f"{self._source} · {self._updated} 更新"
            if self._warning:
                footer += f"（{self._warning}）"
            painter.drawText(QRectF(chart.left(), chart.bottom() + 2, chart.width(), self.TIME_AXIS_HEIGHT - 2), Qt.AlignLeft | Qt.AlignVCenter, footer)

    @staticmethod
    def _is_fund(code: str) -> bool:
        text = str(code).strip()
        return text[:1] == "5" or text[:2] in ("15", "16", "18")

    # ---------- 交互 ----------

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)
        panel = panel_rect(self.width(), self.height()).toRect()
        pos = event.position().toPoint()
        if 0 <= panel.right() - pos.x() <= GRIP and 0 <= panel.bottom() - pos.y() <= GRIP:
            self._resizing = True
            self._resize_origin = event.globalPosition().toPoint()
            self._resize_geom = QRect(self.geometry())
        else:
            self._drag_offset = event.globalPosition().toPoint() - self.pos()
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._resizing and self._resize_geom is not None:
            delta = event.globalPosition().toPoint() - self._resize_origin
            rect = QRect(self._resize_geom)
            rect.setRight(rect.right() + delta.x())
            rect.setBottom(rect.bottom() + delta.y())
            width = max(self.minimumWidth(), rect.width())
            height = max(self.minimumHeight(), rect.height())
            self.resize(width, height)
        elif self._drag_offset is not None:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_offset = None
        self._resizing = False
        self._resize_origin = None
        self._resize_geom = None
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event) -> None:
        menu = QMenu(self)
        menu.addAction("立即刷新", self._refresh)
        on_top = menu.addAction("窗口置顶")
        on_top.setCheckable(True)
        on_top.setChecked(bool(self.windowFlags() & Qt.WindowStaysOnTopHint))
        on_top.triggered.connect(self._toggle_on_top)
        menu.addSeparator()
        menu.addAction("关闭", self.close)
        menu.exec(event.globalPos())

    def _refresh(self) -> None:
        """转交给主窗口：分时数据由取数线程统一提供。"""
        parent = self.parent()
        if parent is not None and hasattr(parent, "request_refresh"):
            parent.request_refresh()

    def _toggle_on_top(self, checked: bool) -> None:
        position = self.pos()
        self.setWindowFlags(Qt.FramelessWindowHint | (Qt.WindowStaysOnTopHint if checked else Qt.WindowType(0)))
        self.show()
        self.move(position)
```

- [ ] **步骤 2：静态检查**

运行：`.\.venv\Scripts\python.exe -m py_compile chart.py`
预期：无输出
运行：`.\.venv\Scripts\python.exe -m unittest tests.test_chart -v`
预期：12 个用例仍全部 PASS

- [ ] **步骤 3：目视验证（先造一段假数据，避免等开盘）**

运行：

```powershell
.\.venv\Scripts\python.exe -c "import sys; from PySide6.QtWidgets import QApplication; from chart import IntradayChartWindow; from intraday import MinutePoint, IntradaySeries; app = QApplication(sys.argv); w = IntradayChartWindow('600519', '贵州茅台'); s = IntradaySeries(code='600519', day='2026-10-15', prev_close=100.0); s.points = [MinutePoint(time=f'{9 + (30 + i) // 60:02d}:{(30 + i) % 60:02d}', price=100 + ((i % 40) - 20) * 0.08, avg=100 + ((i % 30) - 15) * 0.05) for i in range(120)]; s.latest_price = 101.2; w.apply_style({'opacity': 0.9, 'font_size': 14, 'font_opacity': 1.0, 'bg_color': '#101722', 'up_color': '#F05A4F', 'down_color': '#2BC48A', 'flat_color': '#FFFFFF', 'font_color': '#E8EEF7'}); w.set_series(s, 'akshare·新浪分钟', '14:03:11', ''); w.show(); app.exec()"
```

逐项确认：

- [ ] 窗口无系统标题栏，圆角 + 投影与主窗口风格一致
- [ ] 左上角显示「贵州茅台 600519」，右上角价格与涨跌幅为红色（涨）
- [ ] 画出了价格折线（红）、黄色均价线、中间一条昨收虚线
- [ ] 右侧有三档价格刻度、底边有 09:30 / 10:30 / 11:30|13:00 / 14:00 / 15:00 时间刻度
- [ ] 左键拖动可移动窗口；右下角可缩放
- [ ] 右键菜单三项都在：「立即刷新 / 窗口置顶 / 关闭」，置顶可切换

---

