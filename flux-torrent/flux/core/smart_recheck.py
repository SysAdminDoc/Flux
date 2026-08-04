"""File timestamp heuristics and piece-scoped torrent verification."""

from dataclasses import dataclass
from hashlib import sha1
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class FileFingerprint:
    """Filesystem identity used to decide whether a file is dirty."""

    file_index: int
    path: str
    size: int
    mtime_ns: int


@dataclass(frozen=True)
class SmartRecheckPlan:
    """The files and pieces that a smart re-check should inspect."""

    current_fingerprints: tuple[FileFingerprint, ...]
    dirty_files: tuple[int, ...]
    dirty_pieces: tuple[int, ...]
    skip: bool = False
    requires_full_recheck: bool = False
    reason: str = ""


@dataclass(frozen=True)
class PieceVerification:
    """Result of reading and hashing one piece from local storage."""

    piece: int
    available: bool
    matches: bool | None


def _is_pad_file(file_storage, file_index: int) -> bool:
    try:
        return bool(
            file_storage.file_flags(file_index) & file_storage.flag_pad_file
        )
    except (AttributeError, TypeError):
        return False


def _storage_path(
    save_path: str | Path,
    file_storage,
    file_index: int,
) -> Path | None:
    root = Path(save_path).resolve()
    candidate = (root / str(file_storage.file_path(file_index))).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def capture_file_fingerprints(torrent_info, save_path: str | Path) -> tuple[FileFingerprint, ...]:
    """Capture size and nanosecond mtime for every non-padding torrent file."""
    file_storage = torrent_info.files()
    fingerprints = []
    for file_index in range(int(file_storage.num_files())):
        if _is_pad_file(file_storage, file_index):
            continue
        path = _storage_path(save_path, file_storage, file_index)
        try:
            if path is None:
                raise OSError("torrent file path escapes the save directory")
            stat = path.stat()
            size = int(stat.st_size)
            mtime_ns = int(stat.st_mtime_ns)
        except OSError:
            size = -1
            mtime_ns = -1
        fingerprints.append(
            FileFingerprint(
                file_index=file_index,
                path=str(path),
                size=size,
                mtime_ns=mtime_ns,
            )
        )
    return tuple(fingerprints)


def dirty_file_indices(
    previous: Iterable[FileFingerprint] | None,
    current: Iterable[FileFingerprint],
) -> tuple[int, ...]:
    """Return files whose path, size, or modification time changed."""
    current_by_index = {item.file_index: item for item in current}
    if previous is None:
        return tuple(sorted(current_by_index))
    previous_by_index = {item.file_index: item for item in previous}
    dirty = [
        file_index
        for file_index, fingerprint in current_by_index.items()
        if fingerprint.size < 0 or previous_by_index.get(file_index) != fingerprint
    ]
    dirty.extend(set(previous_by_index) - set(current_by_index))
    return tuple(sorted(set(dirty)))


def pieces_for_files(torrent_info, file_indices: Iterable[int]) -> tuple[int, ...] | None:
    """Map changed files to all pieces that overlap them."""
    dirty = {int(index) for index in file_indices}
    if not dirty:
        return ()
    try:
        pieces = set()
        for piece in range(int(torrent_info.num_pieces())):
            size = int(torrent_info.piece_size(piece))
            slices = torrent_info.map_block(piece, 0, size)
            if any(int(item.file_index) in dirty for item in slices):
                pieces.add(piece)
        return tuple(sorted(pieces))
    except (AttributeError, IndexError, RuntimeError, TypeError, ValueError):
        return None


def _has_v1_hashes(torrent_info) -> bool:
    try:
        hashes = torrent_info.info_hashes()
        has_v1 = getattr(hashes, "has_v1", None)
        has_v2 = getattr(hashes, "has_v2", None)
        if callable(has_v1):
            has_v1 = has_v1()
        if callable(has_v2):
            has_v2 = has_v2()
        if has_v1 is not None:
            return bool(has_v1)
    except (AttributeError, RuntimeError, TypeError):
        pass
    try:
        return bool(torrent_info.v1())
    except (AttributeError, RuntimeError, TypeError):
        try:
            return not bool(torrent_info.is_merkle_torrent())
        except (AttributeError, RuntimeError, TypeError):
            return True


