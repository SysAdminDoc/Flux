"""Tests for the entry-point plugin SDK and checked-in examples."""

import json
import threading
import time
import zipfile
from types import SimpleNamespace
from unittest.mock import patch

from flux.core.plugins import PluginContext, PluginManager, PluginResult
from flux.plugins.auto_extract import AutoExtractPlugin
from flux.plugins.post_complete_move import PostCompleteMovePlugin
from flux.plugins.tracker_announce_logger import TrackerAnnounceLoggerPlugin


def test_builtin_examples_are_discoverable_when_explicitly_enabled():
    manager = PluginManager()
    manager.configure(enabled=True, include_examples=True)
    assert manager.plugins == (
        "auto-extract",
        "post-complete-move",
        "tracker-announce-logger",
    )
    manager.shutdown()


def test_entry_point_plugin_runs_asynchronously():
    handled = threading.Event()

    class ExamplePlugin:
        name = "test-plugin"
        events = ("on_finish",)

        def handle(self, context):
            assert context.torrent["name"] == "Done"
            handled.set()
            return PluginResult(self.name, context.event, True, "handled")

    entry_point = SimpleNamespace(
        name="test-plugin",
        value="external_package:ExamplePlugin",
        load=lambda: ExamplePlugin,
    )
    manager = PluginManager()
    with patch("flux.core.plugins._entry_points", return_value=[entry_point]):
        manager.configure(enabled=True)
    assert manager.dispatch("on_finish", {"name": "Done"}) == 1
    assert handled.wait(1.0)
    for _ in range(20):
        if manager.history:
            break
        time.sleep(0.01)
    assert manager.history[0].message == "handled"
    manager.shutdown()


def test_auto_extract_is_opt_in_and_rejects_traversal(tmp_path):
    archive = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("folder/readme.txt", "ok")
        bundle.writestr("../escape.txt", "no")
    context = PluginContext(
        "on_finish",
        {"save_path": str(tmp_path)},
        {"enabled": False},
    )
    assert AutoExtractPlugin().handle(context).message == "disabled"
    context.config["enabled"] = True
    result = AutoExtractPlugin().handle(context)
    assert result.success is False
    assert not (tmp_path.parent / "escape.txt").exists()


def test_post_complete_move_applies_safe_rename_template(tmp_path):
    source = tmp_path / "incoming"
    source.mkdir()
    (source / "payload.bin").write_text("data", encoding="utf-8")
    destination = tmp_path / "ready"
    context = PluginContext(
        "on_finish",
        {"name": "Movie: 01", "category": "movies", "save_path": str(source)},
        {"enabled": True, "destination": str(destination), "rename": "{category}-{name}"},
    )
    result = PostCompleteMovePlugin().handle(context)
    assert result.success is True
    assert (destination / "movies-Movie_ 01" / "payload.bin").read_text(encoding="utf-8") == "data"


def test_tracker_logger_writes_jsonl(tmp_path):
    log_path = tmp_path / "events" / "trackers.jsonl"
    result = TrackerAnnounceLoggerPlugin().handle(PluginContext(
        "on_tracker_announce",
        {"name": "Done", "info_hash": "abc"},
        {"path": str(log_path)},
        {"tracker_url": "https://tracker.example/announce", "alert_type": "tracker_reply"},
    ))
    assert result.success is True
    record = json.loads(log_path.read_text(encoding="utf-8"))
    assert record["tracker_url"].startswith("https://")
    assert record["info_hash"] == "abc"
