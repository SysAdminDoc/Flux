"""Create Torrent dialog - build .torrent files from local content."""

import os
import logging
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QFileDialog, QComboBox, QCheckBox, QTextEdit,
    QProgressBar, QGroupBox, QFormLayout, QSpinBox, QMessageBox,
)

from flux.gui.themes import c
from flux.utils.formatters import format_bytes

logger = logging.getLogger(__name__)

# Standard piece sizes
PIECE_SIZES = [
    (0, "Auto"),
    (16384, "16 KiB"),
    (32768, "32 KiB"),
    (65536, "64 KiB"),
    (131072, "128 KiB"),
    (262144, "256 KiB"),
    (524288, "512 KiB"),
    (1048576, "1 MiB"),
    (2097152, "2 MiB"),
    (4194304, "4 MiB"),
    (8388608, "8 MiB"),
    (16777216, "16 MiB"),
]


def _auto_piece_size(total_bytes: int) -> int:
    """Pick a piece size that targets ~1500 pieces."""
    if total_bytes <= 0:
        return 262144
    target = total_bytes // 1500
    # Clamp to powers of 2 between 16K and 16M
    size = 16384
    while size < target and size < 16777216:
        size *= 2
    return size


def _scan_files(path: str) -> list:
    """Return list of (relative_path, size) for all files under path."""
    p = Path(path)
    if p.is_file():
        return [(p.name, p.stat().st_size)]
    results = []
    for root, dirs, files in os.walk(path):
        for f in files:
            fp = Path(root) / f
            rel = fp.relative_to(p)
            results.append((str(rel), fp.stat().st_size))
    return results


class CreateWorker(QThread):
    """Background thread for torrent creation."""
    progress = pyqtSignal(int)
    finished = pyqtSignal(str)  # output path
    error = pyqtSignal(str)

    def __init__(self, source: str, output: str, trackers: list,
                 piece_size: int, private: bool, comment: str,
                 web_seeds: list):
        super().__init__()
        self.source = source
        self.output = output
        self.trackers = trackers
        self.piece_size = piece_size
        self.private = private
        self.comment = comment
        self.web_seeds = web_seeds

    def run(self):
        try:
            import libtorrent as lt
        except ImportError:
            self.error.emit("libtorrent not available")
            return

        try:
            fs = lt.file_storage()
            source = Path(self.source)

            if source.is_file():
                fs.add_file(source.name, source.stat().st_size)
                parent = str(source.parent)
            else:
                files = _scan_files(str(source))
                for rel, size in files:
                    fs.add_file(os.path.join(source.name, rel), size)
                parent = str(source.parent)

            total_size = fs.total_size()
            piece_size = self.piece_size if self.piece_size > 0 else _auto_piece_size(total_size)

            ct = lt.create_torrent(fs, piece_size)

            # Add trackers (tier 0 for first group, tier 1 for rest)
            for i, tracker in enumerate(self.trackers):
                tracker = tracker.strip()
                if tracker:
                    ct.add_tracker(tracker, i)

            # Web seeds
            for ws in self.web_seeds:
                ws = ws.strip()
                if ws:
                    ct.add_url_seed(ws)

            if self.comment:
                ct.set_comment(self.comment)

            ct.set_creator("Flux Torrent 1.0")

            if self.private:
                ct.set_priv(True)

            # Set piece hashes with progress callback
            def progress_cb(piece_num):
                total = ct.num_pieces()
                if total > 0:
                    pct = int((piece_num / total) * 100)
                    self.progress.emit(pct)

            lt.set_piece_hashes(ct, parent, progress_cb)

            # Write to file
            torrent_data = ct.generate()
            entry = lt.bencode(torrent_data)

            with open(self.output, 'wb') as f:
                f.write(entry)

            self.finished.emit(self.output)

        except Exception as e:
            logger.error(f"Torrent creation failed: {e}", exc_info=True)
            self.error.emit(str(e))


