"""Main application window for Flux Torrent Client.

All libtorrent operations run on a dedicated worker thread.
GUI communicates via signals (GUI->worker) and consumes
thread-safe snapshots (worker->GUI).
"""

import os
import json
import shutil
import logging

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeySequence, QFont, QColor, QDragEnterEvent, QDropEvent, QPalette
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QToolBar, QToolButton, QSplitter, QTableView, QHeaderView,
    QStatusBar, QFrame, QApplication, QMessageBox,
    QInputDialog, QMenu, QSystemTrayIcon, QStyle, QAbstractItemView,
    QSizePolicy, QLineEdit, QDialog, QFormLayout,
    QSpinBox, QDialogButtonBox
)

from flux.core.session_worker import ThreadedSession, SessionStats, DetailData
from flux.core.settings import Settings
from flux.core.torrent import TorrentState
from flux.gui.torrent_model import TorrentListModel, TorrentSortFilterProxy
from flux.gui.widgets.delegates import ProgressBarDelegate, StateIconDelegate
from flux.gui.widgets.sidebar import SidebarWidget
from flux.gui.widgets.detail_panel import DetailPanel
from flux.gui.widgets.speed_graph import SparklineWidget
from flux.gui.dialogs.add_torrent import AddTorrentDialog
from flux.gui.dialogs.settings_dialog import SettingsDialog
from flux.gui.dialogs.create_torrent import CreateTorrentDialog
from flux.gui.dialogs.rss_manager import RSSManagerDialog
from flux.gui.themes import get_stylesheet, get_palette, set_current as set_theme, c as tc
from flux.utils.formatters import format_speed, format_bytes

logger = logging.getLogger(__name__)


