"""Persistent 7 x 24 session activity heatmap helpers."""

from datetime import datetime
from typing import Any


DAY_COUNT = 7
HOUR_COUNT = 24
HEATMAP_VERSION = 1


def empty_heatmap() -> list[list[dict[str, int]]]:
    """Return a fresh Monday-first weekday/hour matrix."""
    return [
        [{"download": 0, "upload": 0} for _ in range(HOUR_COUNT)]
        for _ in range(DAY_COUNT)
    ]


def _non_negative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _cell_values(value: Any) -> tuple[int, int]:
    if isinstance(value, dict):
        download = value.get("download", value.get("dl", 0))
        upload = value.get("upload", value.get("ul", 0))
        return _non_negative_int(download), _non_negative_int(upload)
    if isinstance(value, (list, tuple)):
        download = value[0] if len(value) > 0 else 0
        upload = value[1] if len(value) > 1 else 0
        return _non_negative_int(download), _non_negative_int(upload)
    return 0, 0


def normalize_heatmap(value: Any) -> list[list[dict[str, int]]]:
    """Normalize persisted or remote heatmap data into the current schema."""
    if isinstance(value, dict):
        value = value.get("cells", value.get("heatmap", []))

    result = empty_heatmap()
    if not isinstance(value, (list, tuple)):
        return result

    for day_index, row in enumerate(value[:DAY_COUNT]):
        if not isinstance(row, (list, tuple)):
            continue
        for hour_index, cell in enumerate(row[:HOUR_COUNT]):
            download, upload = _cell_values(cell)
            result[day_index][hour_index] = {
                "download": download,
                "upload": upload,
            }
    return result


def record_activity(
    heatmap: list[list[dict[str, int]]],
    when: datetime,
    download_bytes: int = 0,
    upload_bytes: int = 0,
) -> None:
    """Add a non-negative byte sample to the local weekday/hour cell."""
    day_index = min(DAY_COUNT - 1, max(0, when.weekday()))
    hour_index = min(HOUR_COUNT - 1, max(0, when.hour))
    cell = heatmap[day_index][hour_index]
    cell["download"] = _non_negative_int(cell.get("download", 0)) + _non_negative_int(
        download_bytes
    )
    cell["upload"] = _non_negative_int(cell.get("upload", 0)) + _non_negative_int(
        upload_bytes
    )


def heatmap_totals(heatmap: Any) -> tuple[int, int]:
    """Return total download and upload bytes represented by the matrix."""
    normalized = normalize_heatmap(heatmap)
    download = sum(cell["download"] for row in normalized for cell in row)
    upload = sum(cell["upload"] for row in normalized for cell in row)
    return download, upload


def heatmap_peak(heatmap: Any) -> int:
    """Return the largest combined download/upload cell value."""
    normalized = normalize_heatmap(heatmap)
    return max(
        (cell["download"] + cell["upload"] for row in normalized for cell in row),
        default=0,
    )


def current_local_cell() -> tuple[int, int]:
    """Return the current local weekday/hour coordinates."""
    now = datetime.now()
    return now.weekday(), now.hour
