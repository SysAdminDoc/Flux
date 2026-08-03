"""Remote Web UI and qBittorrent-compatible API shim."""

from __future__ import annotations

import base64
import hashlib
import html
import json
import logging
import secrets
import socket
import ssl
import struct
import threading
import time
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, unquote, urlparse
from urllib.request import Request, urlopen

try:
    from flux import __version__ as FLUX_VERSION
except Exception:  # pragma: no cover - import fallback for frozen builds
    FLUX_VERSION = "0.2.0"

logger = logging.getLogger(__name__)

WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_REMOTE_TORRENT_BYTES = 64 * 1024 * 1024
REMOTE_TORRENT_TIMEOUT_SECONDS = 15


@dataclass
class RemoteConfig:
    """Settings for the embedded remote control server."""

    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 8090
    token: str = ""
    username: str = "admin"
    password: str = ""
    tls_certfile: str = ""
    tls_keyfile: str = ""
    tls_ca_file: str = ""
    require_client_cert: bool = False

    @classmethod
    def from_settings(cls, settings: dict[str, Any]) -> "RemoteConfig":
        return cls(
            enabled=bool(settings.get("remote_enabled", False)),
            host=str(settings.get("remote_host", "127.0.0.1") or "127.0.0.1"),
            port=int(settings.get("remote_port", 8090) or 8090),
            token=str(settings.get("remote_token", "") or ""),
            username=str(settings.get("remote_username", "admin") or "admin"),
            password=str(settings.get("remote_password", "") or ""),
            tls_certfile=str(settings.get("remote_tls_certfile", "") or ""),
            tls_keyfile=str(settings.get("remote_tls_keyfile", "") or ""),
            tls_ca_file=str(settings.get("remote_tls_ca_file", "") or ""),
            require_client_cert=bool(settings.get("remote_require_client_cert", False)),
        )

    @property
    def scheme(self) -> str:
        return "https" if self.tls_certfile and self.tls_keyfile else "http"

    @property
    def auth_secret(self) -> str:
        return self.password or self.token

    @property
    def auth_required(self) -> bool:
        return bool(self.token or self.password)


class RemoteControlServer:
    """Background HTTP/WebSocket server for remote Flux control."""

    def __init__(self, config: RemoteConfig, controller: Any):
        self.config = config
        self.controller = controller
        self._httpd: _RemoteHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @classmethod
    def from_settings(cls, settings: dict[str, Any], controller: Any) -> "RemoteControlServer":
        return cls(RemoteConfig.from_settings(settings), controller)

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    @property
    def url(self) -> str:
        if not self._httpd:
            return f"{self.config.scheme}://{self.config.host}:{self.config.port}/"
        host, port = self._httpd.server_address[:2]
        return f"{self.config.scheme}://{host}:{port}/"

    def start(self) -> bool:
        if not self.config.enabled:
            return False
        if self.is_running:
            return True
        if not self.config.auth_required and not _is_loopback(self.config.host):
            raise ValueError("Remote auth is required when binding outside localhost")
        if self.config.require_client_cert and not self.config.tls_ca_file:
            raise ValueError("Remote client certificate auth requires a CA file")

        server = _RemoteHTTPServer((self.config.host, self.config.port), self.config, self.controller)
        if self.config.tls_certfile and self.config.tls_keyfile:
            context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
            context.load_cert_chain(self.config.tls_certfile, self.config.tls_keyfile)
            if self.config.tls_ca_file:
                context.load_verify_locations(self.config.tls_ca_file)
            context.verify_mode = (
                ssl.CERT_REQUIRED if self.config.require_client_cert else ssl.CERT_NONE
            )
            server.socket = context.wrap_socket(server.socket, server_side=True)

        self._httpd = server
        self._thread = threading.Thread(
            target=server.serve_forever,
            name="FluxRemoteWebUI",
            daemon=True,
        )
        self._thread.start()
        logger.info("Remote Web UI listening on %s", self.url)
        return True

    def stop(self):
        if self._httpd:
            self._httpd.stop_event.set()
            self._httpd.shutdown()
            self._httpd.server_close()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        self._httpd = None
        self._thread = None


