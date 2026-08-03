"""Per-tracker HTTP(S) announce routing.

libtorrent exposes proxy settings on the session, but not on individual
announce entries.  Flux therefore removes configured HTTP(S) trackers from
libtorrent's direct announce list and performs their announces here.  Peers
returned by the proxy-routed announce are fed back into the matching torrent
handle.  UDP trackers remain in libtorrent's normal list because this layer
does not pretend that an HTTP proxy can carry UDP traffic.
"""

from __future__ import annotations

import base64
import gzip
import http.client
import ipaddress
import logging
import socket
import ssl
import struct
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Iterable
from urllib.parse import quote_from_bytes, unquote, urlsplit, urlunsplit

import libtorrent as lt

logger = logging.getLogger(__name__)

DEFAULT_TRACKER_PROXY_TIMEOUT = 10.0
DEFAULT_TRACKER_ANNOUNCE_INTERVAL = 900
MAX_TRACKER_RESPONSE_BYTES = 4 * 1024 * 1024
SUPPORTED_PROXY_SCHEMES = frozenset(("socks5", "socks5h", "http", "https"))
SUPPORTED_TRACKER_SCHEMES = frozenset(("http", "https"))


@dataclass(frozen=True)
class TrackerProxy:
    """Validated proxy endpoint used for one or more tracker rules."""

    scheme: str
    host: str
    port: int
    username: str = ""
    password: str = ""

    def display_name(self) -> str:
        """Return a safe label that never includes credentials."""
        return f"{self.scheme.upper()} {self.host}:{self.port}"

    def as_url(self, include_password: bool = True) -> str:
        """Serialize the endpoint as a proxy URL."""
        userinfo = ""
        if self.username:
            userinfo = quote_from_bytes(self.username.encode("utf-8"), safe="")
            if include_password and self.password:
                userinfo += ":" + quote_from_bytes(
                    self.password.encode("utf-8"), safe=""
                )
            userinfo += "@"
        return urlunsplit((self.scheme, f"{userinfo}{self.host}:{self.port}", "", "", ""))


@dataclass(frozen=True)
class TrackerProxyRule:
    """Exact tracker URL to proxy mapping."""

    tracker_url: str
    proxy: TrackerProxy

    def matches(self, tracker_url: str) -> bool:
        return normalize_tracker_url(tracker_url) == self.tracker_url


@dataclass(frozen=True)
class TrackerAnnounceRequest:
    """Immutable announce values safe to pass to a network worker."""

    info_hash: bytes
    peer_id: bytes
    port: int
    uploaded: int
    downloaded: int
    left: int
    event: str = ""


@dataclass(frozen=True)
class TrackerAnnounceResult:
    """Decoded tracker result, including a user-facing error when present."""

    peers: tuple[tuple[str, int], ...] = ()
    interval: int = DEFAULT_TRACKER_ANNOUNCE_INTERVAL
    seeds: int = 0
    peers_available: int = 0
    warning: str = ""
    failure: str = ""

    @property
    def ok(self) -> bool:
        return not self.failure


@dataclass
class _ManagedTracker:
    info_hash: str
    url: str
    tier: int
    proxy: TrackerProxy
    status: str = "Queued"
    seeds: int = 0
    peers: int = 0
    message: str = ""
    interval: int = DEFAULT_TRACKER_ANNOUNCE_INTERVAL
    next_announce: float = 0.0
    pending: bool = False
    started: bool = True


def normalize_tracker_url(value: str) -> str:
    """Normalize a tracker URL without changing its query parameters."""
    raw = str(value or "").strip()
    parsed = urlsplit(raw)
    if not parsed.scheme or not parsed.hostname:
        return raw
    try:
        port = parsed.port
    except ValueError:
        return raw
    host = parsed.hostname.lower()
    netloc = host
    if ":" in host and not host.startswith("["):
        netloc = f"[{host}]"
    if parsed.username:
        userinfo = quote_from_bytes(unquote(parsed.username).encode("utf-8"), safe="")
        if parsed.password is not None:
            userinfo += ":" + quote_from_bytes(
                unquote(parsed.password).encode("utf-8"), safe=""
            )
        netloc = f"{userinfo}@{netloc}"
    if port is not None and not (
        (parsed.scheme.lower() == "http" and port == 80)
        or (parsed.scheme.lower() == "https" and port == 443)
    ):
        netloc += f":{port}"
    path = parsed.path or "/announce"
    return urlunsplit((parsed.scheme.lower(), netloc, path.rstrip("/") or "/", parsed.query, ""))


