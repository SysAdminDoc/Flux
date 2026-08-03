# Changelog

All notable changes to Flux will be documented in this file.

## Unreleased

- Added a remote session client mode that maps daemon status and detail payloads into the native
  snapshot dataclasses, plus bounded torrent uploads and remote control actions.
- Added remote detail, torrent URL/upload, queue, speed-limit, recheck, and reannounce API routes.
- Preserved full v1/v2 info-hash pairs and used the 64-character v2 identity for v2-only torrents.
- Added validated I2P SAM bridge settings and session wiring for outbound I2P transport.
- Added per-tracker HTTP(S) announce routing through SOCKS5 or HTTP(S) proxies, including peer
  injection, tracker status reporting, and credential-redacted remote settings.
- Added a tracker-table announce test action for direct HTTP(S)/UDP and configured proxy routes.
- Added category/tag-scoped label automation for completion moves, tracker overrides, ratio limits,
  and upload limits.
- Added conditional auto-delete with ratio/seed-age OR thresholds, label exclusion, and optional
  file deletion.
- Added persistent per-torrent weekday start/stop schedules with overnight-window support.
- Added Behavior settings for asynchronous lifecycle script hooks with JSON payload delivery.
- Added RSS episode parsing for `S01E05`/`1x05` releases, per-show quality/group rules, persisted
  feed definitions, and optional asynchronous TMDB/TVDB lookup.
- Added a background Cross-seed Helper with info-hash, piece-hash, piece-size, and optional library
  verification matching.
- Added a private-tracker profile that disables DHT/PEX/LSD and applies a bounded unchoke-slot cap
  to existing and newly added torrents.
- Added named creation presets for piece size, trackers, private flag, comments, and web seeds.

## [v0.1.0] - %Y->- (HEAD -> main, origin/main, origin/HEAD)

- Changed: Update README.md
- Added: Add files via upload
