"""Tests for cross-seed metadata scanning and content verification."""

import hashlib
import tempfile
import unittest
from pathlib import Path

import libtorrent as lt

from flux.core.cross_seed import (
    build_torrent_descriptor,
    find_cross_seed_matches,
    scan_torrent_files,
    verify_library_content,
)


def _write_torrent(folder: Path, filename: str, root_name: str, piece_hash: bytes, tracker: str):
    storage = lt.file_storage()
    storage.add_file(f"{root_name}/episode.mkv", 4)
    creator = lt.create_torrent(storage, 16384)
    creator.set_hash(0, piece_hash)
    creator.add_tracker(tracker)
    path = folder / filename
    path.write_bytes(lt.bencode(creator.generate()))
    return path


class TestCrossSeed(unittest.TestCase):
    def test_scan_and_piece_hash_match(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            content_hash = b"a" * 20
            first = _write_torrent(folder, "first.torrent", "Show", content_hash, "udp://one")
            second = _write_torrent(folder, "second.torrent", "Show.1080p", content_hash, "udp://two")

            descriptors = scan_torrent_files(folder)
            self.assertEqual(len(descriptors), 2)
            matches = find_cross_seed_matches(descriptors)
            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0].method, "piece_hash")
            self.assertEqual(matches[0].candidate_trackers, ("udp://two",))
            self.assertNotEqual(build_torrent_descriptor(first).info_hash_v1,
                                build_torrent_descriptor(second).info_hash_v1)

    def test_same_info_hash_is_exact_match(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            first = _write_torrent(folder, "first.torrent", "Show", b"b" * 20, "udp://one")
            second = _write_torrent(folder, "second.torrent", "Show", b"b" * 20, "udp://two")
            matches = find_cross_seed_matches(scan_torrent_files(folder))
            self.assertEqual(matches[0].method, "info_hash")
            self.assertEqual(build_torrent_descriptor(first).content_key,
                             build_torrent_descriptor(second).content_key)

    def test_piece_size_is_lower_confidence(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            _write_torrent(folder, "first.torrent", "Show", b"c" * 20, "udp://one")
            _write_torrent(folder, "second.torrent", "Show", b"d" * 20, "udp://two")
            matches = find_cross_seed_matches(scan_torrent_files(folder))
            self.assertEqual(matches[0].method, "piece_size")
            self.assertLess(matches[0].confidence, 1.0)

    def test_verify_library_content(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            library = folder / "library" / "Show"
            library.mkdir(parents=True)
            data = b"data"
            (library / "episode.mkv").write_bytes(data)
            torrent = _write_torrent(
                folder, "show.torrent", "Show", hashlib.sha1(data).digest(), "udp://one"
            )
            descriptor = build_torrent_descriptor(torrent)
            self.assertTrue(verify_library_content(descriptor, folder / "library"))
            (library / "episode.mkv").write_bytes(b"bad!")
            self.assertFalse(verify_library_content(descriptor, folder / "library"))


if __name__ == "__main__":
    unittest.main()