class CreateTorrentDialog(QDialog):
    """Dialog for creating .torrent files."""

    torrent_created = pyqtSignal(str)  # output path

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Create Torrent")
        self.setMinimumWidth(550)
        self.setMinimumHeight(500)
        self._worker = None
        self._setup_ui()
        self._apply_theme()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # --- Source ---
        src_group = QGroupBox("Source")
        src_layout = QVBoxLayout(src_group)

        row1 = QHBoxLayout()
        self._source_edit = QLineEdit()
        self._source_edit.setPlaceholderText("Select file or folder...")
        self._source_edit.setReadOnly(True)
        row1.addWidget(self._source_edit)

        self._btn_file = QPushButton("File...")
        self._btn_file.setFixedWidth(70)
        self._btn_file.clicked.connect(self._pick_file)
        row1.addWidget(self._btn_file)

        self._btn_folder = QPushButton("Folder...")
        self._btn_folder.setFixedWidth(70)
        self._btn_folder.clicked.connect(self._pick_folder)
        row1.addWidget(self._btn_folder)

        src_layout.addLayout(row1)

        self._file_info = QLabel("No source selected")
        self._file_info.setStyleSheet(f"color: {c('text_dim')}; font-size: 11px;")
        src_layout.addWidget(self._file_info)

        layout.addWidget(src_group)

        # --- Trackers ---
        tracker_group = QGroupBox("Trackers (one per line)")
        tracker_layout = QVBoxLayout(tracker_group)

        self._tracker_edit = QTextEdit()
        self._tracker_edit.setPlaceholderText(
            "udp://tracker.opentrackr.org:1337/announce\n"
            "udp://tracker.openbittorrent.com:6969/announce"
        )
        self._tracker_edit.setMaximumHeight(90)
        tracker_layout.addWidget(self._tracker_edit)

        layout.addWidget(tracker_group)

        # --- Options ---
        opt_group = QGroupBox("Options")
        opt_layout = QFormLayout(opt_group)

        self._piece_combo = QComboBox()
        for size, label in PIECE_SIZES:
            self._piece_combo.addItem(label, size)
        opt_layout.addRow("Piece size:", self._piece_combo)

        self._comment_edit = QLineEdit()
        self._comment_edit.setPlaceholderText("Optional comment")
        opt_layout.addRow("Comment:", self._comment_edit)

        self._private_check = QCheckBox("Private torrent (disables DHT/PEX)")
        opt_layout.addRow(self._private_check)

        layout.addWidget(opt_group)

        # --- Web Seeds ---
        ws_group = QGroupBox("Web Seeds (optional, one per line)")
        ws_layout = QVBoxLayout(ws_group)
        self._webseed_edit = QTextEdit()
        self._webseed_edit.setMaximumHeight(50)
        ws_layout.addWidget(self._webseed_edit)
        layout.addWidget(ws_group)

        # --- Output ---
        out_row = QHBoxLayout()
        out_row.addWidget(QLabel("Save as:"))
        self._output_edit = QLineEdit()
        self._output_edit.setPlaceholderText("output.torrent")
        out_row.addWidget(self._output_edit)
        self._btn_browse_out = QPushButton("Browse...")
        self._btn_browse_out.setFixedWidth(80)
        self._btn_browse_out.clicked.connect(self._pick_output)
        out_row.addWidget(self._btn_browse_out)
        layout.addLayout(out_row)

        # --- Progress ---
        self._progress = QProgressBar()
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        self._status_label = QLabel("")
        self._status_label.setStyleSheet(f"color: {c('text_dim')};")
        layout.addWidget(self._status_label)

        # --- Buttons ---
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        self._btn_create = QPushButton("Create Torrent")
        self._btn_create.setFixedWidth(140)
        self._btn_create.clicked.connect(self._start_create)
        btn_row.addWidget(self._btn_create)

        self._btn_cancel = QPushButton("Cancel")
        self._btn_cancel.setFixedWidth(80)
        self._btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(self._btn_cancel)

        layout.addLayout(btn_row)

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
            QLineEdit, QTextEdit, QComboBox, QSpinBox {{
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
            QCheckBox {{
                color: {c('text')};
            }}
            QProgressBar {{
                background-color: {c('bg_card')};
                border: 1px solid {c('border')};
                border-radius: 4px;
                text-align: center;
                color: {c('text')};
            }}
            QProgressBar::chunk {{
                background-color: {c('accent')};
                border-radius: 3px;
            }}
        """)

    def _pick_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select File")
        if path:
            self._set_source(path)

    def _pick_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Select Folder")
        if path:
            self._set_source(path)

    def _set_source(self, path: str):
        self._source_edit.setText(path)
        files = _scan_files(path)
        total = sum(s for _, s in files)
        count = len(files)
        self._file_info.setText(f"{count} file{'s' if count != 1 else ''}, {format_bytes(total)}")

        # Auto-suggest output name
        base = Path(path).stem
        default_out = str(Path(path).parent / f"{base}.torrent")
        self._output_edit.setText(default_out)

    def _pick_output(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Torrent", self._output_edit.text() or "output.torrent",
            "Torrent files (*.torrent)")
        if path:
            self._output_edit.setText(path)

    def _start_create(self):
        source = self._source_edit.text().strip()
        output = self._output_edit.text().strip()

        if not source or not os.path.exists(source):
            QMessageBox.warning(self, "Error", "Please select a valid source file or folder.")
            return
        if not output:
            QMessageBox.warning(self, "Error", "Please specify an output path.")
            return

        trackers = [t.strip() for t in self._tracker_edit.toPlainText().split('\n') if t.strip()]
        web_seeds = [w.strip() for w in self._webseed_edit.toPlainText().split('\n') if w.strip()]
        piece_size = self._piece_combo.currentData() or 0
        private = self._private_check.isChecked()
        comment = self._comment_edit.text().strip()

        self._btn_create.setEnabled(False)
        self._progress.setVisible(True)
        self._progress.setValue(0)
        self._status_label.setText("Hashing pieces...")

        self._worker = CreateWorker(source, output, trackers, piece_size,
                                    private, comment, web_seeds)
        self._worker.progress.connect(self._progress.setValue)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_finished(self, path: str):
        self._progress.setValue(100)
        self._status_label.setText(f"Created: {path}")
        self._btn_create.setEnabled(True)
        self.torrent_created.emit(path)
        QMessageBox.information(self, "Success", f"Torrent created:\n{path}")

    def _on_error(self, msg: str):
        self._progress.setVisible(False)
        self._status_label.setText(f"Error: {msg}")
        self._btn_create.setEnabled(True)
        QMessageBox.critical(self, "Error", f"Failed to create torrent:\n{msg}")
