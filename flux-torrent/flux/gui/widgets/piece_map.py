"""Piece map visualization widget."""

from PyQt6.QtCore import QRectF, QSize
from PyQt6.QtGui import QPainter, QColor
from PyQt6.QtWidgets import QWidget

from flux.gui.themes import c


class PieceMapWidget(QWidget):
    """Render piece states and peer availability as a compact color grid."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pieces: list[int] = []
        self._peer_owners: list[int] = []
        self._peer_labels: list[str] = []
        self._cell_size = 6
        self._gap = 1
        self.setMinimumHeight(40)
        self.apply_theme()

    def apply_theme(self):
        self.setStyleSheet(
            f"background-color: {c('bg')}; border: 1px solid {c('border')}; border-radius: 6px;"
        )

    def set_pieces(
        self,
        pieces: list[int],
        peer_owners: list[int] | None = None,
        peer_labels: list[str] | None = None,
    ):
        self._pieces = list(pieces or [])
        self._peer_owners = list(peer_owners or [])
        self._peer_labels = list(peer_labels or [])
        if self._peer_labels:
            self.setToolTip("Peer availability: " + " | ".join(self._peer_labels))
        else:
            self.setToolTip("")
        self.update()

    @staticmethod
    def peer_color(index: int) -> QColor:
        """Return the stable color used for a peer legend entry."""
        return QColor.fromHsv((index * 67 + 18) % 360, 170, 225)

    def paintEvent(self, event):
        del event
        if not self._pieces:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

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

        pieces = self._pieces
        owners = self._peer_owners
        if len(pieces) > cols * 50:  # Max ~50 rows
            factor = len(pieces) // (cols * 50) + 1
            sampled = []
            sampled_owners = []
            for i in range(0, len(pieces), factor):
                chunk = pieces[i:i + factor]
                if 1 in chunk:
                    sampled.append(1)
                elif 2 in chunk:
                    sampled.append(2)
                else:
                    sampled.append(0)
                owner_chunk = owners[i:i + factor]
                sampled_owners.append(next(
                    (owner for owner in owner_chunk if owner >= 0), -1
                ))
            pieces = sampled
            owners = sampled_owners

        for i, state in enumerate(pieces):
            col = i % cols
            row = i // cols
            x = 2 + col * stride
            y = 2 + row * stride

            if y + cell > self.height():
                break

            owner = owners[i] if i < len(owners) else -1
            if owner >= 0 and state != 2:
                color = self.peer_color(owner)
            else:
                color = colors.get(state, colors[0])
            painter.fillRect(QRectF(x, y, cell, cell), color)

        painter.end()

    def minimumSizeHint(self):
        return QSize(100, 40)
