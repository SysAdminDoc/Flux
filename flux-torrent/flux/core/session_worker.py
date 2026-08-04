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
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional, Dict
from datetime import datetime
from dataclasses import dataclass, field

import libtorrent as lt
from PyQt6.QtCore import QObject, QTimer, pyqtSignal, pyqtSlot, QThread, QMetaObject, Qt

from flux.core.torrent import Torrent, get_info_hashes
from flux.core.activity_heatmap import normalize_heatmap, record_activity
from flux.core.notifications import crossed_ratio_milestones, normalize_ratio_milestones
from flux.core.vpn_binding import build_listen_interfaces, is_bind_address_available
from flux.core.blocklist import (
    BlocklistFetchResult,
    fetch_blocklist,
    normalize_blocklist_urls,
    parse_blocklist_ranges,
    write_blocklist_cache,
)
from flux.core.smart_recheck import (
    FileFingerprint,
    build_smart_recheck_plan,
    capture_file_fingerprints,
    read_piece_data,
    verify_pieces,
)
from flux.core.integrity import (
    IntegrityResult,
    build_manifest_plan,
    generate_manifest,
)
from flux.core.settings import (
    Settings,
    build_i2p_settings,
    build_private_tracker_settings,
    build_tracker_proxy_rules,
    build_label_automation_rules,
    build_torrent_schedule_settings,
)
from flux.core.automation import (
    ensure_move_path,
    label_rule_for,
    parse_label_rules,
    parse_torrent_schedules,
    scheduled_action,
    should_auto_delete,
)
from flux.core.peer_filter import PeerFilter
from flux.core.peer_reputation import PeerReputationStore
from flux.core.script_hooks import ScriptHookRunner
from flux.core.tracker_proxy import TrackerProxyManager

logger = logging.getLogger(__name__)

_tf = getattr(lt, 'torrent_flags', None) or getattr(lt, 'torrent_flags_t', None)
_FLAG_PAUSED = getattr(_tf, 'paused', 0x20) if _tf else 0x20
_FLAG_AUTO_MANAGED = getattr(_tf, 'auto_managed', 0x40) if _tf else 0x40
_FLAG_SEQUENTIAL = getattr(_tf, 'sequential_download', 0x200) if _tf else 0x200
_FLAG_DISABLE_DHT = getattr(_tf, 'disable_dht', 0) if _tf else 0
_FLAG_DISABLE_PEX = getattr(_tf, 'disable_pex', 0) if _tf else 0
_FLAG_DISABLE_LSD = getattr(_tf, 'disable_lsd', 0) if _tf else 0
_PRIVATE_TRACKER_FLAGS = _FLAG_DISABLE_DHT | _FLAG_DISABLE_PEX | _FLAG_DISABLE_LSD

_SCHEMA_VERSION = 2
_MAX_TORRENT_LOG_ENTRIES = 250


def _alert_info_hash(alert) -> str:
    """Return the torrent identity attached to a libtorrent alert, if any."""
    direct_hash = getattr(alert, "info_hash", "")
    if direct_hash:
        return str(direct_hash)

    handle = getattr(alert, "handle", None)
    if handle is None:
        return ""
    try:
        info_hash, _, _ = get_info_hashes(handle)
    except Exception:
        return ""
    return str(info_hash or "")


def _alert_log_entry(alert, timestamp: str | None = None) -> dict:
    """Convert a libtorrent alert into a small, GUI-safe log record."""
    alert_type = type(alert).__name__.removesuffix("_alert")
    type_lower = alert_type.lower()
    if "error" in type_lower or "failed" in type_lower:
        level = "ERROR" if "error" in type_lower else "WARN"
    else:
        level = "INFO"

    message = ""
    try:
        message = str(alert.message() or "")
    except Exception:
        pass
    message = " ".join(message.split())
    if not message:
        message = alert_type or "alert"

    return {
        "timestamp": timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "level": level,
        "type": alert_type or "alert",
        "message": message,
    }


def _apply_private_tracker_profile(torrent: Torrent, cfg: dict):
    """Apply per-torrent privacy flags and the configured upload-slot cap."""
    enabled = bool(cfg.get("private_tracker_profile", False))
    try:
        if enabled and _PRIVATE_TRACKER_FLAGS:
            torrent.handle.set_flags(_PRIVATE_TRACKER_FLAGS)
        elif not enabled and _PRIVATE_TRACKER_FLAGS:
            torrent.handle.unset_flags(_PRIVATE_TRACKER_FLAGS)
        settings = build_private_tracker_settings(cfg)
        if "unchoke_slots_limit" in settings:
            max_uploads = settings["unchoke_slots_limit"]
        else:
            try:
                max_uploads = max(1, int(cfg.get("max_uploads_per_torrent", 5) or 5))
            except (TypeError, ValueError):
                max_uploads = 5
        torrent.handle.set_max_uploads(max_uploads)
    except Exception as exc:
        logger.debug("Failed to apply private-tracker profile: %s", exc)


