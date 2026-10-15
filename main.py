"""桌面股票监控小组件 —— 入口。

操作说明：
- 左键按住任意位置拖动，移动窗口
- 拖右下角小三角，缩放窗口大小
- 右键：立即刷新 / 设置 / 切换数据源 / 窗口置顶 / 退出
- 所有样式和自选股都存在同目录 config.json，下次启动自动恢复
"""
from __future__ import annotations

import os
import sys
import threading
import time
from datetime import datetime

from PySide6.QtCore import QPoint, QRect, Qt, QThread, Signal
from PySide6.QtGui import QAction, QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMenu,
    QVBoxLayout,
    QWidget,
)

import intraday
from chart import IntradayChartWindow, SparklineWidget
from config import NUM_FONT, UI_FONT, Config
from datasource import Quote, QuoteService, market_state
from panel import BORDER, GRIP, MIN_PANEL, SHADOW, draw_background, draw_grip, draw_hover_outline, draw_shadow, panel_rect
from settings_dialog import SettingsDialog
from store import Store

WARN_COLOR = "#EFAA17"

_ORPHAN_THREADS: list = []       # 退出时还没结束的取数线程，留着防止被提前析构


def _rgba(color: QColor, alpha: float) -> str:
    """拼出样式表能认的 rgba 颜色，alpha 取 0~1（0 就是完全透明）。"""
    level = max(0, min(255, int(round(float(alpha) * 255))))
    return f"rgba({color.red()}, {color.green()}, {color.blue()}, {level})"


# 缩放方向 -> 鼠标指针
CURSOR_FOR = {
    "l": Qt.SizeHorCursor,
    "r": Qt.SizeHorCursor,
    "t": Qt.SizeVerCursor,
    "b": Qt.SizeVerCursor,
    "lt": Qt.SizeFDiagCursor,
    "rb": Qt.SizeFDiagCursor,
    "br": Qt.SizeFDiagCursor,
    "rt": Qt.SizeBDiagCursor,
    "lb": Qt.SizeBDiagCursor,
}


class StockRow(QWidget):
    """一行自选股：名称 + 最新价 + 涨跌幅。"""

    def __init__(self, code: str, parent=None):
        super().__init__(parent)
        self.code = code
        self._quote: Quote | None = None
        self._full_name = code
        self._font_color = QColor("#FFFFFF")
        self._up_color = QColor("#F05A4F")
        self._down_color = QColor("#2BC48A")
        self._flat_color = QColor("#FFFFFF")
        self._alpha = 1.0

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.name_label = QLabel(code)
        self.sparkline = SparklineWidget(code, self)
        self.price_label = QLabel("--")
        self.pct_label = QLabel("--")
        self.price_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.pct_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.name_label.setToolTip(code)
        # 缩略图与标签一样不接鼠标事件：命中判断统一由主窗口做，拖动/缩放才不受影响
        for label, stretch in ((self.name_label, 5), (self.sparkline, 0), (self.price_label, 3), (self.pct_label, 3)):
            label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            layout.addWidget(label, stretch)

    def apply_style(
        self,
        size: int,
        font_color: str,
        up_color: str,
        down_color: str,
        flat_color: str = "#FFFFFF",
        alpha: float = 1.0,
        show_sparkline: bool = True,
    ) -> None:
        self.name_label.setFont(QFont(UI_FONT, size))
        for label in (self.price_label, self.pct_label):
            label.setFont(QFont(NUM_FONT, size))
        self._font_color = QColor(font_color)
        self._up_color = QColor(up_color)
        self._down_color = QColor(down_color)
        self._flat_color = QColor(flat_color)
        self._alpha = alpha
        self.sparkline.setVisible(show_sparkline)
        self.sparkline.apply_style({
            "up_color": up_color,
            "down_color": down_color,
            "flat_color": flat_color,
            "font_color": font_color,
            "font_opacity": alpha,
        })
        self._apply_name()      # 字号变了，省略位置也要跟着变
        self._recolor()

    def set_series(self, series) -> None:
        self.sparkline.set_series(series)

    def set_quote(self, quote: Quote, fallback_name: str = "") -> None:
        self._quote = quote
        self._full_name = quote.name or fallback_name or quote.code
        self._apply_name()
        if quote.missing:
            # 数据源里没有这只：常见于把 ETF/基金当成股票代码填进来了
            self.price_label.setText("--")
            self.pct_label.setText("无数据")
            self.setToolTip(f"{self.code}：数据源里没有这只代码，ETF/基金请确认代码是否正确")
        elif quote.suspended:
            self.price_label.setText("--")
            self.pct_label.setText("停牌")
            self.setToolTip(self.code)
        else:
            self.price_label.setText(f"{quote.price:.{quote.decimals}f}")
            sign = "+" if quote.change_pct > 0 else ""
            self.pct_label.setText(f"{sign}{quote.change_pct:.2f}%")
            self.setToolTip(f"{self._full_name}  {self.code}")
        self._recolor()

    def _apply_name(self) -> None:
        """名称列窄，长名字（比如「科创50ETF基金」）按宽度省略，悬停看全名。"""
        name = getattr(self, "_full_name", self.code)
        self.name_label.setText(name)
        width = self.name_label.width()
        if width > 10:
            self.name_label.setText(
                self.name_label.fontMetrics().elidedText(name, Qt.ElideRight, width)
            )

    def resizeEvent(self, event) -> None:
        self._apply_name()
        super().resizeEvent(event)

    def _recolor(self) -> None:
        # 价格与涨跌幅：红涨、绿跌、白平（停牌/无数据这些没有方向的也用平盘色）
        price_color = self._flat_color
        if self._quote is not None:
            if self._quote.direction > 0:
                price_color = self._up_color
            elif self._quote.direction < 0:
                price_color = self._down_color
        for label in (self.price_label, self.pct_label):
            label.setStyleSheet(f"color: {_rgba(price_color, self._alpha)};")
        self.name_label.setStyleSheet(f"color: {_rgba(self._font_color, self._alpha)};")


