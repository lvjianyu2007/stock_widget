"""设置面板：自选股、刷新频率、字号与颜色、背景与透明度、窗口尺寸，并实时预览。"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from config import DEFAULTS, NUM_FONT, UI_FONT, normalize_codes


class ColorButton(QPushButton):
    """点击弹出调色板，按钮本身显示当前颜色。"""

    colorChanged = Signal()

    def __init__(self, color: str, parent=None):
        super().__init__(parent)
        self.setFixedWidth(104)
        self._color = QColor("#FFFFFF")
        self.setColor(color)

    def setColor(self, color) -> None:
        qcolor = QColor(color)
        if not qcolor.isValid():
            qcolor = QColor("#FFFFFF")
        self._color = qcolor
        self.setText(qcolor.name().upper())
        text_color = "#111111" if qcolor.lightness() > 140 else "#FFFFFF"
        self.setStyleSheet(
            f"background-color: {qcolor.name()}; color: {text_color};"
            "border: 1px solid rgba(128, 128, 128, 0.5); border-radius: 4px; padding: 4px;"
        )
        self.colorChanged.emit()

    def color(self) -> QColor:
        return self._color

    def mousePressEvent(self, event) -> None:
        picked = QColorDialog.getColor(self._color, self, "选择颜色")
        if picked.isValid():
            self.setColor(picked)
        event.accept()


class PreviewPanel(QWidget):
    """调样式时的实时预览：背景透明度、字号、涨/跌/平颜色一改就能看见。"""

    SAMPLES = (
        ("600519", "贵州茅台", "+1.23%", 1),
        ("000001", "平安银行", "-0.45%", -1),
        ("601668", "中国建筑", "0.00%", 0),
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(112)
        self._style = dict(DEFAULTS)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)
        self._labels = []
        for _code, _name, change, direction in self.SAMPLES:
            name_label = QLabel()
            pct_label = QLabel(change)
            pct_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            row = QHBoxLayout()
            row.setSpacing(8)
            row.addWidget(name_label, 1)
            row.addWidget(pct_label)
            layout.addLayout(row)
            self._labels.append((name_label, pct_label, direction))
        layout.addStretch(1)
        self.refresh(dict(DEFAULTS))

    def refresh(self, values: dict) -> None:
        self._style = dict(values)
        size = int(values["font_size"])
        alpha = max(0.0, min(1.0, float(values["font_opacity"])))

        def css(color: str) -> str:
            qcolor = QColor(color)
            return f"rgba({qcolor.red()}, {qcolor.green()}, {qcolor.blue()}, {int(round(alpha * 255))})"

        for (name_label, pct_label, _), (code, name, _change, direction) in zip(
            self._labels, self.SAMPLES
        ):
            name_label.setText(f"{name}  {code}")
            name_label.setFont(QFont(UI_FONT, size))
            name_label.setStyleSheet(f"color: {css(values['font_color'])};")
            pct_label.setFont(QFont(NUM_FONT, size))
            if direction > 0:
                pct_color = values["up_color"]
            elif direction < 0:
                pct_color = values["down_color"]
            else:
                pct_color = values["flat_color"]
            pct_label.setStyleSheet(f"color: {css(pct_color)};")
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        # 虚线框始终画，方便看清"0% 背景"时小组件的边界
        painter.setPen(QPen(QColor(128, 128, 128, 110), 1, Qt.DashLine))
        painter.drawRoundedRect(
            QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0), 10, 10
        )
        opacity = max(0.0, min(1.0, float(self._style["opacity"])))
        if opacity > 0.0:
            background = QColor(self._style["bg_color"])
            background.setAlphaF(opacity)
            painter.setBrush(QBrush(background))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(
                QRectF(1.0, 1.0, self.width() - 2.0, self.height() - 2.0), 10, 10
            )
        painter.end()


def _row(widget: QWidget) -> QWidget:
    """把控件包一层，方便塞进 QFormLayout。"""
    holder = QWidget()
    layout = QHBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)
    layout.addWidget(widget)
    layout.addStretch(1)
    return holder


def _slider_row(slider: QSlider, value_label: QLabel) -> QWidget:
    """滑杆 + 百分数，占满整行。"""
    holder = QWidget()
    layout = QHBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)
    layout.addWidget(slider, 1)
    layout.addWidget(value_label)
    return holder


class SettingsDialog(QDialog):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.setWindowTitle("小组件设置")
        self.setMinimumWidth(400)

        self.codes_edit = QPlainTextEdit("\n".join(config["codes"]))
        self.codes_edit.setPlaceholderText(
            "每行一个代码：股票/基金填 6 位数字（600519），\n指数加 sh/sz 前缀（sh000001 上证指数、sz399001 深证成指）"
        )
        self.codes_edit.setFixedHeight(96)

        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(2, 600)
        self.interval_spin.setSuffix(" 秒")
        self.interval_spin.setValue(int(config["refresh_seconds"]))

        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(8, 48)
        self.font_size_spin.setSuffix(" px")
        self.font_size_spin.setValue(int(config["font_size"]))

        self.font_color_btn = ColorButton(config["font_color"])
        self.up_color_btn = ColorButton(config["up_color"])
        self.down_color_btn = ColorButton(config["down_color"])
        self.flat_color_btn = ColorButton(config["flat_color"])
        self.bg_color_btn = ColorButton(config["bg_color"])

        self.opacity_slider = QSlider(Qt.Horizontal)
        self.opacity_slider.setRange(0, 100)       # 0% = 只剩文字飘在桌面上
        self.opacity_slider.setValue(int(round(float(config["opacity"]) * 100)))
        self.opacity_slider.setToolTip("0% 时面板完全透明，只留下文字")
        self.opacity_label = QLabel(f"{self.opacity_slider.value()}%")
        self.opacity_label.setFixedWidth(40)
        self.opacity_slider.valueChanged.connect(lambda v: self.opacity_label.setText(f"{v}%"))

        self.font_opacity_slider = QSlider(Qt.Horizontal)
        self.font_opacity_slider.setRange(0, 100)
        self.font_opacity_slider.setValue(int(round(float(config["font_opacity"]) * 100)))
        self.font_opacity_slider.setToolTip("0% 时文字完全透明（面板背景仍然生效）")
        self.font_opacity_label = QLabel(f"{self.font_opacity_slider.value()}%")
        self.font_opacity_label.setFixedWidth(40)
        self.font_opacity_slider.valueChanged.connect(
            lambda v: self.font_opacity_label.setText(f"{v}%")
        )

        self.width_spin = QSpinBox()
        self.width_spin.setRange(180, 2400)
        self.width_spin.setSuffix(" px")
        self.width_spin.setValue(int(config["width"]))

        self.height_spin = QSpinBox()
        self.height_spin.setRange(96, 2400)
        self.height_spin.setSuffix(" px")
        self.height_spin.setValue(int(config["height"]))

        self.primary_box = QComboBox()
        self.primary_box.addItem("akshare（主）", "akshare")
        self.primary_box.addItem("pytdx（主）", "pytdx")
        self.primary_box.setCurrentIndex(max(0, self.primary_box.findData(config["primary_source"])))

        self.on_top_check = QCheckBox("窗口始终置顶")
        self.on_top_check.setChecked(bool(config["always_on_top"]))
        self.show_source_check = QCheckBox("显示数据源状态")
        self.show_source_check.setChecked(bool(config["show_source"]))
        self.sparkline_check = QCheckBox("显示行内分时缩略图")
        self.sparkline_check.setChecked(bool(config["show_sparkline"]))
        self.sparkline_check.setToolTip("关掉后列表里不再显示迷你分时图，分时图仍可右键菜单打开")

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setSpacing(10)
        form.addRow("自选股代码", self.codes_edit)
        form.addRow("刷新间隔", _row(self.interval_spin))
        form.addRow("主数据源", _row(self.primary_box))
        form.addRow("字号", _row(self.font_size_spin))
        form.addRow("文字颜色", _row(self.font_color_btn))
        form.addRow("上涨颜色", _row(self.up_color_btn))
        form.addRow("下跌颜色", _row(self.down_color_btn))
        form.addRow("平盘颜色", _row(self.flat_color_btn))
        form.addRow("背景颜色", _row(self.bg_color_btn))

        form.addRow("背景不透明度", _slider_row(self.opacity_slider, self.opacity_label))
        form.addRow("字体不透明度", _slider_row(self.font_opacity_slider, self.font_opacity_label))

        size_row = QWidget()
        size_layout = QHBoxLayout(size_row)
        size_layout.setContentsMargins(0, 0, 0, 0)
        size_layout.setSpacing(8)
        size_layout.addWidget(QLabel("宽"))
        size_layout.addWidget(self.width_spin)
        size_layout.addWidget(QLabel("高"))
        size_layout.addWidget(self.height_spin)
        size_layout.addStretch(1)
        form.addRow("窗口尺寸", size_row)

        form.addRow("", self.on_top_check)
        form.addRow("", self.show_source_check)
        form.addRow("", self.sparkline_check)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel | QDialogButtonBox.RestoreDefaults
        )
        buttons.button(QDialogButtonBox.Ok).setText("确定")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.button(QDialogButtonBox.RestoreDefaults).setText("恢复默认")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.RestoreDefaults).clicked.connect(self._restore_defaults)

        self.preview = PreviewPanel()
        preview_title = QLabel("效果预览")
        preview_title.setStyleSheet("font-weight: bold;")

        layout = QVBoxLayout(self)
        layout.addWidget(preview_title)
        layout.addWidget(self.preview)
        layout.addLayout(form)
        layout.addWidget(buttons)
        self._connect_preview()

    def _connect_preview(self) -> None:
        self.font_size_spin.valueChanged.connect(self._sync_preview)
        self.opacity_slider.valueChanged.connect(self._sync_preview)
        self.font_opacity_slider.valueChanged.connect(self._sync_preview)
        for button in (
            self.font_color_btn,
            self.up_color_btn,
            self.down_color_btn,
            self.flat_color_btn,
            self.bg_color_btn,
        ):
            button.colorChanged.connect(self._sync_preview)
        self._sync_preview()

    def _sync_preview(self, *_args) -> None:
        self.preview.refresh(self.values())

    def accept(self) -> None:
        if not normalize_codes(self.codes_edit.toPlainText()):
            QMessageBox.warning(
                self, "自选股不能为空", "请至少填一个代码：股票/基金 6 位数字（600519），指数带 sh/sz 前缀（sh000001）。"
            )
            self.codes_edit.setFocus()
            return
        super().accept()

    def _restore_defaults(self) -> None:
        self.codes_edit.setPlainText("\n".join(DEFAULTS["codes"]))
        self.interval_spin.setValue(int(DEFAULTS["refresh_seconds"]))
        self.font_size_spin.setValue(int(DEFAULTS["font_size"]))
        self.font_color_btn.setColor(DEFAULTS["font_color"])
        self.up_color_btn.setColor(DEFAULTS["up_color"])
        self.down_color_btn.setColor(DEFAULTS["down_color"])
        self.flat_color_btn.setColor(DEFAULTS["flat_color"])
        self.bg_color_btn.setColor(DEFAULTS["bg_color"])
        self.opacity_slider.setValue(int(round(float(DEFAULTS["opacity"]) * 100)))
        self.font_opacity_slider.setValue(int(round(float(DEFAULTS["font_opacity"]) * 100)))
        self.width_spin.setValue(int(DEFAULTS["width"]))
        self.height_spin.setValue(int(DEFAULTS["height"]))
        self.primary_box.setCurrentIndex(max(0, self.primary_box.findData(DEFAULTS["primary_source"])))
        self.on_top_check.setChecked(bool(DEFAULTS["always_on_top"]))
        self.show_source_check.setChecked(bool(DEFAULTS["show_source"]))
        self.sparkline_check.setChecked(bool(DEFAULTS["show_sparkline"]))

    def values(self) -> dict:
        return {
            "codes": normalize_codes(self.codes_edit.toPlainText()),
            "refresh_seconds": self.interval_spin.value(),
            "font_size": self.font_size_spin.value(),
            "font_color": self.font_color_btn.color().name(),
            "up_color": self.up_color_btn.color().name(),
            "down_color": self.down_color_btn.color().name(),
            "flat_color": self.flat_color_btn.color().name(),
            "bg_color": self.bg_color_btn.color().name(),
            "opacity": self.opacity_slider.value() / 100.0,
            "font_opacity": self.font_opacity_slider.value() / 100.0,
            "width": self.width_spin.value(),
            "height": self.height_spin.value(),
            "always_on_top": self.on_top_check.isChecked(),
            "show_source": self.show_source_check.isChecked(),
            "show_sparkline": self.sparkline_check.isChecked(),
            "primary_source": self.primary_box.currentData(),
        }