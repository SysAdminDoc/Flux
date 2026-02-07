"""Session worker - runs libtorrent session on a dedicated QThread.

Architecture:
  MainWindow -> (queued signal) -> SessionWorker [on QThread]
  SessionWorker -> (signal) -> MainWindow [on GUI thread]

All libtorrent FFI happens on the worker thread. The GUI thread never
touches libtorrent objects directly. Torrent snapshots (pure Python
dataclasses) cross the thread boundary via signals.
"""

import os
import json
import time
import sqlite3
import logging
from pathlib import Path
from typing import Optional, Dict
from datetime import datetime
from dataclasses import dataclass, field

import libtorrent as lt
from PyQt6.QtCore import QObject, QTimer, pyqtSignal, pyqtSlot, QThread, QMetaObject, Qt

from flux.core.torrent import Torrent, TorrentSnapshot
from flux.core.settings import Settings
from flux.core.peer_filter import PeerFilter

logger = logging.getLogger(__name__)

_tf = getattr(lt, 'torrent_flags', None) or getattr(lt, 'torrent_flags_t', None)
_FLAG_PAUSED = getattr(_tf, 'paused', 0x20) if _tf else 0x20
_FLAG_AUTO_MANAGED = getattr(_tf, 'auto_managed', 0x40) if _tf else 0x40
_FLAG_SEQUENTIAL = getattr(_tf, 'sequential_download', 0x200) if _tf else 0x200

_SCHEMA_VERSION = 2


@dataclass
class SessionStats:
    """Thread-safe snapshot of session-wide statistics."""
    download_rate: int = 0
    upload_rate: int = 0
    dht_nodes: int = 0
    dl_history: list = field(default_factory=list)
    ul_history: list = field(default_factory=list)
    torrent_count: int = 0
    torrents: list = field(default_factory=list)  # List[TorrentSnapshot]


@dataclass
class DetailData:
    """Thread-safe detail data for the focused torrent."""
    info_hash: str = ""
    files: list = field(default_factory=list)
    peers: list = field(default_factory=list)
    trackers: list = field(default_factory=list)
    pieces: list = field(default_factory=list)
    piece_length: int = 0
    dl_history: list = field(default_factory=list)
    ul_history: list = field(default_factory=list)


