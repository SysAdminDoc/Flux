"""Torrent model wrapping libtorrent torrent_handle.

Key design: the snapshot() method captures all status in one call. Properties
read from the cached snapshot instead of hitting libtorrent per-attribute,
eliminating redundant FFI calls and preventing mid-refresh crashes if the
handle becomes invalid.
"""

import time
import logging
from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional, List

import libtorrent as lt

logger = logging.getLogger(__name__)

# Safe flag resolution (API varies between binding versions)
_tf = getattr(lt, 'torrent_flags', None) or getattr(lt, 'torrent_flags_t', None)
_FLAG_AUTO_MANAGED = getattr(_tf, 'auto_managed', 0x40) if _tf else 0x40
_FLAG_SEQUENTIAL = getattr(_tf, 'sequential_download', 0x200) if _tf else 0x200


class TorrentState(Enum):
    DOWNLOADING = auto()
    SEEDING = auto()
    PAUSED = auto()
    QUEUED = auto()
    CHECKING = auto()
    ERROR = auto()
    STALLED = auto()
    COMPLETED = auto()
    METADATA = auto()
    MOVING = auto()

    @property
    def display_name(self) -> str:
        return _STATE_DISPLAY.get(self, "Unknown")


_STATE_DISPLAY = {
    TorrentState.DOWNLOADING: "Downloading",
    TorrentState.SEEDING: "Seeding",
    TorrentState.PAUSED: "Paused",
    TorrentState.QUEUED: "Queued",
    TorrentState.CHECKING: "Checking",
    TorrentState.ERROR: "Error",
    TorrentState.STALLED: "Stalled",
    TorrentState.COMPLETED: "Completed",
    TorrentState.METADATA: "Getting Metadata",
    TorrentState.MOVING: "Moving",
}


@dataclass
class TorrentFile:
    index: int
    path: str
    size: int
    progress: float = 0.0
    priority: int = 4  # 0=skip, 1=low, 4=normal, 7=high


@dataclass
class TorrentPeer:
    ip: str
    port: int
    client: str
    dl_speed: int = 0
    ul_speed: int = 0
    progress: float = 0.0
    downloaded: int = 0
    uploaded: int = 0
    flags: str = ""
    country: str = ""


@dataclass
class TorrentTracker:
    url: str
    status: str = "Not contacted"
    seeds: int = 0
    peers: int = 0
    message: str = ""


@dataclass
class TorrentSnapshot:
    """Immutable snapshot of torrent status. Taken once per refresh cycle."""
    valid: bool = False
    state: TorrentState = TorrentState.ERROR
    name: str = "Unknown"
    info_hash: str = ""
    save_path: str = ""
    has_metadata: bool = False
    progress: float = 0.0
    total_size: int = 0
    completed_size: int = 0
    total_downloaded: int = 0
    total_uploaded: int = 0
    download_speed: int = 0
    upload_speed: int = 0
    num_seeds: int = 0
    num_peers: int = 0
    num_connections: int = 0
    ratio: float = 0.0
    eta: int = 0
    error: str = ""
    category: str = ""
    tags: list = None
    added_time: float = 0.0
    download_limit: int = 0
    upload_limit: int = 0

    def __post_init__(self):
        if self.tags is None:
            self.tags = []


