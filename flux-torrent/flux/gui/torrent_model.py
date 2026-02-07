"""Torrent list model for QTableView.

Works with TorrentSnapshot dataclasses (thread-safe, no FFI).
Differential updates preserve scroll position and selection.
"""

from PyQt6.QtCore import Qt, QAbstractTableModel, QModelIndex, QSortFilterProxyModel
from PyQt6.QtGui import QColor, QFont

from flux.core.torrent import TorrentSnapshot, TorrentState
from flux.utils.formatters import format_bytes, format_speed, format_eta, format_ratio, format_progress


class TorrentListModel(QAbstractTableModel):
    """Model backing the main torrent list table."""

    COLUMNS = [
        ("", "state_icon", 32),
        ("Name", "name", 350),
        ("Size", "size", 80),
        ("Progress", "progress", 130),
        ("Status", "status", 100),
        ("Seeds", "seeds", 65),
        ("Peers", "peers", 65),
        ("DL Speed", "dl_speed", 90),
        ("UL Speed", "ul_speed", 90),
        ("ETA", "eta", 75),
        ("Ratio", "ratio", 60),
        ("Category", "category", 80),
        ("Added", "added", 130),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._snapshots: list[TorrentSnapshot] = []
        self._hash_index: dict[str, int] = {}  # info_hash -> row index
        self._mono_font = QFont("Consolas", 11)
        self._mono_font.setStyleHint(QFont.StyleHint.Monospace)

    def update_from_snapshots(self, snapshots: list[TorrentSnapshot]):
        """Differential sync from SessionStats.torrents list.

        Adds new, removes gone, updates existing without full reset.
        """
        new_hashes = {s.info_hash for s in snapshots}
        old_hashes = set(self._hash_index.keys())

        # Remove torrents that are gone
        removed = old_hashes - new_hashes
        if removed:
            remove_rows = sorted([self._hash_index[h] for h in removed], reverse=True)
            for row in remove_rows:
                self.beginRemoveRows(QModelIndex(), row, row)
                self._snapshots.pop(row)
                self.endRemoveRows()
            self._rebuild_index()

        # Build lookup for incoming snapshots
        snap_map = {s.info_hash: s for s in snapshots}

        # Update existing snapshots in-place
        for ih, row in self._hash_index.items():
            if ih in snap_map:
                self._snapshots[row] = snap_map[ih]

        # Add new torrents
        added = [s for s in snapshots if s.info_hash in (new_hashes - old_hashes)]
        if added:
            insert_at = len(self._snapshots)
            self.beginInsertRows(QModelIndex(), insert_at, insert_at + len(added) - 1)
            self._snapshots.extend(added)
            self.endInsertRows()
            self._rebuild_index()

        # Notify views that data changed for all existing rows
        if self._snapshots:
            top_left = self.index(0, 0)
            bottom_right = self.index(len(self._snapshots) - 1, len(self.COLUMNS) - 1)
            self.dataChanged.emit(top_left, bottom_right)

    def set_snapshots(self, snapshots: list[TorrentSnapshot]):
        """Full reset - used only when model first loads or count mismatch."""
        self.beginResetModel()
        self._snapshots = list(snapshots)
        self._rebuild_index()
        self.endResetModel()

    def _rebuild_index(self):
        self._hash_index = {s.info_hash: i for i, s in enumerate(self._snapshots)}

    def refresh(self):
        """Emit dataChanged for all visible rows."""
        if self._snapshots:
            top_left = self.index(0, 0)
            bottom_right = self.index(len(self._snapshots) - 1, len(self.COLUMNS) - 1)
            self.dataChanged.emit(top_left, bottom_right)

    def get_snapshot(self, row: int) -> TorrentSnapshot | None:
        if 0 <= row < len(self._snapshots):
            return self._snapshots[row]
        return None

    def get_info_hash(self, row: int) -> str | None:
        s = self.get_snapshot(row)
        return s.info_hash if s else None

    def find_row(self, info_hash: str) -> int:
        return self._hash_index.get(info_hash, -1)

    def find_snapshot(self, info_hash: str) -> TorrentSnapshot | None:
        row = self._hash_index.get(info_hash, -1)
        if 0 <= row < len(self._snapshots):
            return self._snapshots[row]
        return None

    # --- QAbstractTableModel interface ---

    def rowCount(self, parent=QModelIndex()):
        return len(self._snapshots)

    def columnCount(self, parent=QModelIndex()):
        return len(self.COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.COLUMNS[section][0]
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None

        row = index.row()
        if row < 0 or row >= len(self._snapshots):
            return None

        snap = self._snapshots[row]
        col_key = self.COLUMNS[index.column()][1]

        if role == Qt.ItemDataRole.DisplayRole:
            return self._display_data(snap, col_key)
        elif role == Qt.ItemDataRole.ForegroundRole:
            return self._foreground(snap, col_key)
        elif role == Qt.ItemDataRole.FontRole:
            if col_key in ("dl_speed", "ul_speed", "eta", "ratio", "size", "seeds", "peers"):
                return self._mono_font
        elif role == Qt.ItemDataRole.TextAlignmentRole:
            if col_key in ("size", "dl_speed", "ul_speed", "eta", "ratio", "seeds", "peers"):
                return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if col_key in ("progress", "status", "category"):
                return int(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
        elif role == Qt.ItemDataRole.UserRole:
            return self._sort_value(snap, col_key)
        elif role == Qt.ItemDataRole.UserRole + 1:
            if col_key == "progress":
                return snap.progress
        elif role == Qt.ItemDataRole.UserRole + 2:
            return snap.state
        elif role == Qt.ItemDataRole.UserRole + 3:
            return snap.info_hash

        return None

    def _display_data(self, s: TorrentSnapshot, col: str) -> str:
        try:
            if col == "state_icon":
                return ""
            elif col == "name":
                return s.name
            elif col == "size":
                return format_bytes(s.total_size) if s.total_size > 0 else "--"
            elif col == "progress":
                return format_progress(s.progress)
            elif col == "status":
                return s.state.display_name
            elif col == "seeds":
                return str(s.num_seeds)
            elif col == "peers":
                return str(s.num_peers)
            elif col == "dl_speed":
                return format_speed(s.download_speed)
            elif col == "ul_speed":
                return format_speed(s.upload_speed)
            elif col == "eta":
                return format_eta(s.eta)
            elif col == "ratio":
                return format_ratio(s.ratio)
            elif col == "category":
                return s.category or "--"
            elif col == "added":
                from flux.utils.formatters import format_timestamp
                return format_timestamp(s.added_time)
        except Exception:
            return "--"
        return ""

    def _foreground(self, s: TorrentSnapshot, col: str) -> QColor | None:
        from flux.gui.themes import c, state_color
        if col == "status":
            return QColor(state_color(s.state))
        if col == "dl_speed" and s.download_speed > 0:
            return QColor(c("accent"))
        if col == "ul_speed" and s.upload_speed > 0:
            return QColor(c("green"))
        if col == "category" and s.category:
            return QColor(c("purple"))
        return None

    def _sort_value(self, s: TorrentSnapshot, col: str):
        try:
            if col == "name":
                return s.name.lower()
            elif col == "size":
                return s.total_size
            elif col == "progress":
                return s.progress
            elif col == "status":
                return s.state.value
            elif col == "seeds":
                return s.num_seeds
            elif col == "peers":
                return s.num_peers
            elif col == "dl_speed":
                return s.download_speed
            elif col == "ul_speed":
                return s.upload_speed
            elif col == "eta":
                return s.eta if s.eta > 0 else 999999999
            elif col == "ratio":
                return s.ratio
            elif col == "category":
                return s.category or "zzz"
            elif col == "added":
                return s.added_time
        except Exception:
            return 0
        return 0


class TorrentSortFilterProxy(QSortFilterProxyModel):
    """Proxy model for sorting and filtering the torrent list."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._filter_state: TorrentState | None = None
        self._filter_category: str = ""
        self._filter_text: str = ""
        self.setSortRole(Qt.ItemDataRole.UserRole)

    def set_state_filter(self, state: TorrentState | None):
        self._filter_state = state
        self.invalidateFilter()

    def set_category_filter(self, category: str):
        self._filter_category = category
        self.invalidateFilter()

    def set_text_filter(self, text: str):
        self._filter_text = text.lower()
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, source_parent):
        model = self.sourceModel()
        snap = model.get_snapshot(source_row)
        if not snap:
            return False

        try:
            if self._filter_state is not None:
                state = snap.state
                if self._filter_state == TorrentState.DOWNLOADING:
                    if state not in (TorrentState.DOWNLOADING, TorrentState.STALLED, TorrentState.METADATA):
                        return False
                elif self._filter_state == TorrentState.SEEDING:
                    if state not in (TorrentState.SEEDING, TorrentState.COMPLETED):
                        return False
                elif state != self._filter_state:
                    return False

            if self._filter_category:
                if snap.category != self._filter_category:
                    return False

            if self._filter_text:
                if self._filter_text not in snap.name.lower():
                    return False
        except Exception:
            return True

        return True
