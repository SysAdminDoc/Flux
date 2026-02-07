"""Unit tests for flux.core.settings."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import unittest
import tempfile
from flux.core.settings import Settings


class TestSettings(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self._tmp.close()
        self.settings = Settings(db_path=self._tmp.name)

    def tearDown(self):
        self.settings.close()
        os.unlink(self._tmp.name)

    def test_defaults(self):
        self.assertEqual(self.settings.get("listen_port"), 6881)
        self.assertTrue(self.settings.get("dht_enabled"))
        self.assertEqual(self.settings.get("max_connections"), 500)

    def test_set_get(self):
        self.settings.set("listen_port", 12345)
        self.assertEqual(self.settings.get("listen_port"), 12345)

    def test_set_string(self):
        self.settings.set("default_save_path", "/tmp/test")
        self.assertEqual(self.settings.get("default_save_path"), "/tmp/test")

    def test_set_bool(self):
        self.settings.set("dht_enabled", False)
        self.assertFalse(self.settings.get("dht_enabled"))

    def test_set_float(self):
        self.settings.set("max_ratio", 3.5)
        self.assertAlmostEqual(self.settings.get("max_ratio"), 3.5)

    def test_get_unknown_default(self):
        self.assertIsNone(self.settings.get("nonexistent_key"))
        self.assertEqual(self.settings.get("nonexistent_key", 42), 42)

    def test_get_all(self):
        all_settings = self.settings.get_all()
        self.assertIn("listen_port", all_settings)
        self.assertIn("max_connections", all_settings)

    def test_get_all_includes_custom(self):
        self.settings.set("custom_key", "custom_value")
        all_settings = self.settings.get_all()
        self.assertEqual(all_settings["custom_key"], "custom_value")

    def test_categories_empty(self):
        self.assertEqual(self.settings.get_categories(), [])

    def test_add_category(self):
        self.settings.add_category("Movies", "/data/movies", "#ff0000")
        cats = self.settings.get_categories()
        self.assertEqual(len(cats), 1)
        self.assertEqual(cats[0]["name"], "Movies")
        self.assertEqual(cats[0]["save_path"], "/data/movies")
        self.assertEqual(cats[0]["color"], "#ff0000")

    def test_remove_category(self):
        self.settings.add_category("Movies")
        self.settings.remove_category("Movies")
        self.assertEqual(self.settings.get_categories(), [])

    def test_multiple_categories_sorted(self):
        self.settings.add_category("Zzz")
        self.settings.add_category("Aaa")
        self.settings.add_category("Mmm")
        cats = self.settings.get_categories()
        names = [c["name"] for c in cats]
        self.assertEqual(names, ["Aaa", "Mmm", "Zzz"])

    def test_tags(self):
        self.settings.add_tag("important")
        self.settings.add_tag("archive")
        tags = self.settings.get_tags()
        self.assertIn("important", tags)
        self.assertIn("archive", tags)

    def test_remove_tag(self):
        self.settings.add_tag("test")
        self.settings.remove_tag("test")
        self.assertNotIn("test", self.settings.get_tags())

    def test_duplicate_tag(self):
        self.settings.add_tag("test")
        self.settings.add_tag("test")
        self.assertEqual(self.settings.get_tags().count("test"), 1)

    def test_overwrite(self):
        self.settings.set("listen_port", 1000)
        self.settings.set("listen_port", 2000)
        self.assertEqual(self.settings.get("listen_port"), 2000)

    def test_persistence(self):
        self.settings.set("listen_port", 9999)
        self.settings.close()
        settings2 = Settings(db_path=self._tmp.name)
        self.assertEqual(settings2.get("listen_port"), 9999)
        settings2.close()
        # Reopen for tearDown
        self.settings = Settings(db_path=self._tmp.name)


if __name__ == "__main__":
    unittest.main()
