"""Tests for creation preset validation and replacement."""

import unittest

from flux.core.creation_presets import (
    CreationPreset,
    parse_creation_presets,
    upsert_creation_preset,
)


class TestCreationPresets(unittest.TestCase):
    def test_parse_normalizes_and_deduplicates(self):
        presets = parse_creation_presets([
            {
                "name": "Private tracker",
                "piece_length": "1048576",
                "trackers": "udp://one\nudp://one\n",
                "private_flag": True,
            },
            {"name": "PRIVATE TRACKER", "trackers": ["udp://two"]},
            {"name": ""},
        ])
        self.assertEqual(len(presets), 1)
        self.assertEqual(presets[0].piece_size, 1048576)
        self.assertEqual(presets[0].trackers, ["udp://one"])
        self.assertTrue(presets[0].private)

    def test_upsert_replaces_case_insensitively(self):
        values = [{"name": "Public", "piece_size": 0}]
        result = upsert_creation_preset(values, CreationPreset(name="public", piece_size=65536))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].piece_size, 65536)

    def test_round_trip(self):
        preset = CreationPreset(
            name="Archive",
            piece_size=262144,
            trackers=["udp://tracker"],
            private=True,
            comment="created by Flux",
            web_seeds=["https://seed/file"],
        )
        restored = CreationPreset.from_dict(preset.to_dict())
        self.assertEqual(restored.__dict__, preset.__dict__)
