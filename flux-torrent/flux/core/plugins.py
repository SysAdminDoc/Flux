"""Entry-point based plugin SDK for Flux lifecycle integrations."""

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from importlib import import_module
from importlib import metadata as importlib_metadata
from typing import Any, Mapping

logger = logging.getLogger(__name__)

PLUGIN_GROUP = "flux.plugins"
PLUGIN_API_VERSION = 1
PLUGIN_EVENTS = (
    "on_add",
    "on_finish",
    "on_delete",
    "on_error",
    "on_tracker_announce",
)


@dataclass
class PluginContext:
    """Detached event data passed to plugin handlers."""

    event: str
    torrent: dict[str, Any]
    config: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PluginResult:
    """Bounded result recorded for diagnostics after a plugin invocation."""

    plugin: str
    event: str
    success: bool
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)


def _entry_points() -> list:
    """Read entry points across importlib.metadata API generations."""
    try:
        points = importlib_metadata.entry_points()
        if hasattr(points, "select"):
            return list(points.select(group=PLUGIN_GROUP))
        return list(points.get(PLUGIN_GROUP, []))
    except Exception as exc:
        logger.warning("Plugin entry-point discovery failed: %s", type(exc).__name__)
        return []


def _builtin_plugins() -> list:
    """Return the checked-in examples for source-tree discovery and tests."""
    modules = (
        ("flux.plugins.auto_extract", "AutoExtractPlugin"),
        ("flux.plugins.post_complete_move", "PostCompleteMovePlugin"),
        ("flux.plugins.tracker_announce_logger", "TrackerAnnounceLoggerPlugin"),
    )
    loaded = []
    for module_name, class_name in modules:
        try:
            plugin_class = getattr(import_module(module_name), class_name)
            loaded.append(plugin_class())
        except Exception as exc:
            logger.warning("Built-in plugin %s failed to load: %s", class_name, type(exc).__name__)
    return loaded


class PluginManager:
    """Discover and run entry-point plugins off the session worker thread."""

    def __init__(self):
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="FluxPlugin")
        self._plugins: list[Any] = []
        self._history: list[PluginResult] = []
        self._history_lock = threading.Lock()
        self._max_history = 100
        self._enabled = False
        self._plugin_config: dict[str, Any] = {}

    @property
    def plugins(self) -> tuple[str, ...]:
        """Return the names of currently loaded plugins."""
        return tuple(str(plugin.name) for plugin in self._plugins)

    @property
    def history(self) -> list[PluginResult]:
        """Return a bounded copy of recent plugin results."""
        with self._history_lock:
            return list(self._history)

    def configure(
        self,
        enabled: bool = False,
        allowlist: list | None = None,
        plugin_config: Mapping[str, Any] | None = None,
        include_examples: bool = False,
    ):
        """Reload plugins from entry points and optional checked-in examples."""
        self._enabled = bool(enabled)
        self._plugins.clear()
        self._plugin_config = dict(plugin_config or {})
        if not self._enabled:
            return

        allowed = {str(item).strip() for item in (allowlist or []) if str(item).strip()}
        if include_examples:
            for plugin in _builtin_plugins():
                self._register(plugin, allowed)

        for entry_point in _entry_points():
            # The project entry points are available after installing Flux. The
            # explicit switch keeps examples opt-in when they are installed.
            if not include_examples and str(getattr(entry_point, "value", "")).startswith("flux.plugins."):
                continue
            try:
                loaded = entry_point.load()
                if isinstance(loaded, type):
                    plugin = loaded()
                elif callable(loaded) and not hasattr(loaded, "handle"):
                    plugin = loaded()
                else:
                    plugin = loaded
                self._register(plugin, allowed)
            except Exception as exc:
                logger.warning("Plugin %s failed to load: %s", entry_point.name, type(exc).__name__)
        logger.info("Loaded %d Flux plugins: %s", len(self._plugins), ", ".join(self.plugins) or "none")

    def _register(self, plugin: Any, allowed: set[str]):
        name = str(getattr(plugin, "name", "")).strip()
        events = set(getattr(plugin, "events", ()))
        try:
            api_version = int(getattr(plugin, "api_version", PLUGIN_API_VERSION))
        except (TypeError, ValueError):
            api_version = 0
        if (
            not name
            or api_version != PLUGIN_API_VERSION
            or (allowed and name not in allowed)
            or not callable(getattr(plugin, "handle", None))
        ):
            return
        if not events.intersection(PLUGIN_EVENTS):
            logger.warning("Ignoring plugin %s with no supported events", name)
            return
        if any(existing.name == name for existing in self._plugins):
            return
        self._plugins.append(plugin)

    def dispatch(
        self,
        event: str,
        torrent: Mapping[str, Any],
        metadata: Mapping[str, Any] | None = None,
        plugin_config: Mapping[str, Any] | None = None,
    ) -> int:
        """Queue matching plugins and return the number of submitted calls."""
        if not self._enabled or event not in PLUGIN_EVENTS:
            return 0
        torrent_copy = dict(torrent or {})
        metadata_copy = dict(metadata or {})
        config = dict(self._plugin_config)
        if plugin_config is not None:
            config.update(plugin_config)
        submitted = 0
        for plugin in self._plugins:
            if event not in set(getattr(plugin, "events", ())):
                continue
            plugin_settings = config.get(plugin.name, {})
            if not isinstance(plugin_settings, Mapping):
                plugin_settings = {}
            context = PluginContext(
                event=event,
                torrent=dict(torrent_copy),
                config=dict(plugin_settings),
                metadata=dict(metadata_copy),
            )
            self._executor.submit(self._invoke, plugin, context)
            submitted += 1
        return submitted

    def _invoke(self, plugin: Any, context: PluginContext):
        started = time.monotonic()
        try:
            result = plugin.handle(context)
            if isinstance(result, PluginResult):
                outcome = result
            else:
                outcome = PluginResult(
                    plugin=plugin.name,
                    event=context.event,
                    success=True,
                    message=str(result or "completed"),
                )
        except Exception as exc:
            outcome = PluginResult(
                plugin=plugin.name,
                event=context.event,
                success=False,
                message=type(exc).__name__,
            )
            logger.warning("Plugin %s failed for %s: %s", plugin.name, context.event, type(exc).__name__)
        with self._history_lock:
            self._history.append(outcome)
            if len(self._history) > self._max_history:
                self._history.pop(0)
        logger.debug(
            "Plugin %s handled %s in %.3fs",
            plugin.name,
            context.event,
            time.monotonic() - started,
        )

    def shutdown(self):
        """Stop accepting plugin work and join workers before session shutdown."""
        self._enabled = False
        self._executor.shutdown(wait=True, cancel_futures=True)
