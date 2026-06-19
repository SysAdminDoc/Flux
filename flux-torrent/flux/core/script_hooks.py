"""ScriptHooks — run user-configured shell commands on torrent lifecycle events.

Supported events:
  - on_add       — torrent added to session
  - on_finish    — torrent download completed
  - on_delete    — torrent removed from session
  - on_error     — torrent encountered an error

Each hook receives a JSON payload on stdin with torrent metadata.
Hooks run in a thread pool so they never block the GUI or session worker.
"""

import json
import logging
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class HookConfig:
    """Configuration for a single hook."""
    event: str = ""           # on_add, on_finish, on_delete, on_error
    command: str = ""         # shell command to run
    enabled: bool = True
    timeout_seconds: int = 60
    pass_json_stdin: bool = True  # pipe JSON payload to stdin
    pass_json_arg: bool = False   # append JSON as command-line argument

    def to_dict(self) -> dict:
        return {
            "event": self.event,
            "command": self.command,
            "enabled": self.enabled,
            "timeout_seconds": self.timeout_seconds,
            "pass_json_stdin": self.pass_json_stdin,
            "pass_json_arg": self.pass_json_arg,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "HookConfig":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# Valid event names
HOOK_EVENTS = ("on_add", "on_finish", "on_delete", "on_error")


def build_payload(event: str, torrent_info: dict) -> dict:
    """Build the JSON payload passed to hook scripts.

    Args:
        event: The lifecycle event name.
        torrent_info: Dict with torrent metadata (name, info_hash, save_path, etc).

    Returns:
        Complete payload dict including event name and timestamp.
    """
    payload = {
        "event": event,
        "timestamp": time.time(),
        "torrent": {
            "name": torrent_info.get("name", ""),
            "info_hash": torrent_info.get("info_hash", ""),
            "save_path": torrent_info.get("save_path", ""),
            "category": torrent_info.get("category", ""),
            "tags": torrent_info.get("tags", []),
            "total_size": torrent_info.get("total_size", 0),
            "progress": torrent_info.get("progress", 0.0),
            "ratio": torrent_info.get("ratio", 0.0),
            "total_downloaded": torrent_info.get("total_downloaded", 0),
            "total_uploaded": torrent_info.get("total_uploaded", 0),
        },
    }
    # Include error message for on_error events
    if event == "on_error" and "error" in torrent_info:
        payload["torrent"]["error"] = torrent_info["error"]
    return payload


def _run_hook_sync(hook: HookConfig, payload: dict) -> dict:
    """Execute a single hook synchronously. Called on a thread pool thread.

    Returns:
        Dict with execution result: {success, exit_code, stdout, stderr, duration}.
    """
    start = time.time()
    result = {
        "event": hook.event,
        "command": hook.command,
        "success": False,
        "exit_code": -1,
        "stdout": "",
        "stderr": "",
        "duration": 0.0,
    }

    try:
        payload_json = json.dumps(payload, indent=2)

        cmd = hook.command
        if hook.pass_json_arg:
            # Append escaped JSON as argument
            cmd = f'{cmd} {json.dumps(payload_json)}'

        proc = subprocess.run(
            cmd,
            shell=True,
            input=payload_json if hook.pass_json_stdin else None,
            capture_output=True,
            text=True,
            timeout=hook.timeout_seconds,
        )

        result["exit_code"] = proc.returncode
        result["stdout"] = proc.stdout[:4096]  # Cap output
        result["stderr"] = proc.stderr[:4096]
        result["success"] = proc.returncode == 0
        result["duration"] = time.time() - start

        if proc.returncode != 0:
            logger.warning(
                f"Hook [{hook.event}] exited {proc.returncode}: "
                f"{proc.stderr[:200] if proc.stderr else '(no stderr)'}"
            )
        else:
            logger.info(
                f"Hook [{hook.event}] completed in {result['duration']:.1f}s"
            )

    except subprocess.TimeoutExpired:
        result["stderr"] = f"Timed out after {hook.timeout_seconds}s"
        result["duration"] = time.time() - start
        logger.error(f"Hook [{hook.event}] timed out: {hook.command}")

    except Exception as e:
        result["stderr"] = str(e)
        result["duration"] = time.time() - start
        logger.error(f"Hook [{hook.event}] failed: {e}")

    return result


class ScriptHookRunner:
    """Manages and executes script hooks for torrent lifecycle events.

    Thread-safe: hooks run on a background thread pool.
    """

    def __init__(self):
        self._hooks: List[HookConfig] = []
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="hook")
        self._history: List[dict] = []
        self._max_history = 100

    def configure(self, hooks_list: list):
        """Load hooks from settings (list of dicts)."""
        self._hooks.clear()
        for d in hooks_list:
            try:
                hook = HookConfig.from_dict(d)
                if hook.event in HOOK_EVENTS and hook.command:
                    self._hooks.append(hook)
            except Exception as e:
                logger.error(f"Invalid hook config: {e}")
        logger.info(f"Loaded {len(self._hooks)} script hooks")

    def save_config(self) -> list:
        """Serialize hooks to a list of dicts for settings storage."""
        return [h.to_dict() for h in self._hooks]

    def add_hook(self, hook: HookConfig):
        """Add a new hook."""
        if hook.event in HOOK_EVENTS and hook.command:
            self._hooks.append(hook)

    def remove_hook(self, index: int):
        """Remove a hook by index."""
        if 0 <= index < len(self._hooks):
            self._hooks.pop(index)

    def get_hooks(self) -> List[HookConfig]:
        """Return current hooks list."""
        return list(self._hooks)

    def fire(self, event: str, torrent_info: dict):
        """Fire all enabled hooks matching the given event.

        Non-blocking: each hook runs on the thread pool.

        Args:
            event: One of HOOK_EVENTS.
            torrent_info: Dict with torrent metadata.
        """
        if event not in HOOK_EVENTS:
            return

        payload = build_payload(event, torrent_info)

        for hook in self._hooks:
            if hook.event == event and hook.enabled:
                self._pool.submit(self._execute_and_record, hook, payload)

    def _execute_and_record(self, hook: HookConfig, payload: dict):
        """Run hook and record result in history."""
        result = _run_hook_sync(hook, payload)
        self._history.append(result)
        if len(self._history) > self._max_history:
            self._history.pop(0)

    @property
    def history(self) -> List[dict]:
        """Recent hook execution history."""
        return list(self._history)

    def shutdown(self):
        """Shut down the thread pool."""
        self._pool.shutdown(wait=False)
