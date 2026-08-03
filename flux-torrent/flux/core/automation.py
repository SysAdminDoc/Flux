"""Validated label-scoped automation rules."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, time as clock_time
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class LabelAutomationRule:
    """Actions applied to torrents whose category or tag matches ``label``."""

    label: str
    move_completed_path: str = ""
    tracker_overrides: tuple[str, ...] = ()
    ratio_limit: float = 0.0
    upload_limit: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "move_completed_path": self.move_completed_path,
            "tracker_overrides": list(self.tracker_overrides),
            "ratio_limit": self.ratio_limit,
            "upload_limit": self.upload_limit,
        }


@dataclass(frozen=True)
class TorrentSchedule:
    """A recurring local-time start/stop window for one torrent."""

    info_hash: str
    start: clock_time
    stop: clock_time
    days: tuple[int, ...] = (0, 1, 2, 3, 4, 5, 6)
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": self.start.strftime("%H:%M"),
            "stop": self.stop.strftime("%H:%M"),
            "days": list(self.days),
            "enabled": self.enabled,
        }


def parse_label_rules(raw: Any) -> tuple[LabelAutomationRule, ...]:
    """Parse JSON/list settings and discard malformed rules safely."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return ()
    if isinstance(raw, dict):
        rows = []
        for label, value in raw.items():
            row = dict(value) if isinstance(value, dict) else {}
            row["label"] = label
            rows.append(row)
    elif isinstance(raw, (list, tuple)):
        rows = [item for item in raw if isinstance(item, dict)]
    else:
        return ()

    parsed: dict[str, LabelAutomationRule] = {}
    for row in rows:
        label = str(row.get("label", row.get("category", "")) or "").strip()
        if not label:
            continue
        move_path = str(row.get("move_completed_path", row.get("move_path", "")) or "").strip()
        trackers = row.get("tracker_overrides", row.get("trackers", []))
        if isinstance(trackers, str):
            trackers = trackers.replace("\r", "\n").replace(",", "\n").split("\n")
        if not isinstance(trackers, (list, tuple)):
            trackers = []
        tracker_values = tuple(dict.fromkeys(
            str(value).strip() for value in trackers if str(value).strip()
        ))
        try:
            ratio_limit = max(0.0, float(row.get("ratio_limit", row.get("ratio", 0)) or 0))
        except (TypeError, ValueError):
            ratio_limit = 0.0
        try:
            upload_limit = max(0, int(row.get("upload_limit", row.get("upload", 0)) or 0))
        except (TypeError, ValueError):
            upload_limit = 0
        parsed[label.casefold()] = LabelAutomationRule(
            label=label,
            move_completed_path=move_path,
            tracker_overrides=tracker_values,
            ratio_limit=ratio_limit,
            upload_limit=upload_limit,
        )
    return tuple(parsed.values())


def label_rule_for(
    category: str, tags: list[str] | tuple[str, ...], rules: Any
) -> LabelAutomationRule | None:
    """Match category first, then tags, using case-insensitive exact labels."""
    parsed = rules if isinstance(rules, tuple) else parse_label_rules(rules)
    candidates = [str(category or "").strip(), *(str(tag).strip() for tag in tags or [])]
    for candidate in candidates:
        if not candidate:
            continue
        folded = candidate.casefold()
        for rule in parsed:
            if rule.label.casefold() == folded:
                return rule
    return None


def label_matches(category: str, tags: list[str] | tuple[str, ...], label: str) -> bool:
    """Return whether a category or tag exactly matches an exclusion label."""
    wanted = str(label or "").strip().casefold()
    if not wanted:
        return False
    values = [str(category or "").strip(), *(str(tag).strip() for tag in tags or [])]
    return any(value and value.casefold() == wanted for value in values)


