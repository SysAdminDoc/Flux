"""Peer filtering and auto-ban system.
Inspired by qBittorrent Enhanced Edition's peer blocking features."""

import re
import struct
import socket
from typing import List, Tuple, Optional
from dataclasses import dataclass

import libtorrent as lt


@dataclass
class BanRule:
    pattern: str
    reason: str
    enabled: bool = True


class PeerFilter:
    """Filters and auto-bans peers based on client ID, IP ranges, and behavior."""

    # Known leeching/abusive client prefixes (Azureus-style peer IDs)
    KNOWN_LEECHERS = [
        BanRule("-XL", "Xunlei/Thunder"),
        BanRule("-SD", "Thunder (Xunlei variant)"),
        BanRule("-XF", "Xfplay"),
        BanRule("-QD", "QQ Tornado/Whirlwind"),
        BanRule("-BN", "Baidu Net"),
        BanRule("-DL", "Dalunlei"),
        BanRule("-TS", "TorrentStorm"),
        BanRule("-FG", "FlashGet"),
        BanRule("-TT", "TuoTu"),  # Another Chinese leecher
    ]

    # Additional suspicious patterns (full client name matching)
    SUSPICIOUS_CLIENTS = [
        BanRule("Xunlei", "Xunlei client name match"),
        BanRule("Thunder", "Thunder client name match"),
        BanRule("QQDownload", "QQ Download"),
        BanRule("7\\.\\d+\\.\\d+\\.\\d+", "Xunlei version pattern"),
    ]

    def __init__(self):
        self._enabled = True
        self._ban_xunlei = True
        self._ban_qq = True
        self._ban_baidu = True
        self._custom_bans: List[BanRule] = []
        self._ip_blocklist: List[Tuple[int, int]] = []  # (start_ip_int, end_ip_int)
        self._whitelist: List[str] = []  # Whitelisted peer ID prefixes
        self._ban_log: List[dict] = []
        self._max_log = 500

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool):
        self._enabled = value

    def configure(self, settings: dict):
        """Configure from settings dict."""
        self._enabled = settings.get("peer_filter_enabled", True)
        self._ban_xunlei = settings.get("auto_ban_xunlei", True)
        self._ban_qq = settings.get("auto_ban_qq", True)
        self._ban_baidu = settings.get("auto_ban_baidu", True)

    def check_peer(self, peer_id: bytes, client_name: str, ip: str) -> Tuple[bool, str]:
        """Check if a peer should be banned.

        Returns:
            (should_ban, reason)
        """
        if not self._enabled:
            return False, ""

        # Check whitelist first
        peer_id_str = peer_id.decode("ascii", errors="replace")[:8] if peer_id else ""
        for prefix in self._whitelist:
            if peer_id_str.startswith(prefix):
                return False, ""

        # Check known leecher peer IDs
        active_rules = self._get_active_rules()
        for rule in active_rules:
            if peer_id_str.startswith(rule.pattern):
                self._log_ban(ip, client_name, rule.reason)
                return True, rule.reason

        # Check client name patterns
        for rule in self.SUSPICIOUS_CLIENTS:
            if rule.enabled and re.search(rule.pattern, client_name, re.IGNORECASE):
                self._log_ban(ip, client_name, rule.reason)
                return True, rule.reason

        # Check custom ban rules
        for rule in self._custom_bans:
            if rule.enabled:
                if peer_id_str.startswith(rule.pattern) or re.search(rule.pattern, client_name, re.IGNORECASE):
                    self._log_ban(ip, client_name, rule.reason)
                    return True, rule.reason

        # Check IP blocklist
        if self._check_ip_blocked(ip):
            self._log_ban(ip, client_name, "IP blocklist")
            return True, "IP blocklist"

        return False, ""

    def _get_active_rules(self) -> List[BanRule]:
        rules = []
        for rule in self.KNOWN_LEECHERS:
            # Filter based on category settings
            if rule.pattern in ("-XL", "-SD", "-DL") and not self._ban_xunlei:
                continue
            if rule.pattern == "-QD" and not self._ban_qq:
                continue
            if rule.pattern == "-BN" and not self._ban_baidu:
                continue
            if rule.enabled:
                rules.append(rule)
        return rules

    def _check_ip_blocked(self, ip: str) -> bool:
        """Check if IP is in the blocklist."""
        try:
            ip_int = struct.unpack("!I", socket.inet_aton(ip))[0]
            for start, end in self._ip_blocklist:
                if start <= ip_int <= end:
                    return True
        except (socket.error, struct.error):
            pass
        return False

    def _log_ban(self, ip: str, client: str, reason: str):
        import time
        self._ban_log.append({
            "time": time.time(),
            "ip": ip,
            "client": client,
            "reason": reason,
        })
        if len(self._ban_log) > self._max_log:
            self._ban_log.pop(0)

    @property
    def ban_log(self) -> List[dict]:
        return self._ban_log

    def add_custom_rule(self, pattern: str, reason: str):
        self._custom_bans.append(BanRule(pattern, reason))

    def remove_custom_rule(self, index: int):
        if 0 <= index < len(self._custom_bans):
            self._custom_bans.pop(index)

    def add_whitelist(self, prefix: str):
        if prefix not in self._whitelist:
            self._whitelist.append(prefix)

    def load_blocklist_p2p(self, filepath: str):
        """Load a P2P-format IP blocklist (used by PeerGuardian, etc.)
        Format: description:start_ip-end_ip
        """
        self._ip_blocklist.clear()
        try:
            with open(filepath, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    try:
                        _, ip_range = line.rsplit(":", 1)
                        start_str, end_str = ip_range.split("-")
                        start_int = struct.unpack("!I", socket.inet_aton(start_str.strip()))[0]
                        end_int = struct.unpack("!I", socket.inet_aton(end_str.strip()))[0]
                        self._ip_blocklist.append((start_int, end_int))
                    except (ValueError, socket.error, struct.error):
                        continue
        except FileNotFoundError:
            pass

    @property
    def blocklist_count(self) -> int:
        return len(self._ip_blocklist)

    @property
    def stats(self) -> dict:
        return {
            "enabled": self._enabled,
            "rules_active": len(self._get_active_rules()) + len(self._custom_bans),
            "ip_ranges_blocked": len(self._ip_blocklist),
            "total_bans": len(self._ban_log),
        }