class _RemoteHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, config: RemoteConfig, controller: Any):
        super().__init__(address, _RemoteRequestHandler)
        self.config = config
        self.controller = controller
        self.sessions: dict[str, float] = {}
        self.stop_event = threading.Event()

    def build_status(self) -> dict[str, Any]:
        return _build_status_payload(self.controller)

    def check_token(self, handler: BaseHTTPRequestHandler) -> bool:
        cfg = self.config
        if not cfg.auth_required:
            return True

        supplied = ""
        auth = handler.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            supplied = auth[7:].strip()
        supplied = supplied or handler.headers.get("X-Flux-Token", "").strip()

        parsed = urlparse(handler.path)
        query = parse_qs(parsed.query)
        supplied = supplied or _first(query, "token")

        cookie_sid = _cookie_value(handler.headers.get("Cookie", ""), "SID")
        if cookie_sid and cookie_sid in self.sessions:
            return True

        return bool(supplied and secrets.compare_digest(supplied, cfg.auth_secret))

    def create_session(self) -> str:
        sid = secrets.token_urlsafe(32)
        self.sessions[sid] = time.time()
        return sid


class _RemoteRequestHandler(BaseHTTPRequestHandler):
    server: _RemoteHTTPServer

    def log_message(self, fmt: str, *args):
        logger.debug("Remote API: " + fmt, *args)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path == "/":
            self._send_html(_index_html(self.server.config))
            return
        if path == "/ws":
            self._handle_websocket()
            return

        if not self._require_auth():
            return

        if path == "/api/v1/status":
            self._send_json(self.server.build_status())
        elif path == "/api/v1/torrents":
            self._send_json(self.server.build_status()["torrents"])
        elif path == "/api/v1/settings":
            self._send_json(_safe_settings(self.server.controller))
        elif path == "/api/v1/version":
            self._send_json({"name": "Flux Torrent", "version": FLUX_VERSION})
        elif path == "/api/v2/app/version":
            self._send_text(f"Flux v{FLUX_VERSION}")
        elif path == "/api/v2/app/webapiVersion":
            self._send_text("2.8.19")
        elif path == "/api/v2/app/preferences":
            self._send_json(_qb_preferences(self.server.controller))
        elif path == "/api/v2/sync/maindata":
            self._send_json(_qb_maindata(self.server.controller))
        elif path == "/api/v2/torrents/info":
            self._send_json(_qb_torrents(self.server.controller))
        elif path.startswith("/api/v1/torrents/") and path.endswith("/details"):
            prefix = "/api/v1/torrents/"
            info_hash = unquote(path[len(prefix):-len("/details")])
            detail = _call(self.server.controller, "get_remote_detail", info_hash)
            self._send_json(_detail_payload(detail, info_hash))
        else:
            self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        params = self._read_params()

        if path == "/api/v2/auth/login":
            self._handle_login(params)
            return

        if not self._require_auth():
            return

        if path == "/api/v1/torrents/add" or path == "/api/v2/torrents/add":
            self._handle_add(params)
        elif path in ("/api/v1/torrents/pause", "/api/v2/torrents/pause"):
            self._handle_hash_command(params, "pause")
        elif path in ("/api/v1/torrents/resume", "/api/v2/torrents/resume"):
            self._handle_hash_command(params, "resume")
        elif path in ("/api/v1/torrents/delete", "/api/v2/torrents/delete"):
            self._handle_hash_command(params, "delete")
        elif path in ("/api/v1/torrents/recheck", "/api/v2/torrents/recheck"):
            self._handle_hash_command(params, "recheck")
        elif path in ("/api/v1/torrents/reannounce", "/api/v2/torrents/reannounce"):
            self._handle_hash_command(params, "reannounce")
        elif path == "/api/v1/torrents/set-sequential":
            self._handle_set_sequential(params)
        elif path == "/api/v1/torrents/set-speed-limit":
            self._handle_set_speed_limit(params)
        elif path == "/api/v1/torrents/queue":
            self._handle_queue_action(params)
        else:
            self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def _handle_login(self, params: dict[str, list[str]]):
        cfg = self.server.config
        username = _first(params, "username")
        password = _first(params, "password")
        secret = cfg.auth_secret
        user_ok = not cfg.username or username == cfg.username
        pass_ok = not secret or secrets.compare_digest(password, secret)
        if user_ok and pass_ok:
            sid = self.server.create_session()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Set-Cookie", f"SID={sid}; HttpOnly; SameSite=Strict; Path=/")
            self.end_headers()
            self.wfile.write(b"Ok.")
            return
        self._send_text("Fails.", HTTPStatus.FORBIDDEN)

    def _handle_add(self, params: dict[str, list[str]]):
        urls = _first(params, "urls") or _first(params, "url") or _first(params, "magnet")
        save_path = _first(params, "savepath") or _first(params, "save_path")
        category = _first(params, "category")
        tags = [t.strip() for t in (_first(params, "tags") or "").replace(",", "|").split("|") if t.strip()]
        paused = _first(params, "paused").lower() in ("true", "1", "yes")
        sequential = _first(params, "sequential").lower() in ("true", "1", "yes")

        encoded = _first(params, "torrent_data")
        if encoded:
            try:
                data = base64.b64decode(encoded, validate=True)
            except (ValueError, TypeError):
                self._send_json({"error": "invalid torrent_data"}, HTTPStatus.BAD_REQUEST)
                return
            if len(data) > MAX_REMOTE_TORRENT_BYTES:
                self._send_json({"error": "torrent file is too large"}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
                return
            added = bool(_call(
                self.server.controller,
                "add_torrent_bytes",
                data,
                save_path,
                category,
                tags,
                paused,
                sequential,
            ))
            self._send_json({"added": 1 if added else 0, "unsupported": 0 if added else 1})
            return

        added = 0
        unsupported = 0
        for item in _split_urls(urls):
            if item.startswith("magnet:"):
                if _call(self.server.controller, "add_magnet", item, save_path, category, tags, paused):
                    added += 1
            elif item.startswith(("http://", "https://")):
                data = _download_torrent(item)
                if data is not None and _call(
                    self.server.controller,
                    "add_torrent_bytes",
                    data,
                    save_path,
                    category,
                    tags,
                    paused,
                    sequential,
                ):
                    added += 1
                else:
                    unsupported += 1
            else:
                if _call(self.server.controller, "add_torrent_url", item, save_path, category, tags, paused):
                    added += 1
                else:
                    unsupported += 1
        status = HTTPStatus.ACCEPTED if unsupported == 0 else HTTPStatus.MULTI_STATUS
        self._send_json({"added": added, "unsupported": unsupported}, status)

    def _handle_hash_command(self, params: dict[str, list[str]], action: str):
        hashes = _first(params, "hashes") or _first(params, "hash")
        delete_files = _first(params, "deleteFiles").lower() in ("true", "1", "yes")
        requested = _hashes_from_request(hashes, self.server.controller)

        if action == "pause" and hashes == "all":
            _call(self.server.controller, "pause_all")
        elif action == "resume" and hashes == "all":
            _call(self.server.controller, "resume_all")
        else:
            for info_hash in requested:
                if action == "pause":
                    _call(self.server.controller, "pause_torrent", info_hash)
                elif action == "resume":
                    _call(self.server.controller, "resume_torrent", info_hash)
                elif action == "delete":
                    _call(self.server.controller, "remove_torrent", info_hash, delete_files)
                elif action == "recheck":
                    _call(self.server.controller, "force_recheck", info_hash)
                elif action == "reannounce":
                    _call(self.server.controller, "force_reannounce", info_hash)
        self._send_json({"ok": True, "count": len(requested)})

    def _handle_set_sequential(self, params: dict[str, list[str]]):
        hashes = _hashes_from_request(_first(params, "hashes") or _first(params, "hash"), self.server.controller)
        enabled = _first(params, "enabled").lower() in ("true", "1", "yes")
        for info_hash in hashes:
            _call(self.server.controller, "set_sequential", info_hash, enabled)
        self._send_json({"ok": True, "count": len(hashes)})

    def _handle_set_speed_limit(self, params: dict[str, list[str]]):
        hashes = _hashes_from_request(_first(params, "hashes") or _first(params, "hash"), self.server.controller)
        download = int(_first(params, "download", "0") or 0)
        upload = int(_first(params, "upload", "0") or 0)
        for info_hash in hashes:
            _call(self.server.controller, "set_torrent_speed_limit", info_hash, download, upload)
        self._send_json({"ok": True, "count": len(hashes)})

    def _handle_queue_action(self, params: dict[str, list[str]]):
        hashes = _hashes_from_request(_first(params, "hashes") or _first(params, "hash"), self.server.controller)
        action = _first(params, "action")
        if action not in {"top", "up", "down", "bottom"}:
            self._send_json({"error": "invalid queue action"}, HTTPStatus.BAD_REQUEST)
            return
        for info_hash in hashes:
            _call(self.server.controller, "queue_torrent", info_hash, action)
        self._send_json({"ok": True, "count": len(hashes)})

    def _handle_websocket(self):
        if self.headers.get("Upgrade", "").lower() != "websocket":
            self._send_json({"error": "websocket upgrade required"}, HTTPStatus.BAD_REQUEST)
            return
        if not self._require_auth():
            return

        key = self.headers.get("Sec-WebSocket-Key", "")
        if not key:
            self._send_json({"error": "missing websocket key"}, HTTPStatus.BAD_REQUEST)
            return

        accept = base64.b64encode(
            hashlib.sha1((key + WEBSOCKET_GUID).encode("ascii")).digest()
        ).decode("ascii")
        response = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
        )
        self.connection.sendall(response.encode("ascii"))
        self.connection.settimeout(2)

        while not self.server.stop_event.is_set():
            try:
                _send_ws_text(self.connection, json.dumps(self.server.build_status()))
                time.sleep(1)
            except (OSError, socket.timeout):
                break

    def _read_params(self) -> dict[str, list[str]]:
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            return params

        raw = self.rfile.read(length)
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type == "application/json":
            try:
                data = json.loads(raw.decode("utf-8"))
                for key, value in data.items():
                    params[key] = value if isinstance(value, list) else [str(value)]
            except json.JSONDecodeError:
                pass
        else:
            form = parse_qs(raw.decode("utf-8", errors="replace"))
            params.update(form)
        return params

    def _require_auth(self) -> bool:
        if self.server.check_token(self):
            return True
        self._send_json({"error": "unauthorized"}, HTTPStatus.UNAUTHORIZED)
        return False

    def _send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK):
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, text: str, status: HTTPStatus = HTTPStatus.OK):
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, text: str):
        body = text.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _build_status_payload(controller: Any) -> dict[str, Any]:
    stats = _call(controller, "get_remote_stats") or _call(controller, "get_stats")
    torrents = list(getattr(stats, "torrents", []) or [])
    return {
        "app": {"name": "Flux Torrent", "version": FLUX_VERSION},
        "session": {
            "download_rate": int(getattr(stats, "download_rate", 0) or 0),
            "upload_rate": int(getattr(stats, "upload_rate", 0) or 0),
            "dht_nodes": int(getattr(stats, "dht_nodes", 0) or 0),
            "torrent_count": int(getattr(stats, "torrent_count", len(torrents)) or len(torrents)),
            "download_history": list(getattr(stats, "dl_history", []) or []),
            "upload_history": list(getattr(stats, "ul_history", []) or []),
        },
        "torrents": [_torrent_payload(snap) for snap in torrents],
        "updated_at": time.time(),
    }