class QuoteFetcher(QThread):
    """后台取数线程，网络请求不阻塞界面。

    休盘（午休、收盘后、周末、节假日）价格不变，所以每个休盘时段只取一次数据，
    之后就不再发请求，只等开盘；右键「立即刷新」或改设置可以强制再取一次。
    """

    started_fetch = Signal()         # 开始取数，用来点亮"取数中…"
    ready = Signal(list, str, str)   # quotes, source, warning
    state_changed = Signal(str)      # 交易中 / 午间休盘 / 已收盘 / 未开盘 / 周末休市 …
    minutes_ready = Signal(dict)     # code -> IntradaySeries

    CLOSED_POLL = 60.0               # 休盘时每分钟看一眼是否开盘（不发请求）
    CLOSED_RETRY = 3                 # 休盘那次取数失败时，最多再试几次
    MINUTE_INTERVAL = 60.0           # 分钟线刷新间隔（秒）

    def __init__(self, service: QuoteService, store=None, parent=None):
        super().__init__(parent)
        self._service = service
        self._store = store
        self._codes: list[str] = []
        self._interval = 10.0
        self._closed_done = False        # 本个休盘时段是否已经成功取到一次
        self._closed_fails = 0           # 本个休盘时段连续失败次数
        self._force = False              # 手动刷新：休盘也要立刻取一次
        self._state = ""
        self._minute_since = 0.0         # 上次拉分钟线的时刻（monotonic）
        self._closed_state = ""          # 上次见到的休盘状态，用来判断"刚切到休盘"
        self.minute_warning = ""
        self.store_failed = ""
        self._wake = threading.Event()
        self._stop = threading.Event()

    def configure(self, codes: list[str], interval: float) -> None:
        self._codes = list(codes)
        self._interval = max(2.0, float(interval))
        self.wake()

    def wake(self) -> None:
        """手动刷新 / 改设置：即使休盘也立刻取一次。"""
        self._force = True
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

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


