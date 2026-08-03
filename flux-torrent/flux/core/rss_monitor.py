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
import json
import logging
import sqlite3
import hashlib
from pathlib import Path
from typing import Optional, List, Dict
from dataclasses import dataclass, field
from urllib.request import urlopen, Request
from urllib.parse import urlencode
from xml.etree import ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

logger = logging.getLogger(__name__)


_SEASON_EPISODE_RE = re.compile(
    r"(?<![A-Za-z0-9])S(?P<season>\d{1,2})[\s._-]*E(?P<episode>\d{1,3})"
    r"(?:[\s._-]*(?:E|-)[\s._-]*(?P<episode_end>\d{1,3})(?![A-Za-z0-9]))?",
    re.IGNORECASE,
)
_ALT_EPISODE_RE = re.compile(
    r"(?<![A-Za-z0-9])(?P<season>\d{1,2})x(?P<episode>\d{1,3})"
    r"(?:[\s._-]*(?:x|-)[\s._-]*(?P<episode_end>\d{1,3})(?![A-Za-z0-9]))?",
    re.IGNORECASE,
)
_RESOLUTION_RE = re.compile(
    r"(?<![A-Za-z0-9])(4320p|2160p|1440p|1080p|720p|576p|480p|360p|8k|4k)"
    r"(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_CODEC_RE = re.compile(
    r"(?<![A-Za-z0-9])(x\.?264|x\.?265|h[ ._-]?264|h[ ._-]?265|hevc|av1|vp9|xvid)"
    r"(?![A-Za-z0-9])",
    re.IGNORECASE,
)


def _as_text_list(value) -> List[str]:
    """Normalize a string or JSON array into a trimmed text list."""
    if value is None:
        return []
    if isinstance(value, str):
        values = re.split(r"[,;\n]", value)
    elif isinstance(value, (list, tuple, set)):
        values = value
    else:
        values = [value]
    return [str(item).strip() for item in values if str(item).strip()]


def _normalize_show(value: str) -> str:
    value = re.sub(r"[._]+", " ", str(value or ""))
    value = re.sub(r"[\[\](){}]", " ", value)
    return " ".join(value.split()).casefold()


def _normalize_token(value: str) -> str:
    return re.sub(r"[^\w]+", "", str(value or "").casefold(), flags=re.UNICODE)


def _normalize_resolution(value: str) -> str:
    value = str(value or "").strip().casefold().replace(" ", "")
    if value == "4k":
        return "2160p"
    if value == "8k":
        return "4320p"
    if value.isdigit():
        return f"{value}p"
    return value


def _normalize_codec(value: str) -> str:
    value = re.sub(r"[ ._-]+", "", str(value or "").casefold())
    if value in {"h264", "avc", "x264"}:
        return "x264"
    if value in {"h265", "hevc", "x265"}:
        return "x265"
    return value


@dataclass(frozen=True)
class EpisodeMatch:
    """Episode and release metadata parsed from a torrent title."""

    title: str
    show: str
    season: int
    episodes: tuple[int, ...]
    resolution: str = ""
    codec: str = ""
    group: str = ""

    @property
    def episode(self) -> int:
        return self.episodes[0]


def parse_episode_title(title: str) -> Optional[EpisodeMatch]:
    """Parse common ``Show.Name.S01E05`` or ``Show Name 1x05`` releases.

    The parser is deliberately conservative: an episode marker without a show
    prefix is not considered a match, which prevents movie titles containing a
    coincidental season marker from entering a show rule.
    """
    title = str(title or "").strip()
    marker = _SEASON_EPISODE_RE.search(title) or _ALT_EPISODE_RE.search(title)
    if marker is None:
        return None

    prefix = title[:marker.start()]
    leading_group = re.match(r"^\s*[\[(]([^\])}]+)[\])]\s*", title)
    group = leading_group.group(1).strip() if leading_group else ""
    if leading_group:
        show_text = prefix[leading_group.end():].strip(" ._-[]()")
        show_text = re.sub(
            r"^\s*[\[(][^\])}]+[\])]\s*", "", show_text
        ).strip(" ._-[]()")
    else:
        show_text = prefix.strip(" ._-[]()")

    show = re.sub(r"[._]+", " ", show_text)
    show = " ".join(show.split()).strip()
    if not show:
        return None

    if not group:
        bracketed = re.findall(r"\[([^\]]+)\]", title)
        if bracketed:
            group = bracketed[-1].strip()
    if not group:
        hyphen_positions = [match.start() for match in re.finditer("-", title)]
        for position in reversed(hyphen_positions):
            if marker.start() <= position < marker.end():
                continue
            candidate = title[position + 1:].strip(" ._-")
            if not 2 <= len(candidate) <= 40 or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._-]*", candidate
            ):
                continue
            if not re.fullmatch(r"(?:\d{3,4}p|[48]k)", candidate, re.IGNORECASE):
                if not _CODEC_RE.fullmatch(candidate) and not re.fullmatch(
                    r"(?:S?\d{1,2}E\d{1,3}|\d{1,2}x\d{1,3})", candidate, re.IGNORECASE
                ):
                    group = candidate
                    break

    episode_values = [int(marker.group("episode"))]
    if marker.group("episode_end"):
        episode_values.append(int(marker.group("episode_end")))
    resolution_match = _RESOLUTION_RE.search(title)
    codec_match = _CODEC_RE.search(title)

    return EpisodeMatch(
        title=title,
        show=show,
        season=int(marker.group("season")),
        episodes=tuple(episode_values),
        resolution=_normalize_resolution(
            resolution_match.group(1) if resolution_match else ""
        ),
        codec=_normalize_codec(codec_match.group(1) if codec_match else ""),
        group=group,
    )


