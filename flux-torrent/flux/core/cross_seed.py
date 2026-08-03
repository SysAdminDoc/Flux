"""Cross-seed metadata scanning and library verification helpers.

The scanner intentionally operates on ``.torrent`` metadata rather than
mutating a session. It can therefore be used from the GUI, a future CLI, or a
remote worker without silently adding torrents. Exact piece hashes are
reported when available; matching file paths, sizes, and piece length is
reported separately as a lower-confidence candidate.
"""

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import libtorrent as lt

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TorrentFileFingerprint:
    """A content file as represented by torrent metadata."""

    path: str
    size: int
    is_pad: bool = False


@dataclass(frozen=True)
class TorrentDescriptor:
    """Metadata needed to compare two torrent files."""

    path: str
    name: str
    piece_length: int
    total_size: int
    files: tuple[TorrentFileFingerprint, ...]
    piece_hashes: tuple[str, ...]
    info_hash_v1: str = ""
    info_hash_v2: str = ""
    trackers: tuple[str, ...] = ()

    @property
    def normalized_files(self) -> tuple[tuple[str, int], ...]:
        """Return sorted non-padding paths and sizes for root-name-independent matching."""
        return tuple(sorted(
            (entry.path.casefold(), entry.size)
            for entry in self.files if not entry.is_pad
        ))

    @property
    def content_key(self) -> str:
        """Stable metadata fingerprint useful for de-duplicating scan results."""
        payload = repr((self.normalized_files, self.piece_length, self.piece_hashes)).encode()
        return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class CrossSeedMatch:
    """A candidate torrent that appears to contain the same content."""

    source_path: str
    candidate_path: str
    method: str
    confidence: float
    total_size: int
    candidate_trackers: tuple[str, ...] = ()


def _canonical_path(value: str) -> str:
    return str(value or "").replace("\\", "/").strip("/")


def _build_file_list(ti) -> tuple[TorrentFileFingerprint, ...]:
    storage = ti.files()
    pad_flag = getattr(lt.file_storage, "flag_pad_file", 0)
    raw = []
    for index in range(storage.num_files()):
        path = _canonical_path(storage.file_path(index))
        size = int(storage.file_size(index))
        flags = storage.file_flags(index)
        raw.append((path, size, bool(flags & pad_flag)))

    # Torrent roots are not content identity. Strip one common root component
    # so a release named ``Show`` matches an otherwise identical ``Show.1080p``
    # torrent.
    roots = {path.split("/", 1)[0] for path, _, _ in raw if "/" in path}
    strip_root = len(roots) == 1 and all("/" in path for path, _, _ in raw)
    if strip_root:
        root = next(iter(roots)) + "/"
        raw = [(path[len(root):], size, is_pad) for path, size, is_pad in raw]
    return tuple(
        TorrentFileFingerprint(path=path, size=size, is_pad=is_pad)
        for path, size, is_pad in raw
    )


def _info_hashes(ti) -> tuple[str, str]:
    try:
        hashes = ti.info_hashes()
        v1 = str(hashes.v1) if hashes.has_v1() else ""
        v2 = str(hashes.v2) if hashes.has_v2() else ""
        return v1, v2
    except Exception:
        try:
            return str(ti.info_hash()), ""
        except Exception:
            return "", ""


def _piece_hashes(ti) -> tuple[str, ...]:
    hashes = []
    for index in range(int(ti.num_pieces())):
        try:
            value = ti.hash_for_piece(index)
            hashes.append(bytes(value).hex())
        except Exception:
            return ()
    return tuple(hashes)


def _trackers(ti) -> tuple[str, ...]:
    values = []
    try:
        for tracker in ti.trackers():
            if isinstance(tracker, dict):
                url = tracker.get("url", "")
            else:
                url = getattr(tracker, "url", "")
            if url:
                values.append(str(url))
    except Exception:
        pass
    return tuple(dict.fromkeys(values))


def build_torrent_descriptor(path: str | Path) -> TorrentDescriptor:
    """Read one torrent file into a comparison descriptor."""
    torrent_path = Path(path).expanduser().resolve()
    ti = lt.torrent_info(str(torrent_path))
    info_hash_v1, info_hash_v2 = _info_hashes(ti)
    return TorrentDescriptor(
        path=str(torrent_path),
        name=str(ti.name()),
        piece_length=int(ti.piece_length()),
        total_size=int(ti.total_size()),
        files=_build_file_list(ti),
        piece_hashes=_piece_hashes(ti),
        info_hash_v1=info_hash_v1,
        info_hash_v2=info_hash_v2,
        trackers=_trackers(ti),
    )


def scan_torrent_files(root: str | Path) -> List[TorrentDescriptor]:
    """Recursively read valid ``.torrent`` files below ``root``.

    Invalid or partially written files are skipped and logged at debug level,
    allowing a library scan to continue when a download directory contains
    unrelated metadata.
    """
    root_path = Path(root).expanduser()
    if root_path.is_file():
        candidates = [root_path] if root_path.suffix.casefold() == ".torrent" else []
    elif root_path.is_dir():
        candidates = sorted(root_path.rglob("*.torrent"))
    else:
        return []

    descriptors = []
    for candidate in candidates:
        try:
            descriptors.append(build_torrent_descriptor(candidate))
        except Exception as exc:
            logger.debug("Skipping invalid torrent metadata %s: %s", candidate, exc)
    return descriptors