def _torrent_payload(snap: Any) -> dict[str, Any]:
    state = _state_name(getattr(snap, "state", "Unknown"))
    return {
        "name": str(getattr(snap, "name", "") or ""),
        "info_hash": str(getattr(snap, "info_hash", "") or ""),
        "state": state,
        "progress": float(getattr(snap, "progress", 0.0) or 0.0),
        "total_size": int(getattr(snap, "total_size", 0) or 0),
        "completed_size": int(getattr(snap, "completed_size", 0) or 0),
        "downloaded": int(getattr(snap, "total_downloaded", 0) or 0),
        "uploaded": int(getattr(snap, "total_uploaded", 0) or 0),
        "download_speed": int(getattr(snap, "download_speed", 0) or 0),
        "upload_speed": int(getattr(snap, "upload_speed", 0) or 0),
        "seeds": int(getattr(snap, "num_seeds", 0) or 0),
        "peers": int(getattr(snap, "num_peers", 0) or 0),
        "ratio": float(getattr(snap, "ratio", 0.0) or 0.0),
        "eta": int(getattr(snap, "eta", 0) or 0),
        "category": str(getattr(snap, "category", "") or ""),
        "tags": list(getattr(snap, "tags", []) or []),
        "save_path": str(getattr(snap, "save_path", "") or ""),
        "added_time": float(getattr(snap, "added_time", 0.0) or 0.0),
        "download_limit": int(getattr(snap, "download_limit", 0) or 0),
        "upload_limit": int(getattr(snap, "upload_limit", 0) or 0),
        "error": str(getattr(snap, "error", "") or ""),
    }


