"""Tests for session worker data structures."""

import unittest
from types import SimpleNamespace
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
from flux.core.smart_recheck import PieceVerification, SmartRecheckPlan


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


class TestRatioMilestoneEmission(unittest.TestCase):
    def test_first_observation_is_baseline_and_crossings_emit_once(self):
        worker = SessionWorker({
            "ratio_notifications_enabled": True,
            "ratio_notification_milestones": [1.0, 2.0],
        })
        emitted = []
        worker.ratio_milestone.connect(lambda info_hash, ratio: emitted.append((info_hash, ratio)))
        worker._emit_ratio_milestones(SimpleNamespace(info_hash="hash", ratio=0.5))
        worker._emit_ratio_milestones(SimpleNamespace(info_hash="hash", ratio=2.1))
        worker._emit_ratio_milestones(SimpleNamespace(info_hash="hash", ratio=2.5))
        self.assertEqual(emitted, [("hash", 1.0), ("hash", 2.0)])
        worker.shutdown()


class TestWebhookQueue(unittest.TestCase):
    def test_invalid_webhook_is_rejected_before_background_submission(self):
        worker = SessionWorker({
            "webhook_enabled": True,
            "webhook_url": "http://discord.com/api/webhooks/id/token",
            "webhook_events": ["on_finish"],
        })
        emitted = []
        worker.webhook_status.connect(lambda success, message: emitted.append((success, message)))

        worker._queue_webhook("on_finish", {"name": "Done"})

        self.assertEqual(emitted, [(False, "Webhook URL is invalid")])
        worker.shutdown()


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


class _RecheckHandle:
    def __init__(self, torrent_info=True, paused=False, have_piece=True):
        self._torrent_info = torrent_info
        self._status = SimpleNamespace(paused=paused)
        self._have_piece = have_piece
        self.pause_calls = 0
        self.resume_calls = 0
        self.add_piece_calls = []

    def torrent_file(self):
        return self._torrent_info

    def status(self):
        return self._status

    def pause(self):
        self.pause_calls += 1

    def resume(self):
        self.resume_calls += 1

    def have_piece(self, piece):
        return self._have_piece

    def add_piece(self, piece, data, flags):
        self.add_piece_calls.append((piece, data, flags))


class _RecheckTorrent:
    def __init__(self, handle):
        self.handle = handle
        self.save_path = "C:/downloads"
        self.full_recheck_calls = 0

    def force_recheck(self):
        self.full_recheck_calls += 1


class TestSmartRecheckWorker(unittest.TestCase):
    def test_missing_metadata_uses_full_recheck(self):
        worker = SessionWorker({})
        torrent = _RecheckTorrent(_RecheckHandle(torrent_info=None))
        worker._torrents["hash"] = torrent

        worker.force_recheck("hash")

        self.assertEqual(torrent.full_recheck_calls, 1)

    def test_clean_plan_skips_without_touching_handle(self):
        worker = SessionWorker({})
        handle = _RecheckHandle()
        torrent = _RecheckTorrent(handle)
        worker._torrents["hash"] = torrent
        plan = SmartRecheckPlan((), (), (), skip=True, reason="unchanged")

        with patch("flux.core.session_worker.build_smart_recheck_plan", return_value=plan):
            worker.force_recheck("hash")

        self.assertEqual(torrent.full_recheck_calls, 0)
        self.assertEqual(handle.pause_calls, 0)
        self.assertEqual(handle.resume_calls, 0)

    def test_dirty_mismatch_is_injected_only_for_that_piece(self):
        worker = SessionWorker({})
        handle = _RecheckHandle(have_piece=True)
        torrent = _RecheckTorrent(handle)
        worker._torrents["hash"] = torrent
        plan = SmartRecheckPlan((), (0,), (3,), reason="one changed file")
        result = PieceVerification(piece=3, available=True, matches=False)

        with patch("flux.core.session_worker.build_smart_recheck_plan", return_value=plan), \
                patch("flux.core.session_worker.verify_pieces", return_value=(result,)), \
                patch("flux.core.session_worker.read_piece_data", return_value=b"bad"):
            worker.force_recheck("hash")

        self.assertEqual(torrent.full_recheck_calls, 0)
        self.assertEqual(handle.pause_calls, 1)
        self.assertEqual(handle.resume_calls, 1)
        self.assertEqual(handle.add_piece_calls[0][:2], (3, b"bad"))


if __name__ == "__main__":
    unittest.main()
