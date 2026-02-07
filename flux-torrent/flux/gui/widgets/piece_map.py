"""Piece map visualization widget."""

from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import QPainter, QColor
from PyQt6.QtWidgets import QWidget

from flux.gui.themes import c


class PieceMapWidget(QWidget):
    """Renders a grid of colored cells representing torrent piece states."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pieces: list[int] = []
        self._cell_size = 6
        self._gap = 1
        self.setMinimumHeight(40)
        self.apply_theme()

    def apply_theme(self):
        self.setStyleSheet(
            f"background-color: {c('bg')}; border: 1px solid {c('border')}; border-radius: 6px;"
        )

    def set_pieces(self, pieces: list[int]):
        self._pieces = pieces
        self.update()

    def paintEvent(self, event):
        if not self._pieces:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        # Read colors at paint time (theme-aware)
        colors = {
            0: QColor(c("border")),    # Missing
            1: QColor(c("accent")),    # Downloading
            2: QColor(c("green")),     # Have
        }

        w = self.width() - 4
        cell = self._cell_size
        gap = self._gap
        stride = cell + gap
        cols = max(1, w // stride)

        # If too many pieces, downsample
        pieces = self._pieces
        if len(pieces) > cols * 50:  # Max ~50 rows
            factor = len(pieces) // (cols * 50) + 1
            sampled = []
            for i in range(0, len(pieces), factor):
                chunk = pieces[i:i + factor]
                if 1 in chunk:
                    sampled.append(1)
                elif 2 in chunk:
                    sampled.append(2)
                else:
                    sampled.append(0)
            pieces = sampled

        for i, state in enumerate(pieces):
            col = i % cols
            row = i // cols
            x = 2 + col * stride
            y = 2 + row * stride

            if y + cell > self.height():
                break

            color = colors.get(state, colors[0])
            painter.fillRect(QRectF(x, y, cell, cell), color)

        painter.end()

    def minimumSizeHint(self):
        from PyQt6.QtCore import QSize
        return QSize(100, 40)
