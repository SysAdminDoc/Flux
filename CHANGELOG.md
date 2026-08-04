# Changelog

All notable changes to Flux will be documented in this file.

## Unreleased

- Added optional SHA-256 JSON sidecars for completed torrents, with a background hash executor,
  atomic publication, race detection, automatic completion generation, and a manual context action.
- Added conservative smart re-checking: unchanged files skip hashing, changed files validate only
  overlapping v1 pieces, and unsafe or unsupported cases fall back to libtorrent's full verifier.
- Added scheduled IP blocklist refresh with HTTP(S) mirror failover, bounded parsing, gzip support,
  atomic cache replacement, and status feedback while failed refreshes retain the last filter.
- Added VPN-address binding with a fail-closed kill switch: a missing configured address pauses
  active torrents, flashes the safety status in the UI, and never auto-resumes on recovery.
- Added fuzzy Settings search across tab names, group titles, labels, setting keys, and control
  placeholders, with matching tabs and groups kept visible while values remain editable.
- Added a persistent 7 x 24 local-time Activity Heatmap with separate download/upload volume bars
  and a Tools dialog for inspecting hourly traffic totals.
- Added a bounded per-torrent libtorrent alert buffer and a read-only Log tab in the detail panel;
  remote detail payloads carry the same filtered records.
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
- Added independent torrent-table column visibility, order, and width profiles per sidebar state.

## [v0.1.0] - %Y->- (HEAD -> main, origin/main, origin/HEAD)

- Changed: Update README.md
- Added: Add files via upload
