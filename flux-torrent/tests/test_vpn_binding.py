"""Tests for VPN bind-address and kill-switch behavior."""

import unittest
from unittest.mock import patch

from flux.core.session_worker import SessionWorker
from flux.core.vpn_binding import (
    build_listen_interfaces,
    is_bind_address_available,
    resolve_bind_address,
)


class _FakeHandle:
    def __init__(self):
        self.paused = False

    def is_paused(self):
        return self.paused


class _FakeTorrent:
    def __init__(self):
        self.handle = _FakeHandle()
        self.pause_calls = 0

    def pause(self):
        self.pause_calls += 1
        self.handle.paused = True


class TestVpnBinding(unittest.TestCase):
    def test_bind_address_formats_ipv4_and_ipv6(self):
        self.assertEqual(resolve_bind_address("10.8.0.2"), "10.8.0.2")
        self.assertEqual(build_listen_interfaces("10.8.0.2", 6881), "10.8.0.2:6881")
        self.assertEqual(
            build_listen_interfaces("2001:db8::2", 6881),
            "[2001:db8::2]:6881",
        )

    def test_invalid_bind_never_falls_back_to_all_interfaces(self):
        self.assertEqual(
            build_listen_interfaces("not-a-real-interface", 6881),
            "127.0.0.1:6881",
        )

    def test_availability_uses_injected_interface_snapshot(self):
        self.assertTrue(
            is_bind_address_available("10.8.0.2", lambda: {"10.8.0.2", "192.168.1.2"})
        )
        self.assertFalse(
            is_bind_address_available("10.8.0.2", lambda: {"192.168.1.2"})
        )

    def test_kill_switch_pauses_once_and_never_auto_resumes(self):
        worker = SessionWorker({
            "vpn_bind_address": "10.8.0.2",
            "vpn_kill_switch": True,
        })
        torrent = _FakeTorrent()
        worker._torrents["hash"] = torrent
        statuses = []
        worker.vpn_status.connect(lambda available, message: statuses.append((available, message)))

        with patch("flux.core.session_worker.is_bind_address_available", return_value=False):
            worker._check_vpn_binding()
            worker._check_vpn_binding()
        self.assertEqual(torrent.pause_calls, 1)
        self.assertFalse(statuses[0][0])
        self.assertIn("paused 1", statuses[0][1])

        with patch("flux.core.session_worker.is_bind_address_available", return_value=True):
            worker._check_vpn_binding()
        self.assertTrue(statuses[-1][0])
        self.assertIn("remain paused", statuses[-1][1])
        self.assertEqual(torrent.pause_calls, 1)


if __name__ == "__main__":
    unittest.main()
