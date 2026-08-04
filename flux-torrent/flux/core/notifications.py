"""Pure helpers for non-invasive ratio milestone notifications."""

from __future__ import annotations

import math
from collections.abc import Iterable


DEFAULT_RATIO_MILESTONES = (1.0, 2.0)
MAX_RATIO_MILESTONES = 20

_ACTION_LABELS = {
    "review": "Review this torrent",
    "pause": "Consider pausing this torrent",
    "seed": "Continue seeding",
}


def normalize_ratio_milestones(value: object) -> list[float]:
    """Return finite, positive, sorted ratio thresholds without duplicates."""
    if isinstance(value, str):
        values: Iterable[object] = value.replace(";", ",").split(",")
    elif isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray)):
        values = value
    else:
        values = DEFAULT_RATIO_MILESTONES

    normalized: set[float] = set()
    for item in values:
        try:
            milestone = float(item)
        except (TypeError, ValueError):
            continue
        if math.isfinite(milestone) and 0 < milestone <= 10000:
            normalized.add(round(milestone, 3))
    if not normalized:
        normalized.update(DEFAULT_RATIO_MILESTONES)
    return sorted(normalized)[:MAX_RATIO_MILESTONES]


def crossed_ratio_milestones(
    previous: float, current: float, milestones: Iterable[float]
) -> list[float]:
    """Return thresholds crossed since the previous observed ratio."""
    try:
        previous_value = float(previous)
        current_value = float(current)
    except (TypeError, ValueError):
        return []
    if not math.isfinite(previous_value) or not math.isfinite(current_value):
        return []
    if current_value < previous_value:
        return []
    return [
        milestone
        for milestone in normalize_ratio_milestones(milestones)
        if previous_value < milestone <= current_value
    ]


def ratio_notification_body(name: str, milestone: float, action: object) -> str:
    """Format a concise tray notification with an explicit suggested action."""
    action_key = str(action or "review").strip().casefold()
    suggestion = _ACTION_LABELS.get(action_key, _ACTION_LABELS["review"])
    return f"{name} reached a {milestone:.3g} upload/download ratio. {suggestion}."