def match_torrent_descriptors(
    source: TorrentDescriptor, candidate: TorrentDescriptor
) -> Optional[CrossSeedMatch]:
    """Compare two descriptors, returning the strongest safe match available."""
    if source.path == candidate.path:
        return None

    source_hashes = {value for value in (source.info_hash_v1, source.info_hash_v2) if value}
    candidate_hashes = {value for value in (candidate.info_hash_v1, candidate.info_hash_v2) if value}
    if source_hashes & candidate_hashes:
        return CrossSeedMatch(
            source_path=source.path,
            candidate_path=candidate.path,
            method="info_hash",
            confidence=1.0,
            total_size=candidate.total_size,
            candidate_trackers=candidate.trackers,
        )

    if source.normalized_files != candidate.normalized_files:
        return None

    if (
        source.piece_length == candidate.piece_length
        and source.piece_hashes
        and source.piece_hashes == candidate.piece_hashes
    ):
        return CrossSeedMatch(
            source_path=source.path,
            candidate_path=candidate.path,
            method="piece_hash",
            confidence=0.99,
            total_size=candidate.total_size,
            candidate_trackers=candidate.trackers,
        )

    if source.piece_length == candidate.piece_length:
        return CrossSeedMatch(
            source_path=source.path,
            candidate_path=candidate.path,
            method="piece_size",
            confidence=0.75,
            total_size=candidate.total_size,
            candidate_trackers=candidate.trackers,
        )
    return CrossSeedMatch(
        source_path=source.path,
        candidate_path=candidate.path,
        method="file_size",
        confidence=0.55,
        total_size=candidate.total_size,
        candidate_trackers=candidate.trackers,
    )


def find_cross_seed_matches(descriptors: List[TorrentDescriptor]) -> List[CrossSeedMatch]:
    """Return unique pairwise matches sorted by confidence and candidate path."""
    matches = []
    for index, source in enumerate(descriptors):
        for candidate in descriptors[index + 1:]:
            match = match_torrent_descriptors(source, candidate)
            if match:
                matches.append(match)
    return sorted(matches, key=lambda item: (-item.confidence, item.candidate_path.casefold()))


def _content_path(root: Path, descriptor: TorrentDescriptor, entry: TorrentFileFingerprint) -> Path:
    relative = Path(*entry.path.split("/"))
    direct = root / relative
    if direct.is_file():
        return direct
    named = root / descriptor.name / relative
    if named.is_file():
        return named
    if len(descriptor.files) == 1:
        single = root / Path(entry.path).name
        if single.is_file():
            return single
    return direct


def verify_library_content(descriptor: TorrentDescriptor, library_root: str | Path) -> bool:
    """Verify a torrent's v1 piece hashes against files in ``library_root``.

    This is intentionally opt-in because it reads every byte in the matched
    content. v2-only metadata cannot be verified with the legacy piece-hash
    API and returns ``False`` rather than claiming a match.
    """
    if not descriptor.piece_hashes or any(len(value) != 40 for value in descriptor.piece_hashes):
        return False
    root = Path(library_root).expanduser()
    if not root.is_dir():
        return False

    streams = []
    for entry in descriptor.files:
        if entry.is_pad:
            streams.append((None, entry.size))
            continue
        path = _content_path(root, descriptor, entry)
        try:
            if not path.is_file() or path.stat().st_size != entry.size:
                return False
            streams.append((path, entry.size))
        except OSError:
            return False

    piece_length = max(1, descriptor.piece_length)
    piece_index = 0
    buffered = 0
    digest = hashlib.sha1()

    def consume(data: bytes) -> bool:
        nonlocal piece_index, buffered, digest
        offset = 0
        while offset < len(data):
            take = min(piece_length - buffered, len(data) - offset)
            digest.update(data[offset:offset + take])
            buffered += take
            offset += take
            if buffered == piece_length:
                if piece_index >= len(descriptor.piece_hashes) or digest.hexdigest() != descriptor.piece_hashes[piece_index]:
                    return False
                piece_index += 1
                buffered = 0
                digest = hashlib.sha1()
        return True

    for path, size in streams:
        if path is None:
            remaining = size
            zero_block = b"\0" * min(piece_length, 1024 * 1024)
            while remaining:
                take = min(remaining, len(zero_block))
                if not consume(zero_block[:take]):
                    return False
                remaining -= take
            continue
        try:
            with path.open("rb") as handle:
                while True:
                    chunk = handle.read(1024 * 1024)
                    if not chunk:
                        break
                    if not consume(chunk):
                        return False
        except OSError:
            return False

    if buffered:
        if piece_index >= len(descriptor.piece_hashes) or digest.hexdigest() != descriptor.piece_hashes[piece_index]:
            return False
        piece_index += 1
    return piece_index == len(descriptor.piece_hashes)
