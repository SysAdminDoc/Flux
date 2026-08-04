"""Tests for provider-specific completion webhooks."""

import json
from unittest.mock import MagicMock, patch

import pytest

from flux.core.webhooks import (
    WebhookConfigError,
    build_webhook_request,
    provider_for_url,
    send_webhook,
)


def test_provider_detection_accepts_supported_https_endpoints():
    assert provider_for_url("https://discord.com/api/webhooks/123/token") == "discord"
    assert provider_for_url("https://api.telegram.org/bot123:secret/sendMessage?chat_id=42") == "telegram"


@pytest.mark.parametrize(
    "url",
    [
        "http://discord.com/api/webhooks/123/token",
        "https://example.com/api/webhooks/123/token",
        "https://discord.com/redirect?url=https://example.com",
        "https://user:pass@discord.com/api/webhooks/123/token",
    ],
)
def test_provider_detection_rejects_unsafe_or_unknown_urls(url):
    with pytest.raises(WebhookConfigError):
        provider_for_url(url)


def test_discord_request_contains_completion_message():
    request = build_webhook_request(
        "https://discord.com/api/webhooks/123/token",
        "on_finish",
        {"name": "Movie.mkv", "info_hash": "abc", "ratio": 1.25},
    )
    payload = json.loads(request.data)
    assert payload["username"] == "Flux Torrent"
    assert "Torrent complete: Movie.mkv" in payload["content"]
    assert request.get_header("Content-type") == "application/json"


def test_telegram_request_uses_plain_text_payload():
    request = build_webhook_request(
        "https://api.telegram.org/bot123:secret/sendMessage?chat_id=42",
        "on_finish",
        {"name": "Show S01E01", "ratio": 2.0},
    )
    payload = json.loads(request.data)
    assert payload["disable_web_page_preview"] is True
    assert "Show S01E01" in payload["text"]


def test_send_webhook_reports_success_without_logging_endpoint():
    response = MagicMock()
    response.status = 204
    response.__enter__.return_value = response
    with patch("flux.core.webhooks.urlopen", return_value=response) as opener:
        result = send_webhook(
            "https://discord.com/api/webhooks/123/token",
            "on_finish",
            {"name": "Done"},
        )

    assert result.success is True
    assert result.provider == "discord"
    assert result.status == 204
    opener.assert_called_once()
