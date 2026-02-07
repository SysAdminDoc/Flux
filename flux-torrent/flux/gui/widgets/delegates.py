"""Custom item delegates for the torrent list."""

from PyQt6.QtCore import Qt, QModelIndex, QRectF
from PyQt6.QtGui import QPainter, QColor, QLinearGradient, QPen, QFont, QBrush
from PyQt6.QtWidgets import QStyledItemDelegate, QStyleOptionViewItem, QStyle

from flux.core.torrent import TorrentState
from flux.gui.themes import c, state_color


class ProgressBarDelegate(QStyledItemDelegate):
    """Renders a gradient progress bar in the Progress column."""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        progress = index.data(Qt.ItemDataRole.UserRole + 1)
        state = index.data(Qt.ItemDataRole.UserRole + 2)

        if progress is None:
            progress = 0.0

        rect = option.rect

        # Draw selection background
        if option.state & QStyle.StateFlag.State_Selected:
            sel = QColor(c("accent"))
            sel.setAlpha(25)
            painter.fillRect(rect, sel)

        # Progress bar dimensions
        bar_h = 4
        bar_y = rect.center().y() - bar_h // 2 + 6
        bar_x = rect.x() + 8
        bar_w = rect.width() - 60

        # Background track
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(c("border")))
        painter.drawRoundedRect(QRectF(bar_x, bar_y, bar_w, bar_h), 2, 2)

        # Progress fill
        if progress > 0:
            fill_w = max(4, bar_w * progress)

            if state == TorrentState.SEEDING or state == TorrentState.COMPLETED:
                grad = QLinearGradient(bar_x, 0, bar_x + fill_w, 0)
                grad.setColorAt(0, QColor(c("green")))
                grad.setColorAt(1, QColor(c("cyan")))
            elif state == TorrentState.PAUSED:
                grad = QLinearGradient(bar_x, 0, bar_x + fill_w, 0)
                grad.setColorAt(0, QColor(c("text_dim")))
                grad.setColorAt(1, QColor(c("text_muted")))
            elif state == TorrentState.ERROR:
                grad = QLinearGradient(bar_x, 0, bar_x + fill_w, 0)
                grad.setColorAt(0, QColor(c("red")))
                grad.setColorAt(1, QColor(c("orange")))
            else:
                grad = QLinearGradient(bar_x, 0, bar_x + fill_w, 0)
                grad.setColorAt(0, QColor(c("accent")))
                grad.setColorAt(1, QColor(c("cyan")))

            painter.setBrush(QBrush(grad))
            painter.drawRoundedRect(QRectF(bar_x, bar_y, fill_w, bar_h), 2, 2)

        # Percentage text
        text_rect = QRectF(bar_x + bar_w + 4, rect.y(), 48, rect.height())
        font = painter.font()
        font.setFamily("Consolas")
        font.setPointSize(max(9, font.pointSize()))
        font.setStyleHint(QFont.StyleHint.Monospace)
        painter.setFont(font)
        painter.setPen(QColor(c("text_muted")))
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         f"{progress * 100:.1f}%")

        painter.restore()

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        size.setHeight(max(size.height(), 36))
        return size


class StateIconDelegate(QStyledItemDelegate):
    """Renders a colored state indicator dot in the first column."""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        state = index.data(Qt.ItemDataRole.UserRole + 2)

        # Draw selection background
        if option.state & QStyle.StateFlag.State_Selected:
            sel = QColor(c("accent"))
            sel.setAlpha(25)
            painter.fillRect(option.rect, sel)

        if state is None:
            painter.restore()
            return

        color = QColor(state_color(state))

        # Draw a filled circle indicator
        cx = option.rect.center().x()
        cy = option.rect.center().y()
        radius = 4

        painter.setPen(Qt.PenStyle.NoPen)

        # Glow
        glow_color = QColor(color)
        glow_color.setAlpha(40)
        painter.setBrush(glow_color)
        painter.drawEllipse(cx - radius - 3, cy - radius - 3,
                            (radius + 3) * 2, (radius + 3) * 2)

        # Solid dot
        painter.setBrush(color)
        painter.drawEllipse(cx - radius, cy - radius, radius * 2, radius * 2)

        painter.restore()

    def sizeHint(self, option, index):
        return option.rect.size() if option.rect.width() > 0 else super().sizeHint(option, index)
