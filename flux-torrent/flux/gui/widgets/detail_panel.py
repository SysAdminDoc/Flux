"""Detail panel - bottom slide-up panel showing selected torrent info.

All data comes from thread-safe dataclasses (TorrentSnapshot, DetailData).
No direct libtorrent FFI calls from the GUI thread.
"""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QColor
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTabWidget,
    QTableWidget, QTableWidgetItem, QTreeWidget, QTreeWidgetItem,
    QHeaderView, QFrame, QGridLayout, QPushButton, QMenu,
    QInputDialog,
)

from flux.core.torrent import TorrentSnapshot, TorrentState
from flux.gui.widgets.speed_graph import SpeedGraphWidget
from flux.gui.widgets.piece_map import PieceMapWidget
from flux.utils.formatters import format_bytes, format_speed, format_eta, format_ratio, format_progress
import flux.gui.themes as themes


def _tc(key):
    return themes.c(key)


_PRIORITY_LABELS = {0: "Skip", 1: "Low", 4: "Normal", 7: "High"}
_PRIORITY_COLORS = {0: "text_muted", 1: "orange", 4: "text", 7: "accent"}


class DetailPanel(QWidget):
    """Bottom panel showing details of the selected torrent.

    Signals emitted to request mutations (handled by MainWindow -> Worker):
        file_priority_requested(info_hash, file_index, priority)
        add_tracker_requested(info_hash, url)
        remove_tracker_requested(info_hash, url)
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_hash: str = ""
        self._current_snap: TorrentSnapshot | None = None
        self._current_detail = None  # DetailData or None
        self.setMinimumHeight(0)
        self._setup_ui()
        self.apply_theme()
        self.hide()

        # Callbacks set by MainWindow to dispatch mutations to worker
        self.on_set_file_priority = None   # (info_hash, file_index, priority) -> None
        self.on_add_tracker = None         # (info_hash, url) -> None
        self.on_remove_tracker = None      # (info_hash, url) -> None

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._handle = QFrame()
        self._handle.setFixedHeight(1)
        layout.addWidget(self._handle)

        self._header = QWidget()
        header_layout = QHBoxLayout(self._header)
        header_layout.setContentsMargins(16, 6, 8, 6)

        self._name_label = QLabel("No torrent selected")
        header_layout.addWidget(self._name_label)
        header_layout.addStretch()

        self._close_btn = QPushButton("X")
        self._close_btn.setFixedSize(24, 24)
        self._close_btn.clicked.connect(self._close_panel)
        header_layout.addWidget(self._close_btn)
        layout.addWidget(self._header)

        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)
        layout.addWidget(self._tabs)

        # --- Overview Tab ---
        self._overview = QWidget()
        self._setup_overview()
        self._tabs.addTab(self._overview, "Overview")

        # --- Files Tab ---
        self._files_table = QTreeWidget()
        self._files_table.setHeaderLabels(["Name", "Size", "Progress", "Priority"])
        self._files_table.setAlternatingRowColors(True)
        self._files_table.setRootIsDecorated(True)
        self._files_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._files_table.customContextMenuRequested.connect(self._show_file_context_menu)
        hdr = self._files_table.header()
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        hdr.resizeSection(1, 90)
        hdr.resizeSection(2, 80)
        hdr.resizeSection(3, 80)
        self._tabs.addTab(self._files_table, "Files")

        # --- Peers Tab ---
        self._peers_table = QTableWidget()
        self._peers_table.setColumnCount(7)
        self._peers_table.setHorizontalHeaderLabels(
            ["IP", "Client", "Flags", "DL Speed", "UL Speed", "Downloaded", "Uploaded"]
        )
        self._peers_table.horizontalHeader().setStretchLastSection(True)
        self._peers_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._peers_table.verticalHeader().setVisible(False)
        self._peers_table.setAlternatingRowColors(True)
        self._tabs.addTab(self._peers_table, "Peers")

        # --- Trackers Tab ---
        trackers_widget = QWidget()
        trackers_layout = QVBoxLayout(trackers_widget)
        trackers_layout.setContentsMargins(0, 0, 0, 0)
        trackers_layout.setSpacing(0)

        tracker_toolbar = QHBoxLayout()
        tracker_toolbar.setContentsMargins(8, 4, 8, 4)
        self._add_tracker_btn = QPushButton("+ Add Tracker")
        self._add_tracker_btn.setFixedHeight(26)
        self._add_tracker_btn.clicked.connect(self._on_add_tracker)
        tracker_toolbar.addWidget(self._add_tracker_btn)
        self._remove_tracker_btn = QPushButton("Remove Selected")
        self._remove_tracker_btn.setFixedHeight(26)
        self._remove_tracker_btn.clicked.connect(self._on_remove_tracker)
        tracker_toolbar.addWidget(self._remove_tracker_btn)
        tracker_toolbar.addStretch()
        trackers_layout.addLayout(tracker_toolbar)

        self._trackers_table = QTableWidget()
        self._trackers_table.setColumnCount(5)
        self._trackers_table.setHorizontalHeaderLabels(
            ["URL", "Status", "Seeds", "Peers", "Message"]
        )
        self._trackers_table.horizontalHeader().setStretchLastSection(True)
        self._trackers_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._trackers_table.verticalHeader().setVisible(False)
        self._trackers_table.setAlternatingRowColors(True)
        self._trackers_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        trackers_layout.addWidget(self._trackers_table)

        self._tabs.addTab(trackers_widget, "Trackers")

        # --- Pieces Tab ---
        self._pieces_widget = QWidget()
        pieces_layout = QVBoxLayout(self._pieces_widget)
        pieces_layout.setContentsMargins(12, 12, 12, 12)
        self._piece_info_label = QLabel("No piece information available")
        pieces_layout.addWidget(self._piece_info_label)
        self._pieces_map = PieceMapWidget()
        pieces_layout.addWidget(self._pieces_map)
        pieces_layout.addStretch()
        self._tabs.addTab(self._pieces_widget, "Pieces")

    def apply_theme(self):
        self._handle.setStyleSheet(f"background-color: {_tc('border')};")
        self._header.setStyleSheet(f"background-color: {_tc('bg_card')};")
        self._name_label.setStyleSheet(f"font-weight: 600; font-size: 13px; color: {_tc('text')};")
        self._close_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {_tc('text_dim')};
                border: none; border-radius: 4px;
                font-size: 12px; font-weight: 700;
            }}
            QPushButton:hover {{ background: {_tc('bg_hover')}; color: {_tc('text')}; }}
        """)
        self._piece_info_label.setStyleSheet(f"color: {_tc('text_muted')}; font-size: 11px;")

        btn_style = f"""
            QPushButton {{
                background: {_tc('bg_hover')}; color: {_tc('text')};
                border: 1px solid {_tc('border')}; border-radius: 4px;
                padding: 2px 10px; font-size: 11px;
            }}
            QPushButton:hover {{ background: {_tc('accent')}; color: #fff; }}
        """
        self._add_tracker_btn.setStyleSheet(btn_style)
        self._remove_tracker_btn.setStyleSheet(btn_style)

        if hasattr(self, '_speed_graph'):
            self._speed_graph.apply_theme()
        if hasattr(self, '_pieces_map'):
            self._pieces_map.apply_theme()

        if hasattr(self, '_label_widgets'):
            label_style = f"color: {_tc('text_dim')}; font-size: 10px; font-weight: 600;"
            for lbl in self._label_widgets:
                lbl.setStyleSheet(label_style)

    def _setup_overview(self):
        layout = QHBoxLayout(self._overview)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(16)

        stats = QWidget()
        grid = QGridLayout(stats)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(6)
        grid.setColumnMinimumWidth(0, 100)
        grid.setColumnMinimumWidth(1, 120)
        grid.setColumnMinimumWidth(2, 100)
        grid.setColumnMinimumWidth(3, 120)

        mono = QFont("Consolas", 11)
        mono.setStyleHint(QFont.StyleHint.Monospace)
        value_style = "font-size: 12px;"

        self._stat_labels = {}
        self._label_widgets = []
        stat_defs = [
            (0, 0, "Status:"), (0, 1, "_status"),
            (0, 2, "DL Speed:"), (0, 3, "_dl_speed"),
            (1, 0, "Progress:"), (1, 1, "_progress"),
            (1, 2, "UL Speed:"), (1, 3, "_ul_speed"),
            (2, 0, "Downloaded:"), (2, 1, "_downloaded"),
            (2, 2, "Uploaded:"), (2, 3, "_uploaded"),
            (3, 0, "Size:"), (3, 1, "_size"),
            (3, 2, "Ratio:"), (3, 3, "_ratio"),
            (4, 0, "Seeds:"), (4, 1, "_seeds"),
            (4, 2, "Peers:"), (4, 3, "_peers"),
            (5, 0, "ETA:"), (5, 1, "_eta"),
            (5, 2, "Category:"), (5, 3, "_category"),
            (6, 0, "Save Path:"), (6, 1, "_save_path"),
        ]

        for r, col, text in stat_defs:
            if text.startswith("_"):
                lbl = QLabel("--")
                lbl.setStyleSheet(value_style)
                lbl.setFont(mono)
                self._stat_labels[text] = lbl
                grid.addWidget(lbl, r, col)
            else:
                lbl = QLabel(text)
                self._label_widgets.append(lbl)
                grid.addWidget(lbl, r, col)

        if "_save_path" in self._stat_labels:
            grid.addWidget(self._stat_labels["_save_path"], 6, 1, 1, 3)

        layout.addWidget(stats, stretch=2)

        right = QVBoxLayout()
        graph_label = QLabel("TRANSFER SPEED")
        graph_label.setObjectName("sectionTitle")
        right.addWidget(graph_label)

        self._speed_graph = SpeedGraphWidget()
        self._speed_graph.setMinimumHeight(120)
        right.addWidget(self._speed_graph)
        right.addStretch()
        layout.addLayout(right, stretch=1)

    # --- Public API ---

    def set_torrent(self, snap: TorrentSnapshot | None):
        """Called when selection changes. snap is from the model."""
        if snap:
            self._current_hash = snap.info_hash
            self._current_snap = snap
            self._name_label.setText(snap.name)
            self.show()
            self._refresh_overview(snap)
        else:
            self._current_hash = ""
            self._current_snap = None
            self._current_detail = None
            self.hide()

    def update_detail(self, detail):
        """Called when SessionWorker emits detail_updated(DetailData)."""
        if detail.info_hash != self._current_hash:
            return
        self._current_detail = detail

        tab = self._tabs.currentIndex()
        if tab == 1:
            self._refresh_files(detail.files)
        elif tab == 2:
            self._refresh_peers(detail.peers)
        elif tab == 3:
            self._refresh_trackers(detail.trackers)
        elif tab == 4:
            self._refresh_pieces(detail.pieces, detail.piece_length)

        self._speed_graph.set_data(detail.dl_history, detail.ul_history)

    def refresh_from_snapshot(self, snap: TorrentSnapshot):
        """Called each stats cycle with the latest snapshot for the focused torrent."""
        if snap.info_hash != self._current_hash:
            return
        self._current_snap = snap
        self._refresh_overview(snap)

    def _refresh_overview(self, s: TorrentSnapshot):
        self._stat_labels.get("_status", QLabel()).setText(s.state.display_name)
        self._stat_labels.get("_status", QLabel()).setStyleSheet(
            f"color: {themes.state_color(s.state)}; font-size: 12px;"
        )
        self._stat_labels.get("_progress", QLabel()).setText(format_progress(s.progress))
        self._stat_labels.get("_dl_speed", QLabel()).setText(format_speed(s.download_speed))
        self._stat_labels.get("_ul_speed", QLabel()).setText(format_speed(s.upload_speed))
        self._stat_labels.get("_downloaded", QLabel()).setText(format_bytes(s.total_downloaded))
        self._stat_labels.get("_uploaded", QLabel()).setText(format_bytes(s.total_uploaded))
        self._stat_labels.get("_size", QLabel()).setText(
            format_bytes(s.total_size) if s.total_size > 0 else "--"
        )
        self._stat_labels.get("_ratio", QLabel()).setText(format_ratio(s.ratio))
        self._stat_labels.get("_seeds", QLabel()).setText(str(s.num_seeds))
        self._stat_labels.get("_peers", QLabel()).setText(str(s.num_peers))
        self._stat_labels.get("_eta", QLabel()).setText(format_eta(s.eta))
        self._stat_labels.get("_category", QLabel()).setText(s.category or "--")
        self._stat_labels.get("_save_path", QLabel()).setText(s.save_path)

    # --- Files Tab ---

    def _refresh_files(self, files: list):
        if not files:
            self._files_table.clear()
            self._files_table.setHeaderLabels(["Name", "Size", "Progress", "Priority"])
            return

        self._files_table.clear()
        self._files_table.setHeaderLabels(["Name", "Size", "Progress", "Priority"])

        tree: dict = {}
        for f in files:
            parts = f.path.replace("\\", "/").split("/")
            node = tree
            for part in parts[:-1]:
                node = node.setdefault(part, {"__children__": {}, "__files__": []})
                node = node["__children__"] if "__children__" in node else node
            leaf_name = parts[-1] if parts else f.path
            if "__files__" not in node:
                node["__files__"] = []
            node["__files__"].append((leaf_name, f))

        def _collect_files(node: dict) -> list:
            result = []
            for name, tf in node.get("__files__", []):
                result.append(tf)
            for child_name in sorted(node.get("__children__", {}).keys()):
                result.extend(_collect_files(node["__children__"][child_name]))
            return result

        def _add_node(parent_item, node: dict):
            children = node.get("__children__", {})
            for folder_name in sorted(children.keys()):
                child_node = children[folder_name]
                folder_item = QTreeWidgetItem()
                folder_item.setText(0, folder_name)

                all_files = _collect_files(child_node)
                total_size = sum(f.size for f in all_files)
                avg_progress = sum(f.progress for f in all_files) / len(all_files) if all_files else 0

                folder_item.setText(1, format_bytes(total_size))
                folder_item.setText(2, f"{avg_progress:.1f}%")
                folder_item.setText(3, "")
                folder_item.setData(0, Qt.ItemDataRole.UserRole, [f.index for f in all_files])

                if parent_item is None:
                    self._files_table.addTopLevelItem(folder_item)
                else:
                    parent_item.addChild(folder_item)

                _add_node(folder_item, child_node)

            for file_name, tf in sorted(node.get("__files__", []), key=lambda x: x[0]):
                file_item = QTreeWidgetItem()
                file_item.setText(0, file_name)
                file_item.setText(1, format_bytes(tf.size))
                file_item.setText(2, f"{tf.progress * 100:.1f}%")
                priority_label = _PRIORITY_LABELS.get(tf.priority, "Normal")
                file_item.setText(3, priority_label)
                file_item.setData(0, Qt.ItemDataRole.UserRole, [tf.index])
                color_key = _PRIORITY_COLORS.get(tf.priority, "text")
                file_item.setForeground(3, QColor(_tc(color_key)))

                if parent_item is None:
                    self._files_table.addTopLevelItem(file_item)
                else:
                    parent_item.addChild(file_item)

        _add_node(None, tree)
        self._files_table.expandAll()

    def _show_file_context_menu(self, pos):
        if not self._current_hash:
            return
        items = self._files_table.selectedItems()
        if not items:
            return

        menu = QMenu(self)
        menu.addAction("High Priority").triggered.connect(lambda: self._set_file_priority(items, 7))
        menu.addAction("Normal Priority").triggered.connect(lambda: self._set_file_priority(items, 4))
        menu.addAction("Low Priority").triggered.connect(lambda: self._set_file_priority(items, 1))
        menu.addSeparator()
        menu.addAction("Skip (Don't Download)").triggered.connect(lambda: self._set_file_priority(items, 0))
        menu.exec(self._files_table.viewport().mapToGlobal(pos))

    def _set_file_priority(self, items, priority: int):
        if not self._current_hash or not self.on_set_file_priority:
            return

        file_indices = set()
        for item in items:
            indices = item.data(0, Qt.ItemDataRole.UserRole)
            if indices:
                file_indices.update(indices)

        for idx in file_indices:
            self.on_set_file_priority(self._current_hash, idx, priority)

    # --- Peers Tab ---

    def _refresh_peers(self, peers: list):
        self._peers_table.setRowCount(len(peers))

        mono = QFont("Consolas", 11)
        mono.setStyleHint(QFont.StyleHint.Monospace)

        for i, p in enumerate(peers):
            texts = [
                p.ip, p.client, p.flags,
                format_speed(p.dl_speed), format_speed(p.ul_speed),
                format_bytes(p.downloaded), format_bytes(p.uploaded)
            ]
            for j, text in enumerate(texts):
                item = QTableWidgetItem(text)
                if j >= 3:
                    item.setFont(mono)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self._peers_table.setItem(i, j, item)

    # --- Trackers Tab ---

    def _refresh_trackers(self, trackers: list):
        self._trackers_table.setRowCount(len(trackers))

        for i, tr in enumerate(trackers):
            items = [tr.url, tr.status, str(tr.seeds), str(tr.peers), tr.message]
            for j, text in enumerate(items):
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if j == 1:
                    if tr.status == "Working":
                        color = _tc("green")
                    elif tr.status == "Error":
                        color = _tc("red")
                    else:
                        color = _tc("text_muted")
                    item.setForeground(QColor(color))
                self._trackers_table.setItem(i, j, item)

    def _on_add_tracker(self):
        if not self._current_hash:
            return
        url, ok = QInputDialog.getText(self, "Add Tracker", "Tracker URL:")
        if ok and url.strip() and self.on_add_tracker:
            self.on_add_tracker(self._current_hash, url.strip())

    def _on_remove_tracker(self):
        if not self._current_hash:
            return
        selected = self._trackers_table.selectedItems()
        if not selected:
            return
        row = selected[0].row()
        url_item = self._trackers_table.item(row, 0)
        if url_item and self.on_remove_tracker:
            self.on_remove_tracker(self._current_hash, url_item.text())

    # --- Pieces Tab ---

    def _refresh_pieces(self, pieces: list, piece_length: int = 0):
        if pieces:
            total = len(pieces)
            have = pieces.count(2)
            downloading = pieces.count(1)
            missing = pieces.count(0)
            self._piece_info_label.setText(
                f"{total} pieces  |  {have} complete  |  {downloading} downloading  |  {missing} missing  |  "
                f"Piece size: {format_bytes(piece_length)}"
            )
            self._pieces_map.set_pieces(pieces)
        else:
            self._piece_info_label.setText("No piece information available")
            self._pieces_map.set_pieces([])

    def _close_panel(self):
        self._current_hash = ""
        self._current_snap = None
        self._current_detail = None
        self.hide()
