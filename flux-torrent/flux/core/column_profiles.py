"""Validation and defaults for per-filter torrent-table column profiles."""

from typing import Any


COLUMN_KEYS = (
    "state_icon", "name", "size", "progress", "status", "seeds", "peers",
    "dl_speed", "ul_speed", "eta", "ratio", "category", "added",
)

_DEFAULT_VISIBLE = {
    "default": COLUMN_KEYS,
    "downloading": (
        "state_icon", "name", "size", "progress", "status", "seeds", "peers",
        "dl_speed", "ul_speed", "eta", "category",
    ),
    "seeding": (
        "state_icon", "name", "size", "progress", "status", "seeds", "peers",
        "ul_speed", "ratio", "category", "added",
    ),
    "completed": (
        "state_icon", "name", "size", "progress", "status", "ratio", "category", "added",
    ),
}


def default_column_profile(profile_key: str) -> dict[str, Any]:
    """Return a fresh default profile for a filter key."""
    visible = list(_DEFAULT_VISIBLE.get(profile_key, COLUMN_KEYS))
    return {
        "visible": visible,
        "order": list(COLUMN_KEYS),
        "widths": {},
    }


def normalize_column_profile(value, profile_key: str = "default") -> dict[str, Any]:
    """Validate one profile while retaining all known columns exactly once."""
    default = default_column_profile(profile_key)
    value = value if isinstance(value, dict) else {}
    visible = [key for key in value.get("visible", default["visible"]) if key in COLUMN_KEYS]
    if not visible:
        visible = list(default["visible"])
    visible = list(dict.fromkeys(visible))

    order = [key for key in value.get("order", COLUMN_KEYS) if key in COLUMN_KEYS]
    order = list(dict.fromkeys(order))
    order.extend(key for key in COLUMN_KEYS if key not in order)

    widths = {}
    raw_widths = value.get("widths", {})
    if isinstance(raw_widths, dict):
        for key, width in raw_widths.items():
            if key not in COLUMN_KEYS:
                continue
            try:
                widths[key] = max(32, min(int(width), 2000))
            except (TypeError, ValueError):
                continue
    return {"visible": visible, "order": order, "widths": widths}


def normalize_column_profiles(value) -> dict[str, dict[str, Any]]:
    """Validate all known profile keys and ignore unknown user data."""
    value = value if isinstance(value, dict) else {}
    return {
        key: normalize_column_profile(value.get(key, {}), key)
        for key in ("default", "downloading", "seeding", "completed")
    }