def _detail_payload(detail: Any, info_hash: str) -> dict[str, Any]:
    """Serialize a DetailData-like object without exposing Qt/libtorrent state."""
    return {
        "info_hash": str(getattr(detail, "info_hash", info_hash) or info_hash),
        "files": [_object_payload(item) for item in getattr(detail, "files", []) or []],
        "peers": [_object_payload(item) for item in getattr(detail, "peers", []) or []],
        "trackers": [_object_payload(item) for item in getattr(detail, "trackers", []) or []],
        "pieces": list(getattr(detail, "pieces", []) or []),
        "piece_length": int(getattr(detail, "piece_length", 0) or 0),
        "download_history": list(getattr(detail, "dl_history", []) or []),
        "upload_history": list(getattr(detail, "ul_history", []) or []),
    }


def _object_payload(value: Any) -> dict[str, Any]:
    if hasattr(value, "__dataclass_fields__"):
        return {name: getattr(value, name) for name in value.__dataclass_fields__}
    if isinstance(value, dict):
        return dict(value)
    return {}


def _qb_maindata(controller: Any) -> dict[str, Any]:
    status = _build_status_payload(controller)
    torrents = {
        item["info_hash"]: _qb_torrent_payload(item)
        for item in status["torrents"]
        if item["info_hash"]
    }
    return {
        "rid": int(status["updated_at"]),
        "full_update": True,
        "torrents": torrents,
        "server_state": {
            "dl_info_speed": status["session"]["download_rate"],
            "up_info_speed": status["session"]["upload_rate"],
            "alltime_dl": sum(t["downloaded"] for t in status["torrents"]),
            "alltime_ul": sum(t["uploaded"] for t in status["torrents"]),
            "connection_status": "connected",
        },
    }


