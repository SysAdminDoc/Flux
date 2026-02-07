"""Add Torrent dialog."""

import os
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QComboBox, QCheckBox, QFileDialog,
    QGroupBox, QGridLayout, QFrame, QTreeWidget, QTreeWidgetItem,
    QHeaderView, QTabWidget, QWidget
)
from PyQt6.QtGui import QFont

from flux.core.settings import Settings
from flux.utils.formatters import format_bytes
from flux.gui.themes import c as tc
from flux.gui.themes import c as tc


class AddTorrentDialog(QDialog):
    """Dialog for adding a new torrent."""

    def __init__(self, settings: Settings, parent=None, torrent_path: str = "", magnet_uri: str = ""):
        super().__init__(parent)
        self._settings = settings
        self._torrent_path = torrent_path
        self._magnet_uri = magnet_uri
        self._torrent_info = None

        self.setWindowTitle("Add Torrent")
        self.setMinimumSize(580, 500)
        self.setModal(True)
        self._setup_ui()

        if torrent_path:
            self._load_torrent_file(torrent_path)
        if magnet_uri:
            self._source_edit.setText(magnet_uri)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(14)

        # --- Source ---
        source_group = QGroupBox("Source")
        source_layout = QVBoxLayout(source_group)

        # Torrent file / magnet URI input
        input_row = QHBoxLayout()
        self._source_edit = QLineEdit()
        self._source_edit.setPlaceholderText("Drag .torrent file here, paste magnet link, or browse...")
        self._source_edit.textChanged.connect(self._on_source_changed)
        input_row.addWidget(self._source_edit)

        browse_btn = QPushButton("Browse")
        browse_btn.setObjectName("primary")
        browse_btn.setFixedWidth(80)
        browse_btn.clicked.connect(self._browse_file)
        input_row.addWidget(browse_btn)
        source_layout.addLayout(input_row)

        # Torrent info
        self._info_label = QLabel("No torrent loaded")
        self._info_label.setStyleSheet(f"color: {tc('text_muted')}; font-size: 11px; padding: 4px 0;")
        source_layout.addWidget(self._info_label)

        layout.addWidget(source_group)

        # --- Save Location ---
        save_group = QGroupBox("Save Location")
        save_layout = QHBoxLayout(save_group)

        self._save_path_edit = QLineEdit(self._settings.get("default_save_path"))
        save_layout.addWidget(self._save_path_edit)

        save_browse = QPushButton("Browse")
        save_browse.setFixedWidth(80)
        save_browse.clicked.connect(self._browse_save_path)
        save_layout.addWidget(save_browse)

        layout.addWidget(save_group)

        # --- Options ---
        opts_group = QGroupBox("Options")
        opts_layout = QGridLayout(opts_group)
        opts_layout.setColumnMinimumWidth(0, 120)
        opts_layout.setColumnMinimumWidth(1, 200)

        # Category
        opts_layout.addWidget(QLabel("Category:"), 0, 0)
        self._category_combo = QComboBox()
        self._category_combo.setEditable(True)
        self._category_combo.addItem("(none)")
        for cat in self._settings.get_categories():
            name = cat["name"] if isinstance(cat, dict) else cat
            self._category_combo.addItem(name)
        opts_layout.addWidget(self._category_combo, 0, 1)

        # Tags
        opts_layout.addWidget(QLabel("Tags:"), 1, 0)
        self._tags_edit = QLineEdit()
        self._tags_edit.setPlaceholderText("Comma-separated tags")
        opts_layout.addWidget(self._tags_edit, 1, 1)

        # Checkboxes
        self._start_paused = QCheckBox("Start paused")
        opts_layout.addWidget(self._start_paused, 2, 0, 1, 2)

        self._sequential = QCheckBox("Sequential download")
        opts_layout.addWidget(self._sequential, 3, 0, 1, 2)

        self._skip_hash = QCheckBox("Skip hash check (if files exist)")
        opts_layout.addWidget(self._skip_hash, 4, 0, 1, 2)

        layout.addWidget(opts_group)

        # --- File List (for .torrent files) ---
        self._files_group = QGroupBox("Files")
        files_layout = QVBoxLayout(self._files_group)

        self._files_tree = QTreeWidget()
        self._files_tree.setHeaderLabels(["Name", "Size"])
        self._files_tree.setAlternatingRowColors(True)
        hdr = self._files_tree.header()
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        hdr.resizeSection(1, 90)
        files_layout.addWidget(self._files_tree)

        self._files_group.setVisible(False)
        layout.addWidget(self._files_group)

        # --- Buttons ---
        layout.addStretch()
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setFixedWidth(100)
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        self._add_btn = QPushButton("Add Torrent")
        self._add_btn.setObjectName("primary")
        self._add_btn.setFixedWidth(120)
        self._add_btn.clicked.connect(self.accept)
        btn_layout.addWidget(self._add_btn)

        layout.addLayout(btn_layout)

    def _browse_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Torrent File", "",
            "Torrent Files (*.torrent);;All Files (*)"
        )
        if path:
            self._source_edit.setText(path)
            self._load_torrent_file(path)

    def _browse_save_path(self):
        path = QFileDialog.getExistingDirectory(
            self, "Select Save Location",
            self._save_path_edit.text()
        )
        if path:
            self._save_path_edit.setText(path)

    def _on_source_changed(self, text):
        text = text.strip()
        if text.startswith("magnet:"):
            self._magnet_uri = text
            self._torrent_path = ""
            self._torrent_info = None
            self._info_label.setText("Magnet link detected - files will be shown after metadata download")
            self._info_label.setStyleSheet(f"color: {tc('accent')}; font-size: 11px;")
            self._files_group.setVisible(False)
        elif os.path.isfile(text) and text.endswith(".torrent"):
            self._load_torrent_file(text)

    def _load_torrent_file(self, path: str):
        try:
            import libtorrent as lt
            ti = lt.torrent_info(path)
            self._torrent_info = ti
            self._torrent_path = path
            self._magnet_uri = ""

            total_size = ti.total_size()
            num_files = ti.num_files()
            name = ti.name()

            self._source_edit.setText(path)
            self._info_label.setText(
                f"{name}  -  {format_bytes(total_size)}  -  {num_files} file{'s' if num_files != 1 else ''}"
            )
            self._info_label.setStyleSheet(f"color: {tc('green')}; font-size: 11px;")

            # Populate file tree
            self._files_tree.clear()
            fs = ti.files()
            for i in range(fs.num_files()):
                item = QTreeWidgetItem()
                item.setText(0, fs.file_path(i))
                item.setText(1, format_bytes(fs.file_size(i)))
                item.setCheckState(0, Qt.CheckState.Checked)
                item.setData(0, Qt.ItemDataRole.UserRole, i)
                self._files_tree.addTopLevelItem(item)

            self._files_group.setVisible(num_files > 0)

        except Exception as e:
            self._info_label.setText(f"Error: {e}")
            self._info_label.setStyleSheet(f"color: {tc('red')}; font-size: 11px;")

    # --- Result accessors ---

    @property
    def torrent_path(self) -> str:
        return self._torrent_path

    @property
    def magnet_uri(self) -> str:
        return self._magnet_uri

    @property
    def save_path(self) -> str:
        return self._save_path_edit.text().strip()

    @property
    def category(self) -> str:
        cat = self._category_combo.currentText()
        return "" if cat == "(none)" else cat

    @property
    def tags(self) -> list:
        text = self._tags_edit.text().strip()
        if not text:
            return []
        return [t.strip() for t in text.split(",") if t.strip()]

    @property
    def start_paused(self) -> bool:
        return self._start_paused.isChecked()

    @property
    def sequential_download(self) -> bool:
        return self._sequential.isChecked()

    @property
    def is_magnet(self) -> bool:
        return bool(self._magnet_uri)
