## 任务 4：抽出 `panel.py` 并让主窗口复用

**文件：**
- 创建：`panel.py`
- 修改：`main.py`（常量声明约 32-38 行；`paintEvent` 约 375-418 行；`_panel_rect` 约 366-373 行；`_hit_region` 约 505-519 行）

> 这是**纯搬移**：绘制结果与交互行为必须完全不变。改动完成后按步骤 4 目视回归。

- [ ] **步骤 1：创建 `panel.py`**

写入 `panel.py`：

```python
"""面板外观：几何常量与圆角/投影/把手绘制，主窗口与分时图窗口共用。"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPen

SHADOW = 14                      # 面板四周留给投影的空白
PANEL_RADIUS = 12                # 面板圆角
BORDER = 6                       # 四条边上的缩放热区
GRIP = 18                        # 右下角缩放热区边长
MIN_PANEL = (180, 96)            # 面板最小尺寸（不含投影）


def panel_rect(width: int, height: int) -> QRectF:
    """面板区域，四周留出 SHADOW 做投影。"""
    return QRectF(SHADOW, SHADOW, max(1.0, width - 2 * SHADOW), max(1.0, height - 2 * SHADOW))


def draw_shadow(painter: QPainter, panel: QRectF, opacity: float) -> None:
    """由外向内叠若干层圆角矩形，越靠里越深，观感接近柔和阴影。"""
    painter.setPen(Qt.NoPen)
    for step in range(SHADOW, 0, -1):
        ratio = 1.0 - step / SHADOW
        alpha = int(36 * ratio * ratio * opacity)
        if alpha <= 0:
            continue
        painter.setBrush(QColor(0, 0, 0, alpha))
        radius = PANEL_RADIUS + step * 0.8
        painter.drawRoundedRect(panel.adjusted(-step, -step + 3, step, step + 3), radius, radius)


def draw_background(painter: QPainter, panel: QRectF, bg_color: str, opacity: float) -> None:
    """圆角背景 + 一圈很淡的高光描边。"""
    background = QColor(bg_color)
    background.setAlphaF(opacity)
    painter.setBrush(QBrush(background))
    painter.setPen(QPen(QColor(255, 255, 255, int(46 * opacity)), 1))
    painter.drawRoundedRect(panel, PANEL_RADIUS, PANEL_RADIUS)


def draw_grip(painter: QPainter, panel: QRectF, opacity: float) -> None:
    """右下角的缩放把手提示（两道斜线）。"""
    right, bottom = int(panel.right()), int(panel.bottom())
    painter.setPen(QPen(QColor(255, 255, 255, int(95 * opacity)), 1.5))
    for offset in (6, 10):
        painter.drawLine(right - offset, bottom - 6, right - 6, bottom - offset)


def draw_hover_outline(painter: QPainter, panel: QRectF, region: str) -> None:
    """面板全透明时的边缘虚线提示，否则鼠标根本找不到缩放位置。"""
    painter.setBrush(Qt.NoBrush)
    painter.setPen(QPen(QColor(255, 255, 255, 110), 1, Qt.DashLine))
    painter.drawRoundedRect(panel, PANEL_RADIUS, PANEL_RADIUS)
    if region == "br":
        right, bottom = int(panel.right()), int(panel.bottom())
        painter.setPen(QPen(QColor(255, 255, 255, 150), 1.5))
        for offset in (6, 10):
            painter.drawLine(right - offset, bottom - 6, right - 6, bottom - offset)
```

- [ ] **步骤 2：`main.py` 改为复用 `panel.py`**

2a. 删除 `main.py` 里这 6 行常量声明（`WARN_COLOR` 保留）：

```python
SHADOW = 14                      # 面板四周留给投影的空白
PANEL_RADIUS = 12                # 面板圆角
BORDER = 6                       # 四条边上的缩放热区
GRIP = 18                        # 右下角缩放热区边长
MIN_PANEL = (180, 96)            # 面板最小尺寸（不含投影）
```

2b. 导入区改为：

```python
from PySide6.QtCore import QRect, Qt, QThread, Signal
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

from config import NUM_FONT, UI_FONT, Config
from datasource import Quote, QuoteService, market_state
from panel import BORDER, GRIP, MIN_PANEL, SHADOW, draw_background, draw_grip, draw_hover_outline, draw_shadow, panel_rect
from settings_dialog import SettingsDialog
```

（`QRectF` / `QBrush` / `QPen` 在搬移后不再被 `main.py` 使用，一并从导入里去掉。）

2c. 删掉 `_panel_rect` 方法，把 `paintEvent` 整体替换为：

```python
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
```

2d. `_hit_region` 第一行改为：

```python
        panel = panel_rect(self.width(), self.height()).toRect()
```

- [ ] **步骤 3：静态检查**

运行：`.\.venv\Scripts\python.exe -m py_compile main.py panel.py`
预期：无输出（编译通过）
再运行：`.\.venv\Scripts\python.exe -c "import main"`
预期：无输出、无异常（说明没有残留的未定义名字）

- [ ] **步骤 4：目视回归（行为必须与改动前一致）**

运行：`.\.venv\Scripts\python.exe main.py`，逐项确认：

- [ ] 圆角背景、投影深浅、右下角两道斜线把手与改动前一致
- [ ] 左键按住任意位置能拖动窗口
- [ ] 四边、四角鼠标指针形状正确、能缩放；右下角把手能缩放
- [ ] 设置里把「背景不透明度」拖到 0% 后，鼠标贴边出现虚线提示
- [ ] 关闭程序后 `config.json` 里的 `pos_x/pos_y/width/height` 正常更新

---

