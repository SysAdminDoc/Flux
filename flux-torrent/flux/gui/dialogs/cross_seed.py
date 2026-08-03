"""Cross-seed helper dialog."""

from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from flux.core.cross_seed import find_cross_seed_matches, scan_torrent_files, verify_library_content
from flux.gui.themes import c


class CrossSeedWorker(QThread):
    """Run metadata scanning and optional library verification off the GUI thread."""

    scan_finished = pyqtSignal(object, object, int)
    failed = pyqtSignal(str)

    def __init__(self, metadata_root: str, library_root: str, verify: bool):
        super().__init__()
        self._metadata_root = metadata_root
        self._library_root = library_root
        self._verify = verify

    def run(self):
        try:
            descriptors = scan_torrent_files(self._metadata_root)
            verified = 0
            if self._verify and self._library_root:
                for descriptor in descriptors:
                    if verify_library_content(descriptor, self._library_root):
                        verified += 1
            matches = find_cross_seed_matches(descriptors)
            self.scan_finished.emit(descriptors, matches, verified)
        except Exception as exc:
            self.failed.emit(str(exc))


class CrossSeedDialog(QDialog):
    """Find torrent metadata pairs that can share downloaded content."""

    def __init__(self, default_library: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Cross-seed Helper")
        self.setMinimumSize(900, 560)
        self._worker = None
        self._setup_ui(default_library)
        self._apply_theme()

    def _setup_ui(self, default_library: str):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        paths = QGroupBox("Library scan")
        paths_layout = QVBoxLayout(paths)
        metadata_row = QHBoxLayout()
        metadata_row.addWidget(QLabel("Torrent metadata:"))
        self._metadata_edit = QLineEdit(default_library)
        self._metadata_edit.setPlaceholderText("Folder containing .torrent files")
        metadata_row.addWidget(self._metadata_edit)
        metadata_button = QPushButton("Browse...")
        metadata_button.clicked.connect(lambda: self._browse(self._metadata_edit))
        metadata_row.addWidget(metadata_button)
        paths_layout.addLayout(metadata_row)

        library_row = QHBoxLayout()
        library_row.addWidget(QLabel("Downloaded library:"))
        self._library_edit = QLineEdit(default_library)
        self._library_edit.setPlaceholderText("Optional content root for piece verification")
        library_row.addWidget(self._library_edit)
        library_button = QPushButton("Browse...")
        library_button.clicked.connect(lambda: self._browse(self._library_edit))
        library_row.addWidget(library_button)
        paths_layout.addLayout(library_row)

        options_row = QHBoxLayout()
        self._verify_check = QCheckBox("Verify source files against piece hashes")
        self._verify_check.setChecked(True)
        options_row.addWidget(self._verify_check)
        self._scan_button = QPushButton("Scan")
        self._scan_button.clicked.connect(self._scan)
        options_row.addWidget(self._scan_button)
        options_row.addStretch()
        paths_layout.addLayout(options_row)
        layout.addWidget(paths)

        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(
            ["Source", "Candidate", "Match", "Confidence", "Candidate trackers"]
        )
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self._table.setColumnWidth(2, 105)
        self._table.setColumnWidth(3, 85)
        layout.addWidget(self._table)

        self._status = QLabel("Choose a metadata folder and scan for candidates.")
        layout.addWidget(self._status)

    def _apply_theme(self):
        self.setStyleSheet(f"""
            QDialog {{ background-color: {c('bg')}; color: {c('text')}; }}
            QGroupBox {{ color: {c('text')}; border: 1px solid {c('border')};
                         border-radius: 6px; margin-top: 8px; padding-top: 12px; }}
            QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; }}
            QLineEdit, QTableWidget {{ background-color: {c('bg_card')}; color: {c('text')};
                                      border: 1px solid {c('border')}; border-radius: 4px; }}
            QHeaderView::section {{ background-color: {c('bg_hover')}; color: {c('text')};
                                    border: 1px solid {c('border')}; padding: 4px; }}
            QPushButton {{ background-color: {c('bg_hover')}; color: {c('text')};
                          border: 1px solid {c('border')}; border-radius: 4px; padding: 6px 12px; }}
            QPushButton:hover {{ background-color: {c('accent')}; color: #ffffff; }}
            QCheckBox, QLabel {{ color: {c('text')}; }}
        """)

    @staticmethod
    def _browse(edit: QLineEdit):
        path = QFileDialog.getExistingDirectory(edit, "Select folder", edit.text())
        if path:
            edit.setText(path)

    def _scan(self):
        metadata_root = self._metadata_edit.text().strip()
        if not metadata_root or not Path(metadata_root).exists():
            self._status.setText("Select an existing metadata folder first.")
            return
        if self._worker and self._worker.isRunning():
            return
        self._scan_button.setEnabled(False)
        self._table.setRowCount(0)
        self._status.setText("Scanning torrent metadata...")
        self._worker = CrossSeedWorker(
            metadata_root,
            self._library_edit.text().strip(),
            self._verify_check.isChecked(),
        )
        self._worker.scan_finished.connect(self._on_scan_finished)
        self._worker.failed.connect(self._on_scan_failed)
        self._worker.scan_finished.connect(lambda *_: self._scan_button.setEnabled(True))
        self._worker.failed.connect(lambda *_: self._scan_button.setEnabled(True))
        self._worker.start()

    def _on_scan_finished(self, descriptors, matches, verified: int):
        self._table.setRowCount(len(matches))
        for row, match in enumerate(matches):
            values = [
                Path(match.source_path).name,
                Path(match.candidate_path).name,
                match.method,
                f"{match.confidence:.0%}",
                ", ".join(match.candidate_trackers) or "(none)",
            ]
            for column, value in enumerate(values):
                self._table.setItem(row, column, QTableWidgetItem(value))
        self._status.setText(
            f"Scanned {len(descriptors)} torrent files; {verified} verified against the library; "
            f"{len(matches)} cross-seed candidate pair(s)."
        )

    def _on_scan_failed(self, message: str):
        self._status.setText(f"Scan failed: {message}")

    def closeEvent(self, event):
        if self._worker and self._worker.isRunning():
            self._worker.wait(2000)
        event.accept()