@dataclass
class ShowRule:
    """Per-show release constraints used by an RSS feed."""

    show: str = ""
    aliases: List[str] = field(default_factory=list)
    resolutions: List[str] = field(default_factory=list)
    codecs: List[str] = field(default_factory=list)
    groups: List[str] = field(default_factory=list)
    seasons: List[int] = field(default_factory=list)
    episodes: List[int] = field(default_factory=list)
    lookup_provider: str = ""
    lookup_id: str = ""

    @classmethod
    def from_dict(cls, value: dict) -> "ShowRule":
        if not isinstance(value, dict):
            return cls()
        raw_seasons = value.get("seasons", value.get("season", []))
        raw_episodes = value.get("episodes", value.get("episode", []))
        seasons = []
        for item in _as_text_list(raw_seasons):
            try:
                seasons.append(int(item))
            except ValueError:
                continue
        episodes = []
        for item in _as_text_list(raw_episodes):
            try:
                episodes.append(int(item))
            except ValueError:
                continue
        return cls(
            show=str(value.get("show", value.get("name", ""))).strip(),
            aliases=_as_text_list(value.get("aliases", [])),
            resolutions=_as_text_list(
                value.get("resolutions", value.get("resolution", []))
            ),
            codecs=_as_text_list(value.get("codecs", value.get("codec", []))),
            groups=_as_text_list(
                value.get("groups", value.get("group_allowlist", value.get("group", [])))
            ),
            seasons=seasons,
            episodes=episodes,
            lookup_provider=str(value.get("lookup_provider", "")).strip().casefold(),
            lookup_id=str(value.get("lookup_id", "")).strip(),
        )

    def to_dict(self) -> dict:
        return {
            "show": self.show,
            "aliases": list(self.aliases),
            "resolutions": list(self.resolutions),
            "codecs": list(self.codecs),
            "groups": list(self.groups),
            "seasons": list(self.seasons),
            "episodes": list(self.episodes),
            "lookup_provider": self.lookup_provider,
            "lookup_id": self.lookup_id,
        }

    def matches(self, episode: EpisodeMatch) -> bool:
        names = {_normalize_show(self.show)}
        names.update(_normalize_show(alias) for alias in self.aliases)
        names.discard("")
        if _normalize_show(episode.show) not in names:
            return False
        if self.seasons and episode.season not in self.seasons:
            return False
        if self.episodes and not any(number in self.episodes for number in episode.episodes):
            return False
        if self.resolutions:
            allowed = {_normalize_resolution(item) for item in self.resolutions}
            if _normalize_resolution(episode.resolution) not in allowed:
                return False
        if self.codecs:
            allowed = {_normalize_codec(item) for item in self.codecs}
            if _normalize_codec(episode.codec) not in allowed:
                return False
        if self.groups:
            allowed = {_normalize_token(item) for item in self.groups}
            if _normalize_token(episode.group) not in allowed:
                return False
        return True


def parse_show_rules(values) -> List[ShowRule]:
    """Parse and discard malformed/empty show-rule entries."""
    if not isinstance(values, list):
        return []
    return [
        rule for value in values
        if isinstance(value, dict)
        for rule in [ShowRule.from_dict(value)]
        if rule.show
    ]


@dataclass(frozen=True)
class ShowLookupResult:
    """Normalized result returned by a TVDB or TMDB series search."""

    provider: str
    identifier: str
    name: str
    year: str = ""
    overview: str = ""


