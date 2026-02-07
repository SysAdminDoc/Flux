"""Formatting utilities for Flux Torrent Client."""

import time
from datetime import datetime


def format_bytes(num_bytes: int, decimals: int = 1) -> str:
    """Format bytes into human-readable string."""
    if num_bytes == 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    k = 1024.0
    for unit in units:
        if abs(num_bytes) < k:
            return f"{num_bytes:.{decimals}f} {unit}"
        num_bytes /= k
    return f"{num_bytes:.{decimals}f} PB"


def format_speed(bps: int) -> str:
    """Format bytes per second into speed string."""
    if bps <= 0:
        return "--"
    return format_bytes(bps) + "/s"


def format_eta(seconds: int) -> str:
    """Format seconds into human-readable ETA."""
    if seconds <= 0:
        return "--"
    if seconds > 86400:
        d = seconds // 86400
        h = (seconds % 86400) // 3600
        return f"{d}d {h}h"
    if seconds > 3600:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        return f"{h}h {m}m"
    if seconds > 60:
        m = seconds // 60
        s = seconds % 60
        return f"{m}m {s}s"
    return f"{seconds}s"


def format_ratio(ratio: float) -> str:
    """Format share ratio."""
    if ratio < 0:
        return "--"
    return f"{ratio:.2f}"


def format_timestamp(ts: float) -> str:
    """Format Unix timestamp to readable date/time."""
    if ts <= 0:
        return "--"
    dt = datetime.fromtimestamp(ts)
    return dt.strftime("%b %d, %Y %I:%M %p")


def format_progress(progress: float) -> str:
    """Format progress as percentage."""
    return f"{progress * 100:.1f}%"
