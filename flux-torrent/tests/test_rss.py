"""Tests for RSS feed parser."""

import unittest
import json
from flux.core.rss_monitor import parse_feed, FeedItem, FeedConfig
from flux.core.rss_monitor import ShowLookupClient, parse_episode_title


class TestFeedConfig(unittest.TestCase):
    """Test FeedConfig filtering and serialization."""

    def test_matches_no_filters(self):
        config = FeedConfig(url="http://test.com")
        self.assertTrue(config.matches("anything"))

    def test_matches_include(self):
        config = FeedConfig(url="http://test.com", include_pattern=r"ubuntu.*22\.04")
        self.assertTrue(config.matches("Ubuntu Desktop 22.04.3 LTS"))
        self.assertFalse(config.matches("Fedora 39"))

    def test_matches_exclude(self):
        config = FeedConfig(url="http://test.com", exclude_pattern=r"cam|ts|hdcam")
        self.assertTrue(config.matches("Movie.2024.1080p.BluRay"))
        self.assertFalse(config.matches("Movie.2024.HDCAM"))

    def test_matches_both_filters(self):
        config = FeedConfig(
            url="http://test.com",
            include_pattern=r"1080p",
            exclude_pattern=r"cam"
        )
        self.assertTrue(config.matches("Movie.1080p.BluRay"))
        self.assertFalse(config.matches("Movie.1080p.HDCAM"))
        self.assertFalse(config.matches("Movie.720p.BluRay"))

    def test_matches_invalid_regex(self):
        config = FeedConfig(url="http://test.com", include_pattern=r"[invalid")
        self.assertFalse(config.matches("anything"))

    def test_serialization(self):
        config = FeedConfig(
            url="http://test.com/rss",
            name="Test Feed",
            interval_minutes=15,
            include_pattern="test",
            auto_download=False,
        )
        d = config.to_dict()
        restored = FeedConfig.from_dict(d)
        self.assertEqual(restored.url, config.url)
        self.assertEqual(restored.name, config.name)
        self.assertEqual(restored.interval_minutes, 15)
        self.assertEqual(restored.include_pattern, "test")
        self.assertFalse(restored.auto_download)

    def test_show_rules_match_episode_metadata(self):
        config = FeedConfig(
            url="http://test.com/rss",
            show_rules=[{
                "show": "The Expanse",
                "aliases": ["Expanse"],
                "resolutions": ["1080p"],
                "codecs": ["x265"],
                "groups": ["QxR"],
            }],
        )
        self.assertTrue(config.matches("The.Expanse.S02E03.1080p.WEB.x265-QxR"))
        self.assertTrue(config.matches("Expanse.S02E03.1080p.WEB.HEVC-QxR"))
        self.assertFalse(config.matches("The.Expanse.S02E03.720p.WEB.x265-QxR"))
        self.assertFalse(config.matches("The.Expanse.S02E03.1080p.WEB.x265-Other"))

    def test_show_rule_serialization(self):
        config = FeedConfig(
            url="http://test.com/rss",
            show_rules=[{"show": "Example", "seasons": [2], "episodes": [3]}],
            lookup_provider="tmdb",
            lookup_api_key="token",
        )
        restored = FeedConfig.from_dict(config.to_dict())
        self.assertEqual(restored.show_rules[0]["show"], "Example")
        self.assertEqual(restored.lookup_provider, "tmdb")
        self.assertEqual(restored.lookup_api_key, "token")


class TestEpisodeParser(unittest.TestCase):
    def test_parse_season_episode_release(self):
        parsed = parse_episode_title("[Subs] The.Show.S01E05-E06.1080p.WEB-DL.x265-GROUP")
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.show, "The Show")
        self.assertEqual(parsed.season, 1)
        self.assertEqual(parsed.episodes, (5, 6))
        self.assertEqual(parsed.resolution, "1080p")
        self.assertEqual(parsed.codec, "x265")
        self.assertEqual(parsed.group, "Subs")

    def test_parse_alt_episode_marker(self):
        parsed = parse_episode_title("The Show - 1x05 - 720p HEVC")
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.show, "The Show")
        self.assertEqual(parsed.episode, 5)
        self.assertEqual(parsed.codec, "x265")

    def test_parse_requires_show_prefix(self):
        self.assertIsNone(parse_episode_title("S01E05.1080p.WEB"))


class _FakeResponse:
    def __init__(self, value):
        self._body = json.dumps(value).encode("utf-8")

    def read(self):
        return self._body

    def close(self):
        pass


class TestShowLookup(unittest.TestCase):
    def test_tmdb_search(self):
        requests = []

        def opener(request, timeout):
            requests.append(request)
            return _FakeResponse({
                "results": [{
                    "id": 42,
                    "name": "The Expanse",
                    "first_air_date": "2015-12-14",
                    "overview": "A science fiction series.",
                }]
            })

        results = ShowLookupClient("tmdb", "api-key", opener=opener).search("Expanse")
        self.assertEqual(results[0].identifier, "42")
        self.assertEqual(results[0].year, "2015")
        self.assertIn("api_key=api-key", requests[0].full_url)

    def test_tvdb_login_then_search(self):
        requests = []

        def opener(request, timeout):
            requests.append(request)
            if request.full_url.endswith("/login"):
                return _FakeResponse({"data": {"token": "session-token"}})
            return _FakeResponse({
                "data": [{"id": 99, "name": "The Expanse", "year": "2015"}]
            })

        results = ShowLookupClient("tvdb", "api-key", "1234", opener=opener).search("Expanse")
        self.assertEqual(results[0].identifier, "99")
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[1].headers["Authorization"], "Bearer session-token")


