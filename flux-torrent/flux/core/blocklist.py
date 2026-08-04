"""IP blocklist parsing, failover fetching, and safe cache writes."""

import gzip
import io
import ipaddress
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.error import URLError
from urllib.request import urlopen


MAX_BLOCKLIST_BYTES = 64 * 1024 * 1024
MAX_BLOCKLIST_RANGES = 500_000


@dataclass(frozen=True)
class BlocklistFetchResult:
    success: bool
    source: str = ""
    content: str = ""
    ranges: tuple[tuple[str, str], ...] = ()
    error: str = ""


def normalize_blocklist_urls(value: Any) -> list[str]:
    """Normalize newline/list input and retain only HTTP(S) mirror URLs."""
    if isinstance(value, str):
        candidates = value.splitlines()
    elif isinstance(value, (list, tuple)):
        candidates = value
    else:
        candidates = []

    urls = []
    seen = set()
    for candidate in candidates:
        url = str(candidate or "").strip()
        if not url or not url.lower().startswith(("http://", "https://")):
            continue
        if url not in seen:
            urls.append(url)
            seen.add(url)
    return urls


def _parse_range_token(token: str) -> tuple[str, str] | None:
    token = token.strip()
    if not token:
        return None
    try:
        if "/" in token:
            network = ipaddress.ip_network(token, strict=False)
            return str(network.network_address), str(network.broadcast_address)
        if "-" not in token:
            return None
        start, end = (part.strip() for part in token.split("-", 1))
        start_ip = ipaddress.ip_address(start)
        end_ip = ipaddress.ip_address(end)
        if start_ip.version != end_ip.version or int(start_ip) > int(end_ip):
            return None
        return str(start_ip), str(end_ip)
    except ValueError:
        return None


def parse_blocklist_ranges(content: str) -> list[tuple[str, str]]:
    """Parse PeerGuardian, plain range, and CIDR blocklist lines."""
    ranges = []
    seen = set()
    for raw_line in str(content or "").splitlines():
        line = raw_line.split("#", 1)[0].split(";", 1)[0].strip()
        if not line:
            continue

        parsed = _parse_range_token(line)
        if parsed is None and ":" in line:
            # PeerGuardian files prefix ranges with ``label:``. Try that
            # form only after preserving native IPv6 ranges above.
            parsed = _parse_range_token(line.split(":", 1)[1].strip())
        if parsed is None or parsed in seen:
            continue
        ranges.append(parsed)
        seen.add(parsed)
        if len(ranges) >= MAX_BLOCKLIST_RANGES:
            break
    return ranges


def _decode_blocklist(raw: bytes | str) -> str:
    if isinstance(raw, str):
        return raw
    payload = bytes(raw)
    if payload[:2] == b"\x1f\x8b":
        with gzip.GzipFile(fileobj=io.BytesIO(payload)) as compressed:
            payload = compressed.read(MAX_BLOCKLIST_BYTES + 1)
    if len(payload) > MAX_BLOCKLIST_BYTES:
        raise ValueError("blocklist exceeds the 64 MiB limit")
    return payload.decode("utf-8", errors="replace")


def fetch_blocklist(
    urls: Iterable[str],
    opener: Callable[..., Any] | None = None,
    timeout: float = 20.0,
) -> BlocklistFetchResult:
    """Try mirrors in order and return the first non-empty valid blocklist."""
    errors = []
    open_url = opener or urlopen
    for url in normalize_blocklist_urls(list(urls)):
        response = None
        try:
            response = open_url(url, timeout=timeout)
            raw = response.read(MAX_BLOCKLIST_BYTES + 1)
            content = _decode_blocklist(raw)
            ranges = parse_blocklist_ranges(content)
            if not ranges:
                raise ValueError("no valid IP ranges found")
            return BlocklistFetchResult(
                success=True,
                source=url,
                content=content,
                ranges=tuple(ranges),
            )
        except (OSError, URLError, ValueError, gzip.BadGzipFile) as exc:
            errors.append(f"{url}: {exc}")
        finally:
            if response is not None and hasattr(response, "close"):
                response.close()
    return BlocklistFetchResult(
        success=False,
        error="; ".join(errors) or "no valid blocklist mirrors configured",
    )


def write_blocklist_cache(content: str, filepath: str) -> None:
    """Atomically replace a local cache after a successful refresh."""
    target = Path(filepath)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_file.write(content)
            temp_name = temp_file.name
        os.replace(temp_name, target)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)
