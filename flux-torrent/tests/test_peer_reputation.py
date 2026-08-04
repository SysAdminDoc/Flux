"""Tests for persistent peer reputation memory."""

import json
import tempfile
import unittest
from pathlib import Path

from flux.core.peer_reputation import PeerReputationStore


class TestPeerReputationStore(unittest.TestCase):
    def test_events_persist_and_score(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "peer-reputation.json"
            store = PeerReputationStore(path)
            store.record("203.0.113.4", "disconnect", "Example")
            store.record("203.0.113.4", "error", "Example")
            reloaded = PeerReputationStore(path)
            record = reloaded.get("203.0.113.4")
            self.assertEqual(record.events, 2)
            self.assertEqual(record.score, 4)
            self.assertEqual(record.client, "Example")
            self.assertTrue(reloaded.should_deprioritize("203.0.113.4", threshold=4))

    def test_invalid_events_are_ignored(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = PeerReputationStore(Path(temp_dir) / "reputation.json")
            self.assertIsNone(store.record("", "error"))
            self.assertIsNone(store.record("203.0.113.5", "unknown"))
            self.assertEqual(store.snapshot(), {})

    def test_record_cap_keeps_newest_entries(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "reputation.json"
            store = PeerReputationStore(path, max_records=2)
            for index in range(3):
                store.record(f"203.0.113.{index}", "disconnect")
            self.assertEqual(len(store.snapshot()), 2)
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(len(payload["peers"]), 2)

    def test_malformed_file_is_ignored(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "reputation.json"
            path.write_text("not json", encoding="utf-8")
            self.assertEqual(PeerReputationStore(path).snapshot(), {})