class SessionWorker(QObject):
    """Owns the libtorrent session. Lives on a QThread.

    All public methods decorated with @pyqtSlot are safe to call from
    the GUI thread via queued connections.
    """

    # --- Outbound signals (worker -> GUI) ---
    torrent_added = pyqtSignal(str)
    torrent_removed = pyqtSignal(str)
    torrent_finished = pyqtSignal(str)
    torrent_error = pyqtSignal(str, str)
    torrent_metadata = pyqtSignal(str)
    stats_updated = pyqtSignal(object)    # SessionStats
    detail_updated = pyqtSignal(object)   # DetailData
    peer_banned = pyqtSignal(str, str)
    magnet_uri_ready = pyqtSignal(str)    # magnet URI string
    started = pyqtSignal()
    stopped = pyqtSignal()

    def __init__(self, cfg: dict):
        super().__init__()
        self._cfg = cfg  # plain dict snapshot (thread-safe, no SQLite)
        self._session: Optional[lt.session] = None
        self._torrents: Dict[str, Torrent] = {}
        self._peer_filter = PeerFilter()
        self._resume_db: Optional[sqlite3.Connection] = None
        self._ip_filter: Optional[lt.ip_filter] = None

        self._session_dl_history: list = []
        self._session_ul_history: list = []
        self._max_session_history = 300

        self._focused_hash: str = ""

        self._alert_timer: Optional[QTimer] = None
        self._stats_timer: Optional[QTimer] = None
        self._save_timer: Optional[QTimer] = None
        self._schedule_timer: Optional[QTimer] = None

    # --- Lifecycle (called on worker thread) ---

    @pyqtSlot()
    def initialize(self):
        """Called when the thread starts. Creates the lt session."""
        logger.info("SessionWorker initializing on thread...")

        settings = {
            'user_agent': 'FluxTorrent/1.0',
            'peer_fingerprint': '-FX1000-',
            'listen_interfaces': '0.0.0.0:{p},[::0]:{p}'.format(
                p=self._cfg.get("listen_port", 6881)),
            'connections_limit': self._cfg.get("max_connections", 500),
            'max_peerlist_size': 4000,
            'enable_dht': self._cfg.get("dht_enabled", True),
            'enable_lsd': self._cfg.get("lsd_enabled", True),
            'active_downloads': self._cfg.get("max_active_downloads", 5),
            'active_seeds': self._cfg.get("max_active_uploads", 5),
            'active_limit': self._cfg.get("max_active_torrents", 10),
            'cache_size': 2048,
            'send_buffer_watermark': 512 * 1024,
            'send_buffer_watermark_factor': 150,
        }

        try:
            settings['alert_mask'] = (
                lt.alert.category_t.error_notification |
                lt.alert.category_t.status_notification |
                lt.alert.category_t.storage_notification |
                lt.alert.category_t.peer_notification |
                lt.alert.category_t.tracker_notification |
                lt.alert.category_t.ip_block_notification
            )
        except AttributeError:
            settings['alert_mask'] = 0x7fffffff

        dl_limit = self._cfg.get("max_download_speed", 0)
        ul_limit = self._cfg.get("max_upload_speed", 0)
        if dl_limit > 0:
            settings['download_rate_limit'] = dl_limit
        if ul_limit > 0:
            settings['upload_rate_limit'] = ul_limit

        enc = self._cfg.get("encryption_mode", 1)
        try:
            if enc == 0:
                settings['out_enc_policy'] = int(lt.enc_policy.disabled)
                settings['in_enc_policy'] = int(lt.enc_policy.disabled)
            elif enc == 1:
                settings['out_enc_policy'] = int(lt.enc_policy.enabled)
                settings['in_enc_policy'] = int(lt.enc_policy.enabled)
            elif enc == 2:
                settings['out_enc_policy'] = int(lt.enc_policy.forced)
                settings['in_enc_policy'] = int(lt.enc_policy.forced)
        except AttributeError:
            if enc == 1:
                settings['out_enc_policy'] = 1
                settings['in_enc_policy'] = 1
            elif enc == 2:
                settings['out_enc_policy'] = 2
                settings['in_enc_policy'] = 2

        self._session = lt.session(settings)
        self._ip_filter = self._session.get_ip_filter()
        self._peer_filter.configure(self._cfg)

        self._init_resume_db()
        self._load_ip_blocklist()
        self._load_resume_data()

        # Create timers on the worker thread
        self._alert_timer = QTimer(self)
        self._alert_timer.timeout.connect(self._process_alerts)
        self._alert_timer.start(500)

        self._stats_timer = QTimer(self)
        self._stats_timer.timeout.connect(self._update_stats)
        self._stats_timer.start(1000)

        self._save_timer = QTimer(self)
        self._save_timer.timeout.connect(self._save_all_resume_data)
        self._save_timer.start(300000)

        self._schedule_timer = QTimer(self)
        self._schedule_timer.timeout.connect(self._check_bandwidth_schedule)
        self._schedule_timer.start(60000)

        logger.info(f"SessionWorker started on port {self._cfg.get('listen_port', 6881)}")
        self.started.emit()

    @pyqtSlot()
    def shutdown(self):
        """Gracefully stop the session. MUST run on worker thread."""
        logger.info("SessionWorker shutting down...")

        if self._alert_timer:
            self._alert_timer.stop()
        if self._stats_timer:
            self._stats_timer.stop()
        if self._save_timer:
            self._save_timer.stop()
        if self._schedule_timer:
            self._schedule_timer.stop()

        if self._session:
            self._session.pause()
            self._save_all_resume_data_sync()
            del self._session
            self._session = None

        if self._resume_db:
            self._resume_db.close()
            self._resume_db = None

        self._torrents.clear()
        self.stopped.emit()
        logger.info("SessionWorker stopped.")

    # --- Torrent operations (slots callable from GUI thread) ---

    @pyqtSlot(str, str, str, str, bool, bool)
    def add_torrent_file(self, filepath: str, save_path: str = "",
                         category: str = "", tags_json: str = "[]",
                         paused: bool = False, sequential: bool = False):
        if not self._session:
            return

        try:
            tags = json.loads(tags_json) if tags_json else []
            ti = lt.torrent_info(filepath)
            atp = lt.add_torrent_params()
            atp.ti = ti
            atp.save_path = save_path or self._cfg.get("default_save_path")

            if paused:
                atp.flags |= _FLAG_PAUSED
                atp.flags &= ~_FLAG_AUTO_MANAGED
            else:
                atp.flags |= _FLAG_AUTO_MANAGED
            if sequential:
                atp.flags |= _FLAG_SEQUENTIAL

            handle = self._session.add_torrent(atp)
            info_hash = str(handle.info_hash())

            if info_hash not in self._torrents:
                torrent = Torrent(handle, category=category, tags=tags)
                self._torrents[info_hash] = torrent
                self.torrent_added.emit(info_hash)
                logger.info(f"Added torrent: {torrent.name}")
            else:
                logger.warning(f"Torrent already exists: {info_hash}")

        except Exception as e:
            logger.error(f"Failed to add torrent file: {e}")

    @pyqtSlot(str, str, str, str, bool)
    def add_magnet(self, uri: str, save_path: str = "",
                   category: str = "", tags_json: str = "[]",
                   paused: bool = False):
        if not self._session:
            return

        if not uri or not uri.strip().startswith("magnet:"):
            logger.error(f"Invalid magnet URI: {uri[:80] if uri else '(empty)'}")
            return

        try:
            tags = json.loads(tags_json) if tags_json else []
            try:
                atp = lt.parse_magnet_uri(uri.strip())
            except AttributeError:
                atp = lt.parse_magnet_uri_dict(uri.strip())

            atp.save_path = save_path or self._cfg.get("default_save_path")

            if paused:
                atp.flags |= _FLAG_PAUSED
                atp.flags &= ~_FLAG_AUTO_MANAGED
            else:
                atp.flags |= _FLAG_AUTO_MANAGED

            handle = self._session.add_torrent(atp)
            info_hash = str(handle.info_hash())

            if info_hash not in self._torrents:
                torrent = Torrent(handle, category=category, tags=tags)
                self._torrents[info_hash] = torrent
                self.torrent_added.emit(info_hash)
                logger.info(f"Added magnet: {info_hash}")

        except Exception as e:
            logger.error(f"Failed to add magnet: {e}", exc_info=True)

    @pyqtSlot(str, bool)
    def remove_torrent(self, info_hash: str, delete_files: bool = False):
        torrent = self._torrents.get(info_hash)
        if not torrent or not self._session:
            return

        name = torrent.name
        if delete_files:
            try:
                self._session.remove_torrent(torrent.handle, lt.session.delete_files)
            except AttributeError:
                try:
                    self._session.remove_torrent(torrent.handle, lt.options_t.delete_files)
                except AttributeError:
                    self._session.remove_torrent(torrent.handle, 1)
        else:
            self._session.remove_torrent(torrent.handle)

        del self._torrents[info_hash]

        if self._resume_db:
            try:
                self._resume_db.execute("DELETE FROM resume_data WHERE info_hash = ?", (info_hash,))
                self._resume_db.commit()
            except Exception as e:
                logger.error(f"Failed to remove resume data: {e}")

        self.torrent_removed.emit(info_hash)
        logger.info(f"Removed torrent: {name} (delete_files={delete_files})")

    @pyqtSlot(str)
    def pause_torrent(self, info_hash: str):
        t = self._torrents.get(info_hash)
        if t:
            t.pause()

    @pyqtSlot(str)
    def resume_torrent(self, info_hash: str):
        t = self._torrents.get(info_hash)
        if t:
            t.resume()

    @pyqtSlot()
    def pause_all(self):
        for t in self._torrents.values():
            t.pause()

    @pyqtSlot()
    def resume_all(self):
        for t in self._torrents.values():
            t.resume()

    @pyqtSlot(str)
    def force_recheck(self, info_hash: str):
        t = self._torrents.get(info_hash)
        if t:
            t.force_recheck()

    @pyqtSlot(str)
    def force_reannounce(self, info_hash: str):
        t = self._torrents.get(info_hash)
        if t:
            t.force_reannounce()

    @pyqtSlot(str)
    def force_resume(self, info_hash: str):
        t = self._torrents.get(info_hash)
        if t:
            t.force_resume()

    @pyqtSlot(str, int, int)
    def set_torrent_speed_limit(self, info_hash: str, dl_limit: int, ul_limit: int):
        t = self._torrents.get(info_hash)
        if t:
            t.set_download_limit(dl_limit)
            t.set_upload_limit(ul_limit)

    @pyqtSlot(str, str)
    def queue_action(self, info_hash: str, action: str):
        """Queue position change: top, up, down, bottom."""
        t = self._torrents.get(info_hash)
        if not t:
            return
        if action == "top":
            t.queue_top()
        elif action == "up":
            t.queue_up()
        elif action == "down":
            t.queue_down()
        elif action == "bottom":
            t.queue_bottom()

    @pyqtSlot(str, bool)
    def set_sequential(self, info_hash: str, enabled: bool):
        t = self._torrents.get(info_hash)
        if t:
            t.set_sequential(enabled)

    @pyqtSlot(str)
    def set_focused_torrent(self, info_hash: str):
        """Set which torrent provides detail data (files/peers/etc)."""
        self._focused_hash = info_hash

    @pyqtSlot(str, int, int)
    def set_file_priority(self, info_hash: str, file_index: int, priority: int):
        """Set file priority for a torrent."""
        t = self._torrents.get(info_hash)
        if t:
            t.set_file_priority(file_index, priority)

    @pyqtSlot(str, str)
    def add_tracker(self, info_hash: str, url: str):
        """Add a tracker to a torrent."""
        t = self._torrents.get(info_hash)
        if t:
            t.add_tracker(url)

    @pyqtSlot(str, str)
    def remove_tracker(self, info_hash: str, url: str):
        """Remove a tracker from a torrent."""
        t = self._torrents.get(info_hash)
        if t:
            t.remove_tracker(url)

    @pyqtSlot(str)
    def request_magnet_uri(self, info_hash: str):
        """Generate a magnet URI and emit it via signal."""
        t = self._torrents.get(info_hash)
        if t and t.is_valid:
            try:
                uri = lt.make_magnet_uri(t.handle)
                self.magnet_uri_ready.emit(uri)
            except Exception as e:
                logger.error(f"Failed to generate magnet URI: {e}")

    @pyqtSlot(dict)
    def apply_settings(self, cfg: dict = None):
        """Re-apply settings. Accepts a fresh config dict from the GUI thread."""
        if cfg is not None:
            self._cfg = cfg
        if not self._session:
            return
        settings = {}
        dl_limit = self._cfg.get("max_download_speed", 0)
        ul_limit = self._cfg.get("max_upload_speed", 0)
        settings['download_rate_limit'] = dl_limit if dl_limit > 0 else 0
        settings['upload_rate_limit'] = ul_limit if ul_limit > 0 else 0
        settings['connections_limit'] = self._cfg.get("max_connections", 500)
        settings['active_downloads'] = self._cfg.get("max_active_downloads", 5)
        settings['active_seeds'] = self._cfg.get("max_active_uploads", 5)
        settings['active_limit'] = self._cfg.get("max_active_torrents", 10)
        self._session.apply_settings(settings)
        self._peer_filter.configure(self._cfg)
        self._load_ip_blocklist()

    # --- Internal: Resume DB ---

    def _init_resume_db(self):
        config_dir = Path.home() / ".flux-torrent"
        config_dir.mkdir(parents=True, exist_ok=True)
        self._resume_db = sqlite3.connect(str(config_dir / "resume.db"))

        self._resume_db.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                version INTEGER NOT NULL
            )
        """)

        version = self._get_schema_version()
        if version == 0:
            self._resume_db.execute("""
                CREATE TABLE IF NOT EXISTS resume_data (
                    info_hash TEXT PRIMARY KEY,
                    resume_data BLOB NOT NULL,
                    name TEXT DEFAULT '',
                    category TEXT DEFAULT '',
                    tags TEXT DEFAULT '[]',
                    added_time REAL DEFAULT 0,
                    save_path TEXT DEFAULT ''
                )
            """)
            self._set_schema_version(_SCHEMA_VERSION)
        elif version < _SCHEMA_VERSION:
            self._migrate_schema(version)
        self._resume_db.commit()

    def _get_schema_version(self) -> int:
        try:
            cursor = self._resume_db.execute("SELECT version FROM schema_version WHERE id = 1")
            row = cursor.fetchone()
            return row[0] if row else 0
        except sqlite3.OperationalError:
            return 0

    def _set_schema_version(self, version: int):
        self._resume_db.execute(
            "INSERT OR REPLACE INTO schema_version (id, version) VALUES (1, ?)", (version,))
        self._resume_db.commit()

    def _migrate_schema(self, from_version: int):
        logger.info(f"Migrating resume DB from v{from_version} to v{_SCHEMA_VERSION}")
        if from_version < 1:
            self._resume_db.execute("""
                CREATE TABLE IF NOT EXISTS resume_data (
                    info_hash TEXT PRIMARY KEY, resume_data BLOB NOT NULL,
                    name TEXT DEFAULT '', category TEXT DEFAULT '',
                    tags TEXT DEFAULT '[]', added_time REAL DEFAULT 0,
                    save_path TEXT DEFAULT '')
            """)
        if from_version < 2:
            try:
                self._resume_db.execute("ALTER TABLE resume_data ADD COLUMN dl_limit INTEGER DEFAULT 0")
                self._resume_db.execute("ALTER TABLE resume_data ADD COLUMN ul_limit INTEGER DEFAULT 0")
            except sqlite3.OperationalError:
                pass
        self._set_schema_version(_SCHEMA_VERSION)

    def _load_resume_data(self):
        if not self._resume_db:
            return
        cursor = self._resume_db.execute(
            "SELECT info_hash, resume_data, category, tags, added_time FROM resume_data")
        count = 0
        for row in cursor.fetchall():
            info_hash, data, category, tags_json, added_time = row
            try:
                atp = lt.read_resume_data(data)
                handle = self._session.add_torrent(atp)
                tags = json.loads(tags_json) if tags_json else []
                torrent = Torrent(handle, category=category, tags=tags)
                torrent.added_time = added_time or time.time()
                self._torrents[str(handle.info_hash())] = torrent
                count += 1
            except Exception as e:
                logger.error(f"Failed to load torrent {info_hash}: {e}")
        logger.info(f"Loaded {count} torrents from resume data")

    def _save_resume_data(self, torrent: Torrent):
        if torrent.is_valid:
            try:
                flags = (lt.save_resume_flags_t.flush_disk_cache |
                         lt.save_resume_flags_t.save_info_dict)
                torrent.handle.save_resume_data(flags)
            except AttributeError:
                torrent.handle.save_resume_data()

    def _save_all_resume_data(self):
        for torrent in self._torrents.values():
            self._save_resume_data(torrent)

    def _save_all_resume_data_sync(self):
        outstanding = 0
        for torrent in self._torrents.values():
            if torrent.is_valid:
                try:
                    torrent.handle.save_resume_data()
                    outstanding += 1
                except Exception:
                    pass
        timeout = time.time() + 10
        while outstanding > 0 and time.time() < timeout:
            alerts = self._session.pop_alerts()
            for alert in alerts:
                if isinstance(alert, lt.save_resume_data_alert):
                    self._handle_save_resume_data(alert)
                    outstanding -= 1
                elif isinstance(alert, lt.save_resume_data_failed_alert):
                    outstanding -= 1
            if outstanding > 0:
                time.sleep(0.1)

    def _handle_save_resume_data(self, alert):
        if not self._resume_db:
            return
        try:
            handle = alert.handle
            info_hash = str(handle.info_hash())
            torrent = self._torrents.get(info_hash)
            data = lt.write_resume_data_buf(alert.params)
            self._resume_db.execute(
                """INSERT OR REPLACE INTO resume_data
                   (info_hash, resume_data, name, category, tags, added_time, save_path)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (info_hash, bytes(data),
                 torrent.name if torrent else "",
                 torrent.category if torrent else "",
                 json.dumps(torrent.tags if torrent else []),
                 torrent.added_time if torrent else time.time(),
                 torrent.save_path if torrent else ""))
            self._resume_db.commit()
        except Exception as e:
            logger.error(f"Failed to save resume data: {e}")

    # --- Internal: IP blocklist ---

    def _load_ip_blocklist(self):
        blocklist_path = self._cfg.get("ip_blocklist_path", "")
        if not blocklist_path or not os.path.isfile(blocklist_path):
            return
        try:
            count = 0
            with open(blocklist_path, 'r', errors='ignore') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    if ':' in line and '-' in line:
                        parts = line.rsplit(':', 1)
                        if len(parts) == 2:
                            ip_range = parts[1].strip()
                            if '-' in ip_range:
                                start, end = ip_range.split('-', 1)
                                self._ip_filter.add_rule(start.strip(), end.strip(), 1)
                                count += 1
                    elif '-' in line:
                        start, end = line.split('-', 1)
                        self._ip_filter.add_rule(start.strip(), end.strip(), 1)
                        count += 1
            if count > 0:
                self._session.set_ip_filter(self._ip_filter)
                logger.info(f"Loaded {count} IP blocklist entries")
        except Exception as e:
            logger.error(f"Failed to load IP blocklist: {e}")

    # --- Internal: Alert processing ---

    def _process_alerts(self):
        if not self._session:
            return
        try:
            alerts = self._session.pop_alerts()
        except Exception as e:
            logger.error(f"Failed to pop alerts: {e}")
            return

        for alert in alerts:
            try:
                atype = type(alert)
                if atype == lt.torrent_finished_alert:
                    self._on_torrent_finished(alert)
                elif atype == lt.torrent_error_alert:
                    ih = str(alert.handle.info_hash())
                    msg = str(alert.error.message()) if alert.error.value() != 0 else "Unknown"
                    self.torrent_error.emit(ih, msg)
                elif atype == lt.metadata_received_alert:
                    self.torrent_metadata.emit(str(alert.handle.info_hash()))
                elif atype == lt.save_resume_data_alert:
                    self._handle_save_resume_data(alert)
                elif atype == lt.save_resume_data_failed_alert:
                    pass
                elif atype == lt.peer_connect_alert:
                    self._on_peer_connect(alert)
                elif atype == lt.listen_succeeded_alert:
                    logger.info(f"Listening on {alert.address}:{alert.port}")
                elif atype == lt.listen_failed_alert:
                    logger.warning(f"Listen failed: {alert.error.message()}")
            except Exception as e:
                logger.debug(f"Alert error ({type(alert).__name__}): {e}")

    def _on_torrent_finished(self, alert):
        info_hash = str(alert.handle.info_hash())
        self.torrent_finished.emit(info_hash)
        torrent = self._torrents.get(info_hash)
        if not torrent:
            return
        on_complete = self._cfg.get("on_complete_action", 1)
        if on_complete == 1:
            torrent.pause()
        elif on_complete == 2:
            self.remove_torrent(info_hash, False)
            return
        max_ratio = self._cfg.get("max_ratio", 0)
        if max_ratio > 0 and torrent.ratio >= max_ratio:
            action = self._cfg.get("ratio_action", 0)
            if action == 0:
                torrent.pause()
            elif action == 1:
                self.remove_torrent(info_hash)

    def _on_peer_connect(self, alert):
        if not self._peer_filter.enabled:
            return
        try:
            handle = alert.handle
            peer_ip = str(alert.ip[0]) if hasattr(alert, 'ip') else None
            if peer_ip:
                for peer in handle.get_peer_info():
                    if peer.ip[0] == peer_ip:
                        should_ban, reason = self._peer_filter.check_peer(
                            peer.pid, peer.client, peer.ip[0])
                        if should_ban:
                            self._ip_filter.add_rule(peer.ip[0], peer.ip[0], 1)
                            self._session.set_ip_filter(self._ip_filter)
                            self.peer_banned.emit(peer.ip[0], reason)
                        break
        except Exception:
            pass

    # --- Internal: Stats ---

    def _update_stats(self):
        if not self._session:
            return

        try:
            status = self._session.status()
            dl_rate = status.download_rate
            ul_rate = status.upload_rate
            dht_count = status.dht_nodes

            self._session_dl_history.append(dl_rate)
            self._session_ul_history.append(ul_rate)
            if len(self._session_dl_history) > self._max_session_history:
                self._session_dl_history.pop(0)
            if len(self._session_ul_history) > self._max_session_history:
                self._session_ul_history.pop(0)
        except Exception:
            dl_rate = 0
            ul_rate = 0
            dht_count = 0

        # Snapshot all torrents
        snapshots = []
        for torrent in self._torrents.values():
            try:
                snap = torrent.snapshot()
                torrent.record_speed()
                snapshots.append(snap)
            except Exception:
                pass

        stats = SessionStats(
            download_rate=dl_rate,
            upload_rate=ul_rate,
            dht_nodes=dht_count,
            dl_history=self._session_dl_history[:],
            ul_history=self._session_ul_history[:],
            torrent_count=len(self._torrents),
            torrents=snapshots,
        )
        self.stats_updated.emit(stats)

        # Emit detail data for focused torrent
        if self._focused_hash:
            t = self._torrents.get(self._focused_hash)
            if t:
                try:
                    detail = DetailData(
                        info_hash=self._focused_hash,
                        files=t.get_files(),
                        peers=t.get_peers(),
                        trackers=t.get_trackers(),
                        pieces=t.get_piece_states(),
                        piece_length=t.piece_length,
                        dl_history=t.speed_history_dl[:],
                        ul_history=t.speed_history_ul[:],
                    )
                    self.detail_updated.emit(detail)
                except Exception as e:
                    logger.debug(f"Detail data error: {e}")

    # --- Internal: Bandwidth schedule ---

    def _check_bandwidth_schedule(self):
        schedule = self._cfg.get("bandwidth_schedule", None)
        if not schedule or not isinstance(schedule, dict):
            return
        if not schedule.get("enabled", False):
            return
        now = datetime.now()
        current_hour = now.hour
        rules = schedule.get("rules", [])
        applied = False
        for rule in rules:
            start = rule.get("start", 0)
            end = rule.get("end", 24)
            if start <= current_hour < end:
                if self._session:
                    self._session.apply_settings({
                        'download_rate_limit': rule.get("dl", 0),
                        'upload_rate_limit': rule.get("ul", 0),
                    })
                applied = True
                break
        if not applied and self._session:
            dl = self._cfg.get("max_download_speed", 0)
            ul = self._cfg.get("max_upload_speed", 0)
            self._session.apply_settings({
                'download_rate_limit': dl if dl > 0 else 0,
                'upload_rate_limit': ul if ul > 0 else 0,
            })


class ThreadedSession:
    """Convenience wrapper: creates QThread + SessionWorker pair.

    Usage in MainWindow:
        self._threaded = ThreadedSession(settings)
        self._threaded.worker.torrent_added.connect(...)
        self._threaded.start()
        # On close:
        self._threaded.stop()
    """

    def __init__(self, settings: Settings):
        self.thread = QThread()
        self.thread.setObjectName("FluxSessionThread")
        # Snapshot all settings to a plain dict so the worker thread
        # never touches the SQLite-backed Settings object.
        cfg = settings.get_all()
        self.worker = SessionWorker(cfg)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.initialize)

    def start(self):
        self.thread.start()

    def stop(self):
        """Shut down worker on its own thread, then stop the thread."""
        # Invoke shutdown on the worker thread (blocking until complete)
        QMetaObject.invokeMethod(
            self.worker, "shutdown",
            Qt.ConnectionType.BlockingQueuedConnection
        )
        self.thread.quit()
        if not self.thread.wait(15000):
            logger.warning("Session thread did not stop in time, terminating")
            self.thread.terminate()
            self.thread.wait()