def parse_tracker_proxy(value: Any) -> TrackerProxy | None:
    """Parse and validate a SOCKS5 or HTTP(S) proxy URL."""
    raw = str(value or "").strip()
    if not raw:
        return None
    parsed = urlsplit(raw)
    scheme = parsed.scheme.lower()
    if scheme not in SUPPORTED_PROXY_SCHEMES or parsed.path not in ("", "/") \
            or parsed.query or parsed.fragment:
        return None
    if not parsed.hostname:
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    if port is None:
        port = 1080 if scheme in ("socks5", "socks5h") else 8080
    if not 1 <= port <= 65535:
        return None
    return TrackerProxy(
        scheme="socks5" if scheme == "socks5h" else scheme,
        host=parsed.hostname,
        port=port,
        username=unquote(parsed.username or ""),
        password=unquote(parsed.password or ""),
    )


def is_proxyable_tracker(value: str) -> bool:
    """Return whether the tracker can be announced by this HTTP layer."""
    return urlsplit(str(value or "").strip()).scheme.lower() in SUPPORTED_TRACKER_SCHEMES


def parse_tracker_proxy_rules(raw: Any) -> tuple[TrackerProxyRule, ...]:
    """Parse settings into deduplicated, validated per-tracker rules.

    Accepted forms are a mapping of tracker URL to proxy URL, a list of
    ``{"tracker_url": ..., "proxy_url": ...}`` dictionaries, or text lines
    written as ``tracker URL | proxy URL``.
    """
    candidates: Iterable[tuple[Any, Any]]
    if isinstance(raw, dict):
        candidates = raw.items()
    elif isinstance(raw, str):
        lines = []
        for line in raw.replace("\r", "").split("\n"):
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            tracker, separator, proxy = line.partition("|")
            if separator:
                lines.append((tracker, proxy))
        candidates = lines
    elif isinstance(raw, (list, tuple)):
        rows = []
        for item in raw:
            if isinstance(item, dict):
                rows.append((item.get("tracker_url", item.get("tracker")),
                             item.get("proxy_url", item.get("proxy"))))
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                rows.append((item[0], item[1]))
            elif isinstance(item, str):
                tracker, separator, proxy = item.partition("|")
                if separator:
                    rows.append((tracker, proxy))
        candidates = rows
    else:
        candidates = ()

    parsed: dict[str, TrackerProxyRule] = {}
    for tracker_value, proxy_value in candidates:
        tracker = normalize_tracker_url(str(tracker_value or ""))
        proxy = parse_tracker_proxy(proxy_value)
        if not tracker or not is_proxyable_tracker(tracker) or proxy is None:
            continue
        parsed[tracker] = TrackerProxyRule(tracker, proxy)
    return tuple(parsed.values())


def tracker_proxy_rules_to_settings(raw: Any) -> list[dict[str, str]]:
    """Return validated rules in the persistent settings shape."""
    return [
        {"tracker_url": rule.tracker_url, "proxy_url": rule.proxy.as_url()}
        for rule in parse_tracker_proxy_rules(raw)
    ]


def redact_tracker_proxy_rules(raw: Any) -> list[dict[str, str]]:
    """Return settings-safe rules with proxy passwords removed."""
    return [
        {"tracker_url": rule.tracker_url,
         "proxy_url": rule.proxy.as_url(include_password=False)}
        for rule in parse_tracker_proxy_rules(raw)
    ]


def build_announce_url(tracker_url: str, request: TrackerAnnounceRequest) -> str:
    """Build a tracker GET URL while preserving binary info-hash fields."""
    parsed = urlsplit(tracker_url)
    query = [
        f"info_hash={quote_from_bytes(request.info_hash, safe='')}",
        f"peer_id={quote_from_bytes(request.peer_id, safe='')}",
        f"port={int(request.port)}",
        f"uploaded={max(0, int(request.uploaded))}",
        f"downloaded={max(0, int(request.downloaded))}",
        f"left={max(0, int(request.left))}",
        "compact=1",
        "numwant=50",
    ]
    if request.event:
        query.append(f"event={quote_from_bytes(request.event.encode('ascii'), safe='')}" )
    existing = f"{parsed.query}&" if parsed.query else ""
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/announce",
                       existing + "&".join(query), ""))


