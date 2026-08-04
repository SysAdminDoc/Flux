"""Stats export — CSV/JSON export of torrent session statistics.

Provides functions to export current session state and historical
data in formats suitable for Grafana dashboards and data analysis.
"""

import csv
import json
import io
import time
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def export_torrent_list_csv(snapshots: list) -> str:
    """Export current torrent list to CSV string.

    Args:
        snapshots: List of TorrentSnapshot dataclass instances.

    Returns:
        CSV-formatted string.
    """
    output = io.StringIO()
    writer = csv.writer(output)

    # Header
    writer.writerow([
        "Name", "Info Hash", "State", "Progress (%)", "Size (bytes)",
        "Downloaded (bytes)", "Uploaded (bytes)", "DL Speed (B/s)",
        "UL Speed (B/s)", "Seeds", "Peers", "Ratio", "ETA (s)",
        "Category", "Tags", "Save Path", "Added",
    ])

    for snap in snapshots:
        try:
            added_str = ""
            if snap.added_time > 0:
                added_str = datetime.fromtimestamp(snap.added_time).isoformat()

            writer.writerow([
                snap.name,
                snap.info_hash,
                snap.state.display_name if hasattr(snap.state, 'display_name') else str(snap.state),
                f"{snap.progress * 100:.1f}",
                snap.total_size,
                snap.total_downloaded,
                snap.total_uploaded,
                snap.download_speed,
                snap.upload_speed,
                snap.num_seeds,
                snap.num_peers,
                f"{snap.ratio:.3f}",
                snap.eta,
                snap.category,
                ";".join(snap.tags) if snap.tags else "",
                snap.save_path,
                added_str,
            ])
        except Exception as e:
            logger.debug(f"CSV export row error: {e}")

    return output.getvalue()


def export_torrent_list_json(snapshots: list) -> str:
    """Export current torrent list to JSON string.

    Args:
        snapshots: List of TorrentSnapshot dataclass instances.

    Returns:
        Pretty-printed JSON string.
    """
    data = {
        "export_time": datetime.now().isoformat(),
        "export_timestamp": time.time(),
        "torrent_count": len(snapshots),
        "torrents": [],
    }

    for snap in snapshots:
        try:
            added_str = ""
            if snap.added_time > 0:
                added_str = datetime.fromtimestamp(snap.added_time).isoformat()

            entry = {
                "name": snap.name,
                "info_hash": snap.info_hash,
                "state": snap.state.display_name if hasattr(snap.state, 'display_name') else str(snap.state),
                "progress_pct": round(snap.progress * 100, 1),
                "total_size": snap.total_size,
                "total_downloaded": snap.total_downloaded,
                "total_uploaded": snap.total_uploaded,
                "download_speed": snap.download_speed,
                "upload_speed": snap.upload_speed,
                "num_seeds": snap.num_seeds,
                "num_peers": snap.num_peers,
                "ratio": round(snap.ratio, 3),
                "eta_seconds": snap.eta,
                "category": snap.category,
                "tags": snap.tags or [],
                "save_path": snap.save_path,
                "added_time": added_str,
            }
            data["torrents"].append(entry)
        except Exception as e:
            logger.debug(f"JSON export entry error: {e}")

    return json.dumps(data, indent=2)


def export_session_stats_json(stats, snapshots: list) -> str:
    """Export full session statistics including speed history.

    Args:
        stats: SessionStats dataclass instance.
        snapshots: List of TorrentSnapshot instances (from stats.torrents).

    Returns:
        Pretty-printed JSON string with session and per-torrent data.
    """
    data = {
        "export_time": datetime.now().isoformat(),
        "history_interval_seconds": 1,
        "session": {
            "download_rate": stats.download_rate,
            "upload_rate": stats.upload_rate,
            "dht_nodes": stats.dht_nodes,
            "torrent_count": stats.torrent_count,
            "download_history": stats.dl_history,
            "upload_history": stats.ul_history,
        },
        "history": [
            {
                "age_seconds": len(stats.dl_history) - index - 1,
                "download_rate": stats.dl_history[index],
                "upload_rate": stats.ul_history[index] if index < len(stats.ul_history) else 0,
            }
            for index in range(len(stats.dl_history))
        ],
        "torrents": [],
    }

    for snap in snapshots:
        try:
            entry = {
                "name": snap.name,
                "info_hash": snap.info_hash,
                "state": snap.state.display_name if hasattr(snap.state, 'display_name') else str(snap.state),
                "progress_pct": round(snap.progress * 100, 1),
                "total_size": snap.total_size,
                "download_speed": snap.download_speed,
                "upload_speed": snap.upload_speed,
                "ratio": round(snap.ratio, 3),
            }
            data["torrents"].append(entry)
        except Exception:
            pass

    return json.dumps(data, indent=2)


def export_session_stats_csv(stats) -> str:
    """Export the rolling one-second transfer history as Grafana-friendly CSV."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Age (seconds)",
        "Download Rate (bytes/s)",
        "Upload Rate (bytes/s)",
        "DHT Nodes",
        "Torrent Count",
    ])
    download_history = list(stats.dl_history or [])
    upload_history = list(stats.ul_history or [])
    sample_count = max(len(download_history), len(upload_history))
    for index in range(sample_count):
        writer.writerow([
            sample_count - index - 1,
            download_history[index] if index < len(download_history) else 0,
            upload_history[index] if index < len(upload_history) else 0,
            stats.dht_nodes,
            stats.torrent_count,
        ])
    return output.getvalue()


def save_export(content: str, filepath: str) -> bool:
    """Write export content to a file.

    Args:
        content: String content (CSV or JSON).
        filepath: Destination file path.

    Returns:
        True if saved successfully.
    """
    try:
        Path(filepath).write_text(content, encoding="utf-8")
        logger.info(f"Stats exported to {filepath}")
        return True
    except Exception as e:
        logger.error(f"Failed to save export: {e}")
        return False
