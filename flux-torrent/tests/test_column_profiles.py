"""Tests for torrent-table column profiles."""

import unittest

from flux.core.column_profiles import (
    COLUMN_KEYS,
    default_column_profile,
    normalize_column_profile,
    normalize_column_profiles,
)


class TestColumnProfiles(unittest.TestCase):
    def test_profiles_have_distinct_filter_defaults(self):
        downloading = default_column_profile("downloading")
        seeding = default_column_profile("seeding")
        completed = default_column_profile("completed")
        self.assertNotEqual(downloading["visible"], seeding["visible"])
        self.assertNotEqual(seeding["visible"], completed["visible"])

    def test_profile_normalization_restores_missing_order(self):
        profile = normalize_column_profile({
            "visible": ["name", "name", "not-a-column"],
            "order": ["ratio"],
            "widths": {"name": "600", "bad": 10, "size": "bad"},
        })
        self.assertEqual(profile["visible"], ["name"])
        self.assertEqual(profile["order"][0], "ratio")
        self.assertEqual(set(profile["order"]), set(COLUMN_KEYS))
        self.assertEqual(profile["widths"], {"name": 600})

    def test_all_profiles_are_present(self):
        profiles = normalize_column_profiles({"unknown": {"visible": []}})
        self.assertEqual(set(profiles), {"default", "downloading", "seeding", "completed"})
