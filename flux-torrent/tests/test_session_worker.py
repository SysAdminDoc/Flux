"""Tests for session worker data structures."""

import unittest
from unittest.mock import patch
from flux.core.session_worker import (
    SessionStats,
    DetailData,
    _apply_private_tracker_profile,
    _PRIVATE_TRACKER_FLAGS,
    _MAX_TORRENT_LOG_ENTRIES,
    _alert_log_entry,
    SessionWorker,
)


class TestSessionStats(unittest.TestCase):
    """Test SessionStats dataclass."""

    def test_defaults(self):
        stats = SessionStats()
        self.assertEqual(stats.download_rate, 0)
        self.assertEqual(stats.upload_rate, 0)
        self.assertEqual(stats.dht_nodes, 0)
        self.assertEqual(stats.torrent_count, 0)
        self.assertIsInstance(stats.dl_history, list)
        self.assertIsInstance(stats.ul_history, list)
        self.assertIsInstance(stats.activity_heatmap, list)
        self.assertIsInstance(stats.torrents, list)

    def test_with_data(self):
        stats = SessionStats(
            download_rate=1024000,
            upload_rate=512000,
            dht_nodes=150,
            dl_history=[100, 200, 300],
            ul_history=[50, 100],
            torrent_count=5,
        )
        self.assertEqual(stats.download_rate, 1024000)
        self.assertEqual(len(stats.dl_history), 3)
        self.assertEqual(stats.torrent_count, 5)

    def test_list_independence(self):
        """Ensure default lists aren't shared between instances."""
        s1 = SessionStats()
        s2 = SessionStats()
        s1.dl_history.append(100)
        self.assertEqual(len(s2.dl_history), 0)

    def test_torrents_list_independence(self):
        s1 = SessionStats()
        s2 = SessionStats()
        s1.torrents.append("dummy")
        self.assertEqual(len(s2.torrents), 0)


class TestDetailData(unittest.TestCase):
    """Test DetailData dataclass."""

    def test_defaults(self):
        d = DetailData()
        self.assertEqual(d.info_hash, "")
        self.assertEqual(d.piece_length, 0)
        self.assertIsInstance(d.files, list)
        self.assertIsInstance(d.peers, list)
        self.assertIsInstance(d.trackers, list)
        self.assertIsInstance(d.pieces, list)
        self.assertIsInstance(d.dl_history, list)
        self.assertIsInstance(d.ul_history, list)
        self.assertIsInstance(d.logs, list)

    def test_list_independence(self):
        d1 = DetailData()
        d2 = DetailData()
        d1.files.append("test")
        self.assertEqual(len(d2.files), 0)
        d1.logs.append({"message": "one"})
        self.assertEqual(len(d2.logs), 0)


class torrent_error_alert:
    """Minimal alert-shaped object for the log buffer tests."""

    def __init__(self, message="connection failed"):
        self.handle = object()
        self._message = message

    def message(self):
        return self._message


class TestTorrentAlertLog(unittest.TestCase):
    def test_alert_entry_normalizes_message_and_level(self):
        entry = _alert_log_entry(torrent_error_alert("line one\nline two"), "now")
        self.assertEqual(entry["timestamp"], "now")
        self.assertEqual(entry["level"], "ERROR")
        self.assertEqual(entry["type"], "torrent_error")
        self.assertEqual(entry["message"], "line one line two")

    def test_worker_keeps_logs_separate_and_bounded_by_hash(self):
        worker = SessionWorker({})
        with patch("flux.core.session_worker.get_info_hashes", return_value=("hash-a", "", "")):
            worker._record_alert_log(torrent_error_alert("first"))
        with patch("flux.core.session_worker.get_info_hashes", return_value=("hash-b", "", "")):
            worker._record_alert_log(torrent_error_alert("other"))

        self.assertEqual([item["message"] for item in worker._torrent_logs["hash-a"]], ["first"])
        self.assertEqual([item["message"] for item in worker._torrent_logs["hash-b"]], ["other"])

        with patch("flux.core.session_worker.get_info_hashes", return_value=("hash-a", "", "")):
            for index in range(_MAX_TORRENT_LOG_ENTRIES + 1):
                worker._record_alert_log(torrent_error_alert(str(index)))
        entries = worker._torrent_logs["hash-a"]
        self.assertEqual(len(entries), _MAX_TORRENT_LOG_ENTRIES)
        self.assertEqual(entries[-1]["message"], str(_MAX_TORRENT_LOG_ENTRIES))


class _FakeHandle:
    def __init__(self):
        self.set_flags_calls = []
        self.unset_flags_calls = []
        self.max_uploads = None

    def set_flags(self, flags):
        self.set_flags_calls.append(flags)

    def unset_flags(self, flags):
        self.unset_flags_calls.append(flags)

    def set_max_uploads(self, value):
        self.max_uploads = value


class _FakeTorrent:
    def __init__(self):
        self.handle = _FakeHandle()


class TestPrivateTrackerProfile(unittest.TestCase):
    def test_profile_sets_privacy_flags_and_slot_cap(self):
        torrent = _FakeTorrent()
        _apply_private_tracker_profile(torrent, {
            "private_tracker_profile": True,
            "private_tracker_unchoke_slots": 3,
        })
        self.assertEqual(torrent.handle.set_flags_calls, [_PRIVATE_TRACKER_FLAGS])
        self.assertEqual(torrent.handle.max_uploads, 3)

    def test_profile_disable_restores_normal_flags(self):
        torrent = _FakeTorrent()
        _apply_private_tracker_profile(torrent, {
            "private_tracker_profile": False,
            "max_uploads_per_torrent": 7,
        })
        self.assertEqual(torrent.handle.unset_flags_calls, [_PRIVATE_TRACKER_FLAGS])
        self.assertEqual(torrent.handle.max_uploads, 7)


if __name__ == "__main__":
    unittest.main()
