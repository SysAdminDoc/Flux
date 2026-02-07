"""Unit tests for flux.core.torrent.TorrentSnapshot and TorrentState."""

import pytest
from flux.core.torrent import TorrentSnapshot, TorrentState


class TestTorrentSnapshot:
    def test_default_state(self):
        snap = TorrentSnapshot()
        assert snap.valid is False
        assert snap.state == TorrentState.ERROR
        assert snap.name == "Unknown"
        assert snap.tags == []

    def test_tags_default_not_shared(self):
        s1 = TorrentSnapshot()
        s2 = TorrentSnapshot()
        s1.tags.append("test")
        assert "test" not in s2.tags

    def test_full_snapshot(self):
        snap = TorrentSnapshot(
            valid=True,
            state=TorrentState.DOWNLOADING,
            name="Test Torrent",
            info_hash="abc123",
            progress=0.5,
            total_size=1048576,
            download_speed=100000,
            upload_speed=50000,
            num_seeds=10,
            num_peers=25,
            ratio=1.5,
            eta=3600,
            category="Movies",
            tags=["hd", "new"],
        )
        assert snap.valid is True
        assert snap.name == "Test Torrent"
        assert snap.progress == 0.5
        assert snap.eta == 3600
        assert "hd" in snap.tags


class TestTorrentState:
    def test_display_names(self):
        assert TorrentState.DOWNLOADING.display_name == "Downloading"
        assert TorrentState.SEEDING.display_name == "Seeding"
        assert TorrentState.PAUSED.display_name == "Paused"
        assert TorrentState.METADATA.display_name == "Getting Metadata"

    def test_all_states_have_display_names(self):
        for state in TorrentState:
            assert state.display_name != "Unknown", f"{state} missing display name"


class TestTorrentListModel:
    """Test the TorrentListModel with TorrentSnapshot data."""

    def test_update_from_snapshots_add(self):
        from flux.gui.torrent_model import TorrentListModel
        model = TorrentListModel()
        snaps = [
            TorrentSnapshot(info_hash="aaa", name="Alpha", valid=True),
            TorrentSnapshot(info_hash="bbb", name="Beta", valid=True),
        ]
        model.update_from_snapshots(snaps)
        assert model.rowCount() == 2
        assert model.get_info_hash(0) == "aaa"
        assert model.get_info_hash(1) == "bbb"

    def test_update_from_snapshots_remove(self):
        from flux.gui.torrent_model import TorrentListModel
        model = TorrentListModel()
        snaps = [
            TorrentSnapshot(info_hash="aaa", name="Alpha", valid=True),
            TorrentSnapshot(info_hash="bbb", name="Beta", valid=True),
        ]
        model.update_from_snapshots(snaps)
        assert model.rowCount() == 2

        # Remove one
        model.update_from_snapshots([TorrentSnapshot(info_hash="bbb", name="Beta", valid=True)])
        assert model.rowCount() == 1
        assert model.get_info_hash(0) == "bbb"

    def test_find_snapshot(self):
        from flux.gui.torrent_model import TorrentListModel
        model = TorrentListModel()
        snaps = [
            TorrentSnapshot(info_hash="aaa", name="Alpha", valid=True),
        ]
        model.update_from_snapshots(snaps)
        found = model.find_snapshot("aaa")
        assert found is not None
        assert found.name == "Alpha"
        assert model.find_snapshot("zzz") is None

    def test_get_snapshot_out_of_range(self):
        from flux.gui.torrent_model import TorrentListModel
        model = TorrentListModel()
        assert model.get_snapshot(-1) is None
        assert model.get_snapshot(0) is None