@dataclass
class SessionStats:
    """Thread-safe snapshot of session-wide statistics."""
    download_rate: int = 0
    upload_rate: int = 0
    dht_nodes: int = 0
    dl_history: list = field(default_factory=list)
    ul_history: list = field(default_factory=list)
    activity_heatmap: list = field(default_factory=list)
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
    peer_piece_owners: list = field(default_factory=list)
    peer_piece_labels: list = field(default_factory=list)
    dl_history: list = field(default_factory=list)
    ul_history: list = field(default_factory=list)
    logs: list = field(default_factory=list)


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
    peer_reputation = pyqtSignal(str, str)
    magnet_uri_ready = pyqtSignal(str)    # magnet URI string
    tracker_tested = pyqtSignal(str, str, object)  # hash, URL, result payload
    vpn_status = pyqtSignal(bool, str)  # available, user-facing status
    blocklist_ready = pyqtSignal(object)  # BlocklistFetchResult
    blocklist_status = pyqtSignal(bool, str)  # success, user-facing status
    recheck_status = pyqtSignal(str, str)  # info hash, user-facing status
    integrity_ready = pyqtSignal(object)  # (info hash, IntegrityResult)
    integrity_status = pyqtSignal(bool, str)  # success/progress, user-facing status
    ratio_milestone = pyqtSignal(str, float)  # info hash, crossed ratio
    started = pyqtSignal()
    stopped = pyqtSignal()

    def __init__(self, cfg: dict):
        super().__init__()
        self._cfg = cfg  # plain dict snapshot (thread-safe, no SQLite)
        self._session: Optional[lt.session] = None
        self._torrents: Dict[str, Torrent] = {}
        self._recheck_fingerprints: Dict[str, tuple[FileFingerprint, ...]] = {}
        self._full_rechecks_pending: set[str] = set()
        self._peer_filter = PeerFilter()
        self._peer_reputation_path = self._reputation_path(self._cfg)
        self._peer_reputation = PeerReputationStore(self._peer_reputation_path)
        self._hook_runner = ScriptHookRunner()
        self._hook_runner.configure(self._cfg.get("script_hooks", []))
        self._resume_db: Optional[sqlite3.Connection] = None
        self._ip_filter: Optional[lt.ip_filter] = None
        self._dynamic_banned_ips: set[str] = set()
        self._tracker_proxy_manager = TrackerProxyManager()
        self._label_rules = ()
        self._torrent_schedules = {}

        self._session_dl_history: list = []
        self._session_ul_history: list = []
        self._max_session_history = 300
        self._activity_heatmap = normalize_heatmap(self._cfg.get("activity_heatmap", []))
        self._last_activity_at: float | None = None
        self._torrent_logs: Dict[str, list] = {}
        self._ratio_last_seen: dict[str, float] = {}
        self._vpn_bind_address = str(self._cfg.get("vpn_bind_address", "") or "").strip()
        self._vpn_kill_switch = bool(self._cfg.get("vpn_kill_switch", False))
        self._vpn_available: bool | None = None
        self._blocklist_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="FluxBlocklist"
        )
        self._blocklist_future = None
        self._blocklist_next_refresh_at = time.monotonic()
        self._blocklist_refresh_generation = 0
        self.blocklist_ready.connect(self._apply_blocklist_result)
        self._integrity_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="FluxIntegrity"
        )
        self._integrity_futures = {}
        self.integrity_ready.connect(self._apply_integrity_result)

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
            'listen_interfaces': build_listen_interfaces(
                self._vpn_bind_address, self._cfg.get("listen_port", 6881)
            ),
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
        settings.update(build_i2p_settings(self._cfg))
        settings.update(build_private_tracker_settings(self._cfg))

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
        self._tracker_proxy_manager.configure(build_tracker_proxy_rules(self._cfg))
        self._label_rules = parse_label_rules(build_label_automation_rules(self._cfg))
        self._torrent_schedules = parse_torrent_schedules(
            build_torrent_schedule_settings(self._cfg)
        )

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
        self._check_blocklist_refresh()

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

        self._tracker_proxy_manager.close()

        self._blocklist_executor.shutdown(wait=True, cancel_futures=True)
        self._integrity_executor.shutdown(wait=True, cancel_futures=True)

        if self._session:
            self._session.pause()
            self._save_all_resume_data_sync()
            del self._session
            self._session = None

        if self._resume_db:
            self._resume_db.close()
            self._resume_db = None

        self._torrents.clear()
        self._torrent_logs.clear()
        self._hook_runner.shutdown()
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
            if self._cfg.get("private_tracker_profile", False) and _PRIVATE_TRACKER_FLAGS:
                atp.flags |= _PRIVATE_TRACKER_FLAGS

            handle = self._session.add_torrent(atp)
            info_hash, _, _ = get_info_hashes(handle)

            if info_hash not in self._torrents:
                torrent = Torrent(handle, category=category, tags=tags)
                self._torrents[info_hash] = torrent
                _apply_private_tracker_profile(torrent, self._cfg)
                self._apply_label_rule(torrent)
                self._tracker_proxy_manager.sync_torrent(torrent)
                self.torrent_added.emit(info_hash)
                self._fire_hook("on_add", torrent)
                logger.info(f"Added torrent: {torrent.name}")
            else:
                logger.warning(f"Torrent already exists: {info_hash}")

        except Exception as e:
            logger.error(f"Failed to add torrent file: {e}")

    @pyqtSlot(bytes, str, str, str, bool, bool)
    def add_torrent_bytes(self, data: bytes, save_path: str = "",
                          category: str = "", tags_json: str = "[]",
                          paused: bool = False, sequential: bool = False):
        """Add a torrent received through the remote API without a temp file."""
        if not self._session or not data:
            return

        try:
            tags = json.loads(tags_json) if tags_json else []
            ti = lt.torrent_info(lt.bdecode(data))
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
            if self._cfg.get("private_tracker_profile", False) and _PRIVATE_TRACKER_FLAGS:
                atp.flags |= _PRIVATE_TRACKER_FLAGS

            handle = self._session.add_torrent(atp)
            info_hash, _, _ = get_info_hashes(handle)
            if info_hash in self._torrents:
                logger.warning(f"Torrent already exists: {info_hash}")
                return

            torrent = Torrent(handle, category=category, tags=tags)
            self._torrents[info_hash] = torrent
            _apply_private_tracker_profile(torrent, self._cfg)
            self._apply_label_rule(torrent)
            self._tracker_proxy_manager.sync_torrent(torrent)
            self.torrent_added.emit(info_hash)
            self._fire_hook("on_add", torrent)
            logger.info(f"Added torrent bytes: {info_hash}")
        except Exception as e:
            logger.error(f"Failed to add torrent bytes: {e}")

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
            if self._cfg.get("private_tracker_profile", False) and _PRIVATE_TRACKER_FLAGS:
                atp.flags |= _PRIVATE_TRACKER_FLAGS

            handle = self._session.add_torrent(atp)
            info_hash, _, _ = get_info_hashes(handle)

            if info_hash not in self._torrents:
                torrent = Torrent(handle, category=category, tags=tags)
                self._torrents[info_hash] = torrent
                _apply_private_tracker_profile(torrent, self._cfg)
                self._apply_label_rule(torrent)
                self._tracker_proxy_manager.sync_torrent(torrent)
                self.torrent_added.emit(info_hash)
                self._fire_hook("on_add", torrent)
                logger.info(f"Added magnet: {info_hash}")

        except Exception as e:
            logger.error(f"Failed to add magnet: {e}", exc_info=True)

    @pyqtSlot(str, bool)
    def remove_torrent(self, info_hash: str, delete_files: bool = False):
        torrent = self._torrents.get(info_hash)
        if not torrent or not self._session:
            return

        name = torrent.name
        self._fire_hook("on_delete", torrent)
        self._tracker_proxy_manager.forget_torrent(info_hash)
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
        self._recheck_fingerprints.pop(info_hash, None)
        self._full_rechecks_pending.discard(info_hash)
        self._torrent_logs.pop(info_hash, None)
        self._ratio_last_seen.pop(info_hash, None)

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
        if not t:
            return
        self._smart_recheck(info_hash, t)

    def _remember_recheck_fingerprint(self, info_hash: str, torrent: Torrent):
        try:
            torrent_info = torrent.handle.torrent_file()
            if torrent_info:
                self._recheck_fingerprints[info_hash] = capture_file_fingerprints(
                    torrent_info, torrent.save_path
                )
        except Exception as exc:
            logger.debug("Could not capture re-check baseline for %s: %s", info_hash, exc)

    def _queue_full_recheck(self, info_hash: str, torrent: Torrent, reason: str):
        self._full_rechecks_pending.add(info_hash)
        torrent.force_recheck()
        self.recheck_status.emit(info_hash, f"Full re-check started: {reason}")

    @staticmethod
    def _inject_piece(handle, piece: int, data: bytes) -> bool:
        try:
            flags_type = getattr(lt, "add_piece_flags_t", None)
            flags = getattr(flags_type, "overwrite_existing", 0)
            handle.add_piece(piece, data, flags)
            return True
        except Exception as exc:
            logger.warning("Could not revalidate piece %s through libtorrent: %s", piece, exc)
            return False

    def _smart_recheck(self, info_hash: str, torrent: Torrent):
        try:
            torrent_info = torrent.handle.torrent_file()
        except Exception:
            torrent_info = None
        if not torrent_info:
            self._queue_full_recheck(info_hash, torrent, "torrent metadata is unavailable")
            return

        try:
            plan = build_smart_recheck_plan(
                torrent_info,
                torrent.save_path,
                self._recheck_fingerprints.get(info_hash),
            )
        except Exception as exc:
            self._queue_full_recheck(info_hash, torrent, f"heuristic planning failed: {exc}")
            return
        if plan.requires_full_recheck:
            self._queue_full_recheck(info_hash, torrent, plan.reason)
            return
        if plan.skip:
            self.recheck_status.emit(info_hash, f"Smart re-check skipped: {plan.reason}")
            return

        was_paused = False
        full_recheck_started = False
        try:
            try:
                was_paused = bool(torrent.handle.status().paused)
            except Exception:
                was_paused = False
            if not was_paused:
                torrent.handle.pause()

            try:
                results = verify_pieces(torrent_info, torrent.save_path, plan.dirty_pieces)
            except Exception as exc:
                self._queue_full_recheck(info_hash, torrent, f"piece verification failed: {exc}")
                full_recheck_started = True
                return
            unavailable = [result for result in results if not result.available or result.matches is None]
            if unavailable:
                self._queue_full_recheck(
                    info_hash,
                    torrent,
                    f"{len(unavailable)} dirty piece(s) could not be read safely",
                )
                full_recheck_started = True
                return

            mismatches = [result for result in results if result.matches is False]
            injected = 0
            for result in results:
                if result.matches is not True:
                    should_inject = True
                else:
                    try:
                        should_inject = not torrent.handle.have_piece(result.piece)
                    except Exception:
                        should_inject = False
                if not should_inject:
                    continue
                data = read_piece_data(torrent_info, torrent.save_path, result.piece)
                if data is None or not self._inject_piece(torrent.handle, result.piece, data):
                    self._queue_full_recheck(
                        info_hash,
                        torrent,
                        f"piece {result.piece} could not be revalidated safely",
                    )
                    full_recheck_started = True
                    return
                injected += 1

            if mismatches:
                self.recheck_status.emit(
                    info_hash,
                    f"Smart re-check found {len(mismatches)} corrupt piece(s); re-download queued",
                )
            else:
                self._recheck_fingerprints[info_hash] = plan.current_fingerprints
                self.recheck_status.emit(
                    info_hash,
                    f"Smart re-check validated {len(results)} piece(s)"
                    + (f" and queued {injected} missing piece(s)" if injected else ""),
                )
        finally:
            if not was_paused and not full_recheck_started:
                try:
                    torrent.handle.resume()
                except Exception:
                    pass

    @pyqtSlot(str)
    def generate_integrity_manifest(self, info_hash: str):
        torrent = self._torrents.get(info_hash)
        if not torrent:
            self.integrity_status.emit(False, "Integrity manifest failed: torrent unavailable")
            return
        try:
            if float(torrent.progress) < 0.999999:
                self.integrity_status.emit(
                    False, "Integrity manifest requires a completed torrent"
                )
                return
        except (TypeError, ValueError):
            self.integrity_status.emit(False, "Integrity manifest failed: invalid progress")
            return
        self._queue_integrity_manifest(info_hash, torrent)

    def _queue_integrity_manifest(self, info_hash: str, torrent: Torrent):
        existing = self._integrity_futures.get(info_hash)
        if existing is not None and not existing.done():
            self.integrity_status.emit(True, "Integrity manifest is already being generated")
            return
        try:
            torrent_info = torrent.handle.torrent_file()
            if not torrent_info:
                raise ValueError("torrent metadata is unavailable")
            plan = build_manifest_plan(
                torrent_info,
                torrent.save_path,
                info_hash,
                torrent.name,
                self._cfg.get("integrity_manifest_dir", ""),
            )
        except (OSError, TypeError, ValueError) as exc:
            self.integrity_status.emit(False, f"Integrity manifest failed: {exc}")
            return
        try:
            future = self._integrity_executor.submit(generate_manifest, plan)
            self._integrity_futures[info_hash] = future
            future.add_done_callback(
                lambda completed, info_hash=info_hash: self._integrity_fetch_done(
                    info_hash, completed
                )
            )
            self.integrity_status.emit(
                True, f"Generating SHA-256 manifest for {torrent.name}..."
            )
        except Exception as exc:
            self._integrity_futures.pop(info_hash, None)
            self.integrity_status.emit(False, f"Integrity manifest failed: {exc}")

    def _integrity_fetch_done(self, info_hash: str, future):
        try:
            result = future.result()
        except Exception as exc:
            result = IntegrityResult(success=False, error=str(exc))
        self.integrity_ready.emit((info_hash, result))

    @pyqtSlot(object)
    def _apply_integrity_result(self, payload):
        info_hash, result = payload
        self._integrity_futures.pop(info_hash, None)
        if not result.success:
            self.integrity_status.emit(
                False, f"Integrity manifest failed: {result.error}"
            )
            return
        self.integrity_status.emit(
            True,
            f"SHA-256 manifest written: {result.output_path} ({result.file_count} files)",
        )

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
            self._apply_label_rule(t)
            self._tracker_proxy_manager.sync_torrent(t)

    @pyqtSlot(str, str)
    def remove_tracker(self, info_hash: str, url: str):
        """Remove a tracker from a torrent."""
        t = self._torrents.get(info_hash)
        if t:
            self._tracker_proxy_manager.forget_tracker(info_hash, url)
            t.remove_tracker(url)

    @pyqtSlot(str, str)
    def test_tracker(self, info_hash: str, url: str):
        """Run one synthetic announce without changing torrent state."""
        t = self._torrents.get(info_hash)
        if not t or not self._session:
            self.tracker_tested.emit(
                info_hash, url,
                {"ok": False, "error_class": "Session", "message": "Torrent is unavailable"},
            )
            return
        result = self._tracker_proxy_manager.test_announce(
            t, url, self._session, int(self._cfg.get("listen_port", 6881) or 0)
        )
        self.tracker_tested.emit(info_hash, url, result)

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
            self._vpn_bind_address = str(
                self._cfg.get("vpn_bind_address", "") or ""
            ).strip()
            self._vpn_kill_switch = bool(self._cfg.get("vpn_kill_switch", False))
            self._vpn_available = None
            self._blocklist_next_refresh_at = time.monotonic()
            self._blocklist_refresh_generation += 1
            self._tracker_proxy_manager.configure(build_tracker_proxy_rules(self._cfg))
            self._label_rules = parse_label_rules(build_label_automation_rules(self._cfg))
            self._torrent_schedules = parse_torrent_schedules(
                build_torrent_schedule_settings(self._cfg)
            )
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
        settings['listen_interfaces'] = build_listen_interfaces(
            self._vpn_bind_address, self._cfg.get("listen_port", 6881)
        )
        settings.update(build_i2p_settings(self._cfg))
        settings.update(build_private_tracker_settings(self._cfg))
        self._session.apply_settings(settings)
        self._peer_filter.configure(self._cfg)
        reputation_path = self._reputation_path(self._cfg)
        if reputation_path != self._peer_reputation_path:
            self._peer_reputation_path = reputation_path
            self._peer_reputation = PeerReputationStore(reputation_path)
        self._hook_runner.configure(self._cfg.get("script_hooks", []))
        self._load_ip_blocklist()
        self._check_blocklist_refresh()
        for torrent in self._torrents.values():
            _apply_private_tracker_profile(torrent, self._cfg)
            self._apply_label_rule(torrent)
            self._tracker_proxy_manager.sync_torrent(torrent)

    # --- Internal: Script hooks ---

    @staticmethod
    def _reputation_path(cfg: dict) -> Path:
        configured = str(cfg.get("peer_reputation_path", "") or "").strip()
        return Path(configured) if configured else Path.home() / ".flux-torrent" / "peer-reputation.json"

    @staticmethod
    def _alert_peer_ip(alert) -> str:
        try:
            value = getattr(alert, "ip", None)
            if isinstance(value, (tuple, list)):
                return str(value[0])
            return str(value or "")
        except Exception:
            return ""

    def _peer_from_alert(self, alert):
        handle = getattr(alert, "handle", None)
        peer_ip = self._alert_peer_ip(alert)
        if handle is None or not peer_ip:
            return None
        try:
            return next(
                (peer for peer in handle.get_peer_info() if str(peer.ip[0]) == peer_ip),
                None,
            )
        except Exception:
            return None

    def _record_peer_reputation_alert(self, alert):
        if not self._cfg.get("peer_reputation_enabled", True):
            return
        alert_name = type(alert).__name__.casefold()
        if "peer_error" in alert_name:
            event = "error"
        elif "peer_disconnected" in alert_name:
            event = "disconnect"
        elif "hash_failed" in alert_name:
            event = "hash_fail"
        else:
            return
        peer = self._peer_from_alert(alert)
        if peer is None:
            return
        peer_ip = str(peer.ip[0])
        record = self._peer_reputation.record(peer_ip, event, getattr(peer, "client", ""))
        if record and self._reputation_should_deprioritize(peer_ip):
            self._deprioritize_peer(getattr(alert, "handle", None), peer)

    def _reputation_should_deprioritize(self, peer_ip: str) -> bool:
        return self._peer_reputation.should_deprioritize(
            peer_ip, self._cfg.get("peer_reputation_threshold", 3)
        )

    def _deprioritize_peer(self, handle, peer) -> bool:
        if handle is None:
            return False
        try:
            limit = max(1024, int(self._cfg.get("peer_reputation_limit", 16384)))
        except (TypeError, ValueError):
            limit = 16384
        peer_ip = str(peer.ip[0])
        port = int(peer.ip[1])
        endpoints = [(peer_ip, port), peer_ip]
        try:
            endpoints.insert(0, lt.tcp_endpoint(peer_ip, port))
        except (AttributeError, TypeError, ValueError):
            pass
        applied = False
        for method_name in ("set_peer_download_limit", "set_peer_upload_limit"):
            method = getattr(handle, method_name, None)
            if method is None:
                continue
            for endpoint in endpoints:
                try:
                    method(endpoint, limit)
                    applied = True
                    break
                except (TypeError, ValueError, RuntimeError):
                    continue
        if applied:
            self.peer_reputation.emit(
                peer_ip,
                f"Peer reputation limited {peer_ip} to {limit // 1024} KiB/s",
            )
        return applied

    def _label_rule(self, torrent: Torrent):
        return label_rule_for(torrent.category, torrent.tags, self._label_rules)

    def _apply_label_rule(self, torrent: Torrent):
        """Apply label-scoped upload and tracker overrides to a torrent."""
        rule = self._label_rule(torrent)
        if rule is None:
            return
        if rule.upload_limit > 0:
            torrent.set_upload_limit(rule.upload_limit)
        if rule.tracker_overrides:
            torrent.replace_trackers(list(rule.tracker_overrides))

    def _apply_label_completion(self, torrent: Torrent):
        rule = self._label_rule(torrent)
        if rule is None or not rule.move_completed_path:
            return
        destination = ensure_move_path(rule.move_completed_path)
        if destination:
            torrent.move_storage(destination)

    def _enforce_label_ratio(self, torrent: Torrent, snap):
        rule = self._label_rule(torrent)
        if rule is None or rule.ratio_limit <= 0 or snap.ratio < rule.ratio_limit:
            return
        state_name = getattr(snap.state, "name", "")
        if state_name not in {"PAUSED", "QUEUED"}:
            torrent.pause()
            logger.info("Label ratio limit reached for %s: %.3f", snap.info_hash, snap.ratio)

    def _fire_hook(self, event: str, torrent: Torrent, error: str = ""):
        """Build torrent info dict and fire script hooks."""
        snap = torrent.snapshot()
        info = {
            "name": snap.name,
            "info_hash": snap.info_hash,
            "save_path": snap.save_path,
            "category": snap.category,
            "tags": snap.tags,
            "total_size": snap.total_size,
            "progress": snap.progress,
            "ratio": snap.ratio,
            "total_downloaded": snap.total_downloaded,
            "total_uploaded": snap.total_uploaded,
        }
        if error:
            info["error"] = error
        self._hook_runner.fire(event, info)

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
                if self._cfg.get("private_tracker_profile", False) and _PRIVATE_TRACKER_FLAGS:
                    atp.flags |= _PRIVATE_TRACKER_FLAGS
                handle = self._session.add_torrent(atp)
                tags = json.loads(tags_json) if tags_json else []
                torrent = Torrent(handle, category=category, tags=tags)
                torrent.added_time = added_time or time.time()
                info_hash, _, _ = get_info_hashes(handle)
                self._torrents[info_hash] = torrent
                self._remember_recheck_fingerprint(info_hash, torrent)
                _apply_private_tracker_profile(torrent, self._cfg)
                self._apply_label_rule(torrent)
                self._tracker_proxy_manager.sync_torrent(torrent)
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
            info_hash, _, _ = get_info_hashes(handle)
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
            return 0
        try:
            with open(blocklist_path, "r", errors="ignore") as blocklist_file:
                ranges = parse_blocklist_ranges(blocklist_file.read())
            count = self._apply_ip_filter_ranges(ranges, blocklist_path)
            return count
        except Exception as e:
            logger.error(f"Failed to load IP blocklist: {e}")
            return 0

    def _apply_ip_filter_ranges(self, ranges, source: str = "") -> int:
        """Replace the active filter only after a complete range set is ready."""
        if not self._session:
            return 0
        try:
            new_filter = lt.ip_filter()
            for start, end in ranges:
                new_filter.add_rule(start, end, 1)
            for ip in sorted(self._dynamic_banned_ips):
                new_filter.add_rule(ip, ip, 1)
            self._ip_filter = new_filter
            self._session.set_ip_filter(new_filter)
            count = len(ranges)
            if count:
                logger.info("Loaded %d IP blocklist entries%s", count, f" from {source}" if source else "")
            return count
        except Exception as exc:
            logger.error("Failed to apply IP blocklist: %s", exc)
            return 0

    def _check_blocklist_refresh(self):
        """Submit one scheduled mirror refresh without blocking the session thread."""
        if not self._cfg.get("ip_blocklist_auto_refresh", False):
            return
        urls = normalize_blocklist_urls(self._cfg.get("ip_blocklist_urls", []))
        if not urls or self._blocklist_future is not None:
            return
        if time.monotonic() < self._blocklist_next_refresh_at:
            return
        try:
            refresh_hours = max(1, int(self._cfg.get("ip_blocklist_refresh_hours", 24) or 24))
        except (TypeError, ValueError):
            refresh_hours = 24
        self._blocklist_next_refresh_at = time.monotonic() + refresh_hours * 3600
        try:
            generation = self._blocklist_refresh_generation
            self._blocklist_future = self._blocklist_executor.submit(fetch_blocklist, urls)
            self._blocklist_future.add_done_callback(
                lambda future, generation=generation: self._blocklist_fetch_done(
                    future, generation
                )
            )
        except Exception as exc:
            self._blocklist_future = None
            self.blocklist_status.emit(False, f"IP blocklist refresh failed: {exc}")

    def _blocklist_fetch_done(self, future, generation):
        """Forward a background fetch result back to the worker thread."""
        try:
            result = future.result()
        except Exception as exc:
            result = BlocklistFetchResult(success=False, error=str(exc))
        self.blocklist_ready.emit((generation, result))

    @pyqtSlot(object)
    def _apply_blocklist_result(self, payload):
        if isinstance(payload, tuple) and len(payload) == 2:
            generation, result = payload
        else:
            generation, result = self._blocklist_refresh_generation, payload
        if generation != self._blocklist_refresh_generation:
            self._blocklist_future = None
            return
        self._blocklist_future = None
        if not result.success:
            self.blocklist_status.emit(False, f"IP blocklist refresh failed: {result.error}")
            return
        count = self._apply_ip_filter_ranges(result.ranges, result.source)
        if count <= 0:
            self.blocklist_status.emit(False, "IP blocklist refresh returned no usable ranges")
            return
        cache_path = str(self._cfg.get("ip_blocklist_path", "") or "").strip()
        if cache_path:
            try:
                write_blocklist_cache(result.content, cache_path)
            except OSError as exc:
                logger.warning("Could not cache IP blocklist: %s", exc)
        self.blocklist_status.emit(
            True,
            f"IP blocklist updated from {result.source} ({count} ranges)",
        )

    # --- Internal: Alert processing ---

    def _record_alert_log(self, alert):
        """Keep a bounded alert history for the torrent that emitted it."""
        info_hash = _alert_info_hash(alert)
        if not info_hash:
            return
        entries = self._torrent_logs.setdefault(info_hash, [])
        entries.append(_alert_log_entry(alert))
        if len(entries) > _MAX_TORRENT_LOG_ENTRIES:
            del entries[:-_MAX_TORRENT_LOG_ENTRIES]

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
                self._record_alert_log(alert)
                self._record_peer_reputation_alert(alert)
                atype = type(alert)
                if atype == lt.torrent_finished_alert:
                    if self._cfg.get("integrity_manifest_auto", False):
                        ih, _, _ = get_info_hashes(alert.handle)
                        torrent = self._torrents.get(ih)
                        if torrent:
                            self._queue_integrity_manifest(ih, torrent)
                    self._on_torrent_finished(alert)
                elif atype == lt.torrent_error_alert:
                    ih, _, _ = get_info_hashes(alert.handle)
                    msg = str(alert.error.message()) if alert.error.value() != 0 else "Unknown"
                    self.torrent_error.emit(ih, msg)
                    t_err = self._torrents.get(ih)
                    if t_err:
                        self._fire_hook("on_error", t_err, error=msg)
                elif atype == lt.metadata_received_alert:
                    ih, _, _ = get_info_hashes(alert.handle)
                    self.torrent_metadata.emit(ih)
                elif atype == lt.save_resume_data_alert:
                    self._handle_save_resume_data(alert)
                elif atype == lt.save_resume_data_failed_alert:
                    pass
                elif atype == lt.torrent_checked_alert:
                    ih, _, _ = get_info_hashes(alert.handle)
                    torrent = self._torrents.get(ih)
                    if torrent:
                        self._remember_recheck_fingerprint(ih, torrent)
                    if ih in self._full_rechecks_pending:
                        self._full_rechecks_pending.discard(ih)
                        self.recheck_status.emit(ih, "Full re-check completed")
                elif atype == lt.peer_connect_alert:
                    self._on_peer_connect(alert)
                elif atype == lt.listen_succeeded_alert:
                    logger.info(f"Listening on {alert.address}:{alert.port}")
                elif atype == lt.listen_failed_alert:
                    logger.warning(f"Listen failed: {alert.error.message()}")
            except Exception as e:
                logger.debug(f"Alert error ({type(alert).__name__}): {e}")

    def _on_torrent_finished(self, alert):
        info_hash, _, _ = get_info_hashes(alert.handle)
        self.torrent_finished.emit(info_hash)
        torrent = self._torrents.get(info_hash)
        if not torrent:
            return
        self._apply_label_completion(torrent)
        self._fire_hook("on_finish", torrent)
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
        if self._cfg.get("peer_reputation_enabled", True):
            peer = self._peer_from_alert(alert)
            if peer is not None and self._reputation_should_deprioritize(str(peer.ip[0])):
                self._deprioritize_peer(getattr(alert, "handle", None), peer)
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
                            self._dynamic_banned_ips.add(peer.ip[0])
                            self._ip_filter.add_rule(peer.ip[0], peer.ip[0], 1)
                            self._session.set_ip_filter(self._ip_filter)
                            self.peer_banned.emit(peer.ip[0], reason)
                        break
        except Exception:
            pass

    # --- Internal: Stats ---

    def _check_vpn_binding(self):
        """Pause torrents when a configured VPN bind address disappears."""
        if not self._vpn_kill_switch or not self._vpn_bind_address:
            self._vpn_available = None
            return

        available = is_bind_address_available(self._vpn_bind_address)
        previous = self._vpn_available
        if previous is available:
            return
        self._vpn_available = available

        if previous is None and available:
            return

        if not available:
            paused_count = 0
            for torrent in self._torrents.values():
                try:
                    if not torrent.handle.is_paused():
                        torrent.pause()
                        paused_count += 1
                except Exception:
                    pass
            self.vpn_status.emit(
                False,
                f"VPN binding lost ({self._vpn_bind_address}); paused {paused_count} torrent(s)",
            )
        else:
            self.vpn_status.emit(
                True,
                f"VPN binding restored ({self._vpn_bind_address}); torrents remain paused",
            )

    def _record_activity(self, download_rate: int, upload_rate: int):
        """Convert the latest rates into byte volume for the local hour cell."""
        now = time.monotonic()
        elapsed = 1.0
        if self._last_activity_at is not None:
            elapsed = min(5.0, max(0.0, now - self._last_activity_at))
        self._last_activity_at = now
        if elapsed <= 0:
            return
        record_activity(
            self._activity_heatmap,
            datetime.now(),
            int(max(0, download_rate) * elapsed),
            int(max(0, upload_rate) * elapsed),
        )

    def _update_stats(self):
        if not self._session:
            return

        self._check_vpn_binding()

        self._tracker_proxy_manager.tick(
            self._torrents.values(), self._session,
            int(self._cfg.get("listen_port", 6881) or 0),
        )

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

        self._record_activity(dl_rate, ul_rate)

        # Snapshot all torrents
        snapshots = []
        auto_delete_hashes = []
        for torrent in self._torrents.values():
            try:
                snap = torrent.snapshot()
                torrent.record_speed()
                self._enforce_label_ratio(torrent, snap)
                snapshots.append(snap)
                self._emit_ratio_milestones(snap)
                if should_auto_delete(snap, self._cfg):
                    auto_delete_hashes.append(snap.info_hash)
            except Exception:
                pass

        stats = SessionStats(
            download_rate=dl_rate,
            upload_rate=ul_rate,
            dht_nodes=dht_count,
            dl_history=self._session_dl_history[:],
            ul_history=self._session_ul_history[:],
            activity_heatmap=normalize_heatmap(self._activity_heatmap),
            torrent_count=len(self._torrents),
            torrents=snapshots,
        )
        self.stats_updated.emit(stats)

        # Emit detail data for focused torrent
        if self._focused_hash:
            t = self._torrents.get(self._focused_hash)
            if t:
                try:
                    peer_piece_owners, peer_piece_labels = t.get_peer_piece_map()
                    detail = DetailData(
                        info_hash=self._focused_hash,
                        files=t.get_files(),
                        peers=t.get_peers(),
                        trackers=(t.get_trackers() +
                                  self._tracker_proxy_manager.tracker_snapshots(self._focused_hash)),
                        pieces=t.get_piece_states(),
                        piece_length=t.piece_length,
                        peer_piece_owners=peer_piece_owners,
                        peer_piece_labels=peer_piece_labels,
                        dl_history=t.speed_history_dl[:],
                        ul_history=t.speed_history_ul[:],
                        logs=[entry.copy() for entry in self._torrent_logs.get(
                            self._focused_hash, []
                        )],
                    )
                    self.detail_updated.emit(detail)
                except Exception as e:
                    logger.debug(f"Detail data error: {e}")

        for info_hash in auto_delete_hashes:
            self.remove_torrent(
                info_hash, bool(self._cfg.get("auto_delete_files", True))
            )

    def _emit_ratio_milestones(self, snap):
        """Emit each configured threshold once as a torrent ratio rises."""
        try:
            current = float(snap.ratio)
        except (AttributeError, TypeError, ValueError):
            return
        previous = self._ratio_last_seen.get(snap.info_hash)
        self._ratio_last_seen[snap.info_hash] = current
        if previous is None or not self._cfg.get("ratio_notifications_enabled", False):
            return
        milestones = normalize_ratio_milestones(
            self._cfg.get("ratio_notification_milestones", [1.0, 2.0])
        )
        for milestone in crossed_ratio_milestones(previous, current, milestones):
            self.ratio_milestone.emit(snap.info_hash, milestone)

    # --- Internal: Bandwidth schedule ---

    def _check_bandwidth_schedule(self):
        self._check_blocklist_refresh()
        self._check_torrent_schedules()
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

    def _check_torrent_schedules(self):
        """Apply recurring per-torrent start/stop windows."""
        for info_hash in self._torrent_schedules:
            torrent = self._torrents.get(info_hash)
            if not torrent or not torrent.is_valid:
                continue
            action = scheduled_action(info_hash, self._torrent_schedules)
            try:
                paused = torrent.handle.is_paused()
            except Exception:
                paused = False
            if action == "pause" and not paused:
                torrent.pause()
            elif action == "resume" and paused:
                torrent.resume()


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
