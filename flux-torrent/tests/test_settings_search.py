"""Tests for the settings dialog's fuzzy search matcher."""

import unittest

from flux.gui.dialogs.settings_dialog import fuzzy_match


class TestSettingsSearch(unittest.TestCase):
    def test_empty_query_matches_everything(self):
        self.assertTrue(fuzzy_match("", "Connection Limits"))

    def test_subsequence_and_token_matches(self):
        self.assertTrue(fuzzy_match("conn lim", "Connection Limits"))
        self.assertTrue(fuzzy_match("upld lim", "Upload limit (KiB/s)"))
        self.assertTrue(fuzzy_match("remote client", "Remote desktop client mode"))

    def test_unrelated_text_does_not_match(self):
        self.assertFalse(fuzzy_match("magnet", "Connection Limits"))


if __name__ == "__main__":
    unittest.main()
