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
