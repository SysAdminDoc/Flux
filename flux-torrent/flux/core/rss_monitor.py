"""RSS Feed Monitor - poll RSS/Atom feeds for new torrents.

Supports:
  - Multiple feeds with per-feed polling interval
  - Regex-based title filters (include/exclude)
  - Auto-download matching items as magnets or .torrent URLs
  - Persistent history to avoid duplicate downloads
  - Background HTTP fetches (non-blocking GUI)
"""

import re
import time
import logging
import sqlite3
import hashlib
from pathlib import Path
from typing import Optional, List, Dict
from dataclasses import dataclass
from urllib.request import urlopen, Request
from urllib.error import URLError
from xml.etree import ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

logger = logging.getLogger(__name__)


@dataclass
class FeedItem:
    """Single parsed item from an RSS/Atom feed."""
    title: str = ""
    link: str = ""
    magnet: str = ""
    torrent_url: str = ""
    pub_date: str = ""
    size: int = 0
    guid: str = ""

    @property
    def download_url(self) -> str:
        if self.magnet:
            return self.magnet
        if self.torrent_url:
            return self.torrent_url
        if self.link and (self.link.endswith('.torrent') or 'magnet:' in self.link):
            return self.link
        return ""

    @property
    def unique_id(self) -> str:
        if self.guid:
            return self.guid
        raw = self.title + self.link + self.magnet
        return hashlib.sha256(raw.encode()).hexdigest()[:32]


@dataclass
class FeedConfig:
    """Configuration for a single RSS feed."""
    url: str = ""
    name: str = ""
    enabled: bool = True
    interval_minutes: int = 30
    include_pattern: str = ""
    exclude_pattern: str = ""
    save_path: str = ""
    category: str = ""
    auto_download: bool = True

    def matches(self, title: str) -> bool:
        if self.include_pattern:
            try:
                if not re.search(self.include_pattern, title, re.IGNORECASE):
                    return False
            except re.error:
                return False
        if self.exclude_pattern:
            try:
                if re.search(self.exclude_pattern, title, re.IGNORECASE):
                    return False
            except re.error:
                pass
        return True

    def to_dict(self) -> dict:
        return {
            'url': self.url, 'name': self.name, 'enabled': self.enabled,
            'interval_minutes': self.interval_minutes,
            'include_pattern': self.include_pattern,
            'exclude_pattern': self.exclude_pattern,
            'save_path': self.save_path, 'category': self.category,
            'auto_download': self.auto_download,
        }

    @classmethod
    def from_dict(cls, d: dict) -> 'FeedConfig':
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def parse_feed(xml_text: str) -> List[FeedItem]:
    """Parse RSS 2.0 or Atom feed XML into FeedItems."""
    items = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        logger.error(f"Feed XML parse error: {e}")
        return items

    if root.tag == 'rss' or root.find('channel') is not None:
        items = _parse_rss(root)
    elif root.tag.endswith('feed') or root.find('{http://www.w3.org/2005/Atom}entry') is not None:
        items = _parse_atom(root)
    else:
        logger.warning(f"Unknown feed format: root tag={root.tag}")

    return items


def _parse_rss(root) -> List[FeedItem]:
    items = []
    channel = root.find('channel')
    if channel is None:
        return items

    for item_el in channel.findall('item'):
        fi = FeedItem()
        fi.title = _text(item_el, 'title')
        fi.link = _text(item_el, 'link')
        fi.guid = _text(item_el, 'guid') or fi.link
        fi.pub_date = _text(item_el, 'pubDate')

        enc = item_el.find('enclosure')
        if enc is not None:
            url = enc.get('url', '')
            if url.endswith('.torrent') or 'torrent' in enc.get('type', ''):
                fi.torrent_url = url
            try:
                fi.size = int(enc.get('length', 0))
            except (ValueError, TypeError):
                pass

        for child in item_el:
            tag = child.tag.split('}')[-1] if '}' in child.tag else child.tag
            text = (child.text or '').strip()
            if tag == 'magnetURI' or (text.startswith('magnet:')):
                fi.magnet = text
            elif tag == 'link' and text.startswith('magnet:'):
                fi.magnet = text

        if not fi.magnet and fi.link.startswith('magnet:'):
            fi.magnet = fi.link

        items.append(fi)

    return items


def _parse_atom(root) -> List[FeedItem]:
    items = []
    ns = '{http://www.w3.org/2005/Atom}'

    for entry in root.findall(f'{ns}entry'):
        fi = FeedItem()
        title_el = entry.find(f'{ns}title')
        fi.title = title_el.text.strip() if title_el is not None and title_el.text else ""

        fi.guid = _text_ns(entry, f'{ns}id')
        fi.pub_date = _text_ns(entry, f'{ns}updated') or _text_ns(entry, f'{ns}published')

        for link_el in entry.findall(f'{ns}link'):
            href = link_el.get('href', '')
            rel = link_el.get('rel', '')
            ltype = link_el.get('type', '')

            if href.startswith('magnet:'):
                fi.magnet = href
            elif href.endswith('.torrent') or 'torrent' in ltype:
                fi.torrent_url = href
            elif rel == 'alternate' or rel == '':
                fi.link = href

        items.append(fi)

    return items


def _text(parent, tag: str) -> str:
    el = parent.find(tag)
    return el.text.strip() if el is not None and el.text else ""


def _text_ns(parent, tag: str) -> str:
    el = parent.find(tag)
    return el.text.strip() if el is not None and el.text else ""


def _fetch_feed_sync(url: str) -> str:
    """Fetch feed XML on a background thread (called via ThreadPoolExecutor)."""
    req = Request(url, headers={'User-Agent': 'FluxTorrent/1.0 RSS'})
    with urlopen(req, timeout=30) as resp:
        return resp.read().decode('utf-8', errors='replace')


