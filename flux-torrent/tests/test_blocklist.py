"""Tests for IP blocklist parsing, refresh failover, and caching."""

import gzip
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

from flux.core.blocklist import (
    MAX_BLOCKLIST_BYTES,
    BlocklistFetchResult,
    fetch_blocklist,
    normalize_blocklist_urls,
    parse_blocklist_ranges,
    write_blocklist_cache,
)
from flux.core.session_worker import SessionWorker


class _Response:
    def __init__(self, payload):
        self.payload = payload
        self.closed = False

    def read(self, limit):
        self.limit = limit
        return self.payload

    def close(self):
        self.closed = True


class _FakeFilter:
    def __init__(self):
        self.rules = []

    def add_rule(self, start, end, flags):
        self.rules.append((start, end, flags))


class _FakeSession:
    def __init__(self):
        self.applied_filter = None

    def set_ip_filter(self, ip_filter):
        self.applied_filter = ip_filter


class TestBlocklistParsing(unittest.TestCase):
    def test_normalizes_mirror_urls(self):
        self.assertEqual(
            normalize_blocklist_urls(
                " https://one.example/list\nftp://bad.example/list\n"
                "https://one.example/list\nhttp://two.example/list"
            ),
            ["https://one.example/list", "http://two.example/list"],
        )

    def test_parses_peer_guardian_plain_and_cidr_ranges(self):
        content = """
        # comment
        Trusted: 1.2.3.0 - 1.2.3.255
        10.0.0.0-10.0.0.255 ; trailing comment
        192.0.2.0/24
        IPv6: 2001:db8::/32
        invalid line
        """
        self.assertEqual(
            parse_blocklist_ranges(content),
            [
                ("1.2.3.0", "1.2.3.255"),
                ("10.0.0.0", "10.0.0.255"),
                ("192.0.2.0", "192.0.2.255"),
                ("2001:db8::", "2001:db8:ffff:ffff:ffff:ffff:ffff:ffff"),
            ],
        )

    def test_deduplicates_and_rejects_reversed_ranges(self):
        self.assertEqual(
            parse_blocklist_ranges("1.2.3.0-1.2.3.1\n1.2.3.0-1.2.3.1\n1.2.3.2-1.2.3.1"),
            [("1.2.3.0", "1.2.3.1")],
        )


class TestBlocklistFetch(unittest.TestCase):
    def test_fails_over_to_gzipped_mirror(self):
        response = _Response(gzip.compress(b"Mirror: 203.0.113.0-203.0.113.255\n"))
        calls = []

        def opener(url, timeout):
            calls.append((url, timeout))
            if len(calls) == 1:
                raise URLError("mirror unavailable")
            return response

        result = fetch_blocklist(
            ["https://first.example/list", "https://second.example/list"],
            opener=opener,
            timeout=7,
        )

        self.assertTrue(result.success)
        self.assertEqual(result.source, "https://second.example/list")
        self.assertEqual(result.ranges, (("203.0.113.0", "203.0.113.255"),))
        self.assertEqual(calls[0][1], 7)
        self.assertEqual(response.limit, MAX_BLOCKLIST_BYTES + 1)
        self.assertTrue(response.closed)

    def test_reports_failure_when_all_mirrors_are_unusable(self):
        result = fetch_blocklist(
            ["https://one.example/list"],
            opener=lambda url, timeout: _Response(b"not a blocklist"),
        )
        self.assertFalse(result.success)
        self.assertIn("no valid IP ranges found", result.error)


class TestBlocklistCache(unittest.TestCase):
    def test_cache_replacement_is_atomic_from_callers_perspective(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "lists" / "blocklist.p2p"
            write_blocklist_cache("new content\n", str(target))
            self.assertEqual(target.read_text(encoding="utf-8"), "new content\n")
            self.assertEqual(list(target.parent.glob("*.tmp")), [])


class TestSessionWorkerBlocklist(unittest.TestCase):
    def test_successful_ranges_replace_filter_before_session_update(self):
        worker = SessionWorker({})
        session = _FakeSession()
        worker._session = session
        fake_filter = _FakeFilter()

        with patch("flux.core.session_worker.lt.ip_filter", return_value=fake_filter):
            count = worker._apply_ip_filter_ranges(
                [("198.51.100.0", "198.51.100.255")], "test"
            )

        self.assertEqual(count, 1)
        self.assertIs(session.applied_filter, fake_filter)
        self.assertEqual(
            fake_filter.rules,
            [("198.51.100.0", "198.51.100.255", 1)],
        )

    def test_fetch_result_is_a_value_only_payload(self):
        result = BlocklistFetchResult(
            success=True,
            source="mirror",
            content="1.2.3.0-1.2.3.1",
            ranges=(("1.2.3.0", "1.2.3.1"),),
        )
        self.assertEqual(result.ranges[0][0], "1.2.3.0")


if __name__ == "__main__":
    unittest.main()
