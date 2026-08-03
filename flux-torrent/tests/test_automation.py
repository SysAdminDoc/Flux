"""Tests for label-scoped automation rules."""

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from flux.core.automation import (
    build_label_rules,
    ensure_move_path,
    label_rule_for,
    parse_label_rules,
    build_torrent_schedules,
    parse_torrent_schedules,
    scheduled_action,
    should_auto_delete,
)


class TestLabelAutomation(unittest.TestCase):
    def test_rules_match_category_before_tags(self):
        rules = parse_label_rules([
            {"label": "HD", "ratio_limit": "2.5", "upload_limit": "1024"},
            {"label": "movies", "tracker_overrides": "udp://one\nhttps://two"},
        ])
        match = label_rule_for("Movies", ["hd"], rules)
        self.assertEqual(match.label, "movies")
        self.assertEqual(match.tracker_overrides, ("udp://one", "https://two"))
        tag_match = label_rule_for("other", ["hd"], rules)
        self.assertEqual(tag_match.ratio_limit, 2.5)
        self.assertEqual(tag_match.upload_limit, 1024)

    def test_rules_are_canonical_and_invalid_values_are_safe(self):
        rules = build_label_rules({
            "label_rules": [
                {"label": "archive", "ratio_limit": "bad", "upload_limit": -1},
                {"label": "archive", "ratio_limit": 3},
                {"label": "", "ratio_limit": 10},
            ]
        })
        self.assertEqual(rules, [{
            "label": "archive",
            "move_completed_path": "",
            "tracker_overrides": [],
            "ratio_limit": 3.0,
            "upload_limit": 0,
        }])

    def test_move_path_is_created(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "completed" / "movies"
            self.assertEqual(ensure_move_path(str(destination)), str(destination))
            self.assertTrue(destination.is_dir())
        self.assertEqual(ensure_move_path(""), "")

    def test_conditional_delete_uses_or_and_respects_archive_exclusion(self):
        base = SimpleNamespace(progress=1.0, ratio=2.0, seeding_time=0,
                               category="movies", tags=[])
        settings = {
            "auto_delete_enabled": True,
            "auto_delete_ratio": 2.0,
            "auto_delete_seed_days": 7,
            "auto_delete_exclude_label": "archive",
        }
        self.assertTrue(should_auto_delete(base, settings))
        base.ratio = 0.0
        base.seeding_time = 7 * 86400
        self.assertTrue(should_auto_delete(base, settings))
        base.category = "archive"
        self.assertFalse(should_auto_delete(base, settings))
        base.progress = 0.5
        self.assertFalse(should_auto_delete(base, settings))

    def test_schedule_handles_weekdays_and_overnight_windows(self):
        info_hash = "a" * 40
        raw = {
            info_hash: {"start": "22:00", "stop": "02:00", "days": [0, 2]},
        }
        schedules = parse_torrent_schedules(raw)
        self.assertEqual(scheduled_action(info_hash, schedules, datetime(2026, 8, 3, 23, 0)), "resume")
        self.assertEqual(scheduled_action(info_hash, schedules, datetime(2026, 8, 4, 1, 0)), "resume")
        self.assertEqual(scheduled_action(info_hash, schedules, datetime(2026, 8, 4, 3, 0)), "pause")
        self.assertEqual(scheduled_action(info_hash, schedules, datetime(2026, 8, 4, 23, 0)), "pause")
        self.assertEqual(build_torrent_schedules({"torrent_schedules": raw})[info_hash]["days"], [0, 2])


if __name__ == "__main__":
    unittest.main()