def should_auto_delete(snapshot: Any, values: dict[str, Any]) -> bool:
    """Evaluate ratio/seed-age deletion without mutating torrent state."""
    if not bool(values.get("auto_delete_enabled", False)):
        return False
    if float(getattr(snapshot, "progress", 0.0) or 0.0) < 0.999:
        return False
    if label_matches(
        getattr(snapshot, "category", ""),
        getattr(snapshot, "tags", []) or [],
        str(values.get("auto_delete_exclude_label", "archive") or "archive"),
    ):
        return False
    try:
        ratio_limit = max(0.0, float(values.get("auto_delete_ratio", 0) or 0))
    except (TypeError, ValueError):
        ratio_limit = 0.0
    try:
        seed_days = max(0.0, float(values.get("auto_delete_seed_days", 0) or 0))
    except (TypeError, ValueError):
        seed_days = 0.0
    ratio_hit = ratio_limit > 0 and float(getattr(snapshot, "ratio", 0.0) or 0.0) >= ratio_limit
    seed_hit = seed_days > 0 and int(getattr(snapshot, "seeding_time", 0) or 0) >= seed_days * 86400
    return ratio_hit or seed_hit


def build_label_rules(values: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the canonical persistent shape for the settings dialog."""
    return [rule.to_dict() for rule in parse_label_rules(values.get("label_rules", []))]


def parse_torrent_schedules(raw: Any) -> dict[str, TorrentSchedule]:
    """Parse ``info_hash -> schedule`` settings and discard invalid rows."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return {}
    if isinstance(raw, list):
        rows = {}
        for item in raw:
            if isinstance(item, dict):
                info_hash = item.get("info_hash", item.get("hash", ""))
                rows[info_hash] = item
    elif isinstance(raw, dict):
        rows = raw
    else:
        return {}

    parsed: dict[str, TorrentSchedule] = {}
    for info_hash_value, row in rows.items():
        if not isinstance(row, dict):
            continue
        info_hash = str(row.get("info_hash", info_hash_value) or "").strip()
        start = _parse_clock_time(row.get("start"))
        stop = _parse_clock_time(row.get("stop"))
        if not info_hash or start is None or stop is None:
            continue
        raw_days = row.get("days", range(7))
        if isinstance(raw_days, str):
            raw_days = raw_days.replace(",", " ").split()
        try:
            days = tuple(sorted({int(day) for day in raw_days if 0 <= int(day) <= 6}))
        except (TypeError, ValueError):
            days = ()
        if not days:
            continue
        parsed[info_hash] = TorrentSchedule(
            info_hash=info_hash,
            start=start,
            stop=stop,
            days=days,
            enabled=bool(row.get("enabled", True)),
        )
    return parsed


def build_torrent_schedules(values: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return canonical persistent schedule settings."""
    return {
        info_hash: schedule.to_dict()
        for info_hash, schedule in parse_torrent_schedules(
            values.get("torrent_schedules", {})
        ).items()
    }


def scheduled_action(
    info_hash: str, schedules: Any, now: datetime | None = None
) -> str | None:
    """Return ``resume`` inside a window, ``pause`` outside it, or ``None``."""
    parsed = (
        schedules if isinstance(schedules, dict)
        and all(isinstance(value, TorrentSchedule) for value in schedules.values())
        else parse_torrent_schedules(schedules)
    )
    schedule = parsed.get(info_hash)
    if schedule is None or not schedule.enabled:
        return None
    current = now or datetime.now()
    current_time = current.time().replace(second=0, microsecond=0)
    minute = current_time.hour * 60 + current_time.minute
    start = schedule.start.hour * 60 + schedule.start.minute
    stop = schedule.stop.hour * 60 + schedule.stop.minute
    weekday = current.weekday()
    if start == stop:
        active = weekday in schedule.days
    elif start < stop:
        active = weekday in schedule.days and start <= minute < stop
    else:
        previous_day = (weekday - 1) % 7
        active = (
            (weekday in schedule.days and minute >= start)
            or (previous_day in schedule.days and minute < stop)
        )
    return "resume" if active else "pause"


def _parse_clock_time(value: Any) -> clock_time | None:
    try:
        parsed = datetime.strptime(str(value or ""), "%H:%M")
        return parsed.time()
    except (TypeError, ValueError):
        return None


def ensure_move_path(path: str) -> str:
    """Create and return a configured completion directory, or empty on failure."""
    value = str(path or "").strip()
    if not value:
        return ""
    try:
        destination = Path(value).expanduser()
        destination.mkdir(parents=True, exist_ok=True)
        return str(destination)
    except OSError:
        return ""
