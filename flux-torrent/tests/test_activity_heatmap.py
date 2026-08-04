"""Tests for the persistent weekday/hour activity heatmap contract."""

import unittest
from datetime import datetime

from flux.core.activity_heatmap import (
    empty_heatmap,
    heatmap_peak,
    heatmap_totals,
    normalize_heatmap,
    record_activity,
)


class TestActivityHeatmap(unittest.TestCase):
    def test_empty_heatmap_has_seven_days_and_24_hours(self):
        heatmap = empty_heatmap()
        self.assertEqual(len(heatmap), 7)
        self.assertTrue(all(len(row) == 24 for row in heatmap))
        self.assertEqual(heatmap_totals(heatmap), (0, 0))

    def test_record_activity_accumulates_by_local_weekday_and_hour(self):
        heatmap = empty_heatmap()
        when = datetime(2026, 8, 3, 14, 30)  # Monday, 14:00
        record_activity(heatmap, when, 1024, 512)
        record_activity(heatmap, when, 256, 128)

        self.assertEqual(heatmap[0][14], {"download": 1280, "upload": 640})
        self.assertEqual(heatmap_totals(heatmap), (1280, 640))
        self.assertEqual(heatmap_peak(heatmap), 1920)

    def test_normalize_accepts_legacy_short_cells_and_rejects_negative_values(self):
        heatmap = normalize_heatmap({"cells": [[[100, -2]]]})
        self.assertEqual(heatmap[0][0], {"download": 100, "upload": 0})
        self.assertEqual(heatmap[6][23], {"download": 0, "upload": 0})

    def test_instances_are_independent(self):
        first = empty_heatmap()
        second = empty_heatmap()
        first[0][0]["download"] = 12
        self.assertEqual(second[0][0]["download"], 0)


if __name__ == "__main__":
    unittest.main()