class Torrent:
    """High-level wrapper around a libtorrent torrent_handle.

    Call snapshot() once per refresh cycle, then read all properties from
    the cached snapshot. This avoids N FFI calls per torrent per second.
    """

    def __init__(self, handle: lt.torrent_handle, category: str = "", tags: list = None):
        self._handle = handle
        self._category = category
        self._tags = tags or []
        self._added_time = time.time()
        self._speed_history_dl: list = []
        self._speed_history_ul: list = []
        self._max_history = 120
        self._snap = TorrentSnapshot()
        self.snapshot()

    # --- Snapshot system ---

    def snapshot(self) -> TorrentSnapshot:
        """Capture current status in one FFI call and cache it."""
        try:
            if not self._handle.is_valid():
                self._snap = TorrentSnapshot(
                    valid=False, state=TorrentState.ERROR,
                    name=self._snap.name or "Invalid",
                    info_hash=self._snap.info_hash,
                    category=self._category, tags=self._tags[:],
                    added_time=self._added_time, error="Handle is invalid",
                )
                return self._snap

            s = self._handle.status()

            # Resolve name
            name = "Unknown"
            if s.has_metadata:
                name = s.name
            elif hasattr(s, 'name') and s.name:
                name = s.name
            else:
                try:
                    name = str(self._handle.info_hash())
                except Exception:
                    name = self._snap.name or "Unknown"

            state = self._resolve_state(s)
            error = s.errc.message() if s.errc.value() != 0 else ""

            total_dl = s.all_time_download
            total_ul = s.all_time_upload
            ratio = total_ul / total_dl if total_dl > 0 else 0.0

            eta = 0
            if s.download_rate > 0:
                remaining = s.total_wanted - s.total_wanted_done
                if remaining > 0:
                    eta = int(remaining / s.download_rate)

            try:
                dl_limit = self._handle.download_limit()
                ul_limit = self._handle.upload_limit()
            except Exception:
                dl_limit = 0
                ul_limit = 0

            self._snap = TorrentSnapshot(
                valid=True, state=state, name=name,
                info_hash=str(self._handle.info_hash()),
                save_path=s.save_path, has_metadata=s.has_metadata,
                progress=s.progress,
                total_size=s.total_wanted if s.has_metadata else 0,
                completed_size=s.total_wanted_done,
                total_downloaded=total_dl, total_uploaded=total_ul,
                download_speed=s.download_rate, upload_speed=s.upload_rate,
                num_seeds=s.num_seeds, num_peers=s.num_peers,
                num_connections=s.num_connections,
                ratio=ratio, eta=eta, error=error,
                category=self._category, tags=self._tags[:],
                added_time=self._added_time,
                download_limit=dl_limit, upload_limit=ul_limit,
            )
            return self._snap

        except Exception as e:
            self._snap = TorrentSnapshot(
                valid=False, state=TorrentState.ERROR,
                name=self._snap.name or "Error",
                info_hash=self._snap.info_hash,
                category=self._category, tags=self._tags[:],
                added_time=self._added_time, error=str(e),
            )
            return self._snap

    def _resolve_state(self, s) -> TorrentState:
        """Determine TorrentState from libtorrent status object."""
        if s.errc.value() != 0:
            return TorrentState.ERROR

        lt_state = s.state

        is_paused = False
        is_auto_managed = False
        try:
            is_paused = s.paused
            is_auto_managed = s.auto_managed
        except AttributeError:
            try:
                flags = s.flags
                is_paused = bool(flags & lt.torrent_flags.paused)
                is_auto_managed = bool(flags & lt.torrent_flags.auto_managed)
            except Exception:
                pass

        if is_paused and not is_auto_managed:
            return TorrentState.PAUSED
        if is_paused and is_auto_managed:
            return TorrentState.QUEUED
        if lt_state in (lt.torrent_status.states.checking_files,
                        lt.torrent_status.states.checking_resume_data):
            return TorrentState.CHECKING
        if lt_state == lt.torrent_status.states.downloading_metadata:
            return TorrentState.METADATA
        if lt_state == lt.torrent_status.states.downloading:
            if s.download_rate < 1024 and s.num_seeds > 0:
                return TorrentState.STALLED
            return TorrentState.DOWNLOADING
        if lt_state == lt.torrent_status.states.finished:
            return TorrentState.COMPLETED
        if lt_state == lt.torrent_status.states.seeding:
            return TorrentState.SEEDING
        return TorrentState.DOWNLOADING

    # --- Cached property accessors ---

    @property
    def handle(self) -> lt.torrent_handle:
        return self._handle

    @property
    def is_valid(self) -> bool:
        try:
            return self._handle.is_valid()
        except Exception:
            return False

    @property
    def info_hash(self) -> str:
        return self._snap.info_hash

    @property
    def name(self) -> str:
        return self._snap.name

    @property
    def save_path(self) -> str:
        return self._snap.save_path

    @property
    def state(self) -> TorrentState:
        return self._snap.state

    @property
    def error(self) -> str:
        return self._snap.error

    @property
    def progress(self) -> float:
        return self._snap.progress

    @property
    def total_size(self) -> int:
        return self._snap.total_size

    @property
    def completed_size(self) -> int:
        return self._snap.completed_size

    @property
    def total_downloaded(self) -> int:
        return self._snap.total_downloaded

    @property
    def total_uploaded(self) -> int:
        return self._snap.total_uploaded

    @property
    def download_speed(self) -> int:
        return self._snap.download_speed

    @property
    def upload_speed(self) -> int:
        return self._snap.upload_speed

    @property
    def eta(self) -> int:
        return self._snap.eta

    @property
    def num_seeds(self) -> int:
        return self._snap.num_seeds

    @property
    def num_peers(self) -> int:
        return self._snap.num_peers

    @property
    def num_connections(self) -> int:
        return self._snap.num_connections

    @property
    def ratio(self) -> float:
        return self._snap.ratio

    @property
    def has_metadata(self) -> bool:
        return self._snap.has_metadata

    @property
    def num_pieces(self) -> int:
        try:
            ti = self._handle.torrent_file()
            return ti.num_pieces() if ti else 0
        except Exception:
            return 0

    @property
    def piece_length(self) -> int:
        try:
            ti = self._handle.torrent_file()
            return ti.piece_length() if ti else 0
        except Exception:
            return 0

    # --- Organization ---

    @property
    def category(self) -> str:
        return self._category

    @category.setter
    def category(self, value: str):
        self._category = value

    @property
    def tags(self) -> list:
        return self._tags

    def add_tag(self, tag: str):
        if tag not in self._tags:
            self._tags.append(tag)

    def remove_tag(self, tag: str):
        if tag in self._tags:
            self._tags.remove(tag)

    @property
    def added_time(self) -> float:
        return self._added_time

    @added_time.setter
    def added_time(self, value: float):
        self._added_time = value

    # --- Files ---

    def get_files(self) -> List[TorrentFile]:
        try:
            ti = self._handle.torrent_file()
            if not ti:
                return []
            files = []
            file_storage = ti.files()
            file_progress = self._handle.file_progress()
            priorities = self._handle.get_file_priorities()
            for i in range(file_storage.num_files()):
                size = file_storage.file_size(i)
                prog = file_progress[i] / size if size > 0 else 0.0
                files.append(TorrentFile(
                    index=i, path=file_storage.file_path(i),
                    size=size, progress=prog, priority=priorities[i],
                ))
            return files
        except Exception as e:
            logger.debug(f"get_files failed: {e}")
            return []

    def set_file_priority(self, index: int, priority: int):
        try:
            priorities = self._handle.get_file_priorities()
            if 0 <= index < len(priorities):
                priorities[index] = priority
                self._handle.prioritize_files(priorities)
        except Exception as e:
            logger.warning(f"set_file_priority failed: {e}")

    # --- Peers ---

    def get_peers(self) -> List[TorrentPeer]:
        peers = []
        try:
            for p in self._handle.get_peer_info():
                peers.append(TorrentPeer(
                    ip=p.ip[0], port=p.ip[1], client=p.client,
                    dl_speed=p.down_speed, ul_speed=p.up_speed,
                    progress=p.progress,
                    downloaded=getattr(p, 'total_download', 0),
                    uploaded=getattr(p, 'total_upload', 0),
                    flags=str(p.flags),
                    country=getattr(p, "country", ""),
                ))
        except Exception:
            pass
        return peers

    # --- Trackers ---

    def get_trackers(self) -> List[TorrentTracker]:
        trackers = []
        try:
            for t in self._handle.trackers():
                status = "Working"
                message = ""
                seeds = 0
                peers = 0
                if t.endpoints:
                    ep = t.endpoints[0]
                    if ep.info_hashes and len(ep.info_hashes) > 0:
                        ih = list(ep.info_hashes)
                        if ih:
                            info = ih[0]
                            seeds = info.scrape_complete
                            peers = info.scrape_incomplete
                            message = info.message
                            if info.fails > 0:
                                status = "Error"
                            elif (not info.updating and info.fails == 0
                                  and info.scrape_complete == 0
                                  and info.scrape_incomplete == 0):
                                status = "Not contacted"
                trackers.append(TorrentTracker(
                    url=t.url, status=status, seeds=seeds,
                    peers=peers, message=message,
                ))
        except Exception as e:
            logger.debug(f"get_trackers failed: {e}")
        return trackers

    def add_tracker(self, url: str, tier: int = 0):
        """Add a tracker to this torrent."""
        try:
            self._handle.add_tracker({"url": url, "tier": tier})
        except Exception as e:
            logger.warning(f"add_tracker failed: {e}")

    def remove_tracker(self, url: str):
        """Remove a tracker by replacing the tracker list without it."""
        try:
            current = self._handle.trackers()
            new_trackers = [t for t in current if t.url != url]
            self._handle.replace_trackers(new_trackers)
        except Exception as e:
            logger.warning(f"remove_tracker failed: {e}")

    # --- Pieces ---

    def get_piece_states(self) -> list:
        """Get piece availability. Returns list: 0=missing, 1=downloading, 2=have."""
        try:
            s = self._handle.status()
            if not s.has_metadata:
                return []
            pieces = []
            piece_info = s.pieces
            downloading = set()
            try:
                queue = self._handle.get_download_queue()
                for piece in queue:
                    downloading.add(piece.piece_index)
            except Exception:
                pass
            for i in range(len(piece_info)):
                if piece_info[i]:
                    pieces.append(2)
                elif i in downloading:
                    pieces.append(1)
                else:
                    pieces.append(0)
            return pieces
        except Exception:
            return []

    # --- Speed History ---

    def record_speed(self):
        """Record current speed from snapshot for history graphs."""
        self._speed_history_dl.append(self._snap.download_speed)
        self._speed_history_ul.append(self._snap.upload_speed)
        if len(self._speed_history_dl) > self._max_history:
            self._speed_history_dl.pop(0)
        if len(self._speed_history_ul) > self._max_history:
            self._speed_history_ul.pop(0)

    @property
    def speed_history_dl(self) -> list:
        return self._speed_history_dl

    @property
    def speed_history_ul(self) -> list:
        return self._speed_history_ul

    # --- Actions ---

    def pause(self):
        try:
            self._handle.unset_flags(_FLAG_AUTO_MANAGED)
        except Exception:
            pass
        try:
            self._handle.pause()
        except Exception as e:
            logger.warning(f"pause failed: {e}")

    def resume(self):
        try:
            self._handle.set_flags(_FLAG_AUTO_MANAGED)
        except Exception:
            pass
        try:
            self._handle.resume()
        except Exception as e:
            logger.warning(f"resume failed: {e}")

    def force_resume(self):
        try:
            self._handle.unset_flags(_FLAG_AUTO_MANAGED)
        except Exception:
            pass
        try:
            self._handle.resume()
        except Exception as e:
            logger.warning(f"force_resume failed: {e}")

    def force_recheck(self):
        try:
            self._handle.force_recheck()
        except Exception as e:
            logger.warning(f"force_recheck failed: {e}")

    def force_reannounce(self):
        try:
            self._handle.force_reannounce()
        except Exception as e:
            logger.warning(f"force_reannounce failed: {e}")

    def set_download_limit(self, limit: int):
        """Set per-torrent download limit in bytes/s. 0 = unlimited."""
        try:
            self._handle.set_download_limit(limit)
        except Exception as e:
            logger.warning(f"set_download_limit failed: {e}")

    def set_upload_limit(self, limit: int):
        try:
            self._handle.set_upload_limit(limit)
        except Exception as e:
            logger.warning(f"set_upload_limit failed: {e}")

    def set_sequential(self, enabled: bool):
        try:
            if enabled:
                self._handle.set_flags(_FLAG_SEQUENTIAL)
            else:
                self._handle.unset_flags(_FLAG_SEQUENTIAL)
        except Exception:
            pass

    def move_storage(self, new_path: str):
        try:
            self._handle.move_storage(new_path)
        except Exception as e:
            logger.warning(f"move_storage failed: {e}")

    # --- Queue position ---

    @property
    def queue_position(self) -> int:
        try:
            return self._handle.queue_position()
        except Exception:
            return -1

    def queue_top(self):
        try:
            self._handle.queue_position_top()
        except Exception:
            pass

    def queue_up(self):
        try:
            self._handle.queue_position_up()
        except Exception:
            pass

    def queue_down(self):
        try:
            self._handle.queue_position_down()
        except Exception:
            pass

    def queue_bottom(self):
        try:
            self._handle.queue_position_bottom()
        except Exception:
            pass

    # --- Serialization ---

    def to_dict(self) -> dict:
        return {
            "info_hash": self.info_hash,
            "category": self._category,
            "tags": self._tags,
            "added_time": self._added_time,
        }
