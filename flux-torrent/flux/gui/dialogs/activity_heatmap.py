"""Activity heatmap dialog for the session's weekday/hour traffic profile."""

import math

from PyQt6.QtCore import QPoint, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QFont, QPainter
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from flux.core.activity_heatmap import DAY_COUNT, HOUR_COUNT, heatmap_peak, heatmap_totals, normalize_heatmap
from flux.gui.themes import c as tc
from flux.utils.formatters import format_bytes


DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


class ActivityHeatmapWidget(QWidget):
    """Paint a compact 7 x 24 grid with separate download/upload bars."""

    _LEFT_MARGIN = 44
    _TOP_MARGIN = 34
    _RIGHT_MARGIN = 12
    _BOTTOM_MARGIN = 12

    def __init__(self, heatmap=None, parent=None):
        super().__init__(parent)
        self._heatmap = normalize_heatmap(heatmap)
        self.setMinimumHeight(230)
        self.setMinimumWidth(760)
        self.setMouseTracking(True)

    def sizeHint(self):
        return QSize(900, 260)

    def set_heatmap(self, heatmap):
        self._heatmap = normalize_heatmap(heatmap)
        self.update()

    def _grid_metrics(self):
        width = max(1, self.width() - self._LEFT_MARGIN - self._RIGHT_MARGIN)
        height = max(1, self.height() - self._TOP_MARGIN - self._BOTTOM_MARGIN)
        return width / HOUR_COUNT, height / DAY_COUNT

    def _cell_at(self, position: QPoint):
        cell_width, cell_height = self._grid_metrics()
        x = position.x() - self._LEFT_MARGIN
        y = position.y() - self._TOP_MARGIN
        if x < 0 or y < 0:
            return None
        hour = int(x / cell_width)
        day = int(y / cell_height)
        if day >= DAY_COUNT or hour >= HOUR_COUNT:
            return None
        return day, hour

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(tc("bg_card")))

        cell_width, cell_height = self._grid_metrics()
        peak = max(1, heatmap_peak(self._heatmap))
        text_color = QColor(tc("text_muted"))
        painter.setPen(text_color)
        painter.setFont(QFont("Segoe UI", 9))

        for hour in range(0, HOUR_COUNT, 3):
            x = self._LEFT_MARGIN + hour * cell_width
            painter.drawText(
                QRectF(x, 8, cell_width, 18),
                int(Qt.AlignmentFlag.AlignCenter),
                f"{hour:02d}",
            )

        download_color = QColor(tc("accent"))
        upload_color = QColor(tc("green"))
        grid_color = QColor(tc("border"))

        for day, name in enumerate(DAY_NAMES):
            y = self._TOP_MARGIN + day * cell_height
            painter.setPen(text_color)
            painter.drawText(
                QRectF(0, y, self._LEFT_MARGIN - 6, cell_height),
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                name,
            )
            for hour in range(HOUR_COUNT):
                x = self._LEFT_MARGIN + hour * cell_width
                cell = self._heatmap[day][hour]
                download = cell["download"]
                upload = cell["upload"]
                total = download + upload
                intensity = math.sqrt(total / peak) if total else 0.0

                background = QColor(tc("bg_hover"))
                background.setAlpha(255)
                painter.fillRect(QRectF(x + 1, y + 1, cell_width - 2, cell_height - 2), background)
                if intensity:
                    fill = QColor(tc("accent"))
                    fill.setAlpha(int(30 + 130 * intensity))
                    painter.fillRect(
                        QRectF(x + 2, y + 2, cell_width - 4, cell_height - 4),
                        fill,
                    )

                bar_width = max(0.0, cell_width - 8)
                bar_height = max(2.0, min(4.0, cell_height / 8))
                download_width = bar_width * min(1.0, download / peak)
                upload_width = bar_width * min(1.0, upload / peak)
                download_color.setAlpha(220)
                upload_color.setAlpha(220)
                painter.fillRect(
                    QRectF(x + 4, y + cell_height - bar_height * 2 - 3, download_width, bar_height),
                    download_color,
                )
                painter.fillRect(
                    QRectF(x + 4, y + cell_height - bar_height - 2, upload_width, bar_height),
                    upload_color,
                )
                painter.setPen(grid_color)
                painter.drawRect(QRectF(x + 0.5, y + 0.5, cell_width - 1, cell_height - 1))

    def mouseMoveEvent(self, event):
        cell_index = self._cell_at(event.position().toPoint())
        if cell_index is None:
            QToolTip.hideText()
            return
        day, hour = cell_index
        cell = self._heatmap[day][hour]
        QToolTip.showText(
            event.globalPosition().toPoint(),
            f"{DAY_NAMES[day]} {hour:02d}:00-{(hour + 1) % 24:02d}:00\n"
            f"Download: {format_bytes(cell['download'])}\n"
            f"Upload: {format_bytes(cell['upload'])}",
            self,
        )

    def leaveEvent(self, _event):
        QToolTip.hideText()


class ActivityHeatmapDialog(QDialog):
    """Display the accumulated session activity by local weekday and hour."""

    def __init__(self, heatmap=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Activity Heatmap")
        self.setMinimumSize(820, 360)

        normalized = normalize_heatmap(heatmap)
        download, upload = heatmap_totals(normalized)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(10)

        heading = QLabel("Activity Heatmap")
        heading.setStyleSheet(f"font-size: 16px; font-weight: 700; color: {tc('text')};")
        layout.addWidget(heading)

        subtitle = QLabel("All recorded session traffic, grouped by local weekday and hour")
        subtitle.setStyleSheet(f"color: {tc('text_muted')}; font-size: 11px;")
        layout.addWidget(subtitle)

        self._heatmap_widget = ActivityHeatmapWidget(normalized)
        layout.addWidget(self._heatmap_widget, stretch=1)

        legend = QHBoxLayout()
        legend.setSpacing(18)
        legend.addWidget(self._legend_label("Download", tc("accent")))
        legend.addWidget(self._legend_label("Upload", tc("green")))
        legend.addWidget(QLabel("Darker cells indicate more combined volume"))
        legend.addStretch()
        legend.addWidget(QLabel(f"Total DL: {format_bytes(download)}"))
        legend.addWidget(QLabel(f"Total UL: {format_bytes(upload)}"))
        layout.addLayout(legend)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    @staticmethod
    def _legend_label(text: str, color: str) -> QLabel:
        label = QLabel(f"■ {text}")
        label.setStyleSheet(f"color: {color}; font-size: 11px;")
        return label