def _qb_torrents(controller: Any) -> list[dict[str, Any]]:
    return [_qb_torrent_payload(item) for item in _build_status_payload(controller)["torrents"]]


def _qb_torrent_payload(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "hash": item["info_hash"],
        "name": item["name"],
        "size": item["total_size"],
        "progress": item["progress"],
        "state": _qb_state(item["state"]),
        "dlspeed": item["download_speed"],
        "upspeed": item["upload_speed"],
        "num_seeds": item["seeds"],
        "num_leechs": item["peers"],
        "ratio": item["ratio"],
        "eta": item["eta"],
        "category": item["category"],
        "tags": ",".join(item["tags"]),
        "save_path": item["save_path"],
        "added_on": int(item["added_time"]),
        "completion_on": 0,
        "tracker": "",
    }


def _qb_preferences(controller: Any) -> dict[str, Any]:
    settings = _safe_settings(controller)
    return {
        "web_ui_domain_list": "*",
        "web_ui_port": int(settings.get("remote_port", 8090) or 8090),
        "web_ui_address": settings.get("remote_host", "127.0.0.1"),
        "max_connec": int(settings.get("max_connections", 500) or 500),
        "dl_limit": int(settings.get("max_download_speed", 0) or 0),
        "up_limit": int(settings.get("max_upload_speed", 0) or 0),
        "save_path": settings.get("default_save_path", ""),
        "dht": bool(settings.get("dht_enabled", True)),
        "pex": bool(settings.get("pex_enabled", True)),
        "lsd": bool(settings.get("lsd_enabled", True)),
    }


def _safe_settings(controller: Any) -> dict[str, Any]:
    settings = _call(controller, "get_remote_settings") or _call(controller, "get_settings") or {}
    safe = dict(settings)
    for key in (
        "proxy_pass",
        "remote_token",
        "remote_password",
        "remote_client_token",
        "remote_client_password",
    ):
        if key in safe and safe[key]:
            safe[key] = "********"
    return safe


def _hashes_from_request(raw: str, controller: Any) -> list[str]:
    if raw == "all":
        status = _build_status_payload(controller)
        return [item["info_hash"] for item in status["torrents"] if item["info_hash"]]
    return [h.strip() for h in raw.replace(",", "|").split("|") if h.strip()]


