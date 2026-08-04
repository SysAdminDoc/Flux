"""Settings dialog for Flux Torrent Client."""

import json

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QWidget,
    QLabel, QLineEdit, QSpinBox, QDoubleSpinBox, QCheckBox,
    QComboBox, QPushButton, QGroupBox, QGridLayout, QFileDialog,
    QRadioButton, QButtonGroup, QFrame, QPlainTextEdit, QScrollArea,
    QAbstractButton,
)

from flux.core.settings import (
    build_tracker_proxy_rules,
    build_label_automation_rules,
    build_torrent_schedule_settings,
)
from flux.core.blocklist import normalize_blocklist_urls
from flux.core.notifications import normalize_ratio_milestones


def _search_text(value: str) -> str:
    return " ".join(
        str(value or "")
        .casefold()
        .replace("_", " ")
        .replace("-", " ")
        .split()
    )


def _is_subsequence(needle: str, haystack: str) -> bool:
    compact_needle = "".join(char for char in needle if char.isalnum())
    compact_haystack = "".join(char for char in haystack if char.isalnum())
    if not compact_needle:
        return True
    position = 0
    for char in compact_needle:
        position = compact_haystack.find(char, position)
        if position < 0:
            return False
        position += 1
    return True


def fuzzy_match(query: str, text: str) -> bool:
    """Return whether query matches text by substring or character subsequence."""
    normalized_query = _search_text(query)
    normalized_text = _search_text(text)
    if not normalized_query:
        return True
    if normalized_query in normalized_text:
        return True
    compact_text = normalized_text.replace(" ", "")
    compact_query = normalized_query.replace(" ", "")
    if _is_subsequence(compact_query, compact_text):
        return True
    return all(_is_subsequence(token, compact_text) for token in normalized_query.split())


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
        self._search_groups = []
        self._setup_ui()
        self._build_search_index()
        self._load_current()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        search_row = QHBoxLayout()
        search_row.setContentsMargins(16, 10, 16, 8)
        search_row.setSpacing(8)
        search_row.addWidget(QLabel("Search settings:"))
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Try \"proxy\", \"upload limit\", or \"remote client\"")
        self._search_input.setClearButtonEnabled(True)
        self._search_input.setFixedHeight(30)
        self._search_input.textChanged.connect(self._on_search_changed)
        search_row.addWidget(self._search_input, stretch=1)
        layout.addLayout(search_row)

        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)
        layout.addWidget(self._tabs)

        self._tabs.addTab(self._build_downloads_tab(), "Downloads")
        self._tabs.addTab(self._build_bandwidth_tab(), "Bandwidth")
        self._tabs.addTab(self._build_connection_tab(), "Connection")
        self._tabs.addTab(self._build_behavior_tab(), "Behavior")
        self._tabs.addTab(self._build_remote_tab(), "Remote")
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

    def _build_search_index(self):
        """Index each top-level setting group without reading user-entered values."""
        self._search_groups.clear()
        for tab_index in range(self._tabs.count()):
            tab_name = self._tabs.tabText(tab_index)
            page = self._tabs.widget(tab_index)
            if isinstance(page, QScrollArea):
                page = page.widget()
            if page is None:
                continue

            groups = []
            for group in page.findChildren(QGroupBox):
                ancestor = group.parentWidget()
                nested = False
                while ancestor is not None and ancestor is not page:
                    if isinstance(ancestor, QGroupBox):
                        nested = True
                        break
                    ancestor = ancestor.parentWidget()
                if nested:
                    continue

                terms = [tab_name, group.title()]
                for child in group.findChildren(QWidget):
                    if isinstance(child, QLabel):
                        terms.append(child.text())
                    elif isinstance(child, QAbstractButton):
                        terms.append(child.text())
                    elif isinstance(child, QLineEdit):
                        terms.append(child.placeholderText())
                    elif isinstance(child, QPlainTextEdit):
                        terms.append(child.placeholderText())
                    elif isinstance(child, QComboBox):
                        terms.extend(child.itemText(i) for i in range(child.count()))

                for key, widget in self._widgets.items():
                    if group.isAncestorOf(widget):
                        terms.append(key)
                groups.append((group, terms))

            self._search_groups.append((tab_index, tab_name, groups))

    def _on_search_changed(self, query: str):
        query = query.strip()
        visible_tabs = []
        for tab_index, tab_name, groups in self._search_groups:
            tab_match = fuzzy_match(query, tab_name)
            tab_has_match = tab_match
            for group, terms in groups:
                group_match = tab_match or any(fuzzy_match(query, term) for term in terms)
                group.setVisible(not query or group_match)
                tab_has_match = tab_has_match or group_match
            self._tabs.setTabVisible(tab_index, not query or tab_has_match)
            if not query or tab_has_match:
                visible_tabs.append(tab_index)

        if visible_tabs and not self._tabs.isTabVisible(self._tabs.currentIndex()):
            self._tabs.setCurrentIndex(visible_tabs[0])

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

        vpn_group = QGroupBox("VPN binding / kill switch")
        vpn_layout = QGridLayout(vpn_group)
        vpn_layout.setSpacing(8)
        vpn_layout.addWidget(QLabel("VPN interface address:"), 0, 0)
        vpn_address = QLineEdit()
        vpn_address.setPlaceholderText("e.g. 10.8.0.2")
        self._widgets["vpn_bind_address"] = vpn_address
        vpn_layout.addWidget(vpn_address, 0, 1, 1, 2)
        vpn_kill = QCheckBox("Pause all torrents if this address disappears")
        self._widgets["vpn_kill_switch"] = vpn_kill
        vpn_layout.addWidget(vpn_kill, 1, 0, 1, 3)
        vpn_hint = QLabel(
            "Binding is restricted to this IPv4/IPv6 address. Recovery never auto-resumes torrents."
        )
        vpn_hint.setWordWrap(True)
        vpn_layout.addWidget(vpn_hint, 2, 0, 1, 3)
        layout.addWidget(vpn_group)

        i2p_group = QGroupBox("I2P / SAM Bridge")
        i2p_layout = QGridLayout(i2p_group)
        i2p_layout.setSpacing(8)

        i2p_enabled = QCheckBox("Enable I2P outbound transport")
        self._widgets["i2p_enabled"] = i2p_enabled
        i2p_layout.addWidget(i2p_enabled, 0, 0, 1, 3)

        i2p_layout.addWidget(QLabel("SAM host:"), 1, 0)
        i2p_host = QLineEdit()
        self._widgets["i2p_hostname"] = i2p_host
        i2p_layout.addWidget(i2p_host, 1, 1, 1, 2)

        i2p_layout.addWidget(QLabel("SAM port:"), 2, 0)
        i2p_port = QSpinBox()
        i2p_port.setRange(1, 65535)
        self._widgets["i2p_port"] = i2p_port
        i2p_layout.addWidget(i2p_port, 2, 1)

        i2p_mixed = QCheckBox("Allow mixed I2P/non-I2P torrents")
        self._widgets["i2p_allow_mixed"] = i2p_mixed
        i2p_layout.addWidget(i2p_mixed, 3, 0, 1, 3)

        layout.addWidget(i2p_group)

        tracker_proxy_group = QGroupBox("Per-tracker proxy routing")
        tracker_proxy_layout = QVBoxLayout(tracker_proxy_group)
        tracker_proxy_layout.addWidget(QLabel(
            "One rule per line: tracker URL | proxy URL. Supported proxies are SOCKS5 and HTTP(S)."
        ))
        tracker_proxy_rules = QPlainTextEdit()
        tracker_proxy_rules.setPlaceholderText(
            "https://tracker.example/announce | socks5://user:password@127.0.0.1:1080"
        )
        tracker_proxy_rules.setMaximumHeight(105)
        self._widgets["tracker_proxy_rules"] = tracker_proxy_rules
        tracker_proxy_layout.addWidget(tracker_proxy_rules)
        layout.addWidget(tracker_proxy_group)

        private_group = QGroupBox("Private-tracker profile")
        private_layout = QGridLayout(private_group)
        private_layout.setSpacing(8)
        private_enabled = QCheckBox(
            "Disable DHT, PEX, and LSD for torrents and cap unchoke slots"
        )
        self._widgets["private_tracker_profile"] = private_enabled
        private_layout.addWidget(private_enabled, 0, 0, 1, 3)
        private_layout.addWidget(QLabel("Unchoke slots cap:"), 1, 0)
        private_slots = QSpinBox()
        private_slots.setRange(1, 500)
        self._widgets["private_tracker_unchoke_slots"] = private_slots
        private_layout.addWidget(private_slots, 1, 1)
        private_layout.addWidget(QLabel("Applied to the session and existing torrents."), 1, 2)
        layout.addWidget(private_group)

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

        reputation_group = QGroupBox("Peer reputation memory")
        reputation_layout = QGridLayout(reputation_group)
        reputation_layout.setSpacing(8)
        reputation_enabled = QCheckBox("Remember repeated peer errors/disconnects across sessions")
        self._widgets["peer_reputation_enabled"] = reputation_enabled
        reputation_layout.addWidget(reputation_enabled, 0, 0, 1, 3)
        reputation_layout.addWidget(QLabel("Deprioritize after score:"), 1, 0)
        reputation_threshold = QSpinBox()
        reputation_threshold.setRange(1, 100)
        self._widgets["peer_reputation_threshold"] = reputation_threshold
        reputation_layout.addWidget(reputation_threshold, 1, 1)
        reputation_layout.addWidget(QLabel("Download/upload cap:"), 2, 0)
        reputation_limit = QSpinBox()
        reputation_limit.setRange(1, 1024)
        reputation_limit.setSuffix(" KiB/s")
        self._widgets["peer_reputation_limit_kib"] = reputation_limit
        reputation_layout.addWidget(reputation_limit, 2, 1)
        reputation_layout.addWidget(QLabel("Memory file:"), 3, 0)
        reputation_path = QLineEdit()
        reputation_path.setPlaceholderText("Blank = ~/.flux-torrent/peer-reputation.json")
        self._widgets["peer_reputation_path"] = reputation_path
        reputation_layout.addWidget(reputation_path, 3, 1)
        reputation_browse = QPushButton("Browse...")
        reputation_browse.clicked.connect(lambda: self._browse_save_file(reputation_path))
        reputation_layout.addWidget(reputation_browse, 3, 2)
        reputation_hint = QLabel(
            "Bad events are weighted: disconnect=1, error=3, hash failure=5. Flux limits matching peers instead of banning them."
        )
        reputation_hint.setWordWrap(True)
        reputation_layout.addWidget(reputation_hint, 4, 0, 1, 3)
        layout.addWidget(reputation_group)

        blocklist_group = QGroupBox("IP blocklist")
        blocklist_layout = QGridLayout(blocklist_group)
        blocklist_layout.setSpacing(8)

        blocklist_enabled = QCheckBox("Refresh automatically on a schedule")
        self._widgets["ip_blocklist_auto_refresh"] = blocklist_enabled
        blocklist_layout.addWidget(blocklist_enabled, 0, 0, 1, 3)

        blocklist_layout.addWidget(QLabel("Cache file:"), 1, 0)
        blocklist_path = QLineEdit()
        blocklist_path.setPlaceholderText("Optional local blocklist or refresh cache")
        self._widgets["ip_blocklist_path"] = blocklist_path
        blocklist_layout.addWidget(blocklist_path, 1, 1)
        blocklist_browse = QPushButton("Browse...")
        blocklist_browse.clicked.connect(lambda: self._browse_file_path(blocklist_path))
        blocklist_layout.addWidget(blocklist_browse, 1, 2)

        blocklist_layout.addWidget(QLabel("Mirrors (one per line):"), 2, 0, Qt.AlignmentFlag.AlignTop)
        blocklist_urls = QPlainTextEdit()
        blocklist_urls.setPlaceholderText("https://mirror.example/blocklist.p2p")
        blocklist_urls.setMaximumHeight(85)
        self._widgets["ip_blocklist_urls"] = blocklist_urls
        blocklist_layout.addWidget(blocklist_urls, 2, 1, 1, 2)

        blocklist_layout.addWidget(QLabel("Refresh interval:"), 3, 0)
        blocklist_hours = QSpinBox()
        blocklist_hours.setRange(1, 720)
        blocklist_hours.setSuffix(" hours")
        self._widgets["ip_blocklist_refresh_hours"] = blocklist_hours
        blocklist_layout.addWidget(blocklist_hours, 3, 1)
        blocklist_layout.addWidget(QLabel("Mirrors are tried in order; a failed refresh keeps the last cache."), 3, 2)

        blocklist_hint = QLabel(
            "Each successful download replaces the active filter atomically. Only HTTP(S) mirrors are accepted."
        )
        blocklist_hint.setWordWrap(True)
        blocklist_layout.addWidget(blocklist_hint, 4, 0, 1, 3)
        layout.addWidget(blocklist_group)

        layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(page)
        return scroll

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

        ratio_notification_group = QGroupBox("Ratio milestone notifications")
        ratio_notification_layout = QGridLayout(ratio_notification_group)
        ratio_notification_layout.setSpacing(8)

        ratio_notifications = QCheckBox("Show a desktop notification when a ratio milestone is reached")
        self._widgets["ratio_notifications_enabled"] = ratio_notifications
        ratio_notification_layout.addWidget(ratio_notifications, 0, 0, 1, 3)

        ratio_notification_layout.addWidget(QLabel("Milestones:"), 1, 0)
        ratio_milestones = QLineEdit()
        ratio_milestones.setPlaceholderText("1.0, 2.0, 3.0")
        self._widgets["ratio_notification_milestones"] = ratio_milestones
        ratio_notification_layout.addWidget(ratio_milestones, 1, 1, 1, 2)

        ratio_notification_layout.addWidget(QLabel("Suggested action:"), 2, 0)
        ratio_action = QComboBox()
        ratio_action.addItem("Review this torrent", "review")
        ratio_action.addItem("Consider pausing", "pause")
        ratio_action.addItem("Continue seeding", "seed")
        self._widgets["ratio_notification_action"] = ratio_action
        ratio_notification_layout.addWidget(ratio_action, 2, 1)

        ratio_hint = QLabel(
            "Notifications are informational only; Flux never pauses or removes a torrent automatically from this setting."
        )
        ratio_hint.setWordWrap(True)
        ratio_notification_layout.addWidget(ratio_hint, 3, 0, 1, 3)
        layout.addWidget(ratio_notification_group)

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

        label_group = QGroupBox("Label automation rules")
        label_layout = QVBoxLayout(label_group)
        label_layout.addWidget(QLabel(
            "JSON array; label matches a category or tag. Limits are bytes/s."
        ))
        label_rules = QPlainTextEdit()
        label_rules.setPlaceholderText(
            '[{"label":"movies","move_completed_path":"D:/Movies",'
            '"tracker_overrides":[],"ratio_limit":2.0,"upload_limit":0}]'
        )
        label_rules.setMaximumHeight(145)
        self._widgets["label_rules"] = label_rules
        label_layout.addWidget(label_rules)
        layout.addWidget(label_group)

        auto_delete_group = QGroupBox("Conditional auto-delete")
        auto_delete_layout = QGridLayout(auto_delete_group)
        auto_delete_layout.setSpacing(8)
        enabled = QCheckBox("Delete completed torrents when either limit is reached")
        self._widgets["auto_delete_enabled"] = enabled
        auto_delete_layout.addWidget(enabled, 0, 0, 1, 4)

        auto_delete_layout.addWidget(QLabel("Ratio at least:"), 1, 0)
        ratio = QDoubleSpinBox()
        ratio.setRange(0, 100)
        ratio.setDecimals(2)
        ratio.setSingleStep(0.1)
        ratio.setSpecialValueText("Disabled")
        self._widgets["auto_delete_ratio"] = ratio
        auto_delete_layout.addWidget(ratio, 1, 1)

        auto_delete_layout.addWidget(QLabel("Seeded days:"), 1, 2)
        seed_days = QDoubleSpinBox()
        seed_days.setRange(0, 999999)
        seed_days.setDecimals(1)
        seed_days.setSingleStep(1)
        seed_days.setSpecialValueText("Disabled")
        self._widgets["auto_delete_seed_days"] = seed_days
        auto_delete_layout.addWidget(seed_days, 1, 3)

        auto_delete_layout.addWidget(QLabel("Exclude label:"), 2, 0)
        exclude = QLineEdit()
        self._widgets["auto_delete_exclude_label"] = exclude
        auto_delete_layout.addWidget(exclude, 2, 1, 1, 3)

        delete_files = QCheckBox("Also delete downloaded files")
        self._widgets["auto_delete_files"] = delete_files
        auto_delete_layout.addWidget(delete_files, 3, 0, 1, 4)
        layout.addWidget(auto_delete_group)

        integrity_group = QGroupBox("SHA-256 integrity manifests")
        integrity_layout = QGridLayout(integrity_group)
        integrity_layout.setSpacing(8)
        integrity_auto = QCheckBox("Generate a JSON sidecar when a torrent completes")
        self._widgets["integrity_manifest_auto"] = integrity_auto
        integrity_layout.addWidget(integrity_auto, 0, 0, 1, 3)
        integrity_layout.addWidget(QLabel("Manifest directory:"), 1, 0)
        integrity_dir = QLineEdit()
        integrity_dir.setPlaceholderText("Blank = beside the torrent's save path")
        self._widgets["integrity_manifest_dir"] = integrity_dir
        integrity_layout.addWidget(integrity_dir, 1, 1)
        integrity_browse = QPushButton("Browse...")
        integrity_browse.clicked.connect(lambda: self._browse_folder(integrity_dir))
        integrity_layout.addWidget(integrity_browse, 1, 2)
        integrity_hint = QLabel(
            "Manual generation is available from a completed torrent's context menu. "
            "Files changing during hashing are rejected without replacing the previous manifest."
        )
        integrity_hint.setWordWrap(True)
        integrity_layout.addWidget(integrity_hint, 2, 0, 1, 3)
        layout.addWidget(integrity_group)

        schedule_group = QGroupBox("Per-torrent start/stop schedules")
        schedule_layout = QVBoxLayout(schedule_group)
        schedule_layout.addWidget(QLabel(
            "JSON object keyed by info hash. Days use Monday=0 through Sunday=6; overnight windows are supported."
        ))
        schedules = QPlainTextEdit()
        schedules.setPlaceholderText(
            '{"0123456789abcdef0123456789abcdef01234567": '
            '{"start":"08:00","stop":"23:00","days":[0,1,2,3,4],"enabled":true}}'
        )
        schedules.setMaximumHeight(125)
        self._widgets["torrent_schedules"] = schedules
        schedule_layout.addWidget(schedules)
        layout.addWidget(schedule_group)

        hooks_group = QGroupBox("Script hooks")
        hooks_layout = QVBoxLayout(hooks_group)
        hooks_layout.addWidget(QLabel(
            "JSON array of lifecycle commands. Events: on_add, on_finish, on_delete, on_error."
        ))
        hooks = QPlainTextEdit()
        hooks.setPlaceholderText(
            '[{"event":"on_finish","command":"powershell -File C:/Scripts/done.ps1",'
            '"enabled":true,"timeout_seconds":60}]'
        )
        hooks.setMaximumHeight(135)
        self._widgets["script_hooks"] = hooks
        hooks_layout.addWidget(hooks)
        layout.addWidget(hooks_group)

        layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(page)
        return scroll

    # --- Remote Tab ---

    def _build_remote_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        web_group = QGroupBox("Web UI / API")
        wg = QGridLayout(web_group)
        wg.setSpacing(8)

        enabled = QCheckBox("Enable remote Web UI")
        self._widgets["remote_enabled"] = enabled
        wg.addWidget(enabled, 0, 0, 1, 3)

        wg.addWidget(QLabel("Bind address:"), 1, 0)
        host = QLineEdit()
        host.setPlaceholderText("127.0.0.1")
        self._widgets["remote_host"] = host
        wg.addWidget(host, 1, 1, 1, 2)

        wg.addWidget(QLabel("Port:"), 2, 0)
        port = QSpinBox()
        port.setRange(1, 65535)
        self._widgets["remote_port"] = port
        wg.addWidget(port, 2, 1)

        wg.addWidget(QLabel("Username:"), 3, 0)
        username = QLineEdit()
        self._widgets["remote_username"] = username
        wg.addWidget(username, 3, 1, 1, 2)

        wg.addWidget(QLabel("Password:"), 4, 0)
        password = QLineEdit()
        password.setEchoMode(QLineEdit.EchoMode.Password)
        self._widgets["remote_password"] = password
        wg.addWidget(password, 4, 1, 1, 2)

        wg.addWidget(QLabel("Bearer token:"), 5, 0)
        token = QLineEdit()
        token.setEchoMode(QLineEdit.EchoMode.Password)
        self._widgets["remote_token"] = token
        wg.addWidget(token, 5, 1, 1, 2)

        layout.addWidget(web_group)

        tls_group = QGroupBox("TLS / mTLS")
        tg = QGridLayout(tls_group)
        tg.setSpacing(8)

        cert = QLineEdit()
        self._widgets["remote_tls_certfile"] = cert
        tg.addWidget(QLabel("Certificate:"), 0, 0)
        tg.addWidget(cert, 0, 1)
        cert_btn = QPushButton("Browse...")
        cert_btn.setFixedWidth(80)
        cert_btn.clicked.connect(lambda: self._browse_file_path(cert))
        tg.addWidget(cert_btn, 0, 2)

        key = QLineEdit()
        self._widgets["remote_tls_keyfile"] = key
        tg.addWidget(QLabel("Private key:"), 1, 0)
        tg.addWidget(key, 1, 1)
        key_btn = QPushButton("Browse...")
        key_btn.setFixedWidth(80)
        key_btn.clicked.connect(lambda: self._browse_file_path(key))
        tg.addWidget(key_btn, 1, 2)

        ca = QLineEdit()
        self._widgets["remote_tls_ca_file"] = ca
        tg.addWidget(QLabel("Client CA:"), 2, 0)
        tg.addWidget(ca, 2, 1)
        ca_btn = QPushButton("Browse...")
        ca_btn.setFixedWidth(80)
        ca_btn.clicked.connect(lambda: self._browse_file_path(ca))
        tg.addWidget(ca_btn, 2, 2)

        require_client = QCheckBox("Require client certificate")
        self._widgets["remote_require_client_cert"] = require_client
        tg.addWidget(require_client, 3, 0, 1, 3)

        layout.addWidget(tls_group)

        client_group = QGroupBox("Remote desktop client mode")
        cg = QGridLayout(client_group)
        cg.setSpacing(8)

        client_enabled = QCheckBox("Connect this desktop to a remote Flux daemon")
        self._widgets["remote_client_enabled"] = client_enabled
        cg.addWidget(client_enabled, 0, 0, 1, 3)

        cg.addWidget(QLabel("Daemon URL:"), 1, 0)
        client_url = QLineEdit()
        client_url.setPlaceholderText("http://127.0.0.1:8090/")
        self._widgets["remote_client_url"] = client_url
        cg.addWidget(client_url, 1, 1, 1, 2)

        cg.addWidget(QLabel("Token:"), 2, 0)
        client_token = QLineEdit()
        client_token.setEchoMode(QLineEdit.EchoMode.Password)
        self._widgets["remote_client_token"] = client_token
        cg.addWidget(client_token, 2, 1, 1, 2)

        cg.addWidget(QLabel("Username:"), 3, 0)
        client_username = QLineEdit()
        self._widgets["remote_client_username"] = client_username
        cg.addWidget(client_username, 3, 1, 1, 2)

        cg.addWidget(QLabel("Password:"), 4, 0)
        client_password = QLineEdit()
        client_password.setEchoMode(QLineEdit.EchoMode.Password)
        self._widgets["remote_client_password"] = client_password
        cg.addWidget(client_password, 4, 1, 1, 2)

        client_tls = QCheckBox("Verify TLS certificates")
        self._widgets["remote_client_verify_tls"] = client_tls
        cg.addWidget(client_tls, 5, 0, 1, 3)

        layout.addWidget(client_group)
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
        self._widgets["vpn_bind_address"].setText(s.get("vpn_bind_address", ""))
        self._widgets["vpn_kill_switch"].setChecked(s.get("vpn_kill_switch", False))
        self._widgets["ip_blocklist_path"].setText(s.get("ip_blocklist_path", ""))
        self._widgets["ip_blocklist_auto_refresh"].setChecked(
            s.get("ip_blocklist_auto_refresh", False)
        )
        self._widgets["ip_blocklist_urls"].setPlainText("\n".join(
            normalize_blocklist_urls(s.get("ip_blocklist_urls", []))
        ))
        try:
            blocklist_hours = int(s.get("ip_blocklist_refresh_hours", 24) or 24)
        except (TypeError, ValueError):
            blocklist_hours = 24
        self._widgets["ip_blocklist_refresh_hours"].setValue(blocklist_hours)
        self._widgets["i2p_enabled"].setChecked(s.get("i2p_enabled", False))
        self._widgets["i2p_hostname"].setText(s.get("i2p_hostname", "127.0.0.1"))
        self._widgets["i2p_port"].setValue(s.get("i2p_port", 7656))
        self._widgets["i2p_allow_mixed"].setChecked(s.get("i2p_allow_mixed", False))
        rules = s.get("tracker_proxy_rules", []) or []
        if isinstance(rules, dict):
            rules = [{"tracker_url": key, "proxy_url": value} for key, value in rules.items()]
        self._widgets["tracker_proxy_rules"].setPlainText("\n".join(
            f"{item.get('tracker_url', item.get('tracker', ''))} | "
            f"{item.get('proxy_url', item.get('proxy', ''))}"
            for item in rules if isinstance(item, dict)
        ))
        self._widgets["private_tracker_profile"].setChecked(
            s.get("private_tracker_profile", False)
        )
        self._widgets["private_tracker_unchoke_slots"].setValue(
            s.get("private_tracker_unchoke_slots", 4)
        )
        self._widgets["dht_enabled"].setChecked(s.get("dht_enabled", True))
        self._widgets["pex_enabled"].setChecked(s.get("pex_enabled", True))
        self._widgets["lsd_enabled"].setChecked(s.get("lsd_enabled", True))
        self._widgets["encryption_mode"].setCurrentIndex(s.get("encryption_mode", 1))
        self._widgets["peer_filter_enabled"].setChecked(s.get("peer_filter_enabled", True))
        self._widgets["auto_ban_xunlei"].setChecked(s.get("auto_ban_xunlei", True))
        self._widgets["auto_ban_qq"].setChecked(s.get("auto_ban_qq", True))
        self._widgets["auto_ban_baidu"].setChecked(s.get("auto_ban_baidu", True))
        self._widgets["peer_reputation_enabled"].setChecked(
            s.get("peer_reputation_enabled", True)
        )
        self._widgets["peer_reputation_threshold"].setValue(
            s.get("peer_reputation_threshold", 3)
        )
        self._widgets["peer_reputation_limit_kib"].setValue(
            max(1, int(s.get("peer_reputation_limit", 16384) or 16384) // 1024)
        )
        self._widgets["peer_reputation_path"].setText(
            s.get("peer_reputation_path", "")
        )

        # Behavior
        self._widgets["confirm_on_delete"].setChecked(s.get("confirm_on_delete", True))
        self._widgets["confirm_on_delete_files"].setChecked(s.get("confirm_on_delete_files", True))
        self._widgets["minimize_to_tray"].setChecked(s.get("minimize_to_tray", True))
        self._widgets["close_to_tray"].setChecked(s.get("close_to_tray", True))
        self._widgets["start_minimized"].setChecked(s.get("start_minimized", False))
        self._widgets["ratio_notifications_enabled"].setChecked(
            s.get("ratio_notifications_enabled", False)
        )
        self._widgets["ratio_notification_milestones"].setText(
            ", ".join(str(value) for value in normalize_ratio_milestones(
                s.get("ratio_notification_milestones", [1.0, 2.0])
            ))
        )
        ratio_action = self._widgets["ratio_notification_action"]
        action_key = s.get("ratio_notification_action", "review")
        action_index = ratio_action.findData(action_key)
        ratio_action.setCurrentIndex(max(0, action_index))
        self._widgets["auto_update_trackers"].setChecked(s.get("auto_update_trackers", False))
        self._widgets["tracker_list_url"].setText(s.get("tracker_list_url", ""))
        self._widgets["label_rules"].setPlainText(json.dumps(
            s.get("label_rules", []) or [], indent=2
        ))
        self._widgets["auto_delete_enabled"].setChecked(s.get("auto_delete_enabled", False))
        self._widgets["auto_delete_ratio"].setValue(s.get("auto_delete_ratio", 0.0))
        self._widgets["auto_delete_seed_days"].setValue(s.get("auto_delete_seed_days", 0.0))
        self._widgets["auto_delete_exclude_label"].setText(
            s.get("auto_delete_exclude_label", "archive")
        )
        self._widgets["auto_delete_files"].setChecked(s.get("auto_delete_files", True))
        self._widgets["integrity_manifest_auto"].setChecked(
            s.get("integrity_manifest_auto", False)
        )
        self._widgets["integrity_manifest_dir"].setText(
            s.get("integrity_manifest_dir", "")
        )
        self._widgets["torrent_schedules"].setPlainText(json.dumps(
            s.get("torrent_schedules", {}) or {}, indent=2
        ))
        self._widgets["script_hooks"].setPlainText(json.dumps(
            s.get("script_hooks", []) or [], indent=2
        ))

        # Remote
        self._widgets["remote_enabled"].setChecked(s.get("remote_enabled", False))
        self._widgets["remote_host"].setText(s.get("remote_host", "127.0.0.1"))
        self._widgets["remote_port"].setValue(s.get("remote_port", 8090))
        self._widgets["remote_username"].setText(s.get("remote_username", "admin"))
        self._widgets["remote_password"].setText(s.get("remote_password", ""))
        self._widgets["remote_token"].setText(s.get("remote_token", ""))
        self._widgets["remote_tls_certfile"].setText(s.get("remote_tls_certfile", ""))
        self._widgets["remote_tls_keyfile"].setText(s.get("remote_tls_keyfile", ""))
        self._widgets["remote_tls_ca_file"].setText(s.get("remote_tls_ca_file", ""))
        self._widgets["remote_require_client_cert"].setChecked(
            s.get("remote_require_client_cert", False)
        )
        self._widgets["remote_client_enabled"].setChecked(s.get("remote_client_enabled", False))
        self._widgets["remote_client_url"].setText(
            s.get("remote_client_url", "http://127.0.0.1:8090/")
        )
        self._widgets["remote_client_token"].setText(s.get("remote_client_token", ""))
        self._widgets["remote_client_username"].setText(s.get("remote_client_username", "admin"))
        self._widgets["remote_client_password"].setText(s.get("remote_client_password", ""))
        self._widgets["remote_client_verify_tls"].setChecked(
            s.get("remote_client_verify_tls", True)
        )

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
        s.set("vpn_bind_address", self._widgets["vpn_bind_address"].text().strip())
        s.set("vpn_kill_switch", self._widgets["vpn_kill_switch"].isChecked())
        s.set("ip_blocklist_path", self._widgets["ip_blocklist_path"].text().strip())
        s.set(
            "ip_blocklist_auto_refresh",
            self._widgets["ip_blocklist_auto_refresh"].isChecked(),
        )
        s.set(
            "ip_blocklist_urls",
            normalize_blocklist_urls(self._widgets["ip_blocklist_urls"].toPlainText()),
        )
        s.set(
            "ip_blocklist_refresh_hours",
            self._widgets["ip_blocklist_refresh_hours"].value(),
        )
        s.set("i2p_enabled", self._widgets["i2p_enabled"].isChecked())
        s.set("i2p_hostname", self._widgets["i2p_hostname"].text().strip() or "127.0.0.1")
        s.set("i2p_port", self._widgets["i2p_port"].value())
        s.set("i2p_allow_mixed", self._widgets["i2p_allow_mixed"].isChecked())
        s.set("tracker_proxy_rules", build_tracker_proxy_rules({
            "tracker_proxy_rules": self._widgets["tracker_proxy_rules"].toPlainText()
        }))
        s.set("private_tracker_profile", self._widgets["private_tracker_profile"].isChecked())
        s.set(
            "private_tracker_unchoke_slots",
            self._widgets["private_tracker_unchoke_slots"].value(),
        )
        s.set("dht_enabled", self._widgets["dht_enabled"].isChecked())
        s.set("pex_enabled", self._widgets["pex_enabled"].isChecked())
        s.set("lsd_enabled", self._widgets["lsd_enabled"].isChecked())
        s.set("encryption_mode", self._widgets["encryption_mode"].currentIndex())
        s.set("peer_filter_enabled", self._widgets["peer_filter_enabled"].isChecked())
        s.set("auto_ban_xunlei", self._widgets["auto_ban_xunlei"].isChecked())
        s.set("auto_ban_qq", self._widgets["auto_ban_qq"].isChecked())
        s.set("auto_ban_baidu", self._widgets["auto_ban_baidu"].isChecked())
        s.set("peer_reputation_enabled", self._widgets["peer_reputation_enabled"].isChecked())
        s.set("peer_reputation_threshold", self._widgets["peer_reputation_threshold"].value())
        s.set(
            "peer_reputation_limit",
            self._widgets["peer_reputation_limit_kib"].value() * 1024,
        )
        s.set("peer_reputation_path", self._widgets["peer_reputation_path"].text().strip())

        # Behavior
        s.set("confirm_on_delete", self._widgets["confirm_on_delete"].isChecked())
        s.set("confirm_on_delete_files", self._widgets["confirm_on_delete_files"].isChecked())
        s.set("minimize_to_tray", self._widgets["minimize_to_tray"].isChecked())
        s.set("close_to_tray", self._widgets["close_to_tray"].isChecked())
        s.set("start_minimized", self._widgets["start_minimized"].isChecked())
        s.set(
            "ratio_notifications_enabled",
            self._widgets["ratio_notifications_enabled"].isChecked(),
        )
        s.set(
            "ratio_notification_milestones",
            normalize_ratio_milestones(
                self._widgets["ratio_notification_milestones"].text()
            ),
        )
        s.set(
            "ratio_notification_action",
            self._widgets["ratio_notification_action"].currentData() or "review",
        )
        s.set("auto_update_trackers", self._widgets["auto_update_trackers"].isChecked())
        s.set("tracker_list_url", self._widgets["tracker_list_url"].text())
        try:
            label_rules = json.loads(self._widgets["label_rules"].toPlainText() or "[]")
        except json.JSONDecodeError:
            label_rules = s.get("label_rules", []) or []
        s.set("label_rules", build_label_automation_rules({"label_rules": label_rules}))
        s.set("auto_delete_enabled", self._widgets["auto_delete_enabled"].isChecked())
        s.set("auto_delete_ratio", self._widgets["auto_delete_ratio"].value())
        s.set("auto_delete_seed_days", self._widgets["auto_delete_seed_days"].value())
        s.set("auto_delete_exclude_label", self._widgets["auto_delete_exclude_label"].text().strip())
        s.set("auto_delete_files", self._widgets["auto_delete_files"].isChecked())
        s.set("integrity_manifest_auto", self._widgets["integrity_manifest_auto"].isChecked())
        s.set("integrity_manifest_dir", self._widgets["integrity_manifest_dir"].text().strip())
        try:
            schedules = json.loads(self._widgets["torrent_schedules"].toPlainText() or "{}")
        except json.JSONDecodeError:
            schedules = s.get("torrent_schedules", {}) or {}
        s.set("torrent_schedules", build_torrent_schedule_settings({
            "torrent_schedules": schedules
        }))
        try:
            script_hooks = json.loads(self._widgets["script_hooks"].toPlainText() or "[]")
        except json.JSONDecodeError:
            script_hooks = s.get("script_hooks", []) or []
        s.set("script_hooks", script_hooks if isinstance(script_hooks, list) else [])

        # Remote
        s.set("remote_enabled", self._widgets["remote_enabled"].isChecked())
        s.set("remote_host", self._widgets["remote_host"].text().strip() or "127.0.0.1")
        s.set("remote_port", self._widgets["remote_port"].value())
        s.set("remote_username", self._widgets["remote_username"].text().strip() or "admin")
        s.set("remote_password", self._widgets["remote_password"].text())
        s.set("remote_token", self._widgets["remote_token"].text())
        s.set("remote_tls_certfile", self._widgets["remote_tls_certfile"].text().strip())
        s.set("remote_tls_keyfile", self._widgets["remote_tls_keyfile"].text().strip())
        s.set("remote_tls_ca_file", self._widgets["remote_tls_ca_file"].text().strip())
        s.set("remote_require_client_cert", self._widgets["remote_require_client_cert"].isChecked())
        s.set("remote_client_enabled", self._widgets["remote_client_enabled"].isChecked())
        s.set("remote_client_url", self._widgets["remote_client_url"].text().strip())
        s.set("remote_client_token", self._widgets["remote_client_token"].text())
        s.set("remote_client_username", self._widgets["remote_client_username"].text().strip())
        s.set("remote_client_password", self._widgets["remote_client_password"].text())
        s.set("remote_client_verify_tls", self._widgets["remote_client_verify_tls"].isChecked())

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

    def _browse_file_path(self, line_edit: QLineEdit):
        path, _ = QFileDialog.getOpenFileName(self, "Select File", line_edit.text())
        if path:
            line_edit.setText(path)

    def _browse_save_file(self, line_edit: QLineEdit):
        path, _ = QFileDialog.getSaveFileName(self, "Select Memory File", line_edit.text())
        if path:
            line_edit.setText(path)