class TrackerAnnounceClient:
    """Synchronous HTTP(S) tracker client used by the worker executor."""

    def __init__(self, timeout: float = DEFAULT_TRACKER_PROXY_TIMEOUT):
        self.timeout = max(1.0, float(timeout))

    def announce(
        self,
        tracker_url: str,
        proxy: TrackerProxy,
        request: TrackerAnnounceRequest,
    ) -> TrackerAnnounceResult:
        if not is_proxyable_tracker(tracker_url):
            return TrackerAnnounceResult(failure="Only HTTP(S) trackers support proxy routing")
        try:
            url = build_announce_url(tracker_url, request)
            return self._request(url, proxy)
        except Exception as exc:  # network errors are tracker status, not worker errors
            logger.debug("Proxy tracker announce failed for %s: %s", tracker_url, exc)
            return TrackerAnnounceResult(failure=f"{type(exc).__name__}: {exc}")

    def _request(self, url: str, proxy: TrackerProxy) -> TrackerAnnounceResult:
        target = urlsplit(url)
        if not target.hostname:
            return TrackerAnnounceResult(failure="Tracker URL has no hostname")
        target_port = target.port or (443 if target.scheme == "https" else 80)
        sock = self._open_tunnel(target.hostname, target_port, target.scheme, proxy)
        try:
            path = target.path or "/"
            if target.query:
                path += "?" + target.query
            host_header = target.hostname
            if target_port not in (80, 443):
                host_header += f":{target_port}"
            sock.sendall((
                f"GET {path} HTTP/1.1\r\n"
                f"Host: {host_header}\r\n"
                "User-Agent: FluxTorrent/1.0\r\n"
                "Accept: */*\r\n"
                "Connection: close\r\n\r\n"
            ).encode("ascii"))
            response = http.client.HTTPResponse(sock)
            response.begin()
            body = response.read(MAX_TRACKER_RESPONSE_BYTES + 1)
            if len(body) > MAX_TRACKER_RESPONSE_BYTES:
                return TrackerAnnounceResult(failure="Tracker response exceeded 4 MiB limit")
            if response.getheader("Content-Encoding", "").lower() == "gzip":
                body = gzip.decompress(body)
                if len(body) > MAX_TRACKER_RESPONSE_BYTES:
                    return TrackerAnnounceResult(failure="Tracker response exceeded 4 MiB limit")
            if response.status < 200 or response.status >= 300:
                return TrackerAnnounceResult(failure=f"HTTP {response.status}")
            return self.decode_response(body)
        finally:
            try:
                sock.close()
            except OSError:
                pass

    def _open_tunnel(
        self,
        target_host: str,
        target_port: int,
        target_scheme: str,
        proxy: TrackerProxy,
    ) -> socket.socket:
        if proxy.scheme == "socks5":
            sock = socket.create_connection((proxy.host, proxy.port), self.timeout)
            try:
                self._socks5_connect(sock, target_host, target_port, proxy)
            except Exception:
                sock.close()
                raise
        else:
            sock = socket.create_connection((proxy.host, proxy.port), self.timeout)
            try:
                if proxy.scheme == "https":
                    context = ssl.create_default_context()
                    sock = context.wrap_socket(sock, server_hostname=proxy.host)
                self._http_connect(sock, target_host, target_port, proxy)
            except Exception:
                sock.close()
                raise

        if target_scheme == "https":
            context = ssl.create_default_context()
            sock = context.wrap_socket(sock, server_hostname=target_host)
        return sock

    def _socks5_connect(
        self,
        sock: socket.socket,
        target_host: str,
        target_port: int,
        proxy: TrackerProxy,
    ) -> None:
        methods = b"\x00" if not proxy.username else b"\x00\x02"
        sock.sendall(b"\x05" + bytes((len(methods),)) + methods)
        version, method = self._read_exact(sock, 2)
        if version != 5 or method == 0xFF:
            raise OSError("SOCKS5 proxy rejected authentication methods")
        if method == 2:
            user = proxy.username.encode("utf-8")
            password = proxy.password.encode("utf-8")
            if len(user) > 255 or len(password) > 255:
                raise ValueError("SOCKS5 credentials are too long")
            sock.sendall(b"\x01" + bytes((len(user),)) + user + bytes((len(password),)) + password)
            auth_version, auth_status = self._read_exact(sock, 2)
            if auth_version != 1 or auth_status != 0:
                raise OSError("SOCKS5 proxy authentication failed")
        elif method != 0:
            raise OSError(f"Unsupported SOCKS5 authentication method: {method}")

        host_bytes = target_host.encode("idna")
        if len(host_bytes) > 255:
            raise ValueError("Tracker hostname is too long")
        request = b"\x05\x01\x00\x03" + bytes((len(host_bytes),)) + host_bytes
        request += struct.pack("!H", target_port)
        sock.sendall(request)
        version, status, _reserved, address_type = self._read_exact(sock, 4)
        if version != 5 or status != 0:
            raise OSError(f"SOCKS5 CONNECT failed with status {status}")
        if address_type == 1:
            self._read_exact(sock, 4)
        elif address_type == 3:
            length = self._read_exact(sock, 1)[0]
            self._read_exact(sock, length)
        elif address_type == 4:
            self._read_exact(sock, 16)
        else:
            raise OSError(f"SOCKS5 proxy returned unknown address type {address_type}")
        self._read_exact(sock, 2)

    def _http_connect(
        self,
        sock: socket.socket,
        target_host: str,
        target_port: int,
        proxy: TrackerProxy,
    ) -> None:
        headers = [
            f"CONNECT {target_host}:{target_port} HTTP/1.1",
            f"Host: {target_host}:{target_port}",
            "Proxy-Connection: Keep-Alive",
        ]
        if proxy.username:
            credentials = f"{proxy.username}:{proxy.password}".encode("utf-8")
            headers.append("Proxy-Authorization: Basic " + base64.b64encode(credentials).decode("ascii"))
        sock.sendall(("\r\n".join(headers) + "\r\n\r\n").encode("ascii"))
        response = bytearray()
        while b"\r\n\r\n" not in response:
            chunk = sock.recv(4096)
            if not chunk:
                raise OSError("Proxy closed CONNECT response")
            response.extend(chunk)
            if len(response) > 64 * 1024:
                raise OSError("Proxy CONNECT response is too large")
        first_line = bytes(response).split(b"\r\n", 1)[0].decode("iso-8859-1")
        parts = first_line.split(" ", 2)
        if len(parts) < 2 or not parts[1].isdigit() or int(parts[1]) != 200:
            raise OSError(f"Proxy CONNECT failed: {first_line}")

    @staticmethod
    def _read_exact(sock: socket.socket, size: int) -> bytes:
        chunks = bytearray()
        while len(chunks) < size:
            chunk = sock.recv(size - len(chunks))
            if not chunk:
                raise OSError("Proxy closed the connection")
            chunks.extend(chunk)
        return bytes(chunks)

    @staticmethod
    def decode_response(body: bytes) -> TrackerAnnounceResult:
        """Decode compact or dictionary-form tracker peers."""
        try:
            payload = lt.bdecode(body)
        except Exception as exc:
            return TrackerAnnounceResult(failure=f"Invalid bencode: {exc}")
        if not isinstance(payload, dict):
            return TrackerAnnounceResult(failure="Tracker response is not a dictionary")

        def value(name: str, default: Any = None) -> Any:
            return payload.get(name.encode("ascii"), payload.get(name, default))

        failure = value("failure reason", b"")
        if isinstance(failure, bytes):
            failure = failure.decode("utf-8", "replace")
        if failure:
            return TrackerAnnounceResult(failure=str(failure))
        warning = value("warning message", b"")
        if isinstance(warning, bytes):
            warning = warning.decode("utf-8", "replace")

        peers: list[tuple[str, int]] = []
        compact = value("peers", b"")
        if isinstance(compact, bytes):
            for offset in range(0, len(compact) - 5, 6):
                try:
                    host = str(ipaddress.ip_address(compact[offset:offset + 4]))
                    port = struct.unpack("!H", compact[offset + 4:offset + 6])[0]
                    if port:
                        peers.append((host, port))
                except ValueError:
                    break
        elif isinstance(compact, list):
            peers.extend(_dictionary_peers(compact))

        compact6 = value("peers6", b"")
        if isinstance(compact6, bytes):
            for offset in range(0, len(compact6) - 17, 18):
                try:
                    host = str(ipaddress.ip_address(compact6[offset:offset + 16]))
                    port = struct.unpack("!H", compact6[offset + 16:offset + 18])[0]
                    if port:
                        peers.append((host, port))
                except ValueError:
                    break
        interval = _positive_int(value("interval"), DEFAULT_TRACKER_ANNOUNCE_INTERVAL)
        seeds = _positive_int(value("complete"), 0)
        peers_available = _positive_int(value("incomplete"), 0)
        return TrackerAnnounceResult(
            peers=tuple(dict.fromkeys(peers)), interval=interval, seeds=seeds,
            peers_available=peers_available, warning=str(warning or ""),
        )


