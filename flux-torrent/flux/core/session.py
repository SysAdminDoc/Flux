"""DEPRECATED: Superseded by session_worker.py (QThread-based).
Use ThreadedSession from flux.core.session_worker instead.

Core BitTorrent session manager wrapping libtorrent.

Improvements:
- Protected alert pipeline (single bad alert can't kill processing)
- Snapshot-driven stats (one FFI call per torrent per cycle)
- DB schema versioning with migration support
- IP blocklist import support
- Bandwidth scheduling support
"""

import os
import json
import time
import sqlite3
import logging
from pathlib import Path
from typing import Optional, List, Dict
from datetime import datetime

import libtorrent as lt
from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from flux.core.torrent import Torrent, TorrentState
from flux.core.settings import Settings
from flux.core.peer_filter import PeerFilter

logger = logging.getLogger(__name__)

# Resolve libtorrent flags safely (API varies between binding versions)
_tf = getattr(lt, 'torrent_flags', None) or getattr(lt, 'torrent_flags_t', None)
_FLAG_PAUSED = getattr(_tf, 'paused', 0x20) if _tf else 0x20
_FLAG_AUTO_MANAGED = getattr(_tf, 'auto_managed', 0x40) if _tf else 0x40
_FLAG_SEQUENTIAL = getattr(_tf, 'sequential_download', 0x200) if _tf else 0x200

# Current DB schema version
_SCHEMA_VERSION = 2


