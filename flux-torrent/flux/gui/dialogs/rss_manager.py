"""RSS Feed Manager dialog - add/edit/remove RSS feeds."""

import logging

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QCheckBox, QSpinBox, QGroupBox, QFormLayout, QMessageBox,
    QAbstractItemView, QFileDialog, QComboBox,
)

from flux.core.rss_monitor import FeedConfig, RSSMonitor
from flux.gui.themes import c

logger = logging.getLogger(__name__)


class FeedEditWidget(QGroupBox):
    """Inline editor for a single feed's properties."""

    def __init__(self, config: FeedConfig = None, parent=None):
        super().__init__("Feed Settings", parent)
        self._config = config or FeedConfig()
        self._setup_ui()
        self._load(self._config)

    def _setup_ui(self):
        layout = QFormLayout(self)

        self._url_edit = QLineEdit()
        self._url_edit.setPlaceholderText("https://example.com/rss")
        layout.addRow("Feed URL:", self._url_edit)

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("My Feed")
        layout.addRow("Name:", self._name_edit)

        self._interval_spin = QSpinBox()
        self._interval_spin.setRange(5, 1440)
        self._interval_spin.setSuffix(" min")
        self._interval_spin.setValue(30)
        layout.addRow("Check interval:", self._interval_spin)

        self._include_edit = QLineEdit()
        self._include_edit.setPlaceholderText("regex pattern (empty = match all)")
        layout.addRow("Include filter:", self._include_edit)

        self._exclude_edit = QLineEdit()
        self._exclude_edit.setPlaceholderText("regex pattern (empty = exclude none)")
        layout.addRow("Exclude filter:", self._exclude_edit)

        self._category_edit = QLineEdit()
        self._category_edit.setPlaceholderText("Optional category")
        layout.addRow("Category:", self._category_edit)

        row = QHBoxLayout()
        self._savepath_edit = QLineEdit()
        self._savepath_edit.setPlaceholderText("Default save path")
        row.addWidget(self._savepath_edit)
        self._btn_browse = QPushButton("Browse...")
        self._btn_browse.setFixedWidth(80)
        self._btn_browse.clicked.connect(self._browse_path)
        row.addWidget(self._btn_browse)
        layout.addRow("Save path:", row)

        check_row = QHBoxLayout()
        self._enabled_check = QCheckBox("Enabled")
        self._enabled_check.setChecked(True)
        check_row.addWidget(self._enabled_check)
        self._auto_dl_check = QCheckBox("Auto-download matches")
        self._auto_dl_check.setChecked(True)
        check_row.addWidget(self._auto_dl_check)
        check_row.addStretch()
        layout.addRow(check_row)

    def _browse_path(self):
        path = QFileDialog.getExistingDirectory(self, "Select Save Path")
        if path:
            self._savepath_edit.setText(path)

    def _load(self, config: FeedConfig):
        self._url_edit.setText(config.url)
        self._name_edit.setText(config.name)
        self._interval_spin.setValue(config.interval_minutes)
        self._include_edit.setText(config.include_pattern)
        self._exclude_edit.setText(config.exclude_pattern)
        self._category_edit.setText(config.category)
        self._savepath_edit.setText(config.save_path)
        self._enabled_check.setChecked(config.enabled)
        self._auto_dl_check.setChecked(config.auto_download)

    def get_config(self) -> FeedConfig:
        return FeedConfig(
            url=self._url_edit.text().strip(),
            name=self._name_edit.text().strip(),
            enabled=self._enabled_check.isChecked(),
            interval_minutes=self._interval_spin.value(),
            include_pattern=self._include_edit.text().strip(),
            exclude_pattern=self._exclude_edit.text().strip(),
            save_path=self._savepath_edit.text().strip(),
            category=self._category_edit.text().strip(),
            auto_download=self._auto_dl_check.isChecked(),
        )


