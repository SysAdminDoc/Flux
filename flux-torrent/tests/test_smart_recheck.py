"""Tests for timestamp-based partial re-check planning."""

import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from flux.core.smart_recheck import (
    build_smart_recheck_plan,
    capture_file_fingerprints,
    dirty_file_indices,
    verify_piece,
)


class _FakeStorage:
    flag_pad_file = 1

    def __init__(self):
        self.paths = ["root\\alpha.bin", "root\\beta.bin"]

    def num_files(self):
        return len(self.paths)

    def file_path(self, index):
        return self.paths[index]

    def file_flags(self, index):
        return 0


class _FakeTorrentInfo:
    def __init__(self, storage):
        self._storage = storage
        self._pieces = [
            [SimpleNamespace(file_index=0, offset=0, size=4)],
            [SimpleNamespace(file_index=0, offset=4, size=4)],
            [SimpleNamespace(file_index=1, offset=0, size=4)],
        ]

    def files(self):
        return self._storage

    def num_pieces(self):
        return len(self._pieces)

    def piece_size(self, piece):
        return sum(item.size for item in self._pieces[piece])

    def map_block(self, piece, offset, size):
        return self._pieces[piece]

    def hash_for_piece(self, piece):
        payloads = [b"abcd", b"efgh", b"ijkl"]
        return hashlib.sha1(payloads[piece]).digest()

    def v1(self):
        return True


class _FakeV2TorrentInfo(_FakeTorrentInfo):
    def info_hashes(self):
        return SimpleNamespace(has_v1=lambda: False, has_v2=lambda: True)


class TestSmartRecheck(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.save_path = Path(self.temp_dir.name)
        (self.save_path / "root").mkdir()
        (self.save_path / "root" / "alpha.bin").write_bytes(b"abcdefgh")
        (self.save_path / "root" / "beta.bin").write_bytes(b"ijkl")
        self.torrent_info = _FakeTorrentInfo(_FakeStorage())

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_unchanged_files_are_skipped(self):
        baseline = capture_file_fingerprints(self.torrent_info, self.save_path)
        plan = build_smart_recheck_plan(self.torrent_info, self.save_path, baseline)
        self.assertTrue(plan.skip)
        self.assertEqual(plan.dirty_pieces, ())

    def test_changed_file_maps_only_to_overlapping_pieces(self):
        baseline = capture_file_fingerprints(self.torrent_info, self.save_path)
        target = self.save_path / "root" / "alpha.bin"
        target.write_bytes(b"abCdefgh")
        stat = target.stat()
        os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10_000_000_000))
        current = capture_file_fingerprints(self.torrent_info, self.save_path)
        self.assertEqual(dirty_file_indices(baseline, current), (0,))

        plan = build_smart_recheck_plan(self.torrent_info, self.save_path, baseline)
        self.assertEqual(plan.dirty_files, (0,))
        self.assertEqual(plan.dirty_pieces, (0, 1))

    def test_missing_file_stays_dirty_even_when_missing_at_baseline(self):
        target = self.save_path / "root" / "beta.bin"
        target.unlink()
        baseline = capture_file_fingerprints(self.torrent_info, self.save_path)
        plan = build_smart_recheck_plan(self.torrent_info, self.save_path, baseline)
        self.assertEqual(plan.dirty_files, (1,))
        self.assertEqual(plan.dirty_pieces, (2,))

    def test_piece_hash_detects_corruption_and_missing_data(self):
        self.assertEqual(
            verify_piece(self.torrent_info, self.save_path, 0).matches,
            True,
        )
        (self.save_path / "root" / "alpha.bin").write_bytes(b"xxxxefgh")
        self.assertEqual(
            verify_piece(self.torrent_info, self.save_path, 0).matches,
            False,
        )
        (self.save_path / "root" / "alpha.bin").unlink()
        result = verify_piece(self.torrent_info, self.save_path, 0)
        self.assertFalse(result.available)
        self.assertIsNone(result.matches)

    def test_no_baseline_requests_conservative_full_recheck(self):
        plan = build_smart_recheck_plan(self.torrent_info, self.save_path, None)
        self.assertTrue(plan.requires_full_recheck)
        self.assertIn("baseline", plan.reason)

    def test_v2_only_metadata_uses_full_recheck_fallback(self):
        baseline = capture_file_fingerprints(self.torrent_info, self.save_path)
        target = self.save_path / "root" / "alpha.bin"
        target.write_bytes(b"abCdefgh")
        stat = target.stat()
        os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10_000_000_000))
        plan = build_smart_recheck_plan(
            _FakeV2TorrentInfo(_FakeStorage()), self.save_path, baseline
        )
        self.assertTrue(plan.requires_full_recheck)
        self.assertIn("v2-only", plan.reason)


if __name__ == "__main__":
    unittest.main()
