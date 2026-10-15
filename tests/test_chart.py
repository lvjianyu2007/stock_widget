"""分时图坐标映射与取值范围。

坐标映射部分纯计算；窗口标志那条要一个 QApplication，用 offscreen 平台跑，不会弹窗。
"""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QRectF, Qt
from PySide6.QtWidgets import QApplication, QWidget

from chart import IntradayChartWindow, IntradayPainter, minutes_of
from intraday import MinutePoint, attach_latest, build_series
from panel import panel_rect

RECT = QRectF(0.0, 0.0, 480.0, 200.0)


def _series(prev_close=100.0):
    records = [
        {"day": "2026-10-15 09:31:00", "close": "102.0", "volume": "1000", "amount": "102000"},
        {"day": "2026-10-15 11:30:00", "close": "103.0", "volume": "1000", "amount": "103000"},
        {"day": "2026-10-15 14:00:00", "close": "98.0", "volume": "1000", "amount": "98000"},
    ]
    return build_series("600519", records, "2026-10-15", prev_close)


def _painter():
    return IntradayPainter("#FF0000", "#00FF00", "#FFFFFF", "#FFFFFF")


class MinutesOfTest(unittest.TestCase):
    def test_session_boundaries(self):
        self.assertEqual(minutes_of("09:30"), 0)
        self.assertEqual(minutes_of("11:30"), 120)
        self.assertEqual(minutes_of("13:00"), 120)
        self.assertEqual(minutes_of("15:00"), 240)

    def test_out_of_session_returns_none(self):
        self.assertIsNone(minutes_of("09:29"))
        self.assertIsNone(minutes_of("12:00"))
        self.assertIsNone(minutes_of("15:01"))
        self.assertIsNone(minutes_of("bad"))


class MappingTest(unittest.TestCase):
    def test_map_x_spans_full_width(self):
        self.assertEqual(IntradayPainter.map_x(0, RECT), 0.0)
        self.assertEqual(IntradayPainter.map_x(240, RECT), 480.0)
        self.assertEqual(IntradayPainter.map_x(120, RECT), 240.0)

    def test_map_y_is_symmetric_around_baseline(self):
        center = 100.0
        self.assertEqual(IntradayPainter.map_y(100.0, 100.0, 5.0, RECT), center)
        self.assertEqual(IntradayPainter.map_y(105.0, 100.0, 5.0, RECT), 0.0)
        self.assertEqual(IntradayPainter.map_y(95.0, 100.0, 5.0, RECT), 200.0)

    def test_map_y_clamps_out_of_range_value(self):
        self.assertEqual(IntradayPainter.map_y(200.0, 100.0, 5.0, RECT), 0.0)

    def test_map_y_without_deviation_sits_on_center(self):
        self.assertEqual(IntradayPainter.map_y(100.0, 100.0, 0.0, RECT), 100.0)


class MappedPointsTest(unittest.TestCase):
    """坐标换算：折线与单点绘制共用，跳过画不出来的点。"""

    def test_single_point_returns_one_coordinate(self):
        points = [MinutePoint(time="09:31", price=102.0)]
        coords = _painter().mapped_points(points, 100.0, 5.0, RECT)
        self.assertEqual(len(coords), 1)
        self.assertEqual(coords[0].x(), IntradayPainter.map_x(1, RECT))
        self.assertEqual(coords[0].y(), IntradayPainter.map_y(102.0, 100.0, 5.0, RECT))

    def test_skips_none_average_when_drawing_average(self):
        points = [
            MinutePoint(time="09:31", price=102.0, avg=None),
            MinutePoint(time="09:32", price=103.0, avg=101.0),
        ]
        self.assertEqual(len(_painter().mapped_points(points, 100.0, 5.0, RECT, use_avg=True)), 1)
        self.assertEqual(len(_painter().mapped_points(points, 100.0, 5.0, RECT)), 2)

    def test_skips_points_outside_session(self):
        points = [MinutePoint(time="09:31", price=102.0), MinutePoint(time="12:00", price=103.0)]
        self.assertEqual(len(_painter().mapped_points(points, 100.0, 5.0, RECT)), 1)


class RangeTest(unittest.TestCase):
    def test_baseline_prefers_prev_close(self):
        series = _series(prev_close=100.0)
        self.assertEqual(IntradayPainter.baseline_of(series, series.points), 100.0)

    def test_baseline_falls_back_to_first_point(self):
        series = _series(prev_close=0.0)
        self.assertEqual(IntradayPainter.baseline_of(series, series.points), 102.0)

    def test_max_deviation_uses_avg_values_too(self):
        series = _series(prev_close=100.0)
        deviation = IntradayPainter.max_deviation(series.points, 100.0)
        self.assertAlmostEqual(deviation, 3.0)      # 均价最高 102.5 也在这条线内

    def test_max_deviation_has_minimum_range(self):
        records = [{"day": "2026-10-15 09:31:00", "close": "100.0", "volume": "1", "amount": "100"}]
        series = build_series("600519", records, "2026-10-15", prev_close=100.0)
        deviation = IntradayPainter.max_deviation(series.points, 100.0)
        self.assertAlmostEqual(deviation, 0.5)      # 0.5% 兜底，避免横盘被放大


