"""Tests for session worker data structures."""

import unittest
from flux.core.session_worker import (
    SessionStats,
    DetailData,
    _apply_private_tracker_profile,
    _PRIVATE_TRACKER_FLAGS,
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

    def test_list_independence(self):
        d1 = DetailData()
        d2 = DetailData()
        d1.files.append("test")
        self.assertEqual(len(d2.files), 0)


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
