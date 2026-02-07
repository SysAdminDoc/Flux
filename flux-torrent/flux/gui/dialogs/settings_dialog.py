"""Settings dialog for Flux Torrent Client."""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QWidget,
    QLabel, QLineEdit, QSpinBox, QDoubleSpinBox, QCheckBox,
    QComboBox, QPushButton, QGroupBox, QGridLayout, QFileDialog,
    QRadioButton, QButtonGroup, QFrame, QSizePolicy
)


class SettingsDialog(QDialog):
    """Multi-tab settings dialog."""

    settings_changed = pyqtSignal()
    theme_changed = pyqtSignal(str)  # theme_key

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self._settings = settings
        self.setWindowTitle("Settings")
        self.setMinimumSize(620, 520)
        self.resize(680, 560)
        self.setModal(True)

        self._widgets = {}  # key -> widget for reading values
        self._setup_ui()
        self._load_current()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)
        layout.addWidget(self._tabs)

        self._tabs.addTab(self._build_downloads_tab(), "Downloads")
        self._tabs.addTab(self._build_bandwidth_tab(), "Bandwidth")
        self._tabs.addTab(self._build_connection_tab(), "Connection")
        self._tabs.addTab(self._build_behavior_tab(), "Behavior")
        self._tabs.addTab(self._build_ui_tab(), "Interface")

        # Bottom buttons
        btn_frame = QFrame()
        btn_frame.setStyleSheet("background-color: transparent;")
        btn_layout = QHBoxLayout(btn_frame)
        btn_layout.setContentsMargins(16, 10, 16, 10)
        btn_layout.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        apply_btn = QPushButton("Apply")
        apply_btn.clicked.connect(self._apply)
        btn_layout.addWidget(apply_btn)

        ok_btn = QPushButton("OK")
        ok_btn.setObjectName("primary")
        ok_btn.clicked.connect(self._ok)
        btn_layout.addWidget(ok_btn)

        layout.addWidget(btn_frame)

    # --- Downloads Tab ---

    def _build_downloads_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        # Save path
        path_group = QGroupBox("Save Location")
        pg = QGridLayout(path_group)
        pg.setSpacing(8)

        pg.addWidget(QLabel("Default save path:"), 0, 0)
        save_path = QLineEdit()
        self._widgets["default_save_path"] = save_path
        pg.addWidget(save_path, 0, 1)
        browse_btn = QPushButton("Browse...")
        browse_btn.setFixedWidth(80)
        browse_btn.clicked.connect(lambda: self._browse_folder(save_path))
        pg.addWidget(browse_btn, 0, 2)

        move_cb = QCheckBox("Move completed downloads to:")
        self._widgets["move_completed_enabled"] = move_cb
        pg.addWidget(move_cb, 1, 0, 1, 1)
        move_path = QLineEdit()
        self._widgets["move_completed_path"] = move_path
        pg.addWidget(move_path, 1, 1)
        browse_mv = QPushButton("Browse...")
        browse_mv.setFixedWidth(80)
        browse_mv.clicked.connect(lambda: self._browse_folder(move_path))
        pg.addWidget(browse_mv, 1, 2)

        temp_cb = QCheckBox("Use temporary folder for incomplete:")
        self._widgets["temp_path_enabled"] = temp_cb
        pg.addWidget(temp_cb, 2, 0, 1, 1)
        temp_path = QLineEdit()
        self._widgets["temp_path"] = temp_path
        pg.addWidget(temp_path, 2, 1)
        browse_tmp = QPushButton("Browse...")
        browse_tmp.setFixedWidth(80)
        browse_tmp.clicked.connect(lambda: self._browse_folder(temp_path))
        pg.addWidget(browse_tmp, 2, 2)

        layout.addWidget(path_group)

        # On completion
        comp_group = QGroupBox("When Torrent Completes")
        cg = QVBoxLayout(comp_group)
        self._complete_nothing = QRadioButton("Do nothing (continue seeding)")
        self._complete_pause = QRadioButton("Stop the torrent")
        self._complete_remove = QRadioButton("Remove from list (keep files)")
        self._complete_group = QButtonGroup(self)
        self._complete_group.addButton(self._complete_nothing, 0)
        self._complete_group.addButton(self._complete_pause, 1)
        self._complete_group.addButton(self._complete_remove, 2)
        cg.addWidget(self._complete_nothing)
        cg.addWidget(self._complete_pause)
        cg.addWidget(self._complete_remove)
        layout.addWidget(comp_group)

        # Defaults
        def_group = QGroupBox("Add Torrent Defaults")
        dg = QVBoxLayout(def_group)
        add_paused = QCheckBox("Start torrents paused")
        self._widgets["add_paused_default"] = add_paused
        dg.addWidget(add_paused)
        seq_dl = QCheckBox("Enable sequential download by default")
        self._widgets["sequential_download_default"] = seq_dl
        dg.addWidget(seq_dl)
        prealloc = QCheckBox("Pre-allocate disk space")
        self._widgets["pre_allocate_storage"] = prealloc
        dg.addWidget(prealloc)
        layout.addWidget(def_group)

        layout.addStretch()
        return page

    # --- Bandwidth Tab ---

    def _build_bandwidth_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        # Speed limits
        speed_group = QGroupBox("Speed Limits")
        sg = QGridLayout(speed_group)
        sg.setSpacing(8)

        sg.addWidget(QLabel("Download limit (KiB/s):"), 0, 0)
        dl_spin = QSpinBox()
        dl_spin.setRange(0, 999999)
        dl_spin.setSpecialValueText("Unlimited")
        dl_spin.setSuffix(" KiB/s")
        self._widgets["max_download_speed_kib"] = dl_spin
        sg.addWidget(dl_spin, 0, 1)

        sg.addWidget(QLabel("Upload limit (KiB/s):"), 1, 0)
        ul_spin = QSpinBox()
        ul_spin.setRange(0, 999999)
        ul_spin.setSpecialValueText("Unlimited")
        ul_spin.setSuffix(" KiB/s")
        self._widgets["max_upload_speed_kib"] = ul_spin
        sg.addWidget(ul_spin, 1, 1)

        sg.addWidget(QLabel("(0 = Unlimited)"), 0, 2)
        layout.addWidget(speed_group)

        # Connection limits
        conn_group = QGroupBox("Connection Limits")
        cg = QGridLayout(conn_group)
        cg.setSpacing(8)

        cg.addWidget(QLabel("Global connections:"), 0, 0)
        max_conn = QSpinBox()
        max_conn.setRange(10, 10000)
        self._widgets["max_connections"] = max_conn
        cg.addWidget(max_conn, 0, 1)

        cg.addWidget(QLabel("Per torrent:"), 0, 2)
        max_conn_t = QSpinBox()
        max_conn_t.setRange(1, 1000)
        self._widgets["max_connections_per_torrent"] = max_conn_t
        cg.addWidget(max_conn_t, 0, 3)

        cg.addWidget(QLabel("Upload slots:"), 1, 0)
        max_ul = QSpinBox()
        max_ul.setRange(1, 500)
        self._widgets["max_uploads"] = max_ul
        cg.addWidget(max_ul, 1, 1)

        cg.addWidget(QLabel("Per torrent:"), 1, 2)
        max_ul_t = QSpinBox()
        max_ul_t.setRange(1, 200)
        self._widgets["max_uploads_per_torrent"] = max_ul_t
        cg.addWidget(max_ul_t, 1, 3)

        layout.addWidget(conn_group)

        # Queue
        queue_group = QGroupBox("Queue")
        qg = QGridLayout(queue_group)
        qg.setSpacing(8)

        qg.addWidget(QLabel("Max active downloads:"), 0, 0)
        ad = QSpinBox()
        ad.setRange(1, 100)
        self._widgets["max_active_downloads"] = ad
        qg.addWidget(ad, 0, 1)

        qg.addWidget(QLabel("Max active uploads:"), 0, 2)
        au = QSpinBox()
        au.setRange(1, 100)
        self._widgets["max_active_uploads"] = au
        qg.addWidget(au, 0, 3)

        qg.addWidget(QLabel("Max active total:"), 1, 0)
        at = QSpinBox()
        at.setRange(1, 200)
        self._widgets["max_active_torrents"] = at
        qg.addWidget(at, 1, 1)

        layout.addWidget(queue_group)

        # Seeding
        seed_group = QGroupBox("Seeding Limits")
        sdg = QGridLayout(seed_group)
        sdg.setSpacing(8)

        sdg.addWidget(QLabel("Max ratio:"), 0, 0)
        ratio = QDoubleSpinBox()
        ratio.setRange(0, 100)
        ratio.setDecimals(1)
        ratio.setSingleStep(0.1)
        ratio.setSpecialValueText("Disabled")
        self._widgets["max_ratio"] = ratio
        sdg.addWidget(ratio, 0, 1)

        sdg.addWidget(QLabel("Max seed time (min):"), 0, 2)
        seed_time = QSpinBox()
        seed_time.setRange(0, 999999)
        seed_time.setSpecialValueText("Unlimited")
        self._widgets["max_seed_time"] = seed_time
        sdg.addWidget(seed_time, 0, 3)

        sdg.addWidget(QLabel("When ratio reached:"), 1, 0)
        ratio_act = QComboBox()
        ratio_act.addItems(["Pause torrent", "Remove torrent"])
        self._widgets["ratio_action"] = ratio_act
        sdg.addWidget(ratio_act, 1, 1, 1, 3)

        layout.addWidget(seed_group)

        layout.addStretch()
        return page

    # --- Connection Tab ---

    def _build_connection_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        # Listening port
        port_group = QGroupBox("Listening Port")
        pg = QGridLayout(port_group)
        pg.setSpacing(8)

        pg.addWidget(QLabel("Port:"), 0, 0)
        port = QSpinBox()
        port.setRange(1024, 65535)
        self._widgets["listen_port"] = port
        pg.addWidget(port, 0, 1)

        upnp = QCheckBox("Enable UPnP port mapping")
        self._widgets["upnp_enabled"] = upnp
        pg.addWidget(upnp, 1, 0, 1, 2)

        natpmp = QCheckBox("Enable NAT-PMP port mapping")
        self._widgets["natpmp_enabled"] = natpmp
        pg.addWidget(natpmp, 2, 0, 1, 2)

        layout.addWidget(port_group)

        # Protocol
        proto_group = QGroupBox("Protocol")
        prg = QVBoxLayout(proto_group)

        dht = QCheckBox("Enable DHT (Distributed Hash Table)")
        self._widgets["dht_enabled"] = dht
        prg.addWidget(dht)

        pex = QCheckBox("Enable PEX (Peer Exchange)")
        self._widgets["pex_enabled"] = pex
        prg.addWidget(pex)

        lsd = QCheckBox("Enable LSD (Local Service Discovery)")
        self._widgets["lsd_enabled"] = lsd
        prg.addWidget(lsd)

        layout.addWidget(proto_group)

        # Encryption
        enc_group = QGroupBox("Encryption")
        eg = QHBoxLayout(enc_group)
        eg.addWidget(QLabel("Mode:"))
        enc_combo = QComboBox()
        enc_combo.addItems(["Disabled", "Prefer encrypted", "Require encrypted"])
        self._widgets["encryption_mode"] = enc_combo
        eg.addWidget(enc_combo)
        eg.addStretch()
        layout.addWidget(enc_group)

        # Peer Filtering
        peer_group = QGroupBox("Peer Filtering")
        pfg = QVBoxLayout(peer_group)

        pf_enabled = QCheckBox("Enable peer filtering")
        self._widgets["peer_filter_enabled"] = pf_enabled
        pfg.addWidget(pf_enabled)

        ban_xunlei = QCheckBox("Auto-ban Xunlei/Thunder clients")
        self._widgets["auto_ban_xunlei"] = ban_xunlei
        pfg.addWidget(ban_xunlei)

        ban_qq = QCheckBox("Auto-ban QQ clients")
        self._widgets["auto_ban_qq"] = ban_qq
        pfg.addWidget(ban_qq)

        ban_baidu = QCheckBox("Auto-ban Baidu clients")
        self._widgets["auto_ban_baidu"] = ban_baidu
        pfg.addWidget(ban_baidu)

        layout.addWidget(peer_group)

        layout.addStretch()
        return page

    # --- Behavior Tab ---

    def _build_behavior_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        gen_group = QGroupBox("General")
        gg = QVBoxLayout(gen_group)

        confirm_del = QCheckBox("Confirm before removing torrents")
        self._widgets["confirm_on_delete"] = confirm_del
        gg.addWidget(confirm_del)

        confirm_del_files = QCheckBox("Confirm before deleting files")
        self._widgets["confirm_on_delete_files"] = confirm_del_files
        gg.addWidget(confirm_del_files)

        layout.addWidget(gen_group)

        tray_group = QGroupBox("System Tray")
        tg = QVBoxLayout(tray_group)

        min_tray = QCheckBox("Minimize to system tray")
        self._widgets["minimize_to_tray"] = min_tray
        tg.addWidget(min_tray)

        close_tray = QCheckBox("Close to system tray")
        self._widgets["close_to_tray"] = close_tray
        tg.addWidget(close_tray)

        start_min = QCheckBox("Start minimized")
        self._widgets["start_minimized"] = start_min
        tg.addWidget(start_min)

        layout.addWidget(tray_group)

        # Trackers
        tracker_group = QGroupBox("Trackers")
        trg = QVBoxLayout(tracker_group)

        auto_trackers = QCheckBox("Auto-update tracker list on new torrents")
        self._widgets["auto_update_trackers"] = auto_trackers
        trg.addWidget(auto_trackers)

        url_row = QHBoxLayout()
        url_row.addWidget(QLabel("Tracker list URL:"))
        tracker_url = QLineEdit()
        self._widgets["tracker_list_url"] = tracker_url
        url_row.addWidget(tracker_url)
        trg.addLayout(url_row)

        layout.addWidget(tracker_group)

        layout.addStretch()
        return page

    # --- Interface Tab ---

    def _build_ui_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        theme_group = QGroupBox("Theme")
        tg = QHBoxLayout(theme_group)
        tg.addWidget(QLabel("Theme:"))
        theme_combo = QComboBox()
        from flux.gui.themes import get_theme_names
        for key, name in get_theme_names():
            theme_combo.addItem(name, key)
        self._widgets["theme"] = theme_combo
        tg.addWidget(theme_combo)
        tg.addStretch()

        # Preview hint
        hint = QLabel("Theme changes apply immediately")
        from flux.gui.themes import c as tc
        hint.setStyleSheet(f"color: {tc('text_muted')}; font-size: 11px; font-style: italic;")
        tg.addWidget(hint)

        layout.addWidget(theme_group)

        ui_group = QGroupBox("Display")
        ug = QVBoxLayout(ui_group)

        speed_title = QCheckBox("Show transfer speed in window title")
        self._widgets["show_speed_in_title"] = speed_title
        ug.addWidget(speed_title)

        layout.addWidget(ui_group)

        layout.addStretch()
        return page

    # --- Load / Save ---

    def _load_current(self):
        s = self._settings

        # Downloads
        self._widgets["default_save_path"].setText(s.get("default_save_path", ""))
        self._widgets["move_completed_enabled"].setChecked(s.get("move_completed_enabled", False))
        self._widgets["move_completed_path"].setText(s.get("move_completed_path", ""))
        self._widgets["temp_path_enabled"].setChecked(s.get("temp_path_enabled", False))
        self._widgets["temp_path"].setText(s.get("temp_path", ""))
        self._complete_group.button(s.get("on_complete_action", 1)).setChecked(True)
        self._widgets["add_paused_default"].setChecked(s.get("add_paused_default", False))
        self._widgets["sequential_download_default"].setChecked(s.get("sequential_download_default", False))
        self._widgets["pre_allocate_storage"].setChecked(s.get("pre_allocate_storage", False))

        # Bandwidth (stored as bytes/s, displayed as KiB/s)
        dl_bps = s.get("max_download_speed", 0)
        ul_bps = s.get("max_upload_speed", 13312)
        self._widgets["max_download_speed_kib"].setValue(dl_bps // 1024 if dl_bps > 0 else 0)
        self._widgets["max_upload_speed_kib"].setValue(ul_bps // 1024 if ul_bps > 0 else 0)

        self._widgets["max_connections"].setValue(s.get("max_connections", 500))
        self._widgets["max_connections_per_torrent"].setValue(s.get("max_connections_per_torrent", 100))
        self._widgets["max_uploads"].setValue(s.get("max_uploads", 20))
        self._widgets["max_uploads_per_torrent"].setValue(s.get("max_uploads_per_torrent", 5))
        self._widgets["max_active_downloads"].setValue(s.get("max_active_downloads", 5))
        self._widgets["max_active_uploads"].setValue(s.get("max_active_uploads", 5))
        self._widgets["max_active_torrents"].setValue(s.get("max_active_torrents", 10))
        self._widgets["max_ratio"].setValue(s.get("max_ratio", 2.0))
        self._widgets["max_seed_time"].setValue(s.get("max_seed_time", 0))
        self._widgets["ratio_action"].setCurrentIndex(s.get("ratio_action", 0))

        # Connection
        self._widgets["listen_port"].setValue(s.get("listen_port", 6881))
        self._widgets["upnp_enabled"].setChecked(s.get("upnp_enabled", True))
        self._widgets["natpmp_enabled"].setChecked(s.get("natpmp_enabled", True))
        self._widgets["dht_enabled"].setChecked(s.get("dht_enabled", True))
        self._widgets["pex_enabled"].setChecked(s.get("pex_enabled", True))
        self._widgets["lsd_enabled"].setChecked(s.get("lsd_enabled", True))
        self._widgets["encryption_mode"].setCurrentIndex(s.get("encryption_mode", 1))
        self._widgets["peer_filter_enabled"].setChecked(s.get("peer_filter_enabled", True))
        self._widgets["auto_ban_xunlei"].setChecked(s.get("auto_ban_xunlei", True))
        self._widgets["auto_ban_qq"].setChecked(s.get("auto_ban_qq", True))
        self._widgets["auto_ban_baidu"].setChecked(s.get("auto_ban_baidu", True))

        # Behavior
        self._widgets["confirm_on_delete"].setChecked(s.get("confirm_on_delete", True))
        self._widgets["confirm_on_delete_files"].setChecked(s.get("confirm_on_delete_files", True))
        self._widgets["minimize_to_tray"].setChecked(s.get("minimize_to_tray", True))
        self._widgets["close_to_tray"].setChecked(s.get("close_to_tray", True))
        self._widgets["start_minimized"].setChecked(s.get("start_minimized", False))
        self._widgets["auto_update_trackers"].setChecked(s.get("auto_update_trackers", False))
        self._widgets["tracker_list_url"].setText(s.get("tracker_list_url", ""))

        # UI
        theme_key = s.get("theme", "dark")
        combo = self._widgets["theme"]
        for i in range(combo.count()):
            if combo.itemData(i) == theme_key:
                combo.setCurrentIndex(i)
                break
        self._widgets["show_speed_in_title"].setChecked(s.get("show_speed_in_title", True))

    def _save_all(self):
        s = self._settings

        # Downloads
        s.set("default_save_path", self._widgets["default_save_path"].text())
        s.set("move_completed_enabled", self._widgets["move_completed_enabled"].isChecked())
        s.set("move_completed_path", self._widgets["move_completed_path"].text())
        s.set("temp_path_enabled", self._widgets["temp_path_enabled"].isChecked())
        s.set("temp_path", self._widgets["temp_path"].text())
        s.set("on_complete_action", self._complete_group.checkedId())
        s.set("add_paused_default", self._widgets["add_paused_default"].isChecked())
        s.set("sequential_download_default", self._widgets["sequential_download_default"].isChecked())
        s.set("pre_allocate_storage", self._widgets["pre_allocate_storage"].isChecked())

        # Bandwidth (KiB/s -> bytes/s)
        dl_kib = self._widgets["max_download_speed_kib"].value()
        ul_kib = self._widgets["max_upload_speed_kib"].value()
        s.set("max_download_speed", dl_kib * 1024 if dl_kib > 0 else 0)
        s.set("max_upload_speed", ul_kib * 1024 if ul_kib > 0 else 0)

        s.set("max_connections", self._widgets["max_connections"].value())
        s.set("max_connections_per_torrent", self._widgets["max_connections_per_torrent"].value())
        s.set("max_uploads", self._widgets["max_uploads"].value())
        s.set("max_uploads_per_torrent", self._widgets["max_uploads_per_torrent"].value())
        s.set("max_active_downloads", self._widgets["max_active_downloads"].value())
        s.set("max_active_uploads", self._widgets["max_active_uploads"].value())
        s.set("max_active_torrents", self._widgets["max_active_torrents"].value())
        s.set("max_ratio", self._widgets["max_ratio"].value())
        s.set("max_seed_time", self._widgets["max_seed_time"].value())
        s.set("ratio_action", self._widgets["ratio_action"].currentIndex())

        # Connection
        s.set("listen_port", self._widgets["listen_port"].value())
        s.set("upnp_enabled", self._widgets["upnp_enabled"].isChecked())
        s.set("natpmp_enabled", self._widgets["natpmp_enabled"].isChecked())
        s.set("dht_enabled", self._widgets["dht_enabled"].isChecked())
        s.set("pex_enabled", self._widgets["pex_enabled"].isChecked())
        s.set("lsd_enabled", self._widgets["lsd_enabled"].isChecked())
        s.set("encryption_mode", self._widgets["encryption_mode"].currentIndex())
        s.set("peer_filter_enabled", self._widgets["peer_filter_enabled"].isChecked())
        s.set("auto_ban_xunlei", self._widgets["auto_ban_xunlei"].isChecked())
        s.set("auto_ban_qq", self._widgets["auto_ban_qq"].isChecked())
        s.set("auto_ban_baidu", self._widgets["auto_ban_baidu"].isChecked())

        # Behavior
        s.set("confirm_on_delete", self._widgets["confirm_on_delete"].isChecked())
        s.set("confirm_on_delete_files", self._widgets["confirm_on_delete_files"].isChecked())
        s.set("minimize_to_tray", self._widgets["minimize_to_tray"].isChecked())
        s.set("close_to_tray", self._widgets["close_to_tray"].isChecked())
        s.set("start_minimized", self._widgets["start_minimized"].isChecked())
        s.set("auto_update_trackers", self._widgets["auto_update_trackers"].isChecked())
        s.set("tracker_list_url", self._widgets["tracker_list_url"].text())

        # UI
        new_theme = self._widgets["theme"].currentData()
        old_theme = s.get("theme", "dark")
        s.set("theme", new_theme)
        s.set("show_speed_in_title", self._widgets["show_speed_in_title"].isChecked())

        self.settings_changed.emit()
        if new_theme != old_theme:
            self.theme_changed.emit(new_theme)

    def _apply(self):
        self._save_all()

    def _ok(self):
        self._save_all()
        self.accept()

    def _browse_folder(self, line_edit: QLineEdit):
        path = QFileDialog.getExistingDirectory(self, "Select Folder", line_edit.text())
        if path:
            line_edit.setText(path)