class RSSManagerDialog(QDialog):
    """Dialog for managing RSS feeds."""

    feeds_changed = pyqtSignal()

    def __init__(self, rss_monitor: RSSMonitor, parent=None):
        super().__init__(parent)
        self._monitor = rss_monitor
        self.setWindowTitle("RSS Feed Manager")
        self.setMinimumSize(700, 550)
        self._setup_ui()
        self._apply_theme()
        self._refresh_table()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # --- Feed table ---
        self._table = QTableWidget()
        self._table.setColumnCount(5)
        self._table.setHorizontalHeaderLabels(["Name", "URL", "Interval", "Enabled", "Auto-DL"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self._table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(0, 140)
        self._table.setColumnWidth(2, 70)
        self._table.setColumnWidth(3, 65)
        self._table.setColumnWidth(4, 65)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.currentRowChanged.connect(self._on_row_changed)
        layout.addWidget(self._table)

        # --- Table buttons ---
        btn_row = QHBoxLayout()
        self._btn_add = QPushButton("Add Feed")
        self._btn_add.clicked.connect(self._add_feed)
        btn_row.addWidget(self._btn_add)

        self._btn_remove = QPushButton("Remove")
        self._btn_remove.clicked.connect(self._remove_feed)
        btn_row.addWidget(self._btn_remove)

        self._btn_check_all = QPushButton("Check All Now")
        self._btn_check_all.clicked.connect(self._check_all)
        btn_row.addWidget(self._btn_check_all)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        # --- Edit panel ---
        self._edit_widget = FeedEditWidget()
        layout.addWidget(self._edit_widget)

        # --- Bottom buttons ---
        bottom_row = QHBoxLayout()
        bottom_row.addStretch()

        self._btn_save = QPushButton("Save Feed")
        self._btn_save.setFixedWidth(100)
        self._btn_save.clicked.connect(self._save_current)
        bottom_row.addWidget(self._btn_save)

        self._btn_close = QPushButton("Close")
        self._btn_close.setFixedWidth(80)
        self._btn_close.clicked.connect(self.accept)
        bottom_row.addWidget(self._btn_close)

        layout.addLayout(bottom_row)

    def _apply_theme(self):
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {c('bg')};
                color: {c('text')};
            }}
            QGroupBox {{
                color: {c('text')};
                border: 1px solid {c('border')};
                border-radius: 6px;
                margin-top: 8px;
                padding-top: 14px;
                font-weight: bold;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 4px;
            }}
            QTableWidget {{
                background-color: {c('bg_card')};
                color: {c('text')};
                border: 1px solid {c('border')};
                gridline-color: {c('border')};
            }}
            QHeaderView::section {{
                background-color: {c('bg_hover')};
                color: {c('text')};
                border: 1px solid {c('border')};
                padding: 4px;
            }}
            QLineEdit, QSpinBox, QComboBox {{
                background-color: {c('bg_card')};
                color: {c('text')};
                border: 1px solid {c('border')};
                border-radius: 4px;
                padding: 4px 8px;
            }}
            QPushButton {{
                background-color: {c('bg_hover')};
                color: {c('text')};
                border: 1px solid {c('border')};
                border-radius: 4px;
                padding: 6px 14px;
            }}
            QPushButton:hover {{
                background-color: {c('accent')};
                color: #ffffff;
            }}
            QCheckBox {{ color: {c('text')}; }}
        """)

    def _refresh_table(self):
        feeds = self._monitor.get_feeds()
        self._table.setRowCount(len(feeds))
        for i, feed in enumerate(feeds):
            self._table.setItem(i, 0, QTableWidgetItem(feed.name or "(unnamed)"))
            self._table.setItem(i, 1, QTableWidgetItem(feed.url))
            self._table.setItem(i, 2, QTableWidgetItem(f"{feed.interval_minutes}m"))

            enabled_item = QTableWidgetItem("Yes" if feed.enabled else "No")
            enabled_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(i, 3, enabled_item)

            auto_item = QTableWidgetItem("Yes" if feed.auto_download else "No")
            auto_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(i, 4, auto_item)

    def _on_row_changed(self, row):
        feeds = self._monitor.get_feeds()
        if 0 <= row < len(feeds):
            self._edit_widget._load(feeds[row])

    def _add_feed(self):
        config = FeedConfig(url="", name="New Feed", interval_minutes=30)
        self._edit_widget._load(config)
        self._edit_widget._url_edit.setFocus()

    def _save_current(self):
        config = self._edit_widget.get_config()
        if not config.url:
            QMessageBox.warning(self, "Error", "Feed URL is required.")
            return
        self._monitor.add_feed(config)
        self._refresh_table()
        self.feeds_changed.emit()

    def _remove_feed(self):
        row = self._table.currentRow()
        feeds = self._monitor.get_feeds()
        if 0 <= row < len(feeds):
            url = feeds[row].url
            self._monitor.remove_feed(url)
            self._refresh_table()
            self.feeds_changed.emit()

    def _check_all(self):
        self._monitor.check_all_now()
        QMessageBox.information(self, "RSS Check", "All feeds are being checked.")