class RSSMonitor(QObject):
    """Monitors RSS feeds and emits signals for new matching items.

    HTTP fetches run on a thread pool so the GUI never blocks.
    """

    new_torrent = pyqtSignal(str, str, str)   # download_url, save_path, category
    feed_checked = pyqtSignal(str, int, int)  # feed_url, total_items, new_items
    feed_error = pyqtSignal(str, str)         # feed_url, error_message

    def __init__(self, parent=None):
        super().__init__(parent)
        self._feeds: Dict[str, FeedConfig] = {}
        self._timers: Dict[str, QTimer] = {}
        self._history_db: Optional[sqlite3.Connection] = None
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="rss")
        self._init_db()

    def _init_db(self):
        config_dir = Path.home() / ".flux-torrent"
        config_dir.mkdir(parents=True, exist_ok=True)
        self._history_db = sqlite3.connect(str(config_dir / "rss_history.db"))
        self._history_db.execute("""
            CREATE TABLE IF NOT EXISTS seen_items (
                item_id TEXT PRIMARY KEY,
                feed_url TEXT NOT NULL,
                title TEXT DEFAULT '',
                seen_time REAL NOT NULL
            )
        """)
        self._history_db.commit()

    def add_feed(self, config: FeedConfig):
        self._feeds[config.url] = config
        self._restart_timer(config)
        logger.info(f"RSS feed added: {config.name or config.url} "
                     f"(interval={config.interval_minutes}m)")

    def remove_feed(self, url: str):
        if url in self._timers:
            self._timers[url].stop()
            del self._timers[url]
        self._feeds.pop(url, None)
        logger.info(f"RSS feed removed: {url}")

    def get_feeds(self) -> List[FeedConfig]:
        return list(self._feeds.values())

    def _restart_timer(self, config: FeedConfig):
        if config.url in self._timers:
            self._timers[config.url].stop()

        if not config.enabled:
            return

        timer = QTimer(self)
        timer.timeout.connect(lambda url=config.url: self._schedule_fetch(url))
        timer.start(config.interval_minutes * 60 * 1000)
        self._timers[config.url] = timer

        # Immediate check
        QTimer.singleShot(2000, lambda url=config.url: self._schedule_fetch(url))

    def check_all_now(self):
        for url in self._feeds:
            self._schedule_fetch(url)

    def _schedule_fetch(self, url: str):
        """Submit HTTP fetch to thread pool so GUI doesn't block."""
        config = self._feeds.get(url)
        if not config or not config.enabled:
            return

        future = self._pool.submit(_fetch_feed_sync, url)
        future.add_done_callback(lambda f, u=url: self._on_fetch_done(u, f))

    def _on_fetch_done(self, url: str, future):
        """Callback from thread pool - schedule processing on GUI thread."""
        try:
            xml_text = future.result()
            # Use QTimer.singleShot(0) to bounce back to the GUI thread
            QTimer.singleShot(0, lambda: self._process_feed(url, xml_text))
        except Exception as e:
            msg = str(e)
            logger.warning(f"RSS fetch failed for {url}: {msg}")
            QTimer.singleShot(0, lambda: self.feed_error.emit(url, msg))

    def _process_feed(self, url: str, xml_text: str):
        """Process fetched XML on the GUI thread (signal emission is safe)."""
        config = self._feeds.get(url)
        if not config:
            return

        items = parse_feed(xml_text)
        new_count = 0

        for item in items:
            if not item.download_url:
                continue
            if not config.matches(item.title):
                continue
            if self._is_seen(item.unique_id):
                continue

            self._mark_seen(item.unique_id, url, item.title)
            new_count += 1

            if config.auto_download:
                save_path = config.save_path or ""
                category = config.category or ""
                self.new_torrent.emit(item.download_url, save_path, category)
                logger.info(f"RSS auto-download: {item.title}")

        self.feed_checked.emit(url, len(items), new_count)
        logger.debug(f"RSS check {config.name or url}: {len(items)} items, {new_count} new")

    def _is_seen(self, item_id: str) -> bool:
        if not self._history_db:
            return False
        cursor = self._history_db.execute(
            "SELECT 1 FROM seen_items WHERE item_id = ?", (item_id,))
        return cursor.fetchone() is not None

    def _mark_seen(self, item_id: str, feed_url: str, title: str):
        if not self._history_db:
            return
        self._history_db.execute(
            "INSERT OR IGNORE INTO seen_items (item_id, feed_url, title, seen_time) "
            "VALUES (?, ?, ?, ?)",
            (item_id, feed_url, title, time.time()))
        self._history_db.commit()

    def cleanup_old_history(self, max_age_days: int = 90):
        if not self._history_db:
            return
        cutoff = time.time() - (max_age_days * 86400)
        self._history_db.execute(
            "DELETE FROM seen_items WHERE seen_time < ?", (cutoff,))
        self._history_db.commit()

    def save_config(self) -> list:
        return [f.to_dict() for f in self._feeds.values()]

    def load_config(self, feed_list: list):
        for d in feed_list:
            try:
                config = FeedConfig.from_dict(d)
                if config.url:
                    self.add_feed(config)
            except Exception as e:
                logger.error(f"Failed to load RSS feed config: {e}")

    def stop_all(self):
        """Stop all timers, shutdown pool, close DB."""
        for timer in self._timers.values():
            timer.stop()
        self._timers.clear()
        self._pool.shutdown(wait=False)
        if self._history_db:
            self._history_db.close()
            self._history_db = None

    # Backwards compat alias
    stop = stop_all