def build_smart_recheck_plan(
    torrent_info,
    save_path: str | Path,
    previous: Iterable[FileFingerprint] | None,
) -> SmartRecheckPlan:
    """Build a conservative plan from file fingerprints and piece mappings."""
    current = capture_file_fingerprints(torrent_info, save_path)
    if previous is None:
        return SmartRecheckPlan(
            current_fingerprints=current,
            dirty_files=tuple(item.file_index for item in current),
            dirty_pieces=(),
            requires_full_recheck=True,
            reason="no file fingerprint baseline is available",
        )
    dirty_files = dirty_file_indices(previous, current)
    if not dirty_files:
        return SmartRecheckPlan(
            current_fingerprints=current,
            dirty_files=(),
            dirty_pieces=(),
            skip=True,
            reason="file sizes and modification times are unchanged",
        )

    dirty_pieces = pieces_for_files(torrent_info, dirty_files)
    if dirty_pieces is None:
        return SmartRecheckPlan(
            current_fingerprints=current,
            dirty_files=dirty_files,
            dirty_pieces=(),
            requires_full_recheck=True,
            reason="torrent metadata could not map dirty files to pieces",
        )
    if not dirty_pieces:
        return SmartRecheckPlan(
            current_fingerprints=current,
            dirty_files=dirty_files,
            dirty_pieces=(),
            skip=True,
            reason="changed files do not overlap torrent data pieces",
        )

    if not _has_v1_hashes(torrent_info):
        return SmartRecheckPlan(
            current_fingerprints=current,
            dirty_files=dirty_files,
            dirty_pieces=dirty_pieces,
            requires_full_recheck=True,
            reason="v2-only torrents require libtorrent's full verifier",
        )

    return SmartRecheckPlan(
        current_fingerprints=current,
        dirty_files=dirty_files,
        dirty_pieces=dirty_pieces,
        reason=f"{len(dirty_files)} changed file(s), {len(dirty_pieces)} affected piece(s)",
    )


def read_piece_data(torrent_info, save_path: str | Path, piece: int) -> bytes | None:
    """Read one piece from storage, inserting zeroes for padding files."""
    file_storage = torrent_info.files()
    try:
        piece_size = int(torrent_info.piece_size(piece))
        slices = torrent_info.map_block(piece, 0, piece_size)
    except (AttributeError, IndexError, RuntimeError, TypeError, ValueError):
        return None

    data = bytearray()
    for item in slices:
        file_index = int(item.file_index)
        size = int(item.size)
        if _is_pad_file(file_storage, file_index):
            data.extend(b"\0" * size)
            continue
        try:
            path = _storage_path(save_path, file_storage, file_index)
            if path is None:
                return None
            with path.open("rb") as file_handle:
                file_handle.seek(int(item.offset))
                chunk = file_handle.read(size)
        except OSError:
            return None
        if len(chunk) != size:
            return None
        data.extend(chunk)
    return bytes(data) if len(data) == piece_size else None


def verify_piece(torrent_info, save_path: str | Path, piece: int) -> PieceVerification:
    """Hash one local piece against its v1 torrent hash."""
    data = read_piece_data(torrent_info, save_path, piece)
    if data is None:
        return PieceVerification(piece=piece, available=False, matches=None)
    try:
        expected = torrent_info.hash_for_piece(piece)
    except (AttributeError, IndexError, RuntimeError, TypeError, ValueError):
        return PieceVerification(piece=piece, available=True, matches=None)
    if not isinstance(expected, bytes) or len(expected) != 20:
        return PieceVerification(piece=piece, available=True, matches=None)
    return PieceVerification(
        piece=piece,
        available=True,
        matches=sha1(data).digest() == expected,
    )


def verify_pieces(
    torrent_info,
    save_path: str | Path,
    pieces: Iterable[int],
) -> tuple[PieceVerification, ...]:
    """Verify a deterministic piece sequence and return each result."""
    return tuple(
        verify_piece(torrent_info, save_path, int(piece))
        for piece in sorted(set(pieces))
    )
