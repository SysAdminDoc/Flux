"""Remote session client for connecting the desktop UI to a Flux daemon.

The client deliberately uses the same snapshot dataclasses as the local
libtorrent worker.  The GUI can therefore consume either a local session or
an HTTP-backed session without a second model or a second rendering path.
"""

from __future__ import annotations

import base64
import json
import logging
import ssl
from dataclasses import dataclass
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import (
    Request,
    HTTPCookieProcessor,
    HTTPSHandler,
    build_opener,
)

from PyQt6.QtCore import QObject, QMetaObject, QThread, QTimer, Qt, pyqtSignal, pyqtSlot

from flux.core.session_worker import DetailData, SessionStats
from flux.core.torrent import (
    TorrentFile,
    TorrentPeer,
    TorrentSnapshot,
    TorrentState,
    TorrentTracker,
)

logger = logging.getLogger(__name__)


class RemoteClientError(RuntimeError):
    """Raised when the remote daemon cannot satisfy a client request."""


@dataclass
class RemoteEndpoint:
    """Connection settings for a remote Flux daemon."""

    url: str = "http://127.0.0.1:8090/"
    token: str = ""
    username: str = "admin"
    password: str = ""
    timeout: float = 10.0
    verify_tls: bool = True

    @classmethod
    def from_settings(cls, settings: dict[str, Any]) -> "RemoteEndpoint":
        return cls(
            url=str(settings.get("remote_client_url", "http://127.0.0.1:8090/") or ""),
            token=str(settings.get("remote_client_token", "") or ""),
            username=str(settings.get("remote_client_username", "admin") or ""),
            password=str(settings.get("remote_client_password", "") or ""),
            timeout=float(settings.get("remote_client_timeout", 10.0) or 10.0),
            verify_tls=bool(settings.get("remote_client_verify_tls", True)),
        )

    def normalized_url(self) -> str:
        value = self.url.strip()
        if not value:
            raise ValueError("Remote daemon URL is required")
        if "://" not in value:
            value = f"http://{value}"
        return value.rstrip("/") + "/"


