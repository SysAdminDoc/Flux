"""Integration tests for the embedded remote protocol and desktop client."""

from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from flux.core.remote import RemoteConfig, RemoteControlServer
from flux.core.remote_client import RemoteEndpoint, RemoteSessionClient
from flux.core.session_worker import DetailData, SessionStats
from flux.core.torrent import TorrentFile, TorrentPeer, TorrentSnapshot, TorrentState, TorrentTracker


@dataclass
class _Controller:
    stats: SessionStats
    detail: DetailData

    def __post_init__(self):
        self.calls: list[tuple[str, tuple]] = []

    def get_remote_stats(self):
        return self.stats

    def get_remote_detail(self, info_hash):
        self.calls.append(("detail", (info_hash,)))
        return self.detail

    def get_remote_settings(self):
        return {"remote_port": 8090, "remote_token": "secret", "max_connections": 500}

    def add_magnet(self, *args):
        self.calls.append(("add_magnet", args))
        return True

    def add_torrent_bytes(self, *args):
        self.calls.append(("add_torrent_bytes", args))
        return True

    def pause_all(self):
        self.calls.append(("pause_all", ()))

    def resume_all(self):
        self.calls.append(("resume_all", ()))

    def pause_torrent(self, info_hash):
        self.calls.append(("pause_torrent", (info_hash,)))


class TestRemoteProtocol(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        snapshot = TorrentSnapshot(
            valid=True,
            state=TorrentState.DOWNLOADING,
            name="Remote test",
            info_hash="abc123",
            info_hash_v1="abc123",
            info_hash_v2="def456" * 10 + "def4",
            save_path="C:/Downloads",
            has_metadata=True,
            progress=0.25,
            total_size=400,
            completed_size=100,
            total_downloaded=120,
            total_uploaded=30,
            download_speed=1024,
            upload_speed=256,
            num_seeds=4,
            num_peers=7,
            ratio=0.25,
            eta=30,
            category="test",
            tags=["hd"],
            added_time=123.0,
        )
        stats = SessionStats(
            download_rate=1024,
            upload_rate=256,
            dht_nodes=9,
            dl_history=[1, 2],
            ul_history=[3, 4],
            activity_heatmap=[
                [{"download": 10, "upload": 2}] + [{"download": 0, "upload": 0}] * 23
            ] + [[{"download": 0, "upload": 0}] * 24 for _ in range(6)],
            torrent_count=1,
            torrents=[snapshot],
        )
        detail = DetailData(
            info_hash="abc123",
            files=[TorrentFile(0, "test.bin", 400, 0.25, 4)],
            peers=[TorrentPeer("127.0.0.1", 6881, "client", progress=0.5)],
            trackers=[TorrentTracker("https://tracker.invalid/announce", "Working", 2, 3)],
            pieces=[2, 1, 0],
            piece_length=16,
            peer_piece_owners=[-1, 0, 1],
            peer_piece_labels=["client @ 127.0.0.1:6881", "other @ 127.0.0.2:6882"],
            dl_history=[5],
            ul_history=[6],
            logs=[{
                "timestamp": "2026-08-03 12:00:00",
                "level": "INFO",
                "type": "metadata_received",
                "message": "metadata received",
            }],
        )
        cls.controller = _Controller(stats, detail)
        cls.server = RemoteControlServer(
            RemoteConfig(enabled=True, host="127.0.0.1", port=0, token="secret"),
            cls.controller,
        )
        cls.server.start()
        parsed = urlparse(cls.server.url)
        cls.endpoint = RemoteEndpoint(url=cls.server.url, token="secret")
        cls.port = parsed.port

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def test_root_is_web_ui_and_api_requires_auth(self):
        root = urlopen(f"{self.server.url}").read().decode("utf-8")
        self.assertIn("Flux Remote", root)

        with self.assertRaises(Exception):
            urlopen(f"{self.server.url}api/v1/status")

    def test_client_maps_status_and_detail_dataclasses(self):
        client = RemoteSessionClient(self.endpoint)
        stats = client.fetch_stats()
        self.assertEqual(stats.torrent_count, 1)
        self.assertEqual(stats.torrents[0].name, "Remote test")
        self.assertEqual(stats.torrents[0].state, TorrentState.DOWNLOADING)
        self.assertEqual(stats.torrents[0].tags, ["hd"])
        self.assertEqual(len(stats.torrents[0].info_hash_v2), 64)
        self.assertEqual(stats.activity_heatmap[0][0]["download"], 10)

        detail = client.fetch_detail("abc123")
        self.assertEqual(detail.info_hash, "abc123")
        self.assertEqual(detail.files[0].path, "test.bin")
        self.assertEqual(detail.peers[0].client, "client")
        self.assertEqual(detail.pieces, [2, 1, 0])
        self.assertEqual(detail.peer_piece_owners, [-1, 0, 1])
        self.assertEqual(len(detail.peer_piece_labels), 2)
        self.assertEqual(detail.logs[0]["type"], "metadata_received")

    def test_client_commands_use_existing_controller_boundary(self):
        client = RemoteSessionClient(self.endpoint)
        self.assertTrue(client.add_magnet("magnet:?xt=urn:btih:abc123"))
        self.assertTrue(client.pause_all())
        self.assertTrue(client.resume_all())
        self.assertTrue(client.pause_torrent("abc123"))
        with tempfile.TemporaryDirectory() as temp_dir:
            torrent_path = Path(temp_dir) / "test.torrent"
            torrent_path.write_bytes(b"torrent bytes")
            self.assertTrue(client.add_torrent_file(str(torrent_path)))

        names = [name for name, _ in self.controller.calls]
        self.assertIn("add_magnet", names)
        self.assertIn("pause_all", names)
        self.assertIn("resume_all", names)
        self.assertIn("pause_torrent", names)

    def test_json_torrent_upload_is_bounded_and_forwarded(self):
        data = json.dumps({"torrent_data": "AA==", "filename": "test.torrent"}).encode()
        request = Request(
            f"{self.server.url}api/v1/torrents/add",
            data=data,
            headers={
                "Authorization": "Bearer secret",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        payload = json.loads(urlopen(request).read())
        self.assertEqual(payload["added"], 1)
        upload_calls = [args for name, args in self.controller.calls if name == "add_torrent_bytes"]
        self.assertEqual(upload_calls[-1][0], b"\x00")


class TestRemoteConfig(unittest.TestCase):
    def test_non_loopback_requires_auth(self):
        server = RemoteControlServer(
            RemoteConfig(enabled=True, host="192.0.2.1", port=0),
            object(),
        )
        with self.assertRaises(ValueError):
            server.start()

    def test_client_normalizes_host_without_scheme(self):
        endpoint = RemoteEndpoint(url="localhost:8090")
        self.assertEqual(endpoint.normalized_url(), "http://localhost:8090/")


if __name__ == "__main__":
    unittest.main()
