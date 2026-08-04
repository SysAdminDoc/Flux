"""Tests for per-peer piece availability mapping."""

import unittest

from flux.core.piece_sources import build_peer_piece_map


class TestPieceSources(unittest.TestCase):
    def test_first_peer_owns_overlapping_piece(self):
        owners, labels = build_peer_piece_map(
            [("Peer A", [True, False, True]), ("Peer B", [False, True, True])],
            3,
        )
        self.assertEqual(owners, [0, 1, 0])
        self.assertEqual(labels, ["Peer A", "Peer B"])

    def test_invalid_and_excess_sources_are_bounded(self):
        owners, labels = build_peer_piece_map(
            [("bad", object())] + [(f"Peer {i}", [True]) for i in range(20)],
            1,
            max_peers=2,
        )
        self.assertEqual(labels, ["Peer 0", "Peer 1"])
        self.assertEqual(owners, [0])

    def test_empty_piece_count(self):
        self.assertEqual(build_peer_piece_map([("Peer", [True])], 0), ([], []))
