"""Unit tests for flux.utils.formatters."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import unittest
from flux.utils.formatters import (
    format_bytes, format_speed, format_eta, format_ratio,
    format_timestamp, format_progress
)


class TestFormatBytes(unittest.TestCase):
    def test_zero(self):
        self.assertEqual(format_bytes(0), "0 B")

    def test_bytes(self):
        self.assertEqual(format_bytes(500), "500.0 B")

    def test_kilobytes(self):
        self.assertIn("KB", format_bytes(1024))

    def test_megabytes(self):
        self.assertIn("MB", format_bytes(1024 * 1024))

    def test_gigabytes(self):
        self.assertIn("GB", format_bytes(1024 ** 3))

    def test_terabytes(self):
        self.assertIn("TB", format_bytes(1024 ** 4))

    def test_decimals(self):
        self.assertIn("1.50", format_bytes(1536, decimals=2))


class TestFormatSpeed(unittest.TestCase):
    def test_zero(self):
        self.assertEqual(format_speed(0), "--")

    def test_negative(self):
        self.assertEqual(format_speed(-100), "--")

    def test_positive(self):
        result = format_speed(1024)
        self.assertIn("/s", result)
        self.assertIn("KB", result)

    def test_high_speed(self):
        self.assertIn("MB", format_speed(50 * 1024 * 1024))


class TestFormatEta(unittest.TestCase):
    def test_zero(self):
        self.assertEqual(format_eta(0), "--")

    def test_negative(self):
        self.assertEqual(format_eta(-10), "--")

    def test_seconds(self):
        self.assertEqual(format_eta(45), "45s")

    def test_minutes(self):
        result = format_eta(125)
        self.assertIn("m", result)

    def test_hours(self):
        result = format_eta(3700)
        self.assertIn("h", result)

    def test_days(self):
        result = format_eta(100000)
        self.assertIn("d", result)


class TestFormatRatio(unittest.TestCase):
    def test_zero(self):
        self.assertEqual(format_ratio(0.0), "0.00")

    def test_negative(self):
        self.assertEqual(format_ratio(-1.0), "--")

    def test_normal(self):
        self.assertEqual(format_ratio(1.5), "1.50")


class TestFormatTimestamp(unittest.TestCase):
    def test_zero(self):
        self.assertEqual(format_timestamp(0), "--")

    def test_valid(self):
        self.assertIn("2023", format_timestamp(1700000000))


class TestFormatProgress(unittest.TestCase):
    def test_zero(self):
        self.assertEqual(format_progress(0.0), "0.0%")

    def test_complete(self):
        self.assertEqual(format_progress(1.0), "100.0%")

    def test_partial(self):
        self.assertEqual(format_progress(0.5), "50.0%")


if __name__ == "__main__":
    unittest.main()