def _split_urls(raw: str) -> list[str]:
    return [line.strip() for line in raw.replace("\r", "\n").split("\n") if line.strip()]


def _download_torrent(url: str) -> bytes | None:
    """Download a qB-compatible torrent URL with a bounded response size."""
    try:
        request = Request(url, headers={"User-Agent": f"FluxTorrent/{FLUX_VERSION}"})
        with urlopen(request, timeout=REMOTE_TORRENT_TIMEOUT_SECONDS) as response:
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_REMOTE_TORRENT_BYTES:
                    logger.warning("Remote torrent URL exceeded size limit: %s", url)
                    return None
                chunks.append(chunk)
            return b"".join(chunks)
    except (HTTPError, URLError, OSError, ValueError) as exc:
        logger.warning("Remote torrent URL failed: %s: %s", url, exc)
        return None


def _state_name(state: Any) -> str:
    if hasattr(state, "display_name"):
        return str(state.display_name)
    if hasattr(state, "name"):
        return str(state.name).title()
    return str(state)


def _qb_state(state: str) -> str:
    name = state.lower()
    if "seed" in name:
        return "uploading"
    if "pause" in name:
        return "pausedDL"
    if "queue" in name:
        return "queuedDL"
    if "check" in name:
        return "checkingDL"
    if "error" in name:
        return "error"
    if "metadata" in name:
        return "metaDL"
    if "complete" in name:
        return "stalledUP"
    if "stall" in name:
        return "stalledDL"
    return "downloading"


def _call(obj: Any, name: str, *args) -> Any:
    func = getattr(obj, name, None)
    if not callable(func):
        return None
    try:
        return func(*args)
    except Exception as exc:
        logger.warning("Remote controller call failed: %s: %s", name, exc)
        return None


def _first(params: dict[str, list[str]], key: str, default: str = "") -> str:
    value = params.get(key, [default])
    if not value:
        return default
    return str(value[0])


def _cookie_value(cookie: str, key: str) -> str:
    for part in cookie.split(";"):
        if "=" not in part:
            continue
        name, value = part.strip().split("=", 1)
        if name == key:
            return value
    return ""


def _send_ws_text(conn: socket.socket, text: str):
    payload = text.encode("utf-8")
    length = len(payload)
    header = bytearray([0x81])
    if length < 126:
        header.append(length)
    elif length < 65536:
        header.append(126)
        header.extend(struct.pack("!H", length))
    else:
        header.append(127)
        header.extend(struct.pack("!Q", length))
    conn.sendall(bytes(header) + payload)


def _is_loopback(host: str) -> bool:
    return host in ("", "localhost", "127.0.0.1", "::1")


def _index_html(config: RemoteConfig) -> str:
    host = html.escape(config.host)
    port = int(config.port)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Flux Remote</title>
