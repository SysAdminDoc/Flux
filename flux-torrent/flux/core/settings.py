"""Settings management for Flux Torrent Client using SQLite."""

import json
import sqlite3
from pathlib import Path
from typing import Any, Optional


class Settings:
    """Persistent settings storage backed by SQLite."""

    DEFAULTS = {
        # Connection
        "listen_port": 6881,
        "upnp_enabled": True,
        "natpmp_enabled": True,
        "dht_enabled": True,
        "pex_enabled": True,
        "lsd_enabled": True,
        "encryption_mode": 1,  # 0=disabled, 1=prefer, 2=require
        "proxy_type": 0,  # 0=none, 1=socks4, 2=socks5, 3=http
        "proxy_host": "",
        "proxy_port": 0,
        "proxy_auth": False,
        "proxy_user": "",
        "proxy_pass": "",
        # Bandwidth
        "max_download_speed": 0,  # 0 = unlimited (bytes/s)
        "max_upload_speed": 13312,  # 13 KiB/s = 13312 bytes/s
        "max_connections": 500,
        "max_connections_per_torrent": 100,
        "max_uploads": 20,
        "max_uploads_per_torrent": 5,
        # Queue
        "max_active_downloads": 5,
        "max_active_uploads": 5,
        "max_active_torrents": 10,
        # Completion
        "on_complete_action": 1,  # 0=nothing, 1=pause, 2=remove
        # Seeding
        "max_ratio": 2.0,
        "max_seed_time": 0,  # 0 = unlimited (minutes)
        "ratio_action": 0,  # 0=pause, 1=remove
        # Paths
        "default_save_path": str(Path.home() / "Downloads"),
        "temp_path_enabled": False,
        "temp_path": "",
        "move_completed_enabled": False,
        "move_completed_path": "",
        # Behavior
        "start_minimized": False,
        "minimize_to_tray": True,
        "close_to_tray": True,
        "confirm_on_delete": True,
        "confirm_on_delete_files": True,
        "sequential_download_default": False,
        "add_paused_default": False,
        "pre_allocate_storage": False,
        # Peer filtering
        "peer_filter_enabled": True,
        "auto_ban_xunlei": True,
        "auto_ban_qq": True,
        "auto_ban_baidu": True,
        "auto_update_trackers": False,
        "tracker_list_url": "https://raw.githubusercontent.com/ngosang/trackerslist/master/trackers_best.txt",
        # UI
        "theme": "dark",
        "sidebar_collapsed": False,
        "detail_panel_height": 260,
        "window_geometry": "",
        "window_state": "",
        "column_widths": "",
        "column_order": "",
        "sort_column": "added",
        "sort_order": "desc",
        "show_speed_in_title": True,
    }

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            config_dir = Path.home() / ".flux-torrent"
            config_dir.mkdir(parents=True, exist_ok=True)
            db_path = str(config_dir / "settings.db")

        self._db_path = db_path
        self._conn = sqlite3.connect(db_path)
        self._init_db()

    def _init_db(self):
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS categories (
                name TEXT PRIMARY KEY,
                save_path TEXT DEFAULT '',
                color TEXT DEFAULT '#6b7280'
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS tags (
                name TEXT PRIMARY KEY
            )
        """)
        self._conn.commit()

    def get(self, key: str, default: Any = None) -> Any:
        cursor = self._conn.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = cursor.fetchone()
        if row is None:
            if default is not None:
                return default
            return self.DEFAULTS.get(key)
        try:
            return json.loads(row[0])
        except (json.JSONDecodeError, TypeError):
            return row[0]

    def set(self, key: str, value: Any):
        serialized = json.dumps(value)
        self._conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, serialized)
        )
        self._conn.commit()

    def get_all(self) -> dict:
        result = dict(self.DEFAULTS)
        cursor = self._conn.execute("SELECT key, value FROM settings")
        for key, value in cursor.fetchall():
            try:
                result[key] = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                result[key] = value
        return result

    def get_categories(self) -> list:
        cursor = self._conn.execute("SELECT name, save_path, color FROM categories ORDER BY name")
        return [{"name": r[0], "save_path": r[1], "color": r[2]} for r in cursor.fetchall()]

    def add_category(self, name: str, save_path: str = "", color: str = "#6b7280"):
        self._conn.execute(
            "INSERT OR REPLACE INTO categories (name, save_path, color) VALUES (?, ?, ?)",
            (name, save_path, color)
        )
        self._conn.commit()

    def remove_category(self, name: str):
        self._conn.execute("DELETE FROM categories WHERE name = ?", (name,))
        self._conn.commit()

    def get_tags(self) -> list:
        cursor = self._conn.execute("SELECT name FROM tags ORDER BY name")
        return [r[0] for r in cursor.fetchall()]

    def add_tag(self, name: str):
        self._conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (name,))
        self._conn.commit()

    def remove_tag(self, name: str):
        self._conn.execute("DELETE FROM tags WHERE name = ?", (name,))
        self._conn.commit()

    def close(self):
        self._conn.close()
