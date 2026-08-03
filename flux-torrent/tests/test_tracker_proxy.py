"""Tests for per-tracker proxy routing and announce decoding."""

import unittest
import socketserver
import threading

from flux.core.tracker_proxy import (
    TrackerAnnounceClient,
    TrackerAnnounceRequest,
    TrackerProxy,
    build_announce_url,
    parse_tracker_proxy,
    parse_tracker_proxy_rules,
    redact_tracker_proxy_rules,
    tracker_proxy_rules_to_settings,
)


class TestTrackerProxyRules(unittest.TestCase):
    def test_rules_are_validated_and_deduplicated(self):
        rules = parse_tracker_proxy_rules([
            {
                "tracker_url": "HTTPS://Tracker.Example:443/announce/",
                "proxy_url": "socks5://user:secret@127.0.0.1:1080",
            },
            {
                "tracker_url": "udp://tracker.example:6969/announce",
                "proxy_url": "http://127.0.0.1:8080",
            },
            {
                "tracker_url": "https://tracker.example/announce",
                "proxy_url": "http://127.0.0.1:8080",
            },
            {"tracker_url": "https://bad.example/announce", "proxy_url": "not-a-url"},
        ])

        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0].proxy.scheme, "http")
        self.assertEqual(rules[0].proxy.port, 8080)
        self.assertEqual(rules[0].tracker_url, "https://tracker.example/announce")

    def test_proxy_credentials_are_redacted_for_remote_settings(self):
        raw = [{
            "tracker_url": "https://tracker.example/announce",
            "proxy_url": "socks5://user:secret@proxy.example:1080",
        }]
        safe = redact_tracker_proxy_rules(raw)
        self.assertEqual(safe[0]["proxy_url"], "socks5://user@proxy.example:1080")
        self.assertNotIn("secret", str(safe))
        self.assertEqual(
            tracker_proxy_rules_to_settings(raw)[0]["proxy_url"],
            "socks5://user:secret@proxy.example:1080",
        )

    def test_proxy_parser_supports_http_and_socks5_defaults(self):
        socks = parse_tracker_proxy("socks5://proxy.example")
        http = parse_tracker_proxy("https://proxy.example")
        self.assertEqual((socks.scheme, socks.port), ("socks5", 1080))
        self.assertEqual((http.scheme, http.port), ("https", 8080))
        self.assertIsNone(parse_tracker_proxy("ftp://proxy.example:21"))


class TestTrackerAnnounce(unittest.TestCase):
    def test_http_proxy_tunnel_carries_announce_request(self):
        requests = []

        class ProxyHandler(socketserver.StreamRequestHandler):
            def handle(self):
                self.assert_request(self.rfile.readline().decode("ascii"), "CONNECT")
                while self.rfile.readline().strip():
                    pass
                self.wfile.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                self.wfile.flush()
                requests.append(self.rfile.readline().decode("ascii"))
                while self.rfile.readline().strip():
                    pass
                body = b"d8:intervali60e5:peers6:\x7f\x00\x00\x01\x1a\xe1e"
                self.wfile.write(
                    b"HTTP/1.1 200 OK\r\nContent-Length: "
                    + str(len(body)).encode("ascii")
                    + b"\r\n\r\n"
                    + body
                )
                self.wfile.flush()

            @staticmethod
            def assert_request(line, prefix):
                if not line.startswith(prefix):
                    raise AssertionError(line)

        server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), ProxyHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = TrackerAnnounceRequest(
                bytes(range(20)), b"-FX1000-123456789012", 6881, 0, 0, 1
            )
            result = TrackerAnnounceClient(2).announce(
                "http://tracker.invalid/announce",
                TrackerProxy("http", "127.0.0.1", server.server_address[1]),
                request,
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertTrue(result.ok, result)
        self.assertEqual(result.peers, (("127.0.0.1", 6881),))
        self.assertIn("%00%01%02", requests[0])

    def test_announce_url_percent_encodes_binary_fields(self):
        request = TrackerAnnounceRequest(
            info_hash=bytes(range(20)), peer_id=b"-FX1000-123456789012",
            port=6881, uploaded=2, downloaded=3, left=4, event="started",
        )
        url = build_announce_url("https://tracker.example/announce?passkey=x", request)
        self.assertIn("passkey=x&info_hash=%00%01%02", url)
        self.assertIn("peer_id=-FX1000-123456789012", url)
        self.assertIn("event=started", url)

    def test_decode_response_supports_compact_v4_and_v6_peers(self):
        body = (
            b"d8:intervali120e8:completei3e10:incompletei4e"
            b"5:peers6:\x7f\x00\x00\x01\x1a\xe1"
            b"6:peers618:\x00\x00\x00\x00\x00\x00\x00\x00"
            b"\x00\x00\x00\x00\x00\x00\x00\x01\x1a\xe2e"
        )
        result = TrackerAnnounceClient.decode_response(body)
        self.assertTrue(result.ok)
        self.assertEqual(result.interval, 120)
        self.assertEqual(result.seeds, 3)
        self.assertEqual(result.peers_available, 4)
        self.assertIn(("127.0.0.1", 6881), result.peers)
        self.assertIn(("::1", 6882), result.peers)

    def test_decode_response_reports_tracker_failure(self):
        result = TrackerAnnounceClient.decode_response(
            b"d14:failure reason11:bad requeste"
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.failure, "bad request")


if __name__ == "__main__":
    unittest.main()