class SpeedLimitDialog(QDialog):
    """Dialog for setting per-torrent speed limits."""

    def __init__(self, name: str, dl_limit: int = 0, ul_limit: int = 0, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Speed Limits - {name[:50]}")
        self.setMinimumWidth(320)

        layout = QFormLayout(self)

        self._dl_spin = QSpinBox()
        self._dl_spin.setRange(0, 999999)
        self._dl_spin.setSuffix(" KiB/s")
        self._dl_spin.setSpecialValueText("Unlimited")
        self._dl_spin.setValue(dl_limit // 1024 if dl_limit > 0 else 0)
        layout.addRow("Download Limit:", self._dl_spin)

        self._ul_spin = QSpinBox()
        self._ul_spin.setRange(0, 999999)
        self._ul_spin.setSuffix(" KiB/s")
        self._ul_spin.setSpecialValueText("Unlimited")
        self._ul_spin.setValue(ul_limit // 1024 if ul_limit > 0 else 0)
        layout.addRow("Upload Limit:", self._ul_spin)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    @property
    def dl_limit(self) -> int:
        return self._dl_spin.value() * 1024

    @property
    def ul_limit(self) -> int:
        return self._ul_spin.value() * 1024


class MainWindow(QMainWindow):
    """Flux Torrent Client main window."""

    def __init__(self):
        super().__init__()

        self._settings = Settings()
        self._selected_info_hash: str | None = None
        self._tray: QSystemTrayIcon | None = None
        self._last_stats: SessionStats | None = None
        self._rss_monitor = None

        self.setWindowTitle("Flux Torrent")
        self.setMinimumSize(1100, 700)
        self.resize(1280, 800)
        self.setAcceptDrops(True)

        self._setup_menu_bar()
        self._setup_ui()
        self._setup_toolbar()
        self._setup_status_bar()
        self._setup_context_menu()
        self._setup_system_tray()

        # Create threaded session and connect signals
        self._threaded = ThreadedSession(self._settings)
        self._worker = self._threaded.worker
        self._connect_signals()
        self._threaded.start()

        self._sidebar.update_categories(self._settings.get_categories())

    # ------------------------------------------------------------------ #
    #  Menu Bar
    # ------------------------------------------------------------------ #

    def _setup_menu_bar(self):
        menubar = self.menuBar()

        # --- File ---
        file_menu = menubar.addMenu("&File")

        add_torrent = file_menu.addAction("&Add Torrent File...")
        add_torrent.setShortcut(QKeySequence("Ctrl+O"))
        add_torrent.triggered.connect(self._on_add_torrent)

        add_magnet = file_menu.addAction("Add &Magnet Link...")
        add_magnet.setShortcut(QKeySequence("Ctrl+M"))
        add_magnet.triggered.connect(self._on_add_magnet)

        create_torrent = file_menu.addAction("&Create Torrent...")
        create_torrent.setShortcut(QKeySequence("Ctrl+N"))
        create_torrent.triggered.connect(self._on_create_torrent)

        file_menu.addSeparator()

        pause_all = file_menu.addAction("Pause &All")
        pause_all.setShortcut(QKeySequence("Ctrl+Shift+P"))
        pause_all.triggered.connect(lambda: self._worker.pause_all())

        resume_all = file_menu.addAction("&Resume All")
        resume_all.setShortcut(QKeySequence("Ctrl+Shift+R"))
        resume_all.triggered.connect(lambda: self._worker.resume_all())

        file_menu.addSeparator()

        exit_action = file_menu.addAction("E&xit")
        exit_action.setShortcut(QKeySequence("Ctrl+Q"))
        exit_action.triggered.connect(self._force_quit)

        # --- View ---
        view_menu = menubar.addMenu("&View")

        self._toggle_sidebar_action = view_menu.addAction("&Sidebar")
        self._toggle_sidebar_action.setCheckable(True)
        self._toggle_sidebar_action.setChecked(True)
        self._toggle_sidebar_action.triggered.connect(self._on_toggle_sidebar)

        self._toggle_detail_action = view_menu.addAction("&Detail Panel")
        self._toggle_detail_action.setCheckable(True)
        self._toggle_detail_action.setChecked(True)

        view_menu.addSeparator()

        self._speed_title_action = view_menu.addAction("Speed in &Title Bar")
        self._speed_title_action.setCheckable(True)
        self._speed_title_action.setChecked(self._settings.get("show_speed_in_title", True))
        self._speed_title_action.triggered.connect(
            lambda checked: self._settings.set("show_speed_in_title", checked)
        )

        # --- Tools ---
        tools_menu = menubar.addMenu("&Tools")

        settings_action = tools_menu.addAction("&Settings...")
        settings_action.setShortcut(QKeySequence("Ctrl+,"))
        settings_action.triggered.connect(self._on_open_settings)

        tools_menu.addSeparator()

        rss_action = tools_menu.addAction("&RSS Feed Manager...")
        rss_action.triggered.connect(self._on_open_rss_manager)

        # --- Help ---
        help_menu = menubar.addMenu("&Help")

        about_action = help_menu.addAction("&About Flux")
        about_action.triggered.connect(self._on_about)

    # ------------------------------------------------------------------ #
    #  UI Setup
    # ------------------------------------------------------------------ #

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # --- Stats Strip ---
        self._stats_strip = QFrame()
        self._stats_strip.setObjectName("statsCard")
        self._stats_strip.setFixedHeight(52)
        stats_layout = QHBoxLayout(self._stats_strip)
        stats_layout.setContentsMargins(16, 0, 16, 0)
        stats_layout.setSpacing(24)

        self._stat_widgets = {}
        self._stat_defs = [
            ("downloading", "Downloading", "accent"),
            ("seeding", "Seeding", "green"),
            ("total", "Total", "text"),
            ("ratio", "Ratio", "cyan"),
        ]

        for key, label, color_key in self._stat_defs:
            card = self._make_stat_card(label, "0", color_key)
            stats_layout.addWidget(card)

        stats_layout.addStretch()

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Filter torrents...")
        self._search_input.setFixedWidth(200)
        self._search_input.setFixedHeight(28)
        self._search_input.setClearButtonEnabled(True)
        self._search_input.textChanged.connect(self._on_search_changed)
        stats_layout.addWidget(self._search_input)

        main_layout.addWidget(self._stats_strip)

        # --- Main content ---
        content = QWidget()
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        self._sidebar = SidebarWidget()
        content_layout.addWidget(self._sidebar)

        self._v_splitter = QSplitter(Qt.Orientation.Vertical)

        self._torrent_model = TorrentListModel()
        self._proxy_model = TorrentSortFilterProxy()
        self._proxy_model.setSourceModel(self._torrent_model)
        self._proxy_model.setSortCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)

        self._table = QTableView()
        self._table.setModel(self._proxy_model)
        self._table.setSortingEnabled(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setAlternatingRowColors(True)
        self._table.setShowGrid(False)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(36)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_context_menu)
        self._table.selectionModel().selectionChanged.connect(self._on_selection_changed)
        self._table.doubleClicked.connect(self._on_double_click)

        header = self._table.horizontalHeader()
        header.setStretchLastSection(True)
        for i, (_, _, width) in enumerate(TorrentListModel.COLUMNS):
            if i < header.count():
                header.resizeSection(i, width)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)

        header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header.customContextMenuRequested.connect(self._show_column_menu)

        self._table.setItemDelegateForColumn(0, StateIconDelegate(self._table))
        self._table.setItemDelegateForColumn(3, ProgressBarDelegate(self._table))

        self._v_splitter.addWidget(self._table)

        self._detail_panel = DetailPanel()
        self._v_splitter.addWidget(self._detail_panel)

        self._v_splitter.setSizes([500, 260])
        self._v_splitter.setCollapsible(0, False)
        self._v_splitter.setCollapsible(1, True)

        content_layout.addWidget(self._v_splitter)
        main_layout.addWidget(content)

    def _make_stat_card(self, label: str, value: str, color_key: str) -> QWidget:
        card = QWidget()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(1)

        val_label = QLabel(value)
        val_label.setObjectName("statValue")
        val_font = QFont("Consolas", 16)
        val_font.setStyleHint(QFont.StyleHint.Monospace)
        val_font.setWeight(QFont.Weight.Bold)
        val_label.setFont(val_font)
        val_label.setStyleSheet(f"color: {tc(color_key)};")
        val_label.setProperty("theme_color_key", color_key)
        layout.addWidget(val_label)

        name_label = QLabel(label)
        name_label.setObjectName("statLabel")
        name_label.setStyleSheet(f"color: {tc('text_dim')}; font-size: 10px; font-weight: 600;")
        name_label.setProperty("is_stat_label", True)
        layout.addWidget(name_label)

        self._stat_widgets[label.lower()] = val_label
        return card

    def _setup_toolbar(self):
        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        toolbar.setIconSize(toolbar.iconSize())
        self.addToolBar(toolbar)

        self._brand = QLabel("  Flux")
        self._brand.setObjectName("brandName")
        self._brand.setStyleSheet(f"color: {tc('accent')}; font-weight: 800; font-size: 16px; padding-right: 8px;")
        toolbar.addWidget(self._brand)
        toolbar.addSeparator()

        self._add_btn = QToolButton()
        self._add_btn.setText("+ Add Torrent")
        self._add_btn.setObjectName("accentButton")
        self._add_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self._add_btn.clicked.connect(self._on_add_torrent)
        toolbar.addWidget(self._add_btn)

        self._magnet_btn = QToolButton()
        self._magnet_btn.setText("Magnet")
        self._magnet_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self._magnet_btn.clicked.connect(self._on_add_magnet)
        toolbar.addWidget(self._magnet_btn)

        toolbar.addSeparator()

        self._resume_btn = QToolButton()
        self._resume_btn.setText("Resume")
        self._resume_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self._resume_btn.clicked.connect(self._on_resume_selected)
        toolbar.addWidget(self._resume_btn)

        self._pause_btn = QToolButton()
        self._pause_btn.setText("Pause")
        self._pause_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self._pause_btn.clicked.connect(self._on_pause_selected)
        toolbar.addWidget(self._pause_btn)

        self._remove_btn = QToolButton()
        self._remove_btn.setText("Remove")
        self._remove_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self._remove_btn.clicked.connect(self._on_remove_selected)
        toolbar.addWidget(self._remove_btn)

        toolbar.addSeparator()

        self._settings_btn = QToolButton()
        self._settings_btn.setText("Settings")
        self._settings_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self._settings_btn.clicked.connect(self._on_open_settings)
        toolbar.addWidget(self._settings_btn)

        self._sidebar_btn = QToolButton()
        self._sidebar_btn.setText("Sidebar")
        self._sidebar_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self._sidebar_btn.setCheckable(True)
        self._sidebar_btn.setChecked(True)
        self._sidebar_btn.clicked.connect(self._on_toggle_sidebar)
        toolbar.addWidget(self._sidebar_btn)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)

        dl_container = QWidget()
        dl_layout = QHBoxLayout(dl_container)
        dl_layout.setContentsMargins(0, 0, 8, 0)
        dl_layout.setSpacing(4)
        self._dl_sparkline = SparklineWidget("accent")
        dl_layout.addWidget(self._dl_sparkline)
        self._dl_speed_label = QLabel("-- DL")
        self._dl_speed_label.setObjectName("mono")
        self._dl_speed_label.setStyleSheet(f"color: {tc('accent')}; font-size: 11px; font-weight: 600;")
        dl_layout.addWidget(self._dl_speed_label)
        toolbar.addWidget(dl_container)

        ul_container = QWidget()
        ul_layout = QHBoxLayout(ul_container)
        ul_layout.setContentsMargins(0, 0, 4, 0)
        ul_layout.setSpacing(4)
        self._ul_sparkline = SparklineWidget("green")
        ul_layout.addWidget(self._ul_sparkline)
        self._ul_speed_label = QLabel("-- UL")
        self._ul_speed_label.setObjectName("mono")
        self._ul_speed_label.setStyleSheet(f"color: {tc('green')}; font-size: 11px; font-weight: 600;")
        ul_layout.addWidget(self._ul_speed_label)
        toolbar.addWidget(ul_container)

    def _setup_status_bar(self):
        sb = QStatusBar()
        self.setStatusBar(sb)

        self._status_label = QLabel("Ready")
        sb.addWidget(self._status_label, 1)

        self._dht_status = QLabel("DHT: --")
        self._dht_status.setStyleSheet(f"color: {tc('text_dim')};")
        sb.addPermanentWidget(self._dht_status)

        self._port_status = QLabel("Port: --")
        self._port_status.setStyleSheet(f"color: {tc('text_dim')};")
        sb.addPermanentWidget(self._port_status)

    def _setup_context_menu(self):
        self._context_menu = QMenu(self)
        self._ctx_resume = self._context_menu.addAction("Resume")
        self._ctx_resume.triggered.connect(self._on_resume_selected)
        self._ctx_pause = self._context_menu.addAction("Pause")
        self._ctx_pause.triggered.connect(self._on_pause_selected)
        self._context_menu.addSeparator()
        self._ctx_force_resume = self._context_menu.addAction("Force Resume")
        self._ctx_force_resume.triggered.connect(self._on_force_resume)
        self._ctx_recheck = self._context_menu.addAction("Force Recheck")
        self._ctx_recheck.triggered.connect(self._on_recheck)
        self._ctx_reannounce = self._context_menu.addAction("Force Reannounce")
        self._ctx_reannounce.triggered.connect(self._on_reannounce)
        self._context_menu.addSeparator()
        self._ctx_sequential = self._context_menu.addAction("Sequential Download")
        self._ctx_sequential.triggered.connect(self._on_toggle_sequential)

        queue_menu = self._context_menu.addMenu("Queue Position")
        queue_menu.addAction("Move to Top").triggered.connect(self._on_queue_top)
        queue_menu.addAction("Move Up").triggered.connect(self._on_queue_up)
        queue_menu.addAction("Move Down").triggered.connect(self._on_queue_down)
        queue_menu.addAction("Move to Bottom").triggered.connect(self._on_queue_bottom)

        self._context_menu.addSeparator()

        self._ctx_speed_limit = self._context_menu.addAction("Speed Limits...")
        self._ctx_speed_limit.triggered.connect(self._on_set_speed_limits)

        self._context_menu.addSeparator()
        self._ctx_open_folder = self._context_menu.addAction("Open Folder")
        self._ctx_open_folder.triggered.connect(self._on_open_folder)
        self._ctx_copy_magnet = self._context_menu.addAction("Copy Magnet Link")
        self._ctx_copy_magnet.triggered.connect(self._on_copy_magnet)
        self._context_menu.addSeparator()
        self._ctx_remove = self._context_menu.addAction("Remove")
        self._ctx_remove.triggered.connect(self._on_remove_selected)
        self._ctx_remove_files = self._context_menu.addAction("Remove with Files")
        self._ctx_remove_files.triggered.connect(self._on_remove_with_files)

    def _setup_system_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return

        self._tray = QSystemTrayIcon(self)
        self._tray.setToolTip("Flux Torrent")
        icon = self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
        self._tray.setIcon(icon)

        tray_menu = QMenu()
        tray_menu.addAction("Show/Hide").triggered.connect(self._toggle_window)
        tray_menu.addSeparator()
        tray_menu.addAction("Pause All").triggered.connect(lambda: self._worker.pause_all())
        tray_menu.addAction("Resume All").triggered.connect(lambda: self._worker.resume_all())
        tray_menu.addSeparator()
        tray_menu.addAction("Quit").triggered.connect(self._force_quit)
        self._tray.setContextMenu(tray_menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    def _toggle_window(self):
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.activateWindow()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._toggle_window()

    def _force_quit(self):
        self._tray = None
        self.close()

    def _connect_signals(self):
        w = self._worker

        # Worker -> GUI
        w.torrent_added.connect(self._on_torrent_added)
        w.torrent_removed.connect(self._on_torrent_removed)
        w.torrent_finished.connect(self._on_torrent_finished)
        w.torrent_error.connect(self._on_torrent_error)
        w.torrent_metadata.connect(self._on_metadata_received)
        w.stats_updated.connect(self._on_stats_updated)
        w.detail_updated.connect(self._on_detail_updated)
        w.peer_banned.connect(self._on_peer_banned)
        w.magnet_uri_ready.connect(self._on_magnet_uri_ready)

        # Detail panel mutation callbacks -> worker slots
        self._detail_panel.on_set_file_priority = w.set_file_priority
        self._detail_panel.on_add_tracker = w.add_tracker
        self._detail_panel.on_remove_tracker = w.remove_tracker

        # Sidebar
        self._sidebar.filter_changed.connect(self._on_filter_state_changed)
        self._sidebar.category_changed.connect(self._on_filter_category_changed)

    # ------------------------------------------------------------------ #
    #  Column Visibility
    # ------------------------------------------------------------------ #

    def _show_column_menu(self, pos):
        menu = QMenu(self)
        header = self._table.horizontalHeader()

        for i, (name, key, _) in enumerate(TorrentListModel.COLUMNS):
            if not name:
                continue
            action = menu.addAction(name)
            action.setCheckable(True)
            action.setChecked(not header.isSectionHidden(i))
            action.setData(i)
            action.triggered.connect(lambda checked, col=i: self._toggle_column(col, checked))

        menu.exec(header.mapToGlobal(pos))

    def _toggle_column(self, column: int, visible: bool):
        self._table.horizontalHeader().setSectionHidden(column, not visible)

    # ------------------------------------------------------------------ #
    #  Search
    # ------------------------------------------------------------------ #

    def _on_search_changed(self, text: str):
        self._proxy_model.set_text_filter(text)

    # ------------------------------------------------------------------ #
    #  Settings
    # ------------------------------------------------------------------ #

    def _on_open_settings(self):
        dlg = SettingsDialog(self._settings, self)
        dlg.settings_changed.connect(self._on_settings_changed)
        dlg.theme_changed.connect(self._on_theme_changed)
        dlg.exec()

    def _on_settings_changed(self):
        self._worker.apply_settings(self._settings.get_all())
        self._sidebar.update_categories(self._settings.get_categories())
        self._status_label.setText("Settings applied")

    def _on_theme_changed(self, theme_key: str):
        set_theme(theme_key)
        app = QApplication.instance()
        if app:
            app.setStyleSheet(get_stylesheet(theme_key))
            p = get_palette(theme_key)
            palette = QPalette()
            palette.setColor(QPalette.ColorRole.Window, QColor(p["bg"]))
            palette.setColor(QPalette.ColorRole.WindowText, QColor(p["text"]))
            palette.setColor(QPalette.ColorRole.Base, QColor(p["bg"]))
            palette.setColor(QPalette.ColorRole.AlternateBase, QColor(p["bg_alt"]))
            palette.setColor(QPalette.ColorRole.Text, QColor(p["text"]))
            palette.setColor(QPalette.ColorRole.Button, QColor(p["bg_hover"]))
            palette.setColor(QPalette.ColorRole.ButtonText, QColor(p["text"]))
            palette.setColor(QPalette.ColorRole.Highlight, QColor(p["accent"]))
            palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
            palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(p["bg_card"]))
            palette.setColor(QPalette.ColorRole.ToolTipText, QColor(p["text"]))
            app.setPalette(palette)

        self._apply_theme()
        self._sidebar.apply_theme()
        self._detail_panel.apply_theme()

    def _apply_theme(self):
        self._stats_strip.setStyleSheet(
            f"QFrame#statsCard {{ background-color: {tc('bg_card')}; border-bottom: 1px solid {tc('border')}; border-radius: 0; }}"
        )
        for key, label, color_key in self._stat_defs:
            w = self._stat_widgets.get(label.lower())
            if w:
                w.setStyleSheet(f"color: {tc(color_key)};")
        for child in self._stats_strip.findChildren(QLabel):
            if child.property("is_stat_label"):
                child.setStyleSheet(f"color: {tc('text_dim')}; font-size: 10px; font-weight: 600;")

        self._brand.setStyleSheet(f"color: {tc('accent')}; font-weight: 800; font-size: 16px; padding-right: 8px;")
        self._dl_speed_label.setStyleSheet(f"color: {tc('accent')}; font-size: 11px; font-weight: 600;")
        self._ul_speed_label.setStyleSheet(f"color: {tc('green')}; font-size: 11px; font-weight: 600;")
        self._dht_status.setStyleSheet(f"color: {tc('text_dim')};")
        self._port_status.setStyleSheet(f"color: {tc('text_dim')};")
        self._table.viewport().update()

    def _on_about(self):
        QMessageBox.about(
            self, "About Flux Torrent",
            "Flux Torrent Client v1.0\n\n"
            "A clean, fast, privacy-focused BitTorrent client.\n\n"
            "Built with Python, PyQt6, and libtorrent."
        )

    def _on_toggle_sidebar(self):
        self._sidebar.toggle_collapsed()
        is_visible = not self._sidebar.is_collapsed
        self._sidebar_btn.setChecked(is_visible)
        self._toggle_sidebar_action.setChecked(is_visible)

    def _on_create_torrent(self):
        dlg = CreateTorrentDialog(self)
        dlg.exec()

    def _on_open_rss_manager(self):
        from flux.core.rss_monitor import RSSMonitor
        if not self._rss_monitor:
            self._rss_monitor = RSSMonitor()
            self._rss_monitor.new_torrent.connect(self._on_rss_new_torrent)
        dlg = RSSManagerDialog(self._rss_monitor, self)
        dlg.exec()

    def _on_rss_new_torrent(self, download_url: str, save_path: str, category: str):
        if download_url.startswith("magnet:"):
            self._worker.add_magnet(
                download_url, save_path=save_path,
                category=category, tags_json="[]", paused=False
            )
        else:
            self._status_label.setText(f"RSS: downloading {download_url[:60]}...")

    # ------------------------------------------------------------------ #
    #  Drag and Drop
    # ------------------------------------------------------------------ #

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.toLocalFile().endswith(".torrent"):
                    event.acceptProposedAction()
                    return
        if event.mimeData().hasText():
            text = event.mimeData().text().strip()
            if text.startswith("magnet:"):
                event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                path = url.toLocalFile()
                if path.endswith(".torrent"):
                    self._add_torrent_with_dialog(torrent_path=path)
                    break
        elif event.mimeData().hasText():
            text = event.mimeData().text().strip()
            if text.startswith("magnet:"):
                self._add_torrent_with_dialog(magnet_uri=text)

    # ------------------------------------------------------------------ #
    #  Actions (all route through worker slots)
    # ------------------------------------------------------------------ #

    def _on_add_torrent(self):
        self._add_torrent_with_dialog()

    def _on_add_magnet(self):
        uri, ok = QInputDialog.getText(self, "Add Magnet Link", "Magnet URI:")
        if ok and uri.strip().startswith("magnet:"):
            self._add_torrent_with_dialog(magnet_uri=uri.strip())
        elif ok:
            QMessageBox.warning(self, "Invalid", "Not a valid magnet link.")

    def _add_torrent_with_dialog(self, torrent_path: str = "", magnet_uri: str = ""):
        dlg = AddTorrentDialog(
            self._settings, self,
            torrent_path=torrent_path,
            magnet_uri=magnet_uri
        )
        if dlg.exec():
            tags_json = json.dumps(dlg.tags) if dlg.tags else "[]"
            if dlg.is_magnet:
                self._worker.add_magnet(
                    dlg.magnet_uri,
                    save_path=dlg.save_path,
                    category=dlg.category,
                    tags_json=tags_json,
                    paused=dlg.start_paused,
                )
            else:
                self._worker.add_torrent_file(
                    dlg.torrent_path,
                    save_path=dlg.save_path,
                    category=dlg.category,
                    tags_json=tags_json,
                    paused=dlg.start_paused,
                    sequential=dlg.sequential_download,
                )

    def _get_selected_hashes(self) -> list[str]:
        hashes = []
        for idx in self._table.selectionModel().selectedRows():
            source_idx = self._proxy_model.mapToSource(idx)
            ih = self._torrent_model.get_info_hash(source_idx.row())
            if ih:
                hashes.append(ih)
        return hashes

    def _on_resume_selected(self):
        for ih in self._get_selected_hashes():
            self._worker.resume_torrent(ih)

    def _on_pause_selected(self):
        for ih in self._get_selected_hashes():
            self._worker.pause_torrent(ih)

    def _on_remove_selected(self):
        hashes = self._get_selected_hashes()
        if not hashes:
            return
        if self._settings.get("confirm_on_delete", True):
            count = len(hashes)
            msg = f"Remove {count} torrent{'s' if count > 1 else ''}?"
            if QMessageBox.question(self, "Confirm", msg) != QMessageBox.StandardButton.Yes:
                return
        for ih in hashes:
            self._worker.remove_torrent(ih, False)

    def _on_remove_with_files(self):
        hashes = self._get_selected_hashes()
        if not hashes:
            return
        count = len(hashes)
        msg = f"Remove {count} torrent{'s' if count > 1 else ''} AND delete files?\nThis cannot be undone."
        if QMessageBox.warning(
            self, "Confirm Delete", msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) == QMessageBox.StandardButton.Yes:
            for ih in hashes:
                self._worker.remove_torrent(ih, True)

    def _on_force_resume(self):
        for ih in self._get_selected_hashes():
            self._worker.force_resume(ih)

    def _on_recheck(self):
        for ih in self._get_selected_hashes():
            self._worker.force_recheck(ih)

    def _on_reannounce(self):
        for ih in self._get_selected_hashes():
            self._worker.force_reannounce(ih)

    def _on_toggle_sequential(self):
        for ih in self._get_selected_hashes():
            self._worker.set_sequential(ih, True)

    def _on_open_folder(self):
        if not self._last_stats:
            return
        for ih in self._get_selected_hashes():
            snap = self._torrent_model.find_snapshot(ih)
            if snap:
                path = snap.save_path
                if os.path.isdir(path):
                    os.startfile(path) if os.name == "nt" else os.system(f'xdg-open "{path}"')
                break

    def _on_copy_magnet(self):
        hashes = self._get_selected_hashes()
        if hashes:
            self._worker.request_magnet_uri(hashes[0])

    def _on_magnet_uri_ready(self, uri: str):
        QApplication.clipboard().setText(uri)
        self._status_label.setText("Magnet link copied to clipboard")

    # --- Queue Position ---

    def _on_queue_top(self):
        for ih in self._get_selected_hashes():
            self._worker.queue_action(ih, "top")

    def _on_queue_up(self):
        for ih in self._get_selected_hashes():
            self._worker.queue_action(ih, "up")

    def _on_queue_down(self):
        for ih in reversed(self._get_selected_hashes()):
            self._worker.queue_action(ih, "down")

    def _on_queue_bottom(self):
        for ih in reversed(self._get_selected_hashes()):
            self._worker.queue_action(ih, "bottom")

    # --- Per-Torrent Speed Limits ---

    def _on_set_speed_limits(self):
        hashes = self._get_selected_hashes()
        if not hashes:
            return
        snap = self._torrent_model.find_snapshot(hashes[0])
        if not snap:
            return
        dlg = SpeedLimitDialog(
            snap.name,
            dl_limit=snap.download_limit,
            ul_limit=snap.upload_limit,
            parent=self
        )
        if dlg.exec():
            for ih in hashes:
                self._worker.set_torrent_speed_limit(ih, dlg.dl_limit, dlg.ul_limit)
            self._status_label.setText("Speed limits applied")

    # ------------------------------------------------------------------ #
    #  Selection & Navigation
    # ------------------------------------------------------------------ #

    def _on_selection_changed(self, selected, deselected):
        indexes = self._table.selectionModel().selectedRows()
        if indexes:
            source_idx = self._proxy_model.mapToSource(indexes[0])
            snap = self._torrent_model.get_snapshot(source_idx.row())
            self._selected_info_hash = snap.info_hash if snap else None
            self._detail_panel.set_torrent(snap)
            # Tell worker which torrent to provide detail data for
            if snap:
                self._worker.set_focused_torrent(snap.info_hash)
        else:
            self._selected_info_hash = None
            self._detail_panel.set_torrent(None)
            self._worker.set_focused_torrent("")

    def _on_double_click(self, index):
        source_idx = self._proxy_model.mapToSource(index)
        snap = self._torrent_model.get_snapshot(source_idx.row())
        if snap:
            path = snap.save_path
            if os.path.isdir(path):
                os.startfile(path) if os.name == "nt" else os.system(f'xdg-open "{path}"')

    def _show_context_menu(self, pos):
        if self._get_selected_hashes():
            self._context_menu.exec(self._table.viewport().mapToGlobal(pos))

    # ------------------------------------------------------------------ #
    #  Filters
    # ------------------------------------------------------------------ #

    def _on_filter_state_changed(self, state):
        self._proxy_model.set_state_filter(state)

    def _on_filter_category_changed(self, category):
        self._proxy_model.set_category_filter(category)

    # ------------------------------------------------------------------ #
    #  Signal Handlers (from worker thread, received on GUI thread)
    # ------------------------------------------------------------------ #

    def _on_torrent_added(self, info_hash):
        self._status_label.setText("Torrent added")

    def _on_torrent_removed(self, info_hash):
        if self._selected_info_hash == info_hash:
            self._detail_panel.set_torrent(None)
            self._selected_info_hash = None

    def _on_torrent_finished(self, info_hash):
        snap = self._torrent_model.find_snapshot(info_hash)
        if snap:
            self._status_label.setText(f"Completed: {snap.name}")

    def _on_torrent_error(self, info_hash, msg):
        self._status_label.setText(f"Error: {msg}")

    def _on_metadata_received(self, info_hash):
        snap = self._torrent_model.find_snapshot(info_hash)
        if snap:
            self._status_label.setText(f"Metadata received: {snap.name}")

    def _on_stats_updated(self, stats: SessionStats):
        """Main UI refresh - driven by worker stats signal (every second)."""
        self._last_stats = stats

        # Update torrent model from snapshots
        self._torrent_model.update_from_snapshots(stats.torrents)

        # Speeds
        dl_rate = stats.download_rate
        ul_rate = stats.upload_rate
        self._dl_speed_label.setText(f"{format_speed(dl_rate)} DL")
        self._ul_speed_label.setText(f"{format_speed(ul_rate)} UL")
        self._dl_sparkline.set_data(stats.dl_history)
        self._ul_sparkline.set_data(stats.ul_history)

        if self._settings.get("show_speed_in_title", True) and (dl_rate > 0 or ul_rate > 0):
            self.setWindowTitle(
                f"Flux Torrent - DL: {format_speed(dl_rate)} | UL: {format_speed(ul_rate)}"
            )
        else:
            self.setWindowTitle("Flux Torrent")

        if self._tray:
            self._tray.setToolTip(
                f"Flux Torrent\nDL: {format_speed(dl_rate)} | UL: {format_speed(ul_rate)}"
            )

        # Stats strip
        downloading = 0
        seeding = 0
        total = stats.torrent_count
        total_dl = 0
        total_ul = 0
        state_counts = {}

        for snap in stats.torrents:
            try:
                st = snap.state
                state_counts[st] = state_counts.get(st, 0) + 1
                if st in (TorrentState.DOWNLOADING, TorrentState.STALLED, TorrentState.METADATA):
                    downloading += 1
                elif st in (TorrentState.SEEDING, TorrentState.COMPLETED):
                    seeding += 1
                total_dl += snap.total_downloaded
                total_ul += snap.total_uploaded
            except Exception:
                pass

        ratio = total_ul / total_dl if total_dl > 0 else 0.0

        if "downloading" in self._stat_widgets:
            self._stat_widgets["downloading"].setText(str(downloading))
        if "seeding" in self._stat_widgets:
            self._stat_widgets["seeding"].setText(str(seeding))
        if "total" in self._stat_widgets:
            self._stat_widgets["total"].setText(str(total))
        if "ratio" in self._stat_widgets:
            self._stat_widgets["ratio"].setText(f"{ratio:.2f}")

        self._sidebar.update_counts(state_counts)

        dht = stats.dht_nodes
        port = self._settings.get("listen_port", 6881)
        save_path = self._settings.get("default_save_path", "")
        try:
            free = shutil.disk_usage(save_path).free if save_path else 0
            free_str = format_bytes(free)
        except Exception:
            free_str = "--"

        self._sidebar.update_session_info(dht, port, free_str)
        self._dht_status.setText(f"DHT: {dht}")
        self._port_status.setText(f"Port: {port}")

        # Update detail panel overview from matching snapshot
        if self._selected_info_hash:
            snap = self._torrent_model.find_snapshot(self._selected_info_hash)
            if snap:
                self._detail_panel.refresh_from_snapshot(snap)

    def _on_detail_updated(self, detail: DetailData):
        """Handle detail data from worker for focused torrent."""
        self._detail_panel.update_detail(detail)

    def _on_peer_banned(self, ip, reason):
        self._status_label.setText(f"Banned peer {ip}: {reason}")

    # ------------------------------------------------------------------ #
    #  Shutdown
    # ------------------------------------------------------------------ #

    def closeEvent(self, event):
        if (self._tray and self._settings.get("close_to_tray", True)
                and self._tray.isVisible()):
            self.hide()
            event.ignore()
            return

        self._settings.set("window_geometry", self.saveGeometry().toHex().data().decode())

        # Stop RSS monitor
        if self._rss_monitor:
            self._rss_monitor.stop_all()

        # Stop session (blocks until worker thread exits)
        self._threaded.stop()
        self._settings.close()

        if self._tray:
            self._tray.hide()

        event.accept()