class TorrentSession(QObject):
    """Manages the libtorrent session and all active torrents."""

    # --- Signals ---
    torrent_added = pyqtSignal(str)
    torrent_removed = pyqtSignal(str)
    torrent_finished = pyqtSignal(str)
    torrent_error = pyqtSignal(str, str)
    torrent_metadata = pyqtSignal(str)
    stats_updated = pyqtSignal()
    peer_banned = pyqtSignal(str, str)

    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self._settings = settings
        self._session: Optional[lt.session] = None
        self._torrents: Dict[str, Torrent] = {}
        self._peer_filter = PeerFilter()
        self._resume_db: Optional[sqlite3.Connection] = None

        # Speed history for session-level graphs
        self._session_dl_history: list = []
        self._session_ul_history: list = []
        self._max_session_history = 300

        # Cached IP filter (avoid rebuilding per-ban)
        self._ip_filter: Optional[lt.ip_filter] = None

        # Alert processing timer
        self._alert_timer = QTimer(self)
        self._alert_timer.timeout.connect(self._process_alerts)

        # Stats update timer
        self._stats_timer = QTimer(self)
        self._stats_timer.timeout.connect(self._update_stats)

        # Resume data save timer
        self._save_timer = QTimer(self)
        self._save_timer.timeout.connect(self._save_all_resume_data)

        # Bandwidth schedule timer (checks every minute)
        self._schedule_timer = QTimer(self)
        self._schedule_timer.timeout.connect(self._check_bandwidth_schedule)

    # --- Lifecycle ---

    def start(self):
        """Initialize and start the libtorrent session."""
        logger.info("Starting Flux torrent session...")

        settings = {
            'user_agent': 'FluxTorrent/1.0',
            'peer_fingerprint': '-FX1000-',
            'listen_interfaces': '0.0.0.0:{p},[::0]:{p}'.format(
                p=self._settings.get("listen_port", 6881)),
            'connections_limit': self._settings.get("max_connections", 500),
            'max_peerlist_size': 4000,
            'enable_dht': self._settings.get("dht_enabled", True),
            'enable_lsd': self._settings.get("lsd_enabled", True),
            'active_downloads': self._settings.get("max_active_downloads", 5),
            'active_seeds': self._settings.get("max_active_uploads", 5),
            'active_limit': self._settings.get("max_active_torrents", 10),
            'cache_size': 2048,
            'send_buffer_watermark': 512 * 1024,
            'send_buffer_watermark_factor': 150,
        }

        # Alert mask
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

        # Bandwidth limits
        dl_limit = self._settings.get("max_download_speed", 0)
        ul_limit = self._settings.get("max_upload_speed", 0)
        if dl_limit > 0:
            settings['download_rate_limit'] = dl_limit
        if ul_limit > 0:
            settings['upload_rate_limit'] = ul_limit

        # Encryption
        enc = self._settings.get("encryption_mode", 1)
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

        # Configure peer filter
        self._peer_filter.configure(self._settings.get_all())

        # Initialize resume data DB
        self._init_resume_db()

        # Load IP blocklist
        self._load_ip_blocklist()

        # Load saved torrents
        self._load_resume_data()

        # Start timers
        self._alert_timer.start(500)
        self._stats_timer.start(1000)
        self._save_timer.start(300000)
        self._schedule_timer.start(60000)

        logger.info(f"Session started on port {self._settings.get('listen_port', 6881)}")

    def stop(self):
        """Gracefully shut down the session."""
        logger.info("Stopping Flux torrent session...")

        self._alert_timer.stop()
        self._stats_timer.stop()
        self._save_timer.stop()
        self._schedule_timer.stop()

        if self._session:
            self._session.pause()
            self._save_all_resume_data_sync()
            del self._session
            self._session = None

        if self._resume_db:
            self._resume_db.close()

        self._torrents.clear()
        logger.info("Session stopped.")

    # --- Resume Data & Schema ---

    def _init_resume_db(self):
        config_dir = Path.home() / ".flux-torrent"
        config_dir.mkdir(parents=True, exist_ok=True)
        self._resume_db = sqlite3.connect(str(config_dir / "resume.db"))

        # Schema versioning
        self._resume_db.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                version INTEGER NOT NULL
            )
        """)

        version = self._get_schema_version()

        if version == 0:
            # Fresh database
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
            "INSERT OR REPLACE INTO schema_version (id, version) VALUES (1, ?)",
            (version,)
        )
        self._resume_db.commit()

    def _migrate_schema(self, from_version: int):
        """Run migrations from from_version to _SCHEMA_VERSION."""
        logger.info(f"Migrating resume DB from v{from_version} to v{_SCHEMA_VERSION}")

        if from_version < 1:
            # v0 -> v1: create base table
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

        if from_version < 2:
            # v1 -> v2: add per-torrent speed limits columns
            try:
                self._resume_db.execute("ALTER TABLE resume_data ADD COLUMN dl_limit INTEGER DEFAULT 0")
                self._resume_db.execute("ALTER TABLE resume_data ADD COLUMN ul_limit INTEGER DEFAULT 0")
            except sqlite3.OperationalError:
                pass  # Columns already exist

        self._set_schema_version(_SCHEMA_VERSION)

    def _load_resume_data(self):
        if not self._resume_db:
            return

        cursor = self._resume_db.execute(
            "SELECT info_hash, resume_data, category, tags, added_time FROM resume_data"
        )
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

        timeout = time.time() + 10  # 10s timeout
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

    def _handle_save_resume_data(self, alert: lt.save_resume_data_alert):
        if not self._resume_db:
            return

        try:
            handle = alert.handle
            info_hash = str(handle.info_hash())
            torrent = self._torrents.get(info_hash)

            data = lt.write_resume_data_buf(alert.params)
            name = torrent.name if torrent else ""
            category = torrent.category if torrent else ""
            tags = json.dumps(torrent.tags if torrent else [])
            added_time = torrent.added_time if torrent else time.time()
            save_path = torrent.save_path if torrent else ""

            self._resume_db.execute(
                """INSERT OR REPLACE INTO resume_data
                   (info_hash, resume_data, name, category, tags, added_time, save_path)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (info_hash, bytes(data), name, category, tags, added_time, save_path)
            )
            self._resume_db.commit()
        except Exception as e:
            logger.error(f"Failed to save resume data: {e}")

    # --- IP Blocklist ---

    def _load_ip_blocklist(self):
        """Load IP blocklist from file if configured."""
        blocklist_path = self._settings.get("ip_blocklist_path", "")
        if not blocklist_path or not os.path.isfile(blocklist_path):
            return

        try:
            count = 0
            with open(blocklist_path, 'r', errors='ignore') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    # PeerGuardian P2P format: name:start-end
                    if ':' in line and '-' in line:
                        parts = line.rsplit(':', 1)
                        if len(parts) == 2:
                            ip_range = parts[1].strip()
                            if '-' in ip_range:
                                start, end = ip_range.split('-', 1)
                                self._ip_filter.add_rule(start.strip(), end.strip(), 1)
                                count += 1
                    # Simple IP range: start-end
                    elif '-' in line:
                        start, end = line.split('-', 1)
                        self._ip_filter.add_rule(start.strip(), end.strip(), 1)
                        count += 1

            if count > 0:
                self._session.set_ip_filter(self._ip_filter)
                logger.info(f"Loaded {count} IP blocklist entries from {blocklist_path}")
        except Exception as e:
            logger.error(f"Failed to load IP blocklist: {e}")

    # --- Torrent Operations ---

    def add_torrent_file(self, filepath: str, save_path: str = "",
                         category: str = "", tags: list = None,
                         paused: bool = False, sequential: bool = False) -> Optional[str]:
        if not self._session:
            return None

        try:
            ti = lt.torrent_info(filepath)
            atp = lt.add_torrent_params()
            atp.ti = ti
            atp.save_path = save_path or self._settings.get("default_save_path")

            if paused:
                atp.flags |= _FLAG_PAUSED
                atp.flags &= ~_FLAG_AUTO_MANAGED
            else:
                atp.flags |= _FLAG_AUTO_MANAGED

            if sequential:
                atp.flags |= _FLAG_SEQUENTIAL

            handle = self._session.add_torrent(atp)
            info_hash = str(handle.info_hash())

            if info_hash in self._torrents:
                logger.warning(f"Torrent already exists: {info_hash}")
                return info_hash

            torrent = Torrent(handle, category=category, tags=tags or [])
            self._torrents[info_hash] = torrent

            self.torrent_added.emit(info_hash)
            logger.info(f"Added torrent: {torrent.name}")
            return info_hash

        except Exception as e:
            logger.error(f"Failed to add torrent file {filepath}: {e}")
            return None

    def add_magnet(self, uri: str, save_path: str = "",
                   category: str = "", tags: list = None,
                   paused: bool = False) -> Optional[str]:
        if not self._session:
            logger.error("Cannot add magnet: session not started")
            return None

        if not uri or not uri.strip().startswith("magnet:"):
            logger.error(f"Invalid magnet URI: {uri[:80] if uri else '(empty)'}")
            return None

        try:
            atp = lt.parse_magnet_uri(uri.strip())
        except AttributeError:
            try:
                atp = lt.add_torrent_params()
                atp = lt.parse_magnet_uri_dict(uri.strip())
            except Exception as e2:
                logger.error(f"Failed to parse magnet URI (no parser available): {e2}")
                return None
        except Exception as e:
            logger.error(f"Failed to parse magnet URI: {e}")
            return None

        try:
            atp.save_path = save_path or self._settings.get("default_save_path")

            if paused:
                atp.flags |= _FLAG_PAUSED
                atp.flags &= ~_FLAG_AUTO_MANAGED
            else:
                atp.flags |= _FLAG_AUTO_MANAGED

            handle = self._session.add_torrent(atp)
            info_hash = str(handle.info_hash())

            if info_hash in self._torrents:
                logger.info(f"Magnet already exists: {info_hash}")
                return info_hash

            torrent = Torrent(handle, category=category, tags=tags or [])
            self._torrents[info_hash] = torrent

            self.torrent_added.emit(info_hash)
            logger.info(f"Added magnet: {info_hash}")
            return info_hash

        except Exception as e:
            logger.error(f"Failed to add magnet to session: {e}", exc_info=True)
            return None

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

    def pause_torrent(self, info_hash: str):
        torrent = self._torrents.get(info_hash)
        if torrent:
            torrent.pause()

    def resume_torrent(self, info_hash: str):
        torrent = self._torrents.get(info_hash)
        if torrent:
            torrent.resume()

    def pause_all(self):
        for torrent in self._torrents.values():
            torrent.pause()

    def resume_all(self):
        for torrent in self._torrents.values():
            torrent.resume()

    def force_recheck(self, info_hash: str):
        torrent = self._torrents.get(info_hash)
        if torrent:
            torrent.force_recheck()

    def force_reannounce(self, info_hash: str):
        torrent = self._torrents.get(info_hash)
        if torrent:
            torrent.force_reannounce()

    def set_torrent_speed_limit(self, info_hash: str, dl_limit: int = 0, ul_limit: int = 0):
        """Set per-torrent speed limits. 0 = unlimited."""
        torrent = self._torrents.get(info_hash)
        if torrent:
            torrent.set_download_limit(dl_limit)
            torrent.set_upload_limit(ul_limit)

    # --- Queries ---

    def get_torrent(self, info_hash: str) -> Optional[Torrent]:
        return self._torrents.get(info_hash)

    def get_all_torrents(self) -> List[Torrent]:
        return list(self._torrents.values())

    @property
    def torrent_count(self) -> int:
        return len(self._torrents)

    @property
    def session_download_rate(self) -> int:
        if self._session:
            try:
                return self._session.status().download_rate
            except Exception:
                return 0
        return 0

    @property
    def session_upload_rate(self) -> int:
        if self._session:
            try:
                return self._session.status().upload_rate
            except Exception:
                return 0
        return 0

    @property
    def dht_nodes(self) -> int:
        if self._session:
            try:
                return self._session.status().dht_nodes
            except Exception:
                return 0
        return 0

    @property
    def session_dl_history(self) -> list:
        return self._session_dl_history

    @property
    def session_ul_history(self) -> list:
        return self._session_ul_history

    @property
    def peer_filter(self) -> PeerFilter:
        return self._peer_filter

    # --- Alert Processing (protected) ---

    def _process_alerts(self):
        """Process all pending libtorrent alerts with per-alert protection."""
        if not self._session:
            return

        try:
            alerts = self._session.pop_alerts()
        except Exception as e:
            logger.error(f"Failed to pop alerts: {e}")
            return

        for alert in alerts:
            try:
                alert_type = type(alert)

                if alert_type == lt.torrent_finished_alert:
                    self._handle_torrent_finished(alert)
                elif alert_type == lt.torrent_error_alert:
                    self._handle_torrent_error(alert)
                elif alert_type == lt.metadata_received_alert:
                    self._handle_metadata_received(alert)
                elif alert_type == lt.save_resume_data_alert:
                    self._handle_save_resume_data(alert)
                elif alert_type == lt.save_resume_data_failed_alert:
                    pass
                elif alert_type == lt.peer_connect_alert:
                    self._handle_peer_connect(alert)
                elif alert_type == lt.torrent_removed_alert:
                    pass
                elif alert_type == lt.listen_succeeded_alert:
                    logger.info(f"Listening on {alert.address}:{alert.port}")
                elif alert_type == lt.listen_failed_alert:
                    logger.warning(f"Listen failed: {alert.error.message()}")
                elif alert_type == lt.portmap_alert:
                    logger.info(f"Port mapped: {alert.external_port}")
            except Exception as e:
                # Individual alert failure must not kill the pipeline
                logger.debug(f"Alert processing error ({type(alert).__name__}): {e}")

    def _handle_torrent_finished(self, alert):
        info_hash = str(alert.handle.info_hash())
        self.torrent_finished.emit(info_hash)
        logger.info(f"Torrent finished: {info_hash}")

        torrent = self._torrents.get(info_hash)
        if not torrent:
            return

        on_complete = self._settings.get("on_complete_action", 1)
        if on_complete == 1:
            torrent.pause()
            logger.info(f"Auto-paused completed torrent: {info_hash}")
        elif on_complete == 2:
            self.remove_torrent(info_hash, delete_files=False)
            logger.info(f"Auto-removed completed torrent: {info_hash}")
            return

        max_ratio = self._settings.get("max_ratio", 0)
        if max_ratio > 0 and torrent.ratio >= max_ratio:
            action = self._settings.get("ratio_action", 0)
            if action == 0:
                torrent.pause()
            elif action == 1:
                self.remove_torrent(info_hash)

    def _handle_torrent_error(self, alert):
        info_hash = str(alert.handle.info_hash())
        msg = str(alert.error.message()) if alert.error.value() != 0 else "Unknown error"
        self.torrent_error.emit(info_hash, msg)
        logger.error(f"Torrent error {info_hash}: {msg}")

    def _handle_metadata_received(self, alert):
        info_hash = str(alert.handle.info_hash())
        self.torrent_metadata.emit(info_hash)
        logger.info(f"Metadata received: {info_hash}")

    def _handle_peer_connect(self, alert):
        """Check newly-connected peer against the filter (single peer only)."""
        if not self._peer_filter.enabled:
            return

        try:
            handle = alert.handle
            # Only check the connecting peer via alert IP, not all peers
            peer_ip = str(alert.ip[0]) if hasattr(alert, 'ip') else None

            if peer_ip:
                # Quick check: just scan for the specific IP in peer info
                for peer in handle.get_peer_info():
                    if peer.ip[0] == peer_ip:
                        should_ban, reason = self._peer_filter.check_peer(
                            peer.pid, peer.client, peer.ip[0]
                        )
                        if should_ban:
                            self._ip_filter.add_rule(peer.ip[0], peer.ip[0], 1)
                            self._session.set_ip_filter(self._ip_filter)
                            self.peer_banned.emit(peer.ip[0], reason)
                            logger.info(f"Banned peer {peer.ip[0]}: {reason}")
                        break
            else:
                # Fallback: check all peers (original behavior)
                for peer in handle.get_peer_info():
                    should_ban, reason = self._peer_filter.check_peer(
                        peer.pid, peer.client, peer.ip[0]
                    )
                    if should_ban:
                        self._ip_filter.add_rule(peer.ip[0], peer.ip[0], 1)
                        self._session.set_ip_filter(self._ip_filter)
                        self.peer_banned.emit(peer.ip[0], reason)
                        logger.info(f"Banned peer {peer.ip[0]}: {reason}")
        except Exception:
            pass

    def _update_stats(self):
        """Snapshot all torrents and record session stats."""
        if self._session:
            try:
                status = self._session.status()
                self._session_dl_history.append(status.download_rate)
                self._session_ul_history.append(status.upload_rate)
                if len(self._session_dl_history) > self._max_session_history:
                    self._session_dl_history.pop(0)
                if len(self._session_ul_history) > self._max_session_history:
                    self._session_ul_history.pop(0)
            except Exception as e:
                logger.debug(f"Session status failed: {e}")

            # Snapshot all torrents (one FFI call each)
            for torrent in self._torrents.values():
                try:
                    torrent.snapshot()
                    torrent.record_speed()
                except Exception:
                    pass

        self.stats_updated.emit()

    # --- Bandwidth Schedule ---

    def _check_bandwidth_schedule(self):
        """Apply bandwidth limits based on time-of-day schedule."""
        schedule = self._settings.get("bandwidth_schedule", None)
        if not schedule or not isinstance(schedule, dict):
            return

        if not schedule.get("enabled", False):
            return

        now = datetime.now()
        current_hour = now.hour

        # Schedule format: {"enabled": true, "rules": [{"start": 9, "end": 17, "dl": 512000, "ul": 128000}]}
        rules = schedule.get("rules", [])
        applied = False

        for rule in rules:
            start = rule.get("start", 0)
            end = rule.get("end", 24)
            if start <= current_hour < end:
                dl = rule.get("dl", 0)
                ul = rule.get("ul", 0)
                if self._session:
                    settings = {}
                    settings['download_rate_limit'] = dl
                    settings['upload_rate_limit'] = ul
                    self._session.apply_settings(settings)
                applied = True
                break

        if not applied and self._session:
            # Outside all schedule windows - use default limits
            dl_limit = self._settings.get("max_download_speed", 0)
            ul_limit = self._settings.get("max_upload_speed", 0)
            settings = {}
            settings['download_rate_limit'] = dl_limit if dl_limit > 0 else 0
            settings['upload_rate_limit'] = ul_limit if ul_limit > 0 else 0
            self._session.apply_settings(settings)

    # --- Session Settings Update ---

    def apply_settings(self):
        if not self._session:
            return

        settings = {}
        dl_limit = self._settings.get("max_download_speed", 0)
        ul_limit = self._settings.get("max_upload_speed", 0)
        settings['download_rate_limit'] = dl_limit if dl_limit > 0 else 0
        settings['upload_rate_limit'] = ul_limit if ul_limit > 0 else 0
        settings['connections_limit'] = self._settings.get("max_connections", 500)
        settings['active_downloads'] = self._settings.get("max_active_downloads", 5)
        settings['active_seeds'] = self._settings.get("max_active_uploads", 5)
        settings['active_limit'] = self._settings.get("max_active_torrents", 10)

        self._session.apply_settings(settings)
        self._peer_filter.configure(self._settings.get_all())

        # Reload blocklist if path changed
        self._load_ip_blocklist()
