"""Speed graph widgets - sparklines and full charts with annotations."""

from PyQt6.QtCore import Qt, QRectF, QPointF
from PyQt6.QtGui import QPainter, QColor, QLinearGradient, QPen, QPainterPath, QFont
from PyQt6.QtWidgets import QWidget

from flux.utils.formatters import format_speed
from flux.gui.themes import c


class SparklineWidget(QWidget):
    """Tiny inline speed sparkline for the toolbar."""

    def __init__(self, theme_key: str = "accent", parent=None):
        super().__init__(parent)
        self._data: list[int] = []
        self._theme_key = theme_key
        self._max_points = 60
        self.setFixedSize(80, 24)

    def set_data(self, data: list[int]):
        self._data = data[-self._max_points:]
        self.update()

    def paintEvent(self, event):
        if len(self._data) < 2:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        color = QColor(c(self._theme_key))
        w = self.width()
        h = self.height()
        padding = 2

        max_val = max(self._data) or 1
        points = []

        for i, val in enumerate(self._data):
            x = padding + (w - 2 * padding) * i / (len(self._data) - 1)
            y = h - padding - (h - 2 * padding) * (val / max_val)
            points.append(QPointF(x, y))

        # Fill
        path = QPainterPath()
        path.moveTo(points[0])
        for p in points[1:]:
            path.lineTo(p)
        path.lineTo(QPointF(points[-1].x(), h))
        path.lineTo(QPointF(points[0].x(), h))
        path.closeSubpath()

        fill_color = QColor(color)
        fill_color.setAlpha(30)
        grad = QLinearGradient(0, 0, 0, h)
        grad.setColorAt(0, fill_color)
        fill_bottom = QColor(color)
        fill_bottom.setAlpha(0)
        grad.setColorAt(1, fill_bottom)
        painter.fillPath(path, grad)

        # Line
        line_path = QPainterPath()
        line_path.moveTo(points[0])
        for p in points[1:]:
            line_path.lineTo(p)

        pen = QPen(color)
        pen.setWidthF(1.5)
        painter.setPen(pen)
        painter.drawPath(line_path)

        painter.end()


