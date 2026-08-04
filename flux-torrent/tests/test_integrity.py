"""Tests for SHA-256 sidecar manifest generation."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from flux.core.integrity import (
    IntegrityError,
    build_manifest_plan,
    generate_manifest,
    manifest_path,
)


class _Storage:
    flag_pad_file = 1

    def __init__(self, paths, sizes, flags=None):
        self.paths = paths
        self.sizes = sizes
        self.flags = flags or [0] * len(paths)

    def num_files(self):
        return len(self.paths)

    def file_path(self, index):
        return self.paths[index]

    def file_size(self, index):
        return self.sizes[index]

    def file_flags(self, index):
        return self.flags[index]


class _TorrentInfo:
    def __init__(self, storage):
        self._storage = storage

    def files(self):
        return self._storage


class TestIntegrityManifest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        (self.root / "root").mkdir()
        self.alpha = self.root / "root" / "alpha.bin"
        self.beta = self.root / "root" / "beta.bin"
        self.alpha.write_bytes(b"alpha")
        self.beta.write_bytes(b"beta")
        self.info = _TorrentInfo(
            _Storage(
                ["root\\alpha.bin", "root\\beta.bin"],
                [5, 4],
            )
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_manifest_contains_stable_file_hashes_and_metadata(self):
        output = manifest_path(self.root, "Movies: Season/1")
        plan = build_manifest_plan(
            self.info,
            self.root,
            "abcdef123456",
            "Movies: Season/1",
        )
        result = generate_manifest(plan)

        self.assertTrue(result.success)
        self.assertEqual(Path(result.output_path), output)
        payload = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(payload["version"], 1)
        self.assertEqual(payload["algorithm"], "SHA-256")
        self.assertEqual(payload["info_hash"], "abcdef123456")
        self.assertEqual(payload["total_bytes"], 9)
        self.assertEqual(
            [entry["sha256"] for entry in payload["files"]],
            [hashlib.sha256(b"alpha").hexdigest(), hashlib.sha256(b"beta").hexdigest()],
        )
        self.assertEqual(list(output.parent.glob("*.tmp")), [])

    def test_changed_file_is_rejected_without_replacing_existing_manifest(self):
        plan = build_manifest_plan(self.info, self.root, "hash", "torrent")
        first = generate_manifest(plan)
        original = Path(first.output_path).read_text(encoding="utf-8")
        self.alpha.write_bytes(b"changed")

        result = generate_manifest(plan)

        self.assertFalse(result.success)
        self.assertIn("changed before hashing", result.error)
        self.assertEqual(Path(first.output_path).read_text(encoding="utf-8"), original)

    def test_pad_files_are_not_hashed(self):
        pad = self.root / "root" / "pad.bin"
        pad.write_bytes(b"padding")
        info = _TorrentInfo(
            _Storage(
                ["root\\alpha.bin", "root\\pad.bin"],
                [5, 7],
                [0, _Storage.flag_pad_file],
            )
        )
        plan = build_manifest_plan(info, self.root, "hash", "torrent")
        result = generate_manifest(plan)
        payload = json.loads(Path(result.output_path).read_text(encoding="utf-8"))
        self.assertEqual(result.file_count, 1)
        self.assertEqual([entry["path"] for entry in payload["files"]], ["root/alpha.bin"])

    def test_path_escape_is_rejected(self):
        info = _TorrentInfo(_Storage(["..\\outside.bin"], [1]))
        with self.assertRaises(IntegrityError):
            build_manifest_plan(info, self.root, "hash", "torrent")


if __name__ == "__main__":
    unittest.main()