class ColorTest(unittest.TestCase):
    def test_line_color_follows_direction(self):
        painter = _painter()
        rising = _series(100.0)
        attach_latest(rising, 105.0)
        self.assertEqual(painter.line_color(rising).name(), "#ff0000")
        falling = _series(100.0)
        attach_latest(falling, 95.0)
        self.assertEqual(painter.line_color(falling).name(), "#00ff00")
        flat = _series(100.0)
        attach_latest(flat, 100.0)
        self.assertEqual(painter.line_color(flat).name(), "#ffffff")

    def test_line_color_without_points_is_flat(self):
        empty = build_series("600519", [], "2026-10-15", 0.0)
        self.assertEqual(_painter().line_color(empty).name(), "#ffffff")


class WindowFlagTest(unittest.TestCase):
    """分时窗口必须是真的独立窗口。

    只写 FramelessWindowHint 会把窗口类型清成 Qt.Widget，而本窗口带着父对象（为了
    右键「立即刷新」能转发给主窗口），于是被当成子控件嵌进主窗口里，表现是「点了没反应」。
    这个坑只看 isVisible() 是发现不了的（offscreen 下照样是 True），必须查 isWindow()。
    """

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_window_is_top_level_even_with_parent(self):
        parent = QWidget()
        window = IntradayChartWindow("600519", "贵州茅台", parent)
        self.assertTrue(window.isWindow())
        self.assertTrue(window.windowFlags() & Qt.Window)
        window.deleteLater()
        parent.deleteLater()

    def test_toggle_on_top_keeps_it_a_window(self):
        window = IntradayChartWindow("600519", "贵州茅台")
        window._toggle_on_top(False)
        self.assertTrue(window.isWindow())
        window._toggle_on_top(True)
        self.assertTrue(window.isWindow())
        self.assertTrue(window.windowFlags() & Qt.WindowStaysOnTopHint)
        window.deleteLater()


class AutoCloseTest(unittest.TestCase):
    """点别的窗口（**含其他软件**）要自动收起。

    跨应用切换时 Qt 发的是 WindowDeactivate 而不是焦点事件——只挂 focusOutEvent 的话，
    同应用内点主窗口能关，点别的软件却没反应（这是踩过的坑）。
    """

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def _shown_window():
        window = IntradayChartWindow("600519", "贵州茅台")
        window.show()
        return window

    def test_window_deactivate_closes(self):
        window = self._shown_window()
        self.assertTrue(window.isVisible())
        window.event(QEvent(QEvent.WindowDeactivate))
        self.assertFalse(window.isVisible(), "切到别的窗口时应该自动收起")
        window.deleteLater()

    def test_suppressed_deactivate_keeps_window(self):
        window = self._shown_window()
        window._suppress_close = True          # 右键菜单开着 / 正在切换窗口标志
        window.event(QEvent(QEvent.WindowDeactivate))
        self.assertTrue(window.isVisible(), "菜单开着时不该被收起")
        window._suppress_close = False
        window.event(QEvent(QEvent.WindowDeactivate))
        self.assertFalse(window.isVisible(), "豁免结束后还是要能自动收起")
        window.deleteLater()


class WindowLayoutTest(unittest.TestCase):
    """底部两行与右上角关闭按钮的位置约束。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_footer_row_sits_below_time_axis(self):
        window = IntradayChartWindow("600519", "贵州茅台")
        panel = panel_rect(window.width(), window.height())
        chart = window._chart_rect(panel)
        tick_bottom = chart.bottom() + 2 + (window.TIME_AXIS_HEIGHT - 2)
        footer_top = chart.bottom() + window.TIME_AXIS_HEIGHT + 1
        self.assertGreater(footer_top, tick_bottom, "状态行不能压在时间刻度上")
        self.assertLessEqual(footer_top + window.FOOTER_HEIGHT, panel.bottom(), "状态行不能溢出面板")
        window.deleteLater()

    def test_close_button_stays_inside_panel(self):
        window = IntradayChartWindow("600519", "贵州茅台")
        panel = panel_rect(window.width(), window.height())
        close_rect = window._close_rect()
        self.assertTrue(panel.contains(close_rect), "关闭按钮不能跑出面板")
        self.assertLess(close_rect.right(), panel.right())
        window.deleteLater()

    def test_header_leaves_room_for_close_button(self):
        """价格文字右边界要在关闭按钮左边，否则会互相压字。"""
        window = IntradayChartWindow("600519", "贵州茅台")
        panel = panel_rect(window.width(), window.height())
        header_right = panel.left() + 12 + (panel.width() - 24 - window.CLOSE_SIZE - 10)
        self.assertLessEqual(header_right, window._close_rect().left())
        window.deleteLater()


if __name__ == "__main__":
    unittest.main()
