"""Checked-in example plugins shipped with Flux."""

from flux.plugins.auto_extract import AutoExtractPlugin
from flux.plugins.post_complete_move import PostCompleteMovePlugin
from flux.plugins.tracker_announce_logger import TrackerAnnounceLoggerPlugin

__all__ = ["AutoExtractPlugin", "PostCompleteMovePlugin", "TrackerAnnounceLoggerPlugin"]
