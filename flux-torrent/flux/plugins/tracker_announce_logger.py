"""Tracker announce JSONL logger example plugin."""

import json
import time
from pathlib import Path

from flux.core.plugins import PluginContext, PluginResult


class TrackerAnnounceLoggerPlugin:
    """Append bounded tracker alert metadata to a configured JSONL file."""

    name = "tracker-announce-logger"
    api_version = 1
    events = ("on_tracker_announce",)

    def handle(self, context: PluginContext) -> PluginResult:
        path = str(context.config.get("path", "") or "").strip()
        if not path:
            return PluginResult(self.name, context.event, True, "disabled")
        record = {
            "timestamp": time.time(),
            "event": context.event,
            "info_hash": context.torrent.get("info_hash", ""),
            "name": context.torrent.get("name", ""),
            "tracker_url": context.metadata.get("tracker_url", ""),
            "alert_type": context.metadata.get("alert_type", ""),
            "message": context.metadata.get("message", ""),
        }
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return PluginResult(self.name, context.event, True, f"logged to {target}")