class TestRSSParser(unittest.TestCase):
    """Test RSS 2.0 and Atom parsing."""

    RSS_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
    <channel>
        <title>Test Feed</title>
        <item>
            <title>Ubuntu 22.04.3 LTS Desktop</title>
            <link>https://example.com/ubuntu</link>
            <guid>item-001</guid>
            <pubDate>Mon, 01 Jan 2024 12:00:00 +0000</pubDate>
            <enclosure url="https://example.com/ubuntu.torrent" type="application/x-bittorrent" length="3456789"/>
        </item>
        <item>
            <title>Magnet Item</title>
            <link>magnet:?xt=urn:btih:abc123</link>
            <guid>item-002</guid>
        </item>
        <item>
            <title>No Download</title>
            <link>https://example.com/page</link>
            <guid>item-003</guid>
        </item>
    </channel>
    </rss>"""

    ATOM_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
        <title>Atom Feed</title>
        <entry>
            <title>Atom Torrent</title>
            <id>atom-001</id>
            <updated>2024-01-15T10:00:00Z</updated>
            <link href="https://example.com/file.torrent" type="application/x-bittorrent"/>
            <link href="https://example.com/page" rel="alternate"/>
        </entry>
        <entry>
            <title>Atom Magnet</title>
            <id>atom-002</id>
            <link href="magnet:?xt=urn:btih:def456" rel="alternate"/>
        </entry>
    </feed>"""

    def test_parse_rss_count(self):
        items = parse_feed(self.RSS_SAMPLE)
        self.assertEqual(len(items), 3)

    def test_parse_rss_enclosure(self):
        items = parse_feed(self.RSS_SAMPLE)
        self.assertEqual(items[0].title, "Ubuntu 22.04.3 LTS Desktop")
        self.assertEqual(items[0].torrent_url, "https://example.com/ubuntu.torrent")
        self.assertEqual(items[0].size, 3456789)
        self.assertEqual(items[0].guid, "item-001")

    def test_parse_rss_magnet(self):
        items = parse_feed(self.RSS_SAMPLE)
        self.assertEqual(items[1].magnet, "magnet:?xt=urn:btih:abc123")

    def test_parse_rss_download_url_priority(self):
        items = parse_feed(self.RSS_SAMPLE)
        # Enclosure torrent should be the download URL
        self.assertEqual(items[0].download_url, "https://example.com/ubuntu.torrent")
        # Magnet should be preferred
        self.assertEqual(items[1].download_url, "magnet:?xt=urn:btih:abc123")
        # No downloadable link
        self.assertEqual(items[2].download_url, "")

    def test_parse_atom_count(self):
        items = parse_feed(self.ATOM_SAMPLE)
        self.assertEqual(len(items), 2)

    def test_parse_atom_torrent_link(self):
        items = parse_feed(self.ATOM_SAMPLE)
        self.assertEqual(items[0].title, "Atom Torrent")
        self.assertEqual(items[0].torrent_url, "https://example.com/file.torrent")
        self.assertEqual(items[0].guid, "atom-001")

    def test_parse_atom_magnet(self):
        items = parse_feed(self.ATOM_SAMPLE)
        self.assertEqual(items[1].magnet, "magnet:?xt=urn:btih:def456")

    def test_parse_invalid_xml(self):
        items = parse_feed("not xml at all")
        self.assertEqual(items, [])

    def test_parse_empty_feed(self):
        xml = '<?xml version="1.0"?><rss version="2.0"><channel><title>Empty</title></channel></rss>'
        items = parse_feed(xml)
        self.assertEqual(items, [])

    def test_feed_item_unique_id(self):
        item = FeedItem(title="Test", link="http://example.com", guid="my-guid")
        self.assertEqual(item.unique_id, "my-guid")

        item2 = FeedItem(title="Test", link="http://example.com")
        self.assertTrue(len(item2.unique_id) > 0)
        self.assertNotEqual(item2.unique_id, "my-guid")


class TestCreateTorrentImport(unittest.TestCase):
    """Test that torrent creator helpers work."""

    def test_auto_piece_size(self):
        # Just ensure import works and basic piece size calc
        from flux.gui.dialogs.create_torrent import _auto_piece_size
        # Small file -> 16KB minimum
        self.assertGreaterEqual(_auto_piece_size(100), 16384)
        # 1 GB -> should be ~1MB pieces
        size = _auto_piece_size(1024 * 1024 * 1024)
        self.assertGreaterEqual(size, 524288)
        self.assertLessEqual(size, 16777216)

    def test_scan_files_nonexistent(self):
        from flux.gui.dialogs.create_torrent import _scan_files
        # Non-existent path should not crash
        try:
            _scan_files("/nonexistent/path/12345")
        except (FileNotFoundError, OSError):
            pass  # Expected


if __name__ == "__main__":
    unittest.main()