class StockWidget(QWidget):
    CLICK_SLOP = 4      # 位移小于这个像素数才算"点击"而不是拖窗口

    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        self._service = QuoteService(
            config["primary_source"], config["tdx_host"], config["names"]
        )
        self._names: dict[str, str] = dict(config["names"])   # pytdx 不返回名称，缓存起来
        self._store, store_error = self._open_store()
        self._series: dict = {}
        self._charts: dict = {}
        self._press_pos = None
        self._rows: dict[str, StockRow] = {}
        self._drag_offset = None
        self._resize_region = ""
        self._resize_origin = None
        self._resize_geom = None
        self._hover_region = ""
        self._last_source = ""
        self._last_warning = store_error
        self._last_update = ""
        self._market = ""

        self.setWindowTitle("自选股监控")
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMouseTracking(True)          # 没按键时也要能感知鼠标、切换指针
        self.setMinimumSize(MIN_PANEL[0] + 2 * SHADOW, MIN_PANEL[1] + 2 * SHADOW)
        self.setWindowFlags(self._window_flags(bool(config["always_on_top"])))

        self._build_ui()
        self._rebuild_rows()
        self.apply_style()
        self._restore_geometry()

        self._fetcher = QuoteFetcher(self._service, self._store, self)
        self._fetcher.started_fetch.connect(self.on_fetch_start)
        self._fetcher.ready.connect(self.on_quotes)
        self._fetcher.state_changed.connect(self.on_market_state)
        self._fetcher.minutes_ready.connect(self.on_minutes)
        self._fetcher.configure(config["codes"], config["refresh_seconds"])
        self._fetcher.start()

    @staticmethod
    def _window_flags(on_top: bool):
        """无边框 + 半透明，才像桌面小组件；否则会带一条系统标题栏。"""
        flags = Qt.FramelessWindowHint
        if on_top:
            flags |= Qt.WindowStaysOnTopHint
        return flags

    # ---------- 界面搭建 ----------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(SHADOW + 14, SHADOW + 12, SHADOW + 14, SHADOW + 8)
        root.setSpacing(6)

        self.rows_layout = QVBoxLayout()
        self.rows_layout.setSpacing(4)
        root.addLayout(self.rows_layout)
        root.addStretch(1)

        self.footer_label = QLabel("正在取数…")
        self.footer_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        root.addWidget(self.footer_label)

    def _rebuild_rows(self) -> None:
        while self.rows_layout.count():
            item = self.rows_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._rows = {}
        for code in self.config["codes"]:
            row = StockRow(code)
            row.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            # 新建的行立刻套一次样式与已有序列，免得先闪一下默认样式
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
            self._rows[code] = row
            self.rows_layout.addWidget(row)

    # ---------- 样式与几何 ----------

    @property
    def _text_alpha(self) -> float:
        """字体不透明度，0~1。"""
        return max(0.0, min(1.0, float(self.config["font_opacity"])))

    def _style_label(
        self, label: QLabel, size: int, color: str, *, bold=False, lighten=0, alpha=None
    ) -> None:
        font = QFont(UI_FONT, size)
        font.setBold(bold)
        label.setFont(font)
        qcolor = QColor(color)
        if lighten:
            qcolor = qcolor.lighter(lighten)
        label.setStyleSheet(f"color: {_rgba(qcolor, self._text_alpha if alpha is None else alpha)};")

    def apply_style(self) -> None:
        cfg = self.config
        size = int(cfg["font_size"])
        alpha = self._text_alpha
        for row in self._rows.values():
            row.apply_style(
                size,
                cfg["font_color"],
                cfg["up_color"],
                cfg["down_color"],
                cfg["flat_color"],
                alpha,
                bool(cfg["show_sparkline"]),
            )
        self._style_label(self.footer_label, max(8, size - 4), cfg["font_color"], lighten=150)
        self._render_footer()
        self.update()

    def _restore_geometry(self) -> None:
        screen = QApplication.primaryScreen().availableGeometry()
        width = max(self.minimumWidth(), int(self.config["width"]))
        height = max(self.minimumHeight(), int(self.config["height"]))
        self.resize(width, height)

        x, y = self.config["pos_x"], self.config["pos_y"]
        if x is None or y is None:
            self.move(screen.right() - width - 40, screen.top() + 80)
            return
        x = min(max(int(x), screen.left() - width + 60), screen.right() - 60)
        y = min(max(int(y), screen.top()), screen.bottom() - 40)
        self.move(x, y)

    def _remember_geometry(self) -> None:
        self.config.update(
            pos_x=self.x(), pos_y=self.y(), width=self.width(), height=self.height()
        )
        self.config.save()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        panel = panel_rect(self.width(), self.height())
        opacity = max(0.0, min(1.0, float(self.config["opacity"])))
        if opacity > 0.0:
            draw_shadow(painter, panel, opacity)
            draw_background(painter, panel, self.config["bg_color"], opacity)
            draw_grip(painter, panel, opacity)
            painter.end()
            return
        # 面板全透明时，鼠标贴到边缘给个淡淡的方向线，否则根本找不到缩放位置
        if self._hover_region and self._hover_region != "move":
            draw_hover_outline(painter, panel, self._hover_region)
        painter.end()

    # ---------- 数据 ----------

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
            row = self._rows.get(code)
            if row is not None:
                row.set_series(series)
            window = self._charts.get(code)
            if window is not None:
                window.set_series(series, self._service.source_label, self._last_update, self._last_warning)

    def on_fetch_start(self) -> None:
        """取数可能要好几秒（新浪全市场要翻几十页），先让界面有个动静。"""
        size = max(8, int(self.config["font_size"]) - 4)
        self._style_label(self.footer_label, size, self.config["font_color"], lighten=150)
        self._set_footer("取数中…")

    def on_quotes(self, quotes: list, source: str, warning: str) -> None:
        self._last_source = source
        # 落盘失败也要让人看见：它和取数告警共用底部那一行
        self._last_warning = warning or self._fetcher.store_failed
        self._last_update = datetime.now().strftime("%H:%M:%S")

        if not quotes:
            self._render_footer()
            return

        for quote in quotes:
            if quote.name:
                self._names[quote.code] = quote.name
        self.config["names"] = dict(self._names)     # 名字缓存进内存，关窗口时落盘
        if self._service.tdx_host:
            self.config["tdx_host"] = self._service.tdx_host
        for quote in quotes:
            row = self._rows.get(quote.code)
            if row is not None:
                row.set_quote(quote, self._names.get(quote.code, ""))

        self._render_footer()

    @staticmethod
    def _short_reason(warning: str, limit: int = 30) -> str:
        """报错可能很长，取数面板又窄，截一段显示，完整内容放悬浮提示。"""
        text = " ".join(str(warning).split())
        return text if len(text) <= limit else text[:limit] + "…"

    def _set_footer(self, text: str, tooltip: str = "") -> None:
        """按标签宽度做省略，免得文字被硬生生截断。"""
        width = self.footer_label.width()
        if width > 20 and self.footer_label.fontMetrics().horizontalAdvance(text) > width:
            text = self.footer_label.fontMetrics().elidedText(text, Qt.ElideRight, width)
        self.footer_label.setText(text)
        self.footer_label.setToolTip(tooltip)

    def _render_footer(self) -> None:
        """画底部那行：取数失败 > 不显示数据源 > 通道 + 更新时间。"""
        size = max(8, int(self.config["font_size"]) - 4)
        if not self._last_source:
            self._style_label(self.footer_label, size, WARN_COLOR)
            reason = self._short_reason(self._last_warning, 34)
            self._set_footer(f"取数失败：{reason}" if reason else "取数失败", self._last_warning)
            return

        if not self.config["show_source"]:
            self._set_footer("")
            return

        if self._last_warning:
            # 走了备用通道：用告警色把原因说清楚
            self._style_label(self.footer_label, size, WARN_COLOR)
        else:
            self._style_label(self.footer_label, size, self.config["font_color"], lighten=150)

        if self._market and self._market != "交易中":
            # 休盘：价格不变，取过一次就不再重复取，所以这里标明状态，免得看着像卡住
            shown = f"{self._market} · {self._last_update} 数据"
            if self._last_warning:
                shown += f"（{self._short_reason(self._last_warning)}）"
            self._set_footer(
                shown, "休盘期间不再重复取数，开盘后自动恢复；右键「立即刷新」可手动取一次。"
            )
            return

        shown = f"{self._service.source_label} · {self._last_update} 更新"
        if self._last_warning:
            shown += f"（{self._short_reason(self._last_warning)}）"
        self._set_footer(shown, self._last_warning)

    def on_market_state(self, state: str) -> None:
        """交易中 / 午间休盘 / 已收盘 / 未开盘 / 周末休市 / 节假日休市。"""
        self._market = state
        self._render_footer()

    # ---------- 交互：拖动 / 缩放 / 右键 ----------

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
        window.activateWindow()      # 拿到键盘焦点：Esc 能关窗，点别处也会失焦自动收起来

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

    def _sparkline_code_at(self, pos) -> str:
        """落点在某行的缩略图上就返回那只代码（缩略图自己不接收鼠标事件）。"""
        for code, row in self._rows.items():
            if not row.sparkline.isVisible():
                continue
            if row.sparkline.geometry().translated(row.pos()).contains(pos):
                return code
        return ""

    def _is_click(self, event) -> bool:
        """按下到松开几乎没动、且没在缩放：算一次点击，而不是拖窗口。"""
        if self._press_pos is None or self._resize_geom is not None or self._drag_offset is None:
            return False
        return (event.globalPosition().toPoint() - self._press_pos).manhattanLength() <= self.CLICK_SLOP

    def _hit_region(self, pos) -> str:
        """判断鼠标落在哪里：四边/四角返回方向串（如 'lt'），右下角把手返回 'br'，其余返回 'move'。"""
        panel = panel_rect(self.width(), self.height()).toRect()
        if 0 <= panel.right() - pos.x() <= GRIP and 0 <= panel.bottom() - pos.y() <= GRIP:
            return "br"
        region = ""
        if pos.x() - panel.left() <= BORDER:
            region += "l"
        if panel.right() - pos.x() <= BORDER:
            region += "r"
        if pos.y() - panel.top() <= BORDER:
            region += "t"
        if panel.bottom() - pos.y() <= BORDER:
            region += "b"
        return region or "move"

    def _in_grip(self, pos) -> bool:
        return self._hit_region(pos) == "br"

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)
        region = self._hit_region(event.position().toPoint())
        if region == "move":
            self._drag_offset = event.globalPosition().toPoint() - self.pos()
        else:
            self._resize_region = region
            self._resize_origin = event.globalPosition().toPoint()
            self._resize_geom = QRect(self.geometry())
        self._press_pos = event.globalPosition().toPoint()
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if not (event.buttons() & Qt.LeftButton):
            pos = event.position().toPoint()
            region = self._hit_region(pos)
            # 缩略图上给小手：暗示"这里可以点开分时图"
            self.setCursor(Qt.PointingHandCursor if self._sparkline_code_at(pos) else CURSOR_FOR.get(region, Qt.OpenHandCursor))
            if region != self._hover_region:
                self._hover_region = region
                if float(self.config["opacity"]) <= 0.0:
                    self.update()      # 全透明时靠这条虚线提示能缩放的边界
            return super().mouseMoveEvent(event)
        if self._resize_geom is not None:
            self._apply_resize(event.globalPosition().toPoint())
        elif self._drag_offset is not None:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
        event.accept()

    def _apply_resize(self, global_pos) -> None:
        delta = global_pos - self._resize_origin
        region = self._resize_region
        rect = QRect(self._resize_geom)
        if "l" in region:
            rect.setLeft(rect.left() + delta.x())
        if "r" in region:
            rect.setRight(rect.right() + delta.x())
        if "t" in region:
            rect.setTop(rect.top() + delta.y())
        if "b" in region:
            rect.setBottom(rect.bottom() + delta.y())

        if rect.width() < self.minimumWidth():      # 顶到最小尺寸时钉住被拖的那条边
            if "l" in region:
                rect.setLeft(rect.right() - self.minimumWidth() + 1)
            else:
                rect.setRight(rect.left() + self.minimumWidth() - 1)
        if rect.height() < self.minimumHeight():
            if "t" in region:
                rect.setTop(rect.bottom() - self.minimumHeight() + 1)
            else:
                rect.setBottom(rect.top() + self.minimumHeight() - 1)
        self.setGeometry(rect)

    def mouseReleaseEvent(self, event) -> None:
        if self._is_click(event):
            code = self._sparkline_code_at(event.position().toPoint())
            if code:
                self._open_chart(code)
        self._press_pos = None
        if self._drag_offset is not None or self._resize_geom is not None:
            self._remember_geometry()
        self._drag_offset = None
        self._resize_region = ""
        self._resize_origin = None
        self._resize_geom = None
        event.accept()

    def leaveEvent(self, event) -> None:
        self.unsetCursor()
        if self._hover_region:
            self._hover_region = ""
            self.update()
        super().leaveEvent(event)

    def contextMenuEvent(self, event) -> None:
        menu = QMenu(self)
        menu.addAction("立即刷新", self._fetcher.wake)
        menu.addAction("设置…", self.open_settings)

        chart_menu = menu.addMenu("分时图")
        for code in self.config["codes"]:
            name = self._names.get(code, code)
            chart_menu.addAction(f"{name}  {code}", lambda _=False, c=code: self._open_chart(c))

        source_menu = menu.addMenu("数据源")
        for text, value in (("自动切换（主通道失败走备用）", None), ("仅用 akshare", "akshare"), ("仅用 pytdx", "pytdx")):
            action = QAction(text, self)
            action.setCheckable(True)
            action.setChecked(self._service.override == value)
            action.triggered.connect(lambda _=False, v=value: self._set_override(v))
            source_menu.addAction(action)

        menu.addSeparator()
        on_top = QAction("窗口置顶", self)
        on_top.setCheckable(True)
        on_top.setChecked(bool(self.config["always_on_top"]))
        on_top.triggered.connect(self._toggle_on_top)
        menu.addAction(on_top)
        menu.addSeparator()
        menu.addAction("退出", self.close)
        menu.exec(event.globalPos())

    def _set_override(self, value) -> None:
        self._service.set_override(value)
        self._last_warning = ""
        self._fetcher.wake()

    def _toggle_on_top(self, checked: bool) -> None:
        self.config.save()
        self._apply_on_top(bool(checked))

    def _apply_on_top(self, on_top: bool) -> None:
        """改窗口标志后必须重新 show 才生效，位置要自己保住。"""
        position = self.pos()
        visible = self.isVisible()
        self.setWindowFlags(self._window_flags(on_top))
        if visible:
            self.show()
        self.move(position)

    def open_settings(self) -> None:
        dialog = SettingsDialog(self.config, self)
        if dialog.exec() != QDialog.Accepted:
            return
        self.config.update(**dialog.values())
        self.config.save()
        self._service.set_primary(self.config["primary_source"])
        self._rebuild_rows()
        self.apply_style()
        self.resize(
            max(self.minimumWidth(), int(self.config["width"])),
            max(self.minimumHeight(), int(self.config["height"])),
        )
        self._fetcher.configure(self.config["codes"], self.config["refresh_seconds"])
        on_top = bool(self.config["always_on_top"])
        if on_top != bool(self.windowFlags() & Qt.WindowStaysOnTopHint):
            self._apply_on_top(on_top)

    def closeEvent(self, event) -> None:
        self.config["names"] = dict(self._names)
        if self._service.tdx_host:
            self.config["tdx_host"] = self._service.tdx_host
        for window in list(self._charts.values()):
            window.close()
        self._remember_geometry()
        self._service.close()
        self._fetcher.stop()
        if not self._fetcher.wait(5000):
            # 取数正卡在网络请求里：不拖着退出流程，把线程挂到模块级避免被提前销毁
            _ORPHAN_THREADS.append(self._fetcher)
            self._fetcher.setParent(None)
        if self._store is not None:
            self._store.close()      # 必须等取数线程停了再关，否则它的连接还在写
        super().closeEvent(event)


def _ensure_streams() -> None:
    """打包成不带控制台的 exe 后，sys.stdout/stderr 是 None，
    第三方库里任何一次 print/warn 都会抛异常，这里兜一层。"""
    for name in ("stdout", "stderr"):
        if getattr(sys, name, None) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))


def main() -> int:
    _ensure_streams()
    app = QApplication(sys.argv)
    app.setApplicationName("自选股监控")
    widget = StockWidget(Config.load())
    widget.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())