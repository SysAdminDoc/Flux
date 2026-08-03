"""Unit tests for flux.core.torrent.TorrentSnapshot and TorrentState."""

from flux.core.torrent import TorrentSnapshot, TorrentState, get_info_hashes


class _HashSet:
    def __init__(self, v1="", v2=""):
        self.v1 = v1
        self.v2 = v2

    def has_v1(self):
        return bool(self.v1)

    def has_v2(self):
        return bool(self.v2)


class _HashHandle:
    def __init__(self, hashes, legacy="legacy"):
        self._hashes = hashes
        self._legacy = legacy

    def info_hashes(self):
        return self._hashes

    def info_hash(self):
        return self._legacy


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

    def test_hash_type_distinguishes_v2_and_hybrid(self):
        assert TorrentSnapshot(info_hash_v2="a" * 64).hash_type == "v2"
        assert TorrentSnapshot(info_hash_v1="b" * 40, info_hash_v2="a" * 64).hash_type == "hybrid"

    def test_v2_hash_is_not_truncated_by_legacy_accessor(self):
        v2 = "a" * 64
        primary, v1, actual_v2 = get_info_hashes(_HashHandle(_HashSet(v2=v2), legacy=v2[:40]))
        assert primary == v2
        assert v1 == ""
        assert actual_v2 == v2

    def test_hybrid_uses_v1_as_legacy_primary_key(self):
        v1 = "b" * 40
        v2 = "a" * 64
        primary, actual_v1, actual_v2 = get_info_hashes(_HashHandle(_HashSet(v1, v2)))
        assert primary == v1
        assert actual_v1 == v1
        assert actual_v2 == v2


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
