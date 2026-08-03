"""Tests for label-scoped automation rules."""

import tempfile
import unittest
from pathlib import Path

from flux.core.automation import (
    build_label_rules,
    ensure_move_path,
    label_rule_for,
    parse_label_rules,
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


if __name__ == "__main__":
    unittest.main()
