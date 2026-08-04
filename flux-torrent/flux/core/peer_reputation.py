"""Persistent, bounded peer reputation memory.

The store records only connection identities and counters. It never stores
payloads, tracker credentials, or torrent names, and it writes atomically so a
crash cannot leave a half-written reputation file.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path


SCHEMA_VERSION = 1
DEFAULT_MAX_RECORDS = 20_000
EVENT_WEIGHTS = {"disconnect": 1, "error": 3, "hash_fail": 5}


@dataclass(frozen=True)
class PeerRecord:
    """A GUI-safe snapshot of one peer identity's reputation counters."""

    peer: str
    client: str = ""
    disconnects: int = 0
    errors: int = 0
    hash_failures: int = 0
    last_seen: float = 0.0

    @property
    def events(self) -> int:
        return self.disconnects + self.errors + self.hash_failures

    @property
    def score(self) -> int:
        return (
            self.disconnects * EVENT_WEIGHTS["disconnect"]
            + self.errors * EVENT_WEIGHTS["error"]
            + self.hash_failures * EVENT_WEIGHTS["hash_fail"]
        )


def _clean_peer(value: object) -> str:
    return str(value or "").strip()[:256]


class PeerReputationStore:
    """Load, update, and atomically persist bounded peer reputation data."""

    def __init__(self, path: str | Path | None = None, max_records: int = DEFAULT_MAX_RECORDS):
        self.path = Path(path) if path else Path.home() / ".flux-torrent" / "peer-reputation.json"
        self.max_records = max(1, int(max_records))
        self._records: dict[str, PeerRecord] = {}
        self._load()

    def get(self, peer: object) -> PeerRecord:
        key = _clean_peer(peer)
        return self._records.get(key, PeerRecord(peer=key))

    def record(self, peer: object, event: str, client: object = "") -> PeerRecord | None:
        """Record a supported bad-peer event and persist the updated snapshot."""
        key = _clean_peer(peer)
        event_key = str(event or "").strip().casefold()
        if not key or event_key not in EVENT_WEIGHTS:
            return None
        current = self.get(key)
        counts = {
            "disconnects": current.disconnects,
            "errors": current.errors,
            "hash_failures": current.hash_failures,
        }
        field = {
            "disconnect": "disconnects",
            "error": "errors",
            "hash_fail": "hash_failures",
        }[event_key]
        counts[field] += 1
        record = PeerRecord(
            peer=key,
            client=str(client or current.client)[:256],
            **counts,
            last_seen=time.time(),
        )
        self._records[key] = record
        self._trim()
        self._save()
        return record

    def should_deprioritize(self, peer: object, threshold: int = 3) -> bool:
        """Return true after repeated evidence, using weighted bad-event scores."""
        try:
            minimum = max(1, int(threshold))
        except (TypeError, ValueError):
            minimum = 3
        return self.get(peer).score >= minimum

    def snapshot(self) -> dict[str, PeerRecord]:
        return dict(self._records)

    def _trim(self) -> None:
        if len(self._records) <= self.max_records:
            return
        ordered = sorted(
            self._records.values(),
            key=lambda item: (item.last_seen, item.peer),
            reverse=True,
        )[: self.max_records]
        self._records = {item.peer: item for item in ordered}

    def _load(self) -> None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            return
        if not isinstance(payload, dict) or payload.get("version") != SCHEMA_VERSION:
            return
        records = payload.get("peers", {})
        if not isinstance(records, dict):
            return
        for key, value in records.items():
            if not isinstance(value, dict):
                continue
            peer = _clean_peer(key)
            if not peer:
                continue
            try:
                record = PeerRecord(
                    peer=peer,
                    client=str(value.get("client", ""))[:256],
                    disconnects=max(0, int(value.get("disconnects", 0))),
                    errors=max(0, int(value.get("errors", 0))),
                    hash_failures=max(0, int(value.get("hash_failures", 0))),
                    last_seen=float(value.get("last_seen", 0.0)),
                )
            except (TypeError, ValueError, OverflowError):
                continue
            self._records[peer] = record
        self._trim()

    def _save(self) -> None:
        payload = {
            "version": SCHEMA_VERSION,
            "updated_at": int(time.time()),
            "peers": {
                key: {
                    "client": record.client,
                    "disconnects": record.disconnects,
                    "errors": record.errors,
                    "hash_failures": record.hash_failures,
                    "last_seen": record.last_seen,
                }
                for key, record in sorted(self._records.items())
            },
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
        finally:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
