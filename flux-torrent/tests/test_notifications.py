"""Tests for ratio milestone notification helpers."""

import unittest

from flux.core.notifications import (
    crossed_ratio_milestones,
    normalize_ratio_milestones,
    ratio_notification_body,
)


class TestRatioMilestones(unittest.TestCase):
    def test_normalizes_text_and_deduplicates(self):
        self.assertEqual(
            normalize_ratio_milestones("2, 1; 2, invalid, 0"),
            [1.0, 2.0],
        )

    def test_invalid_values_fall_back_to_defaults(self):
        self.assertEqual(normalize_ratio_milestones(["nan", -1]), [1.0, 2.0])

    def test_crossing_returns_only_new_thresholds(self):
        self.assertEqual(
            crossed_ratio_milestones(0.75, 2.25, [1, 2, 3]),
            [1.0, 2.0],
        )

    def test_ratio_drop_does_not_emit(self):
        self.assertEqual(crossed_ratio_milestones(2.0, 1.5, [1, 2]), [])

    def test_body_includes_suggested_action(self):
        body = ratio_notification_body("Example", 2.0, "pause")
        self.assertIn("Example", body)
        self.assertIn("2", body)
        self.assertIn("pausing", body)
