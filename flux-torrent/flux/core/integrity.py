"""SHA-256 sidecar manifest planning and generation."""

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


MANIFEST_VERSION = 1
HASH_CHUNK_BYTES = 4 * 1024 * 1024
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._ -]+")


class IntegrityError(ValueError):
    """Raised when a manifest cannot be generated safely."""


@dataclass(frozen=True)
class IntegrityFile:
    """A validated torrent file and the fingerprint used during hashing."""

    file_index: int
    relative_path: str
    absolute_path: str
    size: int
    mtime_ns: int


@dataclass(frozen=True)
class IntegrityPlan:
    """Pure-data work request that can safely cross to a hash executor."""

    info_hash: str
    name: str
    output_path: str
    files: tuple[IntegrityFile, ...]


@dataclass(frozen=True)
class IntegrityResult:
    """Result of a completed sidecar generation."""

    success: bool
    output_path: str = ""
    file_count: int = 0
    total_bytes: int = 0
    error: str = ""


def _is_pad_file(file_storage, file_index: int) -> bool:
    try:
        return bool(
            file_storage.file_flags(file_index) & file_storage.flag_pad_file
        )
    except (AttributeError, TypeError):
        return False


def _safe_storage_path(save_path: str | Path, file_storage, file_index: int) -> Path:
    root = Path(save_path).resolve()
    candidate = (root / str(file_storage.file_path(file_index))).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise IntegrityError("torrent file path escapes the save directory") from exc
    return candidate


def _relative_path(file_storage, file_index: int) -> str:
    value = str(file_storage.file_path(file_index)).replace("\\", "/")
    if not value or value.startswith("/") or re.match(r"^[A-Za-z]:/", value):
        raise IntegrityError("torrent file path is not relative")
    return value


def _safe_manifest_name(name: str) -> str:
    cleaned = _SAFE_NAME.sub("_", str(name or "").strip()).strip(" .")
    return cleaned or "torrent"


def manifest_path(
    save_path: str | Path,
    torrent_name: str,
    manifest_directory: str | Path = "",
) -> Path:
    """Return the stable JSON sidecar location for a torrent."""
    parent = Path(manifest_directory).expanduser() if str(manifest_directory or "").strip() else Path(save_path)
    return parent / f"{_safe_manifest_name(torrent_name)}.sha256.json"


def build_manifest_plan(
    torrent_info,
    save_path: str | Path,
    info_hash: str,
    torrent_name: str,
    manifest_directory: str | Path = "",
) -> IntegrityPlan:
    """Validate torrent paths and capture a pure-data hashing plan."""
    file_storage = torrent_info.files()
    files = []
    for file_index in range(int(file_storage.num_files())):
        if _is_pad_file(file_storage, file_index):
            continue
        relative_path = _relative_path(file_storage, file_index)
        path = _safe_storage_path(save_path, file_storage, file_index)
        try:
            stat = path.stat()
        except OSError as exc:
            raise IntegrityError(f"missing torrent file: {relative_path}") from exc
        expected_size = int(file_storage.file_size(file_index))
        if stat.st_size != expected_size:
            raise IntegrityError(
                f"size mismatch for {relative_path}: expected {expected_size}, found {stat.st_size}"
            )
        files.append(
            IntegrityFile(
                file_index=file_index,
                relative_path=relative_path,
                absolute_path=str(path),
                size=expected_size,
                mtime_ns=int(stat.st_mtime_ns),
            )
        )
    return IntegrityPlan(
        info_hash=str(info_hash or ""),
        name=str(torrent_name or ""),
        output_path=str(manifest_path(save_path, torrent_name, manifest_directory)),
        files=tuple(files),
    )


def _hash_file(file_entry: IntegrityFile) -> str:
    path = Path(file_entry.absolute_path)
    try:
        before = path.stat()
    except OSError as exc:
        raise IntegrityError(f"file disappeared: {file_entry.relative_path}") from exc
    if before.st_size != file_entry.size or before.st_mtime_ns != file_entry.mtime_ns:
        raise IntegrityError(f"file changed before hashing: {file_entry.relative_path}")

    digest = hashlib.sha256()
    try:
        with path.open("rb") as file_handle:
            while chunk := file_handle.read(HASH_CHUNK_BYTES):
                digest.update(chunk)
        after = path.stat()
    except OSError as exc:
        raise IntegrityError(f"could not hash file: {file_entry.relative_path}") from exc
    if after.st_size != file_entry.size or after.st_mtime_ns != file_entry.mtime_ns:
        raise IntegrityError(f"file changed while hashing: {file_entry.relative_path}")
    return digest.hexdigest()


def _write_manifest_atomic(manifest: dict, output_path: str) -> None:
    target = Path(output_path)
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
            json.dump(manifest, temp_file, indent=2, sort_keys=True)
            temp_file.write("\n")
            temp_file.flush()
            os.fsync(temp_file.fileno())
            temp_name = temp_file.name
        os.replace(temp_name, target)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)


def generate_manifest(plan: IntegrityPlan) -> IntegrityResult:
    """Hash a validated plan and atomically publish its JSON sidecar."""
    try:
        entries = []
        total_bytes = 0
        for file_entry in plan.files:
            entries.append({
                "path": file_entry.relative_path,
                "size": file_entry.size,
                "sha256": _hash_file(file_entry),
            })
            total_bytes += file_entry.size
        manifest = {
            "version": MANIFEST_VERSION,
            "algorithm": "SHA-256",
            "info_hash": plan.info_hash,
            "name": plan.name,
            "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
                "+00:00", "Z"
            ),
            "total_bytes": total_bytes,
            "files": entries,
        }
        _write_manifest_atomic(manifest, plan.output_path)
        return IntegrityResult(
            success=True,
            output_path=plan.output_path,
            file_count=len(entries),
            total_bytes=total_bytes,
        )
    except (IntegrityError, OSError, TypeError, ValueError) as exc:
        return IntegrityResult(
            success=False,
            output_path=plan.output_path,
            error=str(exc),
        )
