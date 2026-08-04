"""Opt-in archive extraction example plugin."""

import logging
import tarfile
import zipfile
from pathlib import Path

from flux.core.plugins import PluginContext, PluginResult

logger = logging.getLogger(__name__)


def _safe_member_path(root: Path, member_name: str) -> Path | None:
    candidate = (root / member_name).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


class AutoExtractPlugin:
    """Extract completed ZIP/TAR archives when explicitly enabled in config."""

    name = "auto-extract"
    api_version = 1
    events = ("on_finish",)

    def handle(self, context: PluginContext) -> PluginResult:
        if not context.config.get("enabled", False):
            return PluginResult(self.name, context.event, True, "disabled")
        source = Path(str(context.torrent.get("save_path", "") or ""))
        if not source.is_dir():
            return PluginResult(self.name, context.event, False, "save path is unavailable")
        destination = Path(str(context.config.get("destination", "") or "extracted"))
        destination = destination if destination.is_absolute() else source / destination
        destination.mkdir(parents=True, exist_ok=True)
        extracted = []
        try:
            for archive in sorted(source.iterdir()):
                if archive.suffix.lower() == ".zip":
                    with zipfile.ZipFile(archive) as bundle:
                        for member in bundle.infolist():
                            target = _safe_member_path(destination, member.filename)
                            if target is None:
                                raise ValueError("archive contains a path traversal entry")
                        bundle.extractall(destination)
                    extracted.append(archive.name)
                elif archive.name.lower().endswith((".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2")):
                    with tarfile.open(archive) as bundle:
                        for member in bundle.getmembers():
                            if (
                                _safe_member_path(destination, member.name) is None
                                or member.issym()
                                or member.islnk()
                                or not (member.isfile() or member.isdir())
                            ):
                                raise ValueError("archive contains a path traversal entry")
                        bundle.extractall(destination)
                    extracted.append(archive.name)
        except (OSError, ValueError, tarfile.TarError, zipfile.BadZipFile) as exc:
            return PluginResult(self.name, context.event, False, type(exc).__name__)
        return PluginResult(
            self.name,
            context.event,
            True,
            f"extracted {len(extracted)} archive(s)",
            {"archives": extracted, "destination": str(destination)},
        )
