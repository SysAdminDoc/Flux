"""Opt-in completion move example with a safe renaming template."""

import re
import shutil
from pathlib import Path

from flux.core.plugins import PluginContext, PluginResult

_INVALID_FILENAME = re.compile(r"[<>:\"/\\|?*\x00-\x1f]")


def _safe_name(value: str) -> str:
    cleaned = _INVALID_FILENAME.sub("_", value).strip(" .")
    return cleaned or "torrent"


class PostCompleteMovePlugin:
    """Move a completed torrent to a configured destination when enabled."""

    name = "post-complete-move"
    api_version = 1
    events = ("on_finish",)

    def handle(self, context: PluginContext) -> PluginResult:
        if not context.config.get("enabled", False):
            return PluginResult(self.name, context.event, True, "disabled")
        source = Path(str(context.torrent.get("save_path", "") or ""))
        destination_root = Path(str(context.config.get("destination", "") or ""))
        if not source.exists() or not destination_root:
            return PluginResult(self.name, context.event, False, "source or destination is unavailable")
        template = str(context.config.get("rename", "{name}") or "{name}")
        try:
            renamed = template.format(
                name=context.torrent.get("name", "torrent"),
                info_hash=context.torrent.get("info_hash", ""),
                category=context.torrent.get("category", ""),
            )
        except (KeyError, ValueError):
            return PluginResult(self.name, context.event, False, "invalid rename template")
        target = destination_root / _safe_name(renamed)
        destination_root.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))
        return PluginResult(self.name, context.event, True, f"moved to {target}", {"target": str(target)})
