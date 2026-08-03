"""Validation and persistence helpers for named torrent-creation presets."""

from dataclasses import dataclass, field
from typing import List


@dataclass
class CreationPreset:
    """Reusable options for the Create Torrent dialog."""

    name: str = ""
    piece_size: int = 0
    trackers: List[str] = field(default_factory=list)
    private: bool = False
    comment: str = ""
    web_seeds: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, value: dict) -> "CreationPreset":
        if not isinstance(value, dict):
            return cls()
        try:
            piece_size = max(0, int(value.get("piece_size", value.get("piece_length", 0)) or 0))
        except (TypeError, ValueError):
            piece_size = 0
        return cls(
            name=str(value.get("name", "")).strip(),
            piece_size=piece_size,
            trackers=_clean_lines(value.get("trackers", [])),
            private=bool(value.get("private", value.get("private_flag", False))),
            comment=str(value.get("comment", "")).strip(),
            web_seeds=_clean_lines(value.get("web_seeds", value.get("url_seeds", []))),
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "piece_size": self.piece_size,
            "trackers": list(self.trackers),
            "private": self.private,
            "comment": self.comment,
            "web_seeds": list(self.web_seeds),
        }


def _clean_lines(value) -> List[str]:
    if isinstance(value, str):
        values = value.splitlines()
    elif isinstance(value, (list, tuple, set)):
        values = value
    else:
        values = []
    return list(dict.fromkeys(str(item).strip() for item in values if str(item).strip()))


def parse_creation_presets(values) -> List[CreationPreset]:
    """Return valid, uniquely named presets in their stored order."""
    result = []
    names = set()
    if not isinstance(values, list):
        return result
    for value in values:
        preset = CreationPreset.from_dict(value)
        name_key = preset.name.casefold()
        if not preset.name or name_key in names:
            continue
        names.add(name_key)
        result.append(preset)
    return result


def upsert_creation_preset(values, preset: CreationPreset) -> List[CreationPreset]:
    """Replace a same-named preset or append a new one."""
    result = parse_creation_presets(values)
    key = preset.name.casefold()
    for index, existing in enumerate(result):
        if existing.name.casefold() == key:
            result[index] = preset
            return result
    result.append(preset)
    return result
