"""Unit tests for flux.core.stats_export."""
import sys
import os
import json
import csv
import io
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import unittest
from unittest.mock import MagicMock
from flux.core.stats_export import (
    export_torrent_list_csv,
    export_torrent_list_json,
    export_session_stats_json,
    save_export,
)


class MockState:
    def __init__(self, name="Downloading"):
        self._name = name
        self.display_name = name


class MockSnapshot:
    def __init__(self, **kwargs):
        self.name = kwargs.get("name", "Test Torrent")
        self.info_hash = kwargs.get("info_hash", "abc123def456")
        self.state = MockState(kwargs.get("state", "Downloading"))
        self.progress = kwargs.get("progress", 0.5)
        self.total_size = kwargs.get("total_size", 1024 * 1024 * 100)
        self.total_downloaded = kwargs.get("total_downloaded", 50 * 1024 * 1024)
        self.total_uploaded = kwargs.get("total_uploaded", 25 * 1024 * 1024)
        self.download_speed = kwargs.get("download_speed", 1024 * 500)
        self.upload_speed = kwargs.get("upload_speed", 1024 * 100)
        self.num_seeds = kwargs.get("num_seeds", 10)
        self.num_peers = kwargs.get("num_peers", 25)
        self.ratio = kwargs.get("ratio", 0.5)
        self.eta = kwargs.get("eta", 300)
        self.category = kwargs.get("category", "movies")
        self.tags = kwargs.get("tags", ["hd", "1080p"])
        self.save_path = kwargs.get("save_path", "/tmp/downloads")
        self.added_time = kwargs.get("added_time", 1700000000.0)


class MockSessionStats:
    def __init__(self, torrents=None):
        self.download_rate = 1024 * 500
        self.upload_rate = 1024 * 100
        self.dht_nodes = 150
        self.torrent_count = len(torrents) if torrents else 0
        self.torrents = torrents or []
        self.dl_history = [100, 200, 300, 400, 500]
        self.ul_history = [50, 100, 150, 200, 250]


class TestExportCSV(unittest.TestCase):
    def test_csv_with_torrents(self):
        snaps = [MockSnapshot(name="Movie.mkv"), MockSnapshot(name="Show.mp4")]
        result = export_torrent_list_csv(snaps)
        reader = csv.reader(io.StringIO(result))
        rows = list(reader)
        self.assertEqual(len(rows), 3)  # header + 2 data rows
        self.assertEqual(rows[0][0], "Name")
        self.assertEqual(rows[1][0], "Movie.mkv")
        self.assertEqual(rows[2][0], "Show.mp4")

    def test_csv_empty_list(self):
        result = export_torrent_list_csv([])
        reader = csv.reader(io.StringIO(result))
        rows = list(reader)
        self.assertEqual(len(rows), 1)  # header only

    def test_csv_contains_progress(self):
        snaps = [MockSnapshot(progress=0.75)]
        result = export_torrent_list_csv(snaps)
        self.assertIn("75.0", result)

    def test_csv_tags_serialized(self):
        snaps = [MockSnapshot(tags=["hd", "sci-fi"])]
        result = export_torrent_list_csv(snaps)
        self.assertIn("hd;sci-fi", result)

    def test_csv_no_tags(self):
        snaps = [MockSnapshot(tags=[])]
        result = export_torrent_list_csv(snaps)
        # Should not crash, tags column should be empty
        reader = csv.reader(io.StringIO(result))
        rows = list(reader)
        self.assertEqual(len(rows), 2)


class TestExportJSON(unittest.TestCase):
    def test_json_structure(self):
        snaps = [MockSnapshot(name="Test")]
        result = export_torrent_list_json(snaps)
        data = json.loads(result)
        self.assertIn("export_time", data)
        self.assertIn("torrent_count", data)
        self.assertEqual(data["torrent_count"], 1)
        self.assertEqual(len(data["torrents"]), 1)
        self.assertEqual(data["torrents"][0]["name"], "Test")

    def test_json_empty(self):
        result = export_torrent_list_json([])
        data = json.loads(result)
        self.assertEqual(data["torrent_count"], 0)
        self.assertEqual(len(data["torrents"]), 0)

    def test_json_fields(self):
        snaps = [MockSnapshot(ratio=1.234)]
        result = export_torrent_list_json(snaps)
        data = json.loads(result)
        t = data["torrents"][0]
        self.assertIn("info_hash", t)
        self.assertIn("state", t)
        self.assertIn("progress_pct", t)
        self.assertIn("ratio", t)
        self.assertEqual(t["ratio"], 1.234)


class TestExportSessionStats(unittest.TestCase):
    def test_session_json(self):
        snaps = [MockSnapshot()]
        stats = MockSessionStats(torrents=snaps)
        result = export_session_stats_json(stats, snaps)
        data = json.loads(result)
        self.assertIn("session", data)
        self.assertIn("torrents", data)
        self.assertEqual(data["session"]["download_rate"], 1024 * 500)
        self.assertEqual(data["session"]["dht_nodes"], 150)
        self.assertEqual(len(data["session"]["download_history"]), 5)


class TestSaveExport(unittest.TestCase):
    def test_save_to_file(self):
        content = "name,size\ntest,100\n"
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name
        try:
            result = save_export(content, path)
            self.assertTrue(result)
            with open(path, "r", encoding="utf-8") as f:
                self.assertEqual(f.read(), content)
        finally:
            os.unlink(path)

    def test_save_to_invalid_path(self):
        result = save_export("data", "/nonexistent/dir/file.csv")
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
