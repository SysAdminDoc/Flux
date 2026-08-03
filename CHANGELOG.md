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

## [v0.1.0] - %Y->- (HEAD -> main, origin/main, origin/HEAD)

- Changed: Update README.md
- Added: Add files via upload
