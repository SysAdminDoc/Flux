"""Small, provider-aware webhook client for torrent lifecycle notifications.

Only HTTPS Discord and Telegram endpoints are accepted.  Delivery is kept
separate from the session worker so network timeouts cannot pause libtorrent's
alert loop.
"""

import json
import logging
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

MAX_WEBHOOK_URL_LENGTH = 2048
MAX_RESPONSE_BYTES = 4096
DISCORD_HOSTS = {"discord.com", "discordapp.com", "www.discord.com", "www.discordapp.com"}
TELEGRAM_HOST = "api.telegram.org"


class WebhookConfigError(ValueError):
    """Raised when a configured endpoint is not a supported webhook URL."""


@dataclass(frozen=True)
class WebhookResult:
    """Bounded result returned by a background delivery attempt."""

    success: bool
    provider: str
    status: int = 0
    error: str = ""


def provider_for_url(url: str) -> str:
    """Return ``discord`` or ``telegram`` for a supported HTTPS endpoint."""
    value = str(url or "").strip()
    if not value or len(value) > MAX_WEBHOOK_URL_LENGTH:
        raise WebhookConfigError("Webhook URL is empty or too long")

    parsed = urlsplit(value)
    if parsed.scheme.lower() != "https" or parsed.username or parsed.password:
        raise WebhookConfigError("Webhook URL must use HTTPS without embedded credentials")
    if parsed.query and any(part.lower().startswith(("url=", "redirect=")) for part in parsed.query.split("&")):
        raise WebhookConfigError("Webhook URL contains a redirect parameter")

    host = (parsed.hostname or "").lower().rstrip(".")
    path = parsed.path.rstrip("/")
    if host in DISCORD_HOSTS and path.startswith("/api/webhooks/"):
        return "discord"
    if host == TELEGRAM_HOST and path.startswith("/bot") and path.endswith("/sendMessage"):
        return "telegram"
    raise WebhookConfigError("Only Discord and Telegram HTTPS webhooks are supported")


def _message(event: str, torrent: dict) -> str:
    name = str(torrent.get("name", "Torrent") or "Torrent").strip()
    info_hash = str(torrent.get("info_hash", "") or "").strip()
    ratio = float(torrent.get("ratio", 0.0) or 0.0)
    lines = [f"Torrent complete: {name}"] if event == "on_finish" else [f"Torrent event: {event} — {name}"]
    if info_hash:
        lines.append(f"Info hash: {info_hash}")
    lines.append(f"Ratio: {ratio:.3f}")
    save_path = str(torrent.get("save_path", "") or "").strip()
    if save_path:
        lines.append(f"Save path: {save_path}")
    return "\n".join(lines)


def build_webhook_request(url: str, event: str, torrent: dict) -> Request:
    """Build a provider-specific JSON POST request without performing I/O."""
    provider = provider_for_url(url)
    message = _message(event, torrent)
    if provider == "discord":
        payload = {"username": "Flux Torrent", "content": message}
    else:
        payload = {"text": message, "disable_web_page_preview": True}
    return Request(
        str(url).strip(),
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "FluxTorrent/1.1.0"},
        method="POST",
    )


def send_webhook(url: str, event: str, torrent: dict, timeout: float = 10.0) -> WebhookResult:
    """Deliver one webhook and return a redacted, machine-readable result."""
    provider = provider_for_url(url)
    request = build_webhook_request(url, event, torrent)
    try:
        with urlopen(request, timeout=timeout) as response:
            response.read(MAX_RESPONSE_BYTES)
            status = int(getattr(response, "status", 200) or 200)
        return WebhookResult(True, provider, status=status)
    except HTTPError as exc:
        return WebhookResult(False, provider, status=int(exc.code), error=f"HTTP {exc.code}")
    except (URLError, TimeoutError, OSError) as exc:
        return WebhookResult(False, provider, error=type(exc).__name__)
