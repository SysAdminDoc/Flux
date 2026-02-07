"""Unit tests for flux.core.peer_filter."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import unittest
import tempfile


class TestPeerFilter(unittest.TestCase):

    def setUp(self):
        from flux.core.peer_filter import PeerFilter
        self.pf = PeerFilter()
        self.pf.configure({
            "peer_filter_enabled": True,
            "auto_ban_xunlei": True,
            "auto_ban_qq": True,
            "auto_ban_baidu": True,
        })

    def test_disabled_passes_all(self):
        self.pf.enabled = False
        banned, reason = self.pf.check_peer(b"-XL1234-", "Xunlei", "1.2.3.4")
        self.assertFalse(banned)

    def test_ban_xunlei_peer_id(self):
        banned, reason = self.pf.check_peer(b"-XL1234-abcdef", "Unknown", "1.2.3.4")
        self.assertTrue(banned)
        self.assertIn("Xunlei", reason)

    def test_ban_thunder_peer_id(self):
        banned, reason = self.pf.check_peer(b"-SD1234-abcdef", "Unknown", "1.2.3.4")
        self.assertTrue(banned)

    def test_ban_qq_peer_id(self):
        banned, reason = self.pf.check_peer(b"-QD1234-abcdef", "Unknown", "1.2.3.4")
        self.assertTrue(banned)

    def test_ban_baidu_peer_id(self):
        banned, reason = self.pf.check_peer(b"-BN1234-abcdef", "Unknown", "1.2.3.4")
        self.assertTrue(banned)

    def test_pass_qbittorrent(self):
        banned, reason = self.pf.check_peer(b"-qB4500-abcdef", "qBittorrent/4.5.0", "1.2.3.4")
        self.assertFalse(banned)

    def test_ban_by_client_name(self):
        banned, reason = self.pf.check_peer(b"-XX1234-abcdef", "Xunlei 7.2.1", "1.2.3.4")
        self.assertTrue(banned)

    def test_whitelist_overrides(self):
        self.pf.add_whitelist("-XL")
        banned, reason = self.pf.check_peer(b"-XL1234-abcdef", "Unknown", "1.2.3.4")
        self.assertFalse(banned)

    def test_custom_rule(self):
        self.pf.add_custom_rule("-MY", "Custom leecher")
        banned, reason = self.pf.check_peer(b"-MY1234-abcdef", "Unknown", "1.2.3.4")
        self.assertTrue(banned)
        self.assertEqual(reason, "Custom leecher")

    def test_remove_custom_rule(self):
        self.pf.add_custom_rule("-MY", "Custom leecher")
        self.pf.remove_custom_rule(0)
        banned, reason = self.pf.check_peer(b"-MY1234-abcdef", "Unknown", "1.2.3.4")
        self.assertFalse(banned)

    def test_disable_xunlei_only(self):
        self.pf.configure({
            "peer_filter_enabled": True,
            "auto_ban_xunlei": False,
            "auto_ban_qq": True,
            "auto_ban_baidu": True,
        })
        banned, _ = self.pf.check_peer(b"-XL1234-abcdef", "Unknown", "1.2.3.4")
        self.assertFalse(banned)
        banned, _ = self.pf.check_peer(b"-QD1234-abcdef", "Unknown", "1.2.3.4")
        self.assertTrue(banned)

    def test_ip_blocklist(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.p2p', delete=False) as f:
            f.write("Test Range:1.2.3.0-1.2.3.255\n")
            f.write("# comment line\n")
            f.write("Another:10.0.0.1-10.0.0.10\n")
            f.flush()
            self.pf.load_blocklist_p2p(f.name)

        self.assertEqual(self.pf.blocklist_count, 2)
        banned, reason = self.pf.check_peer(b"-qB1234-abcdef", "qBittorrent", "1.2.3.50")
        self.assertTrue(banned)
        self.assertEqual(reason, "IP blocklist")

        banned, _ = self.pf.check_peer(b"-qB1234-abcdef", "qBittorrent", "2.2.2.2")
        self.assertFalse(banned)

        os.unlink(f.name)

    def test_ban_log(self):
        self.pf.check_peer(b"-XL1234-abcdef", "Xunlei", "1.2.3.4")
        self.assertEqual(len(self.pf.ban_log), 1)
        self.assertEqual(self.pf.ban_log[0]["ip"], "1.2.3.4")

    def test_stats(self):
        stats = self.pf.stats
        self.assertTrue(stats["enabled"])
        self.assertGreater(stats["rules_active"], 0)


if __name__ == "__main__":
    unittest.main()