def _dictionary_peers(values: list[Any]) -> list[tuple[str, int]]:
    result = []
    for item in values:
        if not isinstance(item, dict):
            continue
        host = item.get(b"ip", item.get("ip", ""))
        port = item.get(b"port", item.get("port", 0))
        if isinstance(host, bytes):
            host = host.decode("utf-8", "replace")
        try:
            port = int(port)
        except (TypeError, ValueError):
            continue
        if host and 1 <= port <= 65535:
            result.append((str(host), port))
    return result


def _positive_int(value: Any, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


class TrackerProxyManager:
    """Own proxy-routed tracker state for one session worker."""

    def __init__(self, timeout: float = DEFAULT_TRACKER_PROXY_TIMEOUT):
        self._rules: tuple[TrackerProxyRule, ...] = ()
        self._managed: dict[tuple[str, str], _ManagedTracker] = {}
        self._futures: dict[Future[TrackerAnnounceResult], tuple[str, str]] = {}
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="FluxTracker")
        self._client = TrackerAnnounceClient(timeout)
        self._peer_id: bytes | None = None

    def configure(self, raw_rules: Any) -> None:
        self._rules = parse_tracker_proxy_rules(raw_rules)

    def close(self) -> None:
        for future in self._futures:
            future.cancel()
        self._futures.clear()
        self._executor.shutdown(wait=True, cancel_futures=True)

    def forget_torrent(self, info_hash: str) -> None:
        for key in [key for key in self._managed if key[0] == info_hash]:
            del self._managed[key]
        for future, key in list(self._futures.items()):
            if key[0] == info_hash:
                future.cancel()
                del self._futures[future]

    def forget_tracker(self, info_hash: str, tracker_url: str) -> None:
        self._managed.pop((info_hash, normalize_tracker_url(tracker_url)), None)

    def sync_torrent(self, torrent: Any) -> None:
        """Move newly configured HTTP(S) trackers out of direct announces."""
        info_hash = str(getattr(torrent, "info_hash", "") or "")
        if not info_hash or not torrent.is_valid:
            return
        try:
            current = list(torrent.handle.trackers())
        except Exception:
            return

        keep = []
        managed_now = set()
        current_urls = set()
        for entry in current:
            url = normalize_tracker_url(getattr(entry, "url", ""))
            current_urls.add(url)
            rule = self._rule_for(url)
            if rule is None or not is_proxyable_tracker(url):
                keep.append(entry)
                continue
            key = (info_hash, url)
            self._managed.setdefault(
                key,
                _ManagedTracker(
                    info_hash=info_hash, url=url,
                    tier=int(getattr(entry, "tier", 0) or 0), proxy=rule.proxy,
                ),
            )
            self._managed[key].proxy = rule.proxy
            managed_now.add(key)

        if len(keep) != len(current):
            try:
                torrent.handle.replace_trackers(keep)
            except Exception as exc:
                logger.warning("Could not detach proxied trackers for %s: %s", info_hash, exc)
                for key in managed_now:
                    self._managed.pop(key, None)
                return

        # Reattach a tracker if its rule was removed or invalidated.
        for key, managed in list(self._managed.items()):
            if key[0] != info_hash:
                continue
            rule = self._rule_for(key[1])
            if rule is not None and is_proxyable_tracker(key[1]):
                managed.proxy = rule.proxy
                continue
            if key[1] not in current_urls:
                try:
                    torrent.handle.add_tracker({"url": managed.url, "tier": managed.tier})
                except Exception as exc:
                    logger.debug("Could not restore direct tracker %s: %s", managed.url, exc)
            del self._managed[key]

    def tick(self, torrents: Iterable[Any], session: Any, listen_port: int) -> None:
        """Collect completed announces and schedule due announces."""
        self._collect(torrents)
        now = time.monotonic()
        if self._peer_id is None:
            try:
                self._peer_id = session.id().to_bytes()
            except Exception:
                self._peer_id = b"-FX1000-FluxTorrent"

        for torrent in torrents:
            self.sync_torrent(torrent)
            info_hash = str(getattr(torrent, "info_hash", "") or "")
            if not info_hash or not torrent.is_valid:
                continue
            try:
                status = torrent.handle.status()
                if not getattr(status, "has_metadata", False) or getattr(status, "paused", False):
                    continue
                info_hash_bytes = _announce_info_hash_bytes(torrent.handle)
                if not info_hash_bytes:
                    continue
                left = max(0, int(status.total_wanted - status.total_wanted_done))
            except Exception:
                continue

            for key, managed in self._managed.items():
                if key[0] != info_hash or managed.pending or managed.next_announce > now:
                    continue
                managed.pending = True
                managed.status = "Announcing"
                request = TrackerAnnounceRequest(
                    info_hash=info_hash_bytes, peer_id=self._peer_id,
                    port=max(0, int(listen_port)),
                    uploaded=max(0, int(status.all_time_upload)),
                    downloaded=max(0, int(status.all_time_download)),
                    left=left,
                    event="started" if managed.started else "",
                )
                managed.started = False
                future = self._executor.submit(
                    self._client.announce, managed.url, managed.proxy, request
                )
                self._futures[future] = key

    def tracker_snapshots(self, info_hash: str) -> list[Any]:
        """Return serializable tracker rows for the detail panel."""
        from flux.core.torrent import TorrentTracker

        return [
            TorrentTracker(
                url=managed.url, status=managed.status, seeds=managed.seeds,
                peers=managed.peers, message=managed.message,
                proxy=managed.proxy.display_name(),
            )
            for key, managed in self._managed.items() if key[0] == info_hash
        ]

    def _collect(self, torrents: Iterable[Any]) -> None:
        torrent_by_hash = {
            str(getattr(torrent, "info_hash", "") or ""): torrent for torrent in torrents
        }
        for future, key in list(self._futures.items()):
            if not future.done():
                continue
            del self._futures[future]
            managed = self._managed.get(key)
            if managed is None:
                continue
            managed.pending = False
            try:
                result = future.result()
            except Exception as exc:
                result = TrackerAnnounceResult(failure=f"{type(exc).__name__}: {exc}")
            managed.next_announce = time.monotonic() + max(30, result.interval)
            if not result.ok:
                managed.status = "Error"
                managed.message = result.failure
                continue
            managed.status = "Working"
            managed.seeds = result.seeds
            managed.peers = result.peers_available
            managed.message = result.warning
            torrent = torrent_by_hash.get(key[0])
            if torrent is None:
                continue
            for host, port in result.peers:
                try:
                    torrent.handle.connect_peer((host, port))
                except Exception as exc:
                    logger.debug("Could not inject tracker peer %s:%d: %s", host, port, exc)

    def _rule_for(self, tracker_url: str) -> TrackerProxyRule | None:
        for rule in self._rules:
            if rule.matches(tracker_url):
                return rule
        return None


def _announce_info_hash_bytes(handle: Any) -> bytes:
    try:
        hashes = handle.info_hashes()
        if hashes.has_v1():
            return hashes.v1.to_bytes()
        if hashes.has_v2():
            return hashes.v2.to_bytes()
    except Exception:
        pass
    try:
        return handle.info_hash().to_bytes()
    except Exception:
        return b""