class SpeedGraphWidget(QWidget):
    """Larger speed graph for the detail panel with peak/average annotations."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._dl_data: list[int] = []
        self._ul_data: list[int] = []
        self._max_points = 120
        self.setMinimumHeight(100)
        self.apply_theme()

    def apply_theme(self):
        self.setStyleSheet(
            f"background-color: {c('bg')}; border: 1px solid {c('border')}; border-radius: 6px;"
        )

    def set_data(self, dl_data: list[int], ul_data: list[int]):
        self._dl_data = dl_data[-self._max_points:]
        self._ul_data = ul_data[-self._max_points:]
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()
        pad_l = 60
        pad_r = 10
        pad_t = 10
        pad_b = 20
        chart_w = w - pad_l - pad_r
        chart_h = h - pad_t - pad_b

        if chart_w < 20 or chart_h < 20:
            painter.end()
            return

        all_vals = self._dl_data + self._ul_data
        max_val = max(all_vals) if all_vals else 1
        if max_val == 0:
            max_val = 1024

        # Grid lines
        grid_pen = QPen(QColor(c("border")))
        grid_pen.setWidthF(0.5)

        label_font = QFont("Consolas", 8)
        label_font.setStyleHint(QFont.StyleHint.Monospace)
        label_font.setStyleHint(QFont.StyleHint.Monospace)

        for i in range(5):
            y = pad_t + chart_h * i / 4
            painter.setPen(grid_pen)
            painter.drawLine(QPointF(pad_l, y), QPointF(w - pad_r, y))

            val = max_val * (1 - i / 4)
            painter.setFont(label_font)
            painter.setPen(QColor(c("text_dim")))
            painter.drawText(QRectF(0, y - 8, pad_l - 6, 16),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                             format_speed(int(val)))

        accent = c("accent")
        green = c("green")

        def draw_series(data, color_str, alpha=60):
            if len(data) < 2:
                return
            color = QColor(color_str)
            points = []
            for i, val in enumerate(data):
                x = pad_l + chart_w * i / (len(data) - 1)
                y = pad_t + chart_h * (1 - val / max_val)
                points.append(QPointF(x, y))

            # Fill gradient
            fill_path = QPainterPath()
            fill_path.moveTo(points[0])
            for p in points[1:]:
                fill_path.lineTo(p)
            fill_path.lineTo(QPointF(points[-1].x(), pad_t + chart_h))
            fill_path.lineTo(QPointF(points[0].x(), pad_t + chart_h))
            fill_path.closeSubpath()

            fill_grad = QLinearGradient(0, pad_t, 0, pad_t + chart_h)
            c_top = QColor(color)
            c_top.setAlpha(alpha)
            c_bot = QColor(color)
            c_bot.setAlpha(0)
            fill_grad.setColorAt(0, c_top)
            fill_grad.setColorAt(1, c_bot)
            painter.fillPath(fill_path, fill_grad)

            # Line
            line_path = QPainterPath()
            line_path.moveTo(points[0])
            for p in points[1:]:
                line_path.lineTo(p)
            pen = QPen(color)
            pen.setWidthF(1.5)
            painter.setPen(pen)
            painter.drawPath(line_path)

        draw_series(self._dl_data, accent, 50)
        draw_series(self._ul_data, green, 30)

        # --- Peak / Average annotation lines ---
        def draw_annotation(data, color_str, side="left"):
            if not data:
                return
            peak = max(data)
            avg = sum(data) / len(data)
            if peak <= 0:
                return

            color = QColor(color_str)
            annotation_font = QFont("Consolas", 7)
            annotation_font.setStyleHint(QFont.StyleHint.Monospace)
            annotation_font.setStyleHint(QFont.StyleHint.Monospace)

            # Peak line (dashed)
            peak_y = pad_t + chart_h * (1 - peak / max_val)
            if pad_t <= peak_y <= pad_t + chart_h:
                dash_pen = QPen(color)
                dash_pen.setWidthF(0.8)
                dash_pen.setStyle(Qt.PenStyle.DashLine)
                painter.setPen(dash_pen)
                painter.drawLine(QPointF(pad_l, peak_y), QPointF(w - pad_r, peak_y))

                peak_color = QColor(color)
                peak_color.setAlpha(200)
                painter.setPen(peak_color)
                painter.setFont(annotation_font)
                label = f"Peak: {format_speed(peak)}"
                if side == "left":
                    painter.drawText(QRectF(pad_l + 4, peak_y - 12, 120, 12),
                                     Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
                                     label)
                else:
                    painter.drawText(QRectF(w - pad_r - 124, peak_y - 12, 120, 12),
                                     Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom,
                                     label)

            # Average line (dotted)
            avg_y = pad_t + chart_h * (1 - avg / max_val)
            if pad_t <= avg_y <= pad_t + chart_h:
                dot_pen = QPen(color)
                dot_pen.setWidthF(0.6)
                dot_pen.setStyle(Qt.PenStyle.DotLine)
                painter.setPen(dot_pen)
                painter.drawLine(QPointF(pad_l, avg_y), QPointF(w - pad_r, avg_y))

                avg_color = QColor(color)
                avg_color.setAlpha(160)
                painter.setPen(avg_color)
                painter.setFont(annotation_font)
                label = f"Avg: {format_speed(int(avg))}"
                if side == "left":
                    painter.drawText(QRectF(pad_l + 4, avg_y + 1, 120, 12),
                                     Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                                     label)
                else:
                    painter.drawText(QRectF(w - pad_r - 124, avg_y + 1, 120, 12),
                                     Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop,
                                     label)

        draw_annotation(self._dl_data, accent, "left")
        draw_annotation(self._ul_data, green, "right")

        # --- Legend ---
        legend_font = QFont("Segoe UI", 9)
        painter.setFont(legend_font)

        lx = pad_l + 10
        ly = pad_t + 6

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(accent))
        painter.drawEllipse(lx, ly, 6, 6)
        painter.setPen(QColor(c("text_muted")))
        painter.drawText(lx + 10, ly + 6, "Download")

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(green))
        painter.drawEllipse(lx + 85, ly, 6, 6)
        painter.setPen(QColor(c("text_muted")))
        painter.drawText(lx + 95, ly + 6, "Upload")

        painter.end()
