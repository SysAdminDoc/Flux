"""Validated label-scoped automation rules."""

from __future__ import annotations

import json
from dataclasses import dataclass
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


def build_label_rules(values: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the canonical persistent shape for the settings dialog."""
    return [rule.to_dict() for rule in parse_label_rules(values.get("label_rules", []))]


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