class RemoteSessionClient:
    """Synchronous protocol client used by :class:`RemoteSessionWorker`.

    Network calls are kept behind this small class so they can be tested with
    an injected opener and never leak HTTP details into the Qt model layer.
    """

    def __init__(
        self,
        endpoint: RemoteEndpoint,
        opener: Any | None = None,
        urlopen: Callable[..., Any] | None = None,
    ):
        self.endpoint = endpoint
        self._base_url = endpoint.normalized_url()
        self._urlopen = urlopen
        if opener is None:
            handlers: list[Any] = [HTTPCookieProcessor(CookieJar())]
            if self._base_url.startswith("https://") and not endpoint.verify_tls:
                handlers.append(HTTPSHandler(context=ssl._create_unverified_context()))
            self._opener = build_opener(*handlers)
        else:
            self._opener = opener
        self._logged_in = False

    @classmethod
    def from_settings(cls, settings: dict[str, Any]) -> "RemoteSessionClient":
        return cls(RemoteEndpoint.from_settings(settings))

    @property
    def base_url(self) -> str:
        return self._base_url

    def login(self) -> None:
        """Authenticate with the daemon's qB-compatible cookie endpoint."""
        if self.endpoint.token or not self.endpoint.password:
            self._logged_in = True
            return
        self._request(
            "/api/v2/auth/login",
            method="POST",
            form={
                "username": self.endpoint.username,
                "password": self.endpoint.password,
            },
            authenticate=False,
        )
        self._logged_in = True

    def fetch_status(self) -> dict[str, Any]:
        """Fetch a validated v1 status payload from the daemon."""
        payload = self._request_json("/api/v1/status")
        if not isinstance(payload, dict) or not isinstance(payload.get("session"), dict):
            raise RemoteClientError("Remote status response is not a Flux payload")
        payload.setdefault("torrents", [])
        return payload

    def fetch_stats(self) -> SessionStats:
        """Fetch status and convert it to the local session snapshot type."""
        payload = self.fetch_status()
        session = payload["session"]
        torrents = [
            _snapshot_from_payload(item)
            for item in payload.get("torrents", [])
            if isinstance(item, dict)
        ]
        return SessionStats(
            download_rate=_as_int(session.get("download_rate")),
            upload_rate=_as_int(session.get("upload_rate")),
            dht_nodes=_as_int(session.get("dht_nodes")),
            dl_history=_as_int_list(session.get("download_history")),
            ul_history=_as_int_list(session.get("upload_history")),
            torrent_count=_as_int(session.get("torrent_count"), len(torrents)),
            torrents=torrents,
        )

    def fetch_detail(self, info_hash: str) -> DetailData:
        """Fetch detail data for one torrent."""
        payload = self._request_json(f"/api/v1/torrents/{info_hash}/details")
        if not isinstance(payload, dict):
            raise RemoteClientError("Remote detail response is not an object")
        return _detail_from_payload(payload, info_hash)

    def add_magnet(
        self,
        uri: str,
        save_path: str = "",
        category: str = "",
        tags: list[str] | None = None,
        paused: bool = False,
    ) -> bool:
        response = self._command(
            "/api/v1/torrents/add",
            {
                "magnet": uri,
                "save_path": save_path,
                "category": category,
                "tags": "|".join(tags or []),
                "paused": paused,
            },
        )
        return _as_int(response.get("added")) > 0

    def add_torrent_file(
        self,
        filepath: str,
        save_path: str = "",
        category: str = "",
        tags: list[str] | None = None,
        paused: bool = False,
        sequential: bool = False,
    ) -> bool:
        data = Path(filepath).read_bytes()
        response = self._command(
            "/api/v1/torrents/add",
            {
                "torrent_data": base64.b64encode(data).decode("ascii"),
                "filename": Path(filepath).name,
                "save_path": save_path,
                "category": category,
                "tags": "|".join(tags or []),
                "paused": paused,
                "sequential": sequential,
            },
        )
        return _as_int(response.get("added")) > 0

    def _command(self, path: str, values: dict[str, Any]) -> dict[str, Any]:
        payload = self._request_json(path, method="POST", json_body=values)
        if not isinstance(payload, dict):
            raise RemoteClientError("Remote command response is not an object")
        return payload

    def pause_torrent(self, info_hash: str) -> bool:
        return self._command_hash("pause", info_hash)

    def resume_torrent(self, info_hash: str) -> bool:
        return self._command_hash("resume", info_hash)

    def remove_torrent(self, info_hash: str, delete_files: bool = False) -> bool:
        return self._command_hash("delete", info_hash, delete_files=delete_files)

    def pause_all(self) -> bool:
        return self._command_hash("pause", "all")

    def resume_all(self) -> bool:
        return self._command_hash("resume", "all")

    def force_recheck(self, info_hash: str) -> bool:
        return self._command_hash("recheck", info_hash)

    def force_reannounce(self, info_hash: str) -> bool:
        return self._command_hash("reannounce", info_hash)

    def set_sequential(self, info_hash: str, enabled: bool) -> bool:
        response = self._command(
            "/api/v1/torrents/set-sequential",
            {"hashes": info_hash, "enabled": enabled},
        )
        return bool(response.get("ok"))

    def set_speed_limit(self, info_hash: str, download: int, upload: int) -> bool:
        response = self._command(
            "/api/v1/torrents/set-speed-limit",
            {"hashes": info_hash, "download": download, "upload": upload},
        )
        return bool(response.get("ok"))

    def queue_action(self, info_hash: str, action: str) -> bool:
        response = self._command(
            "/api/v1/torrents/queue",
            {"hashes": info_hash, "action": action},
        )
        return bool(response.get("ok"))

    def _command_hash(
        self,
        action: str,
        info_hash: str,
        delete_files: bool = False,
    ) -> bool:
        response = self._command(
            f"/api/v1/torrents/{action}",
            {"hashes": info_hash, "deleteFiles": delete_files},
        )
        return bool(response.get("ok"))

    def _request_json(
        self,
        path: str,
        method: str = "GET",
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        body = json.dumps(json_body).encode("utf-8") if json_body is not None else None
        return self._request(
            path,
            method=method,
            data=body,
            headers={"Content-Type": "application/json"} if body is not None else None,
        )

    def _request(
        self,
        path: str,
        method: str = "GET",
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        form: dict[str, str] | None = None,
        authenticate: bool = True,
    ) -> Any:
        if authenticate and self.endpoint.password and not self.endpoint.token and not self._logged_in:
            self.login()

        if form is not None:
            data = urlencode(form).encode("utf-8")
            headers = {**(headers or {}), "Content-Type": "application/x-www-form-urlencoded"}

        request_headers = {"Accept": "application/json", **(headers or {})}
        if self.endpoint.token:
            request_headers["Authorization"] = f"Bearer {self.endpoint.token}"

        request = Request(
            urljoin(self._base_url, path.lstrip("/")),
            data=data,
            headers=request_headers,
            method=method,
        )
        try:
            if self._urlopen is not None:
                response = self._urlopen(request, timeout=self.endpoint.timeout)
            else:
                response = self._opener.open(request, timeout=self.endpoint.timeout)
            raw = response.read()
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise RemoteClientError(f"Remote HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise RemoteClientError(f"Remote connection failed: {exc.reason}") from exc
        except OSError as exc:
            raise RemoteClientError(f"Remote connection failed: {exc}") from exc

        content_type = str(getattr(response, "headers", {}).get("Content-Type", ""))
        if "json" in content_type or raw[:1] in (b"{", b"["):
            try:
                return json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RemoteClientError("Remote returned invalid JSON") from exc
        return raw.decode("utf-8", errors="replace")


class RemoteSessionWorker(QObject):
    """Qt worker that polls a remote session and mirrors local worker signals."""

    torrent_added = pyqtSignal(str)
    torrent_removed = pyqtSignal(str)
    torrent_finished = pyqtSignal(str)
    torrent_error = pyqtSignal(str, str)
    torrent_metadata = pyqtSignal(str)
    stats_updated = pyqtSignal(object)
    detail_updated = pyqtSignal(object)
    peer_banned = pyqtSignal(str, str)
    magnet_uri_ready = pyqtSignal(str)
    started = pyqtSignal()
    stopped = pyqtSignal()

    def __init__(self, cfg: dict[str, Any]):
        super().__init__()
        self._cfg = dict(cfg)
        self._client = RemoteSessionClient.from_settings(self._cfg)
        self._focused_hash = ""
        self._timer: QTimer | None = None
        self._running = False

    @pyqtSlot()
    def initialize(self):
        self._timer = QTimer(self)
        self._timer.setInterval(max(250, int(self._cfg.get("remote_client_poll_ms", 1000))))
        self._timer.timeout.connect(self._poll)
        self._running = True
        self._timer.start()
        self.started.emit()
        self._poll()

    @pyqtSlot()
    def shutdown(self):
        self._running = False
        if self._timer:
            self._timer.stop()
            self._timer.deleteLater()
            self._timer = None
        self.stopped.emit()

    @pyqtSlot()
    def _poll(self):
        if not self._running:
            return
        try:
            self.stats_updated.emit(self._client.fetch_stats())
            if self._focused_hash:
                self.detail_updated.emit(self._client.fetch_detail(self._focused_hash))
        except RemoteClientError as exc:
            logger.warning("Remote session poll failed: %s", exc)

    @pyqtSlot(str, str, str, str, bool)
    def add_magnet(self, uri: str, save_path: str = "", category: str = "", tags_json: str = "[]", paused: bool = False):
        try:
            tags = json.loads(tags_json) if tags_json else []
            if self._client.add_magnet(uri, save_path, category, tags, paused):
                self.torrent_added.emit("")
        except (RemoteClientError, OSError, json.JSONDecodeError) as exc:
            logger.warning("Remote add magnet failed: %s", exc)

    @pyqtSlot(str, str, str, str, bool, bool)
    def add_torrent_file(self, filepath: str, save_path: str = "", category: str = "", tags_json: str = "[]", paused: bool = False, sequential: bool = False):
        try:
            tags = json.loads(tags_json) if tags_json else []
            if self._client.add_torrent_file(filepath, save_path, category, tags, paused, sequential):
                self.torrent_added.emit("")
        except (RemoteClientError, OSError, json.JSONDecodeError) as exc:
            logger.warning("Remote add torrent failed: %s", exc)

    @pyqtSlot(str, bool)
    def remove_torrent(self, info_hash: str, delete_files: bool = False):
        self._run_action(self._client.remove_torrent, info_hash, delete_files)

    @pyqtSlot(str)
    def pause_torrent(self, info_hash: str):
        self._run_action(self._client.pause_torrent, info_hash)

    @pyqtSlot(str)
    def resume_torrent(self, info_hash: str):
        self._run_action(self._client.resume_torrent, info_hash)

    @pyqtSlot()
    def pause_all(self):
        self._run_action(self._client.pause_all)

    @pyqtSlot()
    def resume_all(self):
        self._run_action(self._client.resume_all)

    @pyqtSlot(str)
    def force_recheck(self, info_hash: str):
        self._run_action(self._client.force_recheck, info_hash)

    @pyqtSlot(str)
    def force_reannounce(self, info_hash: str):
        self._run_action(self._client.force_reannounce, info_hash)

    @pyqtSlot(str)
    def force_resume(self, info_hash: str):
        self._run_action(self._client.resume_torrent, info_hash)

    @pyqtSlot(str, bool)
    def set_sequential(self, info_hash: str, enabled: bool):
        self._run_action(self._client.set_sequential, info_hash, enabled)

    @pyqtSlot(str, str)
    def queue_action(self, info_hash: str, action: str):
        self._run_action(self._client.queue_action, info_hash, action)

    @pyqtSlot(str, int, int)
    def set_torrent_speed_limit(self, info_hash: str, dl_limit: int, ul_limit: int):
        self._run_action(self._client.set_speed_limit, info_hash, dl_limit, ul_limit)

    @pyqtSlot(str)
    def set_focused_torrent(self, info_hash: str):
        self._focused_hash = info_hash
        if info_hash:
            try:
                self.detail_updated.emit(self._client.fetch_detail(info_hash))
            except RemoteClientError as exc:
                logger.warning("Remote detail fetch failed: %s", exc)

    @pyqtSlot(dict)
    def apply_settings(self, cfg: dict[str, Any] | None = None):
        if cfg is not None:
            self._cfg = dict(cfg)
            self._client = RemoteSessionClient.from_settings(self._cfg)

    @pyqtSlot(str)
    def request_magnet_uri(self, info_hash: str):
        logger.info("Remote magnet URI generation is not available for %s", info_hash)

    def _run_action(self, func: Callable[..., bool], *args: Any):
        try:
            func(*args)
        except RemoteClientError as exc:
            logger.warning("Remote action failed: %s", exc)


class RemoteThreadedSession:
    """Thread wrapper with the same surface as ``ThreadedSession``."""

    def __init__(self, settings):
        self.thread = QThread()
        self.thread.setObjectName("FluxRemoteSessionThread")
        self.worker = RemoteSessionWorker(settings.get_all())
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.initialize)

    def start(self):
        self.thread.start()

    def stop(self):
        if self.thread.isRunning():
            QMetaObject.invokeMethod(
                self.worker,
                "shutdown",
                Qt.ConnectionType.BlockingQueuedConnection,
            )
            self.thread.quit()
            if not self.thread.wait(15000):
                logger.warning("Remote session thread did not stop in time")
                self.thread.terminate()
                self.thread.wait()


def _snapshot_from_payload(item: dict[str, Any]) -> TorrentSnapshot:
    state_name = str(item.get("state", "Error") or "Error").lower()
    state = _state_from_name(state_name)
    return TorrentSnapshot(
        valid=True,
        state=state,
        name=str(item.get("name", "Unknown") or "Unknown"),
        info_hash=str(item.get("info_hash", "") or ""),
        info_hash_v1=str(item.get("info_hash_v1", "") or ""),
        info_hash_v2=str(item.get("info_hash_v2", "") or ""),
        save_path=str(item.get("save_path", "") or ""),
        has_metadata=bool(item.get("total_size", 0)),
        progress=_as_float(item.get("progress")),
        total_size=_as_int(item.get("total_size")),
        completed_size=_as_int(item.get("completed_size")),
        total_downloaded=_as_int(item.get("downloaded")),
        total_uploaded=_as_int(item.get("uploaded")),
        download_speed=_as_int(item.get("download_speed")),
        upload_speed=_as_int(item.get("upload_speed")),
        num_seeds=_as_int(item.get("seeds")),
        num_peers=_as_int(item.get("peers")),
        ratio=_as_float(item.get("ratio")),
        seeding_time=_as_int(item.get("seeding_time")),
        eta=_as_int(item.get("eta")),
        error=str(item.get("error", "") or ""),
        category=str(item.get("category", "") or ""),
        tags=[str(tag) for tag in item.get("tags", []) or []],
        added_time=_as_float(item.get("added_time")),
        download_limit=_as_int(item.get("download_limit")),
        upload_limit=_as_int(item.get("upload_limit")),
    )


def _detail_from_payload(payload: dict[str, Any], info_hash: str) -> DetailData:
    return DetailData(
        info_hash=str(payload.get("info_hash", info_hash) or info_hash),
        files=[_file_from_payload(item) for item in payload.get("files", []) if isinstance(item, dict)],
        peers=[_peer_from_payload(item) for item in payload.get("peers", []) if isinstance(item, dict)],
        trackers=[_tracker_from_payload(item) for item in payload.get("trackers", []) if isinstance(item, dict)],
        pieces=[_as_int(item) for item in payload.get("pieces", []) or []],
        piece_length=_as_int(payload.get("piece_length")),
        dl_history=_as_int_list(payload.get("download_history")),
        ul_history=_as_int_list(payload.get("upload_history")),
    )


def _file_from_payload(item: dict[str, Any]) -> TorrentFile:
    return TorrentFile(
        index=_as_int(item.get("index")),
        path=str(item.get("path", "") or ""),
        size=_as_int(item.get("size")),
        progress=_as_float(item.get("progress")),
        priority=_as_int(item.get("priority"), 4),
    )


def _peer_from_payload(item: dict[str, Any]) -> TorrentPeer:
    return TorrentPeer(
        ip=str(item.get("ip", "") or ""),
        port=_as_int(item.get("port")),
        client=str(item.get("client", "") or ""),
        dl_speed=_as_int(item.get("dl_speed")),
        ul_speed=_as_int(item.get("ul_speed")),
        progress=_as_float(item.get("progress")),
        downloaded=_as_int(item.get("downloaded")),
        uploaded=_as_int(item.get("uploaded")),
        flags=str(item.get("flags", "") or ""),
        country=str(item.get("country", "") or ""),
    )


def _tracker_from_payload(item: dict[str, Any]) -> TorrentTracker:
    return TorrentTracker(
        url=str(item.get("url", "") or ""),
        status=str(item.get("status", "Not contacted") or "Not contacted"),
        seeds=_as_int(item.get("seeds")),
        peers=_as_int(item.get("peers")),
        message=str(item.get("message", "") or ""),
        proxy=str(item.get("proxy", "") or ""),
    )


def _state_from_name(name: str) -> TorrentState:
    normalized = name.replace("_", " ").lower()
    if "metadata" in normalized or "meta" in normalized:
        return TorrentState.METADATA
    if "check" in normalized:
        return TorrentState.CHECKING
    if "moving" in normalized:
        return TorrentState.MOVING
    if "pause" in normalized:
        return TorrentState.PAUSED
    if "queue" in normalized:
        return TorrentState.QUEUED
    if "seed" in normalized or "upload" in normalized:
        return TorrentState.SEEDING
    if "complete" in normalized:
        return TorrentState.COMPLETED
    if "stall" in normalized:
        return TorrentState.STALLED
    if "error" in normalized:
        return TorrentState.ERROR
    return TorrentState.DOWNLOADING


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int_list(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    return [_as_int(item) for item in value]
