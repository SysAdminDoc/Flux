"""Helpers for mapping advertised pieces to deterministic peer colors."""

from __future__ import annotations

from collections.abc import Iterable


MAX_PEER_PIECE_SOURCES = 12


def build_peer_piece_map(
    peers: Iterable[tuple[str, object]],
    piece_count: int,
    max_peers: int = MAX_PEER_PIECE_SOURCES,
) -> tuple[list[int], list[str]]:
    """Return the first peer owner for each piece and the matching labels."""
    count = max(0, int(piece_count))
    limit = max(0, min(MAX_PEER_PIECE_SOURCES, int(max_peers)))
    owners = [-1] * count
    labels: list[str] = []
    if not count or not limit:
        return owners, labels

    for label, bitfield in peers:
        if len(labels) >= limit:
            break
        try:
            bits = len(bitfield)
        except (TypeError, AttributeError):
            continue
        peer_index = len(labels)
        labels.append(str(label or "Peer")[:96])
        for piece_index in range(min(count, bits)):
            try:
                available = bool(bitfield[piece_index])
            except (IndexError, KeyError, TypeError, ValueError):
                available = False
            if available and owners[piece_index] < 0:
                owners[piece_index] = peer_index
    return owners, labels