<style>
:root{{color-scheme:dark;--bg:#11111b;--panel:#181825;--panel2:#1e1e2e;--text:#cdd6f4;--muted:#a6adc8;--accent:#89b4fa;--green:#a6e3a1;--red:#f38ba8;--border:#313244}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.4 "Segoe UI",system-ui,sans-serif}}header{{height:56px;display:flex;align-items:center;gap:16px;padding:0 20px;background:var(--panel);border-bottom:1px solid var(--border)}}h1{{font-size:18px;margin:0;color:var(--accent)}}main{{padding:18px;display:grid;gap:16px}}.stats{{display:grid;grid-template-columns:repeat(4,minmax(120px,1fr));gap:12px}}.stat{{background:var(--panel2);border:1px solid var(--border);border-radius:8px;padding:12px}}.stat b{{display:block;font-size:22px;color:var(--green)}}.stat span{{color:var(--muted);font-size:12px}}.toolbar{{display:flex;gap:8px;align-items:center}}input,button{{background:var(--panel2);color:var(--text);border:1px solid var(--border);border-radius:6px;padding:8px 10px}}button{{cursor:pointer}}button:hover{{border-color:var(--accent)}}table{{width:100%;border-collapse:collapse;background:var(--panel2);border:1px solid var(--border);border-radius:8px;overflow:hidden}}th,td{{padding:9px 10px;border-bottom:1px solid var(--border);text-align:left;white-space:nowrap}}th{{color:var(--muted);font-weight:600}}td.name{{white-space:normal}}.empty{{color:var(--muted);padding:18px;background:var(--panel2);border:1px solid var(--border);border-radius:8px}}.status{{margin-left:auto;color:var(--muted)}}@media(max-width:760px){{.stats{{grid-template-columns:repeat(2,1fr)}}th:nth-child(3),td:nth-child(3),th:nth-child(4),td:nth-child(4){{display:none}}}}
</style>
</head>
<body>
<header><h1>Flux Remote</h1><div class="status" id="status">Connecting to {host}:{port}</div></header>
<main>
<section class="toolbar">
<input id="token" type="password" placeholder="Token" autocomplete="current-password">
<input id="magnet" type="text" placeholder="Magnet URI">
<button id="add">Add</button>
<button id="pause">Pause All</button>
<button id="resume">Resume All</button>
</section>
<section class="stats">
<div class="stat"><b id="down">0 B/s</b><span>Download</span></div>
<div class="stat"><b id="up">0 B/s</b><span>Upload</span></div>
<div class="stat"><b id="count">0</b><span>Torrents</span></div>
<div class="stat"><b id="dht">0</b><span>DHT Nodes</span></div>
</section>
<section id="table"></section>
</main>
<script>
const $=id=>document.getElementById(id);
const saved=localStorage.getItem("flux_token")||new URLSearchParams(location.search).get("token")||"";
$("token").value=saved;
function token(){{const v=$("token").value.trim();localStorage.setItem("flux_token",v);return v}}
function fmt(n){{if(!n)return"0 B/s";const u=["B/s","KiB/s","MiB/s","GiB/s"];let i=0;while(n>=1024&&i<u.length-1){{n/=1024;i++}}return `${{n.toFixed(n>=10||i==0?0:1)}} ${{u[i]}}`}}
function authUrl(path){{const t=encodeURIComponent(token());return `${{path}}${{path.includes("?")?"&":"?"}}token=${{t}}`}}
function render(data){{$("status").textContent=`Flux v${{data.app.version}} - updated ${{new Date().toLocaleTimeString()}}`;$("down").textContent=fmt(data.session.download_rate);$("up").textContent=fmt(data.session.upload_rate);$("count").textContent=data.session.torrent_count;$("dht").textContent=data.session.dht_nodes;const rows=data.torrents.map(t=>`<tr><td class="name">${{t.name}}</td><td>${{t.state}}</td><td>${{(t.progress*100).toFixed(1)}}%</td><td>${{fmt(t.download_speed)}}</td><td>${{fmt(t.upload_speed)}}</td><td>${{t.ratio.toFixed(2)}}</td></tr>`).join("");$("table").innerHTML=rows?`<table><thead><tr><th>Name</th><th>State</th><th>Progress</th><th>Down</th><th>Up</th><th>Ratio</th></tr></thead><tbody>${{rows}}</tbody></table>`:`<div class="empty">No torrents</div>`}}
async function fetchStatus(){{const r=await fetch(authUrl("/api/v1/status"));if(r.ok)render(await r.json());else $("status").textContent=`HTTP ${{r.status}}`}}
function connect(){{try{{const ws=new WebSocket(`${{location.protocol==="https:"?"wss":"ws"}}://${{location.host}}/ws?token=${{encodeURIComponent(token())}}`);ws.onmessage=e=>render(JSON.parse(e.data));ws.onclose=()=>setTimeout(connect,2000);ws.onerror=()=>ws.close()}}catch(e){{setInterval(fetchStatus,2000)}}}}
$("add").onclick=async()=>{{const magnet=$("magnet").value.trim();if(!magnet)return;await fetch(authUrl("/api/v1/torrents/add"),{{method:"POST",headers:{{"Content-Type":"application/json"}},body:JSON.stringify({{magnet}})}});$("magnet").value=""}};
$("pause").onclick=()=>fetch(authUrl("/api/v1/torrents/pause"),{{method:"POST",headers:{{"Content-Type":"application/json"}},body:JSON.stringify({{hashes:"all"}})}});
$("resume").onclick=()=>fetch(authUrl("/api/v1/torrents/resume"),{{method:"POST",headers:{{"Content-Type":"application/json"}},body:JSON.stringify({{hashes:"all"}})}});
connect();fetchStatus();
</script>
</body>
</html>"""