class ShowLookupClient:
    """Small, optional TVDB v4/TMDB TV search client.

    The RSS monitor never calls this client unless a caller explicitly creates
    one and invokes ``search``. API credentials therefore remain opt-in and
    feed polling stays local and non-blocking by default.
    """

    def __init__(self, provider: str, api_key: str, pin: str = "", timeout: float = 10.0,
                 opener=None):
        self.provider = str(provider or "").strip().casefold()
        self.api_key = str(api_key or "").strip()
        self.pin = str(pin or "").strip()
        self.timeout = max(1.0, float(timeout))
        self._opener = opener or urlopen
        self._tvdb_token = ""
        self._cache: Dict[str, List[ShowLookupResult]] = {}

    def search(self, query: str) -> List[ShowLookupResult]:
        query = str(query or "").strip()
        if not query:
            return []
        cache_key = query.casefold()
        if cache_key in self._cache:
            return list(self._cache[cache_key])
        if self.provider not in {"tmdb", "tvdb"}:
            raise ValueError("lookup provider must be 'tmdb' or 'tvdb'")
        if not self.api_key:
            raise ValueError(f"{self.provider.upper()} API credentials are required")

        if self.provider == "tmdb":
            results = self._search_tmdb(query)
        else:
            results = self._search_tvdb(query)
        self._cache[cache_key] = results
        return list(results)

    def _request_json(self, request: Request) -> dict:
        response = self._opener(request, timeout=self.timeout)
        try:
            raw = response.read()
        finally:
            close = getattr(response, "close", None)
            if close:
                close()
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        return json.loads(raw)

    def _search_tmdb(self, query: str) -> List[ShowLookupResult]:
        params = {"query": query, "include_adult": "false", "language": "en-US"}
        headers = {"User-Agent": "FluxTorrent/1.0 RSS"}
        token = self.api_key
        if token.lower().startswith("bearer ") or token.count(".") == 2:
            headers["Authorization"] = token if token.lower().startswith("bearer ") else f"Bearer {token}"
        else:
            params["api_key"] = token
        request = Request(
            "https://api.themoviedb.org/3/search/tv?" + urlencode(params),
            headers=headers,
        )
        payload = self._request_json(request)
        return [self._result("tmdb", item) for item in payload.get("results", [])]

    def _search_tvdb(self, query: str) -> List[ShowLookupResult]:
        if not self._tvdb_token:
            login = Request(
                "https://api4.thetvdb.com/v4/login",
                data=json.dumps({"apikey": self.api_key, "pin": self.pin}).encode(),
                headers={"Content-Type": "application/json", "User-Agent": "FluxTorrent/1.0 RSS"},
            )
            login_payload = self._request_json(login)
            self._tvdb_token = str(login_payload.get("data", {}).get("token", ""))
            if not self._tvdb_token:
                raise ValueError("TVDB login did not return a token")
        request = Request(
            "https://api4.thetvdb.com/v4/search?" + urlencode({"query": query, "type": "series"}),
            headers={
                "Authorization": f"Bearer {self._tvdb_token}",
                "User-Agent": "FluxTorrent/1.0 RSS",
            },
        )
        payload = self._request_json(request)
        return [self._result("tvdb", item) for item in payload.get("data", [])]

    @staticmethod
    def _result(provider: str, value: dict) -> ShowLookupResult:
        first_air = value.get("first_air_date") or value.get("firstAired") or value.get("first_air_time") or ""
        year = str(first_air)[:4] if first_air else str(value.get("year", ""))
        return ShowLookupResult(
            provider=provider,
            identifier=str(value.get("id", value.get("seriesId", ""))),
            name=str(value.get("name", value.get("seriesName", ""))).strip(),
            year=year,
            overview=str(value.get("overview", "")).strip(),
        )


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

    @property
    def episode_match(self) -> Optional[EpisodeMatch]:
        return parse_episode_title(self.title)


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
    show_rules: List[dict] = field(default_factory=list)
    lookup_provider: str = ""
    lookup_api_key: str = ""
    lookup_pin: str = ""

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
        if self.show_rules:
            episode = parse_episode_title(title)
            if episode is None:
                return False
            if not any(rule.matches(episode) for rule in parse_show_rules(self.show_rules)):
                return False
        return True

    def to_dict(self) -> dict:
        return {
            'url': self.url, 'name': self.name, 'enabled': self.enabled,
            'interval_minutes': self.interval_minutes,
            'include_pattern': self.include_pattern,
            'exclude_pattern': self.exclude_pattern,
            'save_path': self.save_path, 'category': self.category,
            'auto_download': self.auto_download,
            'show_rules': self.show_rules,
            'lookup_provider': self.lookup_provider,
            'lookup_api_key': self.lookup_api_key,
            'lookup_pin': self.lookup_pin,
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
    show_lookup_finished = pyqtSignal(str, object)  # query, ShowLookupResult list
    show_lookup_error = pyqtSignal(str, str)        # query, error_message

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

    def lookup_show(self, provider: str, api_key: str, pin: str, query: str):
        """Search TVDB or TMDB without blocking the GUI thread."""
        query = str(query or "").strip()
        future = self._pool.submit(
            lambda: ShowLookupClient(provider, api_key, pin).search(query)
        )
        future.add_done_callback(lambda f, q=query: self._on_lookup_done(q, f))

    def _on_lookup_done(self, query: str, future):
        try:
            results = future.result()
            QTimer.singleShot(0, lambda: self.show_lookup_finished.emit(query, results))
        except Exception as exc:
            message = str(exc)
            logger.warning("RSS show lookup failed for %s: %s", query, message)
            QTimer.singleShot(0, lambda: self.show_lookup_error.emit(query, message))

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
