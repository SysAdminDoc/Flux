"""Sidebar widget with status filters, categories, and session info."""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QIcon
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QFrame, QSizePolicy
)

from flux.core.torrent import TorrentState
from flux.gui.themes import c, state_color


class SidebarWidget(QWidget):
    """Collapsible left sidebar with filters and session info."""

    filter_changed = pyqtSignal(object)       # TorrentState or None
    category_changed = pyqtSignal(str)        # category name or ""

    # Status items: (state_or_None, label, theme_color_key_or_None)
    _STATUS_DEFS = [
        (None, "All Torrents", "text"),
        (TorrentState.DOWNLOADING, "Downloading", None),
        (TorrentState.SEEDING, "Seeding", None),
        (TorrentState.COMPLETED, "Completed", None),
        (TorrentState.PAUSED, "Paused", None),
        (TorrentState.ERROR, "Error", None),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._collapsed = False
        self._expanded_width = 180
        self._collapsed_width = 0
        self.setObjectName("sidebar")
        self.setFixedWidth(self._expanded_width)
        self._setup_ui()
        self.apply_theme()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 8)
        layout.setSpacing(0)

        # --- Status Filters ---
        self._section_label = QLabel("  STATUS")
        self._section_label.setObjectName("sectionTitle")
        self._section_label.setFixedHeight(28)
        layout.addWidget(self._section_label)

        self._status_list = QListWidget()
        self._status_list.setObjectName("sidebar")
        self._status_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._status_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        for state, label, _ in self._STATUS_DEFS:
            item = QListWidgetItem(f"  {label}")
            item.setData(Qt.ItemDataRole.UserRole, state)
            self._status_list.addItem(item)

        self._status_list.setCurrentRow(0)
        self._status_list.setFixedHeight(len(self._STATUS_DEFS) * 32 + 4)
        self._status_list.currentRowChanged.connect(self._on_status_changed)

        layout.addWidget(self._status_list)

        # --- Separator ---
        self._sep1 = QFrame()
        self._sep1.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(self._sep1)

        # --- Categories ---
        self._cat_label = QLabel("  LABELS")
        self._cat_label.setObjectName("sectionTitle")
        self._cat_label.setFixedHeight(28)
        layout.addWidget(self._cat_label)

        self._category_list = QListWidget()
        self._category_list.setObjectName("sidebar")
        self._category_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._category_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        all_item = QListWidgetItem("  All")
        all_item.setData(Qt.ItemDataRole.UserRole, "")
        self._category_list.addItem(all_item)

        self._category_list.setCurrentRow(0)
        self._category_list.currentRowChanged.connect(self._on_category_changed)

        layout.addWidget(self._category_list)

        # --- Spacer ---
        layout.addStretch()

        # --- Session Info ---
        self._sep2 = QFrame()
        self._sep2.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(self._sep2)

        self._info_frame = QWidget()
        info_layout = QVBoxLayout(self._info_frame)
        info_layout.setContentsMargins(14, 6, 14, 6)
        info_layout.setSpacing(3)

        self._dht_label = QLabel("DHT: --")
        info_layout.addWidget(self._dht_label)

        self._port_label = QLabel("Port: --")
        info_layout.addWidget(self._port_label)

        self._disk_label = QLabel("Free: --")
        info_layout.addWidget(self._disk_label)

        layout.addWidget(self._info_frame)

    def apply_theme(self):
        """Re-apply all theme colors. Call after theme change."""
        self.setStyleSheet(f"""
            QWidget#sidebar {{
                background-color: {c('bg_card')};
                border-right: 1px solid {c('border')};
            }}
        """)

        self._sep1.setStyleSheet(f"background-color: {c('border')}; max-height: 1px; margin: 8px 12px;")
        self._sep2.setStyleSheet(f"background-color: {c('border')}; max-height: 1px; margin: 4px 12px;")

        info_style = f"color: {c('text_dim')}; font-size: 10px;"
        self._dht_label.setStyleSheet(info_style)
        self._port_label.setStyleSheet(info_style)
        self._disk_label.setStyleSheet(info_style)

        # Re-color status filter items
        for i, (state, label, key) in enumerate(self._STATUS_DEFS):
            item = self._status_list.item(i)
            if item:
                if state is None:
                    item.setForeground(QColor(c(key)))
                else:
                    item.setForeground(QColor(state_color(state)))

        # Re-color category items
        for i in range(self._category_list.count()):
            item = self._category_list.item(i)
            if item:
                cat_data = item.data(Qt.ItemDataRole.UserRole)
                if not cat_data:
                    item.setForeground(QColor(c("text")))
                else:
                    item.setForeground(QColor(c("text_muted")))

    def _on_status_changed(self, row):
        if 0 <= row < len(self._STATUS_DEFS):
            state = self._STATUS_DEFS[row][0]
            self.filter_changed.emit(state)

    def _on_category_changed(self, row):
        item = self._category_list.item(row)
        if item:
            cat = item.data(Qt.ItemDataRole.UserRole) or ""
            self.category_changed.emit(cat)

    def update_categories(self, categories: list):
        """Refresh category list from settings."""
        current = self._category_list.currentRow()
        self._category_list.clear()

        all_item = QListWidgetItem("  All")
        all_item.setData(Qt.ItemDataRole.UserRole, "")
        all_item.setForeground(QColor(c("text")))
        self._category_list.addItem(all_item)

        for cat in categories:
            name = cat["name"] if isinstance(cat, dict) else cat
            color = cat.get("color", c("text_muted")) if isinstance(cat, dict) else c("text_muted")
            item = QListWidgetItem(f"  {name}")
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setForeground(QColor(color))
            self._category_list.addItem(item)

        if current >= 0 and current < self._category_list.count():
            self._category_list.setCurrentRow(current)
        else:
            self._category_list.setCurrentRow(0)

    def update_session_info(self, dht_nodes: int = 0, port: int = 0, free_space: str = "--"):
        self._dht_label.setText(f"DHT: {dht_nodes} nodes")
        self._port_label.setText(f"Port: {port}")
        self._disk_label.setText(f"Free: {free_space}")

    def toggle_collapsed(self):
        self._collapsed = not self._collapsed
        if self._collapsed:
            self.setFixedWidth(0)
            self.hide()
        else:
            self.show()
            self.setFixedWidth(self._expanded_width)

    @property
    def is_collapsed(self) -> bool:
        return self._collapsed

    def update_counts(self, counts: dict):
        """Update item text with torrent counts."""
        for i, (state, label, _) in enumerate(self._STATUS_DEFS):
            count = counts.get(state, 0) if state else sum(counts.values())
            item = self._status_list.item(i)
            if item:
                item.setText(f"  {label}  ({count})")
