"""Unit tests for flux.core.script_hooks."""
import sys
import os
import json
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import unittest
from flux.core.script_hooks import (
    HookConfig,
    ScriptHookRunner,
    build_payload,
    HOOK_EVENTS,
)


class TestHookConfig(unittest.TestCase):
    def test_roundtrip(self):
        hook = HookConfig(
            event="on_finish",
            command="echo done",
            enabled=True,
            timeout_seconds=30,
        )
        d = hook.to_dict()
        restored = HookConfig.from_dict(d)
        self.assertEqual(restored.event, "on_finish")
        self.assertEqual(restored.command, "echo done")
        self.assertTrue(restored.enabled)
        self.assertEqual(restored.timeout_seconds, 30)

    def test_from_dict_ignores_extra_keys(self):
        d = {"event": "on_add", "command": "ls", "unknown_key": "val"}
        hook = HookConfig.from_dict(d)
        self.assertEqual(hook.event, "on_add")
        self.assertEqual(hook.command, "ls")

    def test_defaults(self):
        hook = HookConfig()
        self.assertEqual(hook.event, "")
        self.assertEqual(hook.command, "")
        self.assertTrue(hook.enabled)
        self.assertEqual(hook.timeout_seconds, 60)
        self.assertTrue(hook.pass_json_stdin)
        self.assertFalse(hook.pass_json_arg)


class TestBuildPayload(unittest.TestCase):
    def test_basic_payload(self):
        info = {
            "name": "Test Torrent",
            "info_hash": "abc123",
            "save_path": "/tmp",
            "category": "movies",
            "tags": ["hd"],
        }
        payload = build_payload("on_finish", info)
        self.assertEqual(payload["event"], "on_finish")
        self.assertIn("timestamp", payload)
        self.assertEqual(payload["torrent"]["name"], "Test Torrent")
        self.assertEqual(payload["torrent"]["info_hash"], "abc123")
        self.assertEqual(payload["torrent"]["category"], "movies")
        self.assertEqual(payload["torrent"]["tags"], ["hd"])

    def test_error_event_includes_error(self):
        info = {"name": "Bad", "error": "disk full"}
        payload = build_payload("on_error", info)
        self.assertEqual(payload["torrent"]["error"], "disk full")

    def test_non_error_event_omits_error(self):
        info = {"name": "Good"}
        payload = build_payload("on_add", info)
        self.assertNotIn("error", payload["torrent"])

    def test_missing_keys_default_to_empty(self):
        payload = build_payload("on_add", {})
        self.assertEqual(payload["torrent"]["name"], "")
        self.assertEqual(payload["torrent"]["info_hash"], "")
        self.assertEqual(payload["torrent"]["tags"], [])


class TestScriptHookRunner(unittest.TestCase):
    def test_configure_loads_valid_hooks(self):
        runner = ScriptHookRunner()
        runner.configure([
            {"event": "on_add", "command": "echo added"},
            {"event": "on_finish", "command": "echo done"},
            {"event": "invalid_event", "command": "echo bad"},
            {"event": "on_delete", "command": ""},
        ])
        hooks = runner.get_hooks()
        self.assertEqual(len(hooks), 2)
        self.assertEqual(hooks[0].event, "on_add")
        self.assertEqual(hooks[1].event, "on_finish")
        runner.shutdown()

    def test_add_and_remove_hook(self):
        runner = ScriptHookRunner()
        runner.add_hook(HookConfig(event="on_add", command="echo hi"))
        self.assertEqual(len(runner.get_hooks()), 1)
        runner.remove_hook(0)
        self.assertEqual(len(runner.get_hooks()), 0)
        runner.shutdown()

    def test_save_config(self):
        runner = ScriptHookRunner()
        runner.add_hook(HookConfig(event="on_finish", command="notify-send done"))
        config = runner.save_config()
        self.assertEqual(len(config), 1)
        self.assertEqual(config[0]["event"], "on_finish")
        self.assertEqual(config[0]["command"], "notify-send done")
        runner.shutdown()

    def test_fire_runs_hook(self):
        """Test that fire() actually runs a simple command."""
        runner = ScriptHookRunner()
        if sys.platform == "win32":
            cmd = "echo hook_ran"
        else:
            cmd = "echo hook_ran"
        runner.add_hook(HookConfig(event="on_finish", command=cmd))
        runner.fire("on_finish", {"name": "test"})
        # Give the thread pool a moment to execute
        time.sleep(1)
        self.assertEqual(len(runner.history), 1)
        self.assertTrue(runner.history[0]["success"])
        runner.shutdown()

    def test_fire_ignores_wrong_event(self):
        runner = ScriptHookRunner()
        runner.add_hook(HookConfig(event="on_finish", command="echo done"))
        runner.fire("on_add", {"name": "test"})
        time.sleep(0.5)
        self.assertEqual(len(runner.history), 0)
        runner.shutdown()

    def test_fire_ignores_disabled_hook(self):
        runner = ScriptHookRunner()
        runner.add_hook(HookConfig(event="on_add", command="echo hi", enabled=False))
        runner.fire("on_add", {"name": "test"})
        time.sleep(0.5)
        self.assertEqual(len(runner.history), 0)
        runner.shutdown()

    def test_fire_invalid_event_ignored(self):
        runner = ScriptHookRunner()
        runner.add_hook(HookConfig(event="on_add", command="echo hi"))
        runner.fire("bogus_event", {"name": "test"})
        time.sleep(0.5)
        self.assertEqual(len(runner.history), 0)
        runner.shutdown()


class TestHookEvents(unittest.TestCase):
    def test_all_events_present(self):
        self.assertIn("on_add", HOOK_EVENTS)
        self.assertIn("on_finish", HOOK_EVENTS)
        self.assertIn("on_delete", HOOK_EVENTS)
        self.assertIn("on_error", HOOK_EVENTS)


if __name__ == "__main__":
    unittest.main()
