# Changelog

All notable changes to Flux will be documented in this file.

## [v1.1.0] - 2026-08-03

- Added optional SHA-256 JSON sidecars for completed torrents, with a background hash executor,
  atomic publication, race detection, automatic completion generation, and a manual context action.
- Added reproducible unsigned portable, Debian, Flatpak, and AUR packaging from the native
  PyInstaller output.
- Added optional ratio milestone desktop notifications with custom thresholds and informational
  suggested actions that never mutate torrent state.
- Added bounded persistent peer reputation memory with weighted disconnect/error/hash-failure
  scores and per-peer transfer caps for repeat offenders without automatic IP bans.
- Added peer-colored piece availability mapping in the Pieces tab, with bounded cross-thread data
  and a legend for connected peers advertising incomplete pieces.
- Added non-blocking HTTPS completion webhooks for Discord and Telegram, with provider-specific
  payloads and redacted delivery status.
- Added Tools > Export Session Stats with rolling-history CSV and JSON output for Grafana-style
  dashboards.
- Added an opt-in `flux.plugins` Python entry-point SDK with asynchronous lifecycle dispatch and
  archive extraction, completion move, and tracker announce logger examples.
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

## Roadmap archive — 2026-08-10 — ROADMAP.md

<details>
<summary>Original roadmap snapshot</summary>

```markdown
# ROADMAP

Backlog for Flux Torrent Client. Target: match qBittorrent's feature density with a cleaner
PyQt6 UI, while staying single-binary-installable.

## Planned Features

### Web / remote

### Protocol coverage

### Scheduling / automation

### Content tools

### UI

### Safety / integrity

### Distribution

## Competitive Research

- **qBittorrent** — the reference: polished WebUI, *arr integration, tag system, search engines.
  Flux should match the API and tag/category coverage to slot into existing home-server stacks.
- **Deluge** — daemon/client split and plugin ecosystem (label, ltConfig, execute). Borrow the
  daemon architecture and execute-style ScriptHooks.
- **Transmission** — minimal resource use, strong remote tooling. Benchmark Flux's idle CPU/mem
  against it.
- **rTorrent + ruTorrent** — CLI + web. The cross-seed and ratio-group features are mature here.
- **Vuze / Azureus (legacy)** — cautionary tale on feature bloat; don't replicate its plugin
  market mess.

## Nice-to-Haves

## Open-Source Research (Round 2)

### Related OSS Projects
- **qBittorrent** — https://github.com/qbittorrent/qBittorrent — C++/Qt6 on libtorrent-rasterbar; de-facto reference implementation.
- **MacTorrent** — https://github.com/al-macleod/MacTorrent — Python + PyQt6 + libtorrent; closest peer. Randomized listen ports, optional post-download encryption.
- **BAT-Torrent** — https://github.com/Mateuscruz19/BAT-Torrent — C++/Qt6/libtorrent with simplicity + privacy focus; minimal feature set — good baseline UI.
- **R3DDY97/BitTorrent-client** — https://github.com/R3DDY97/BitTorrent-client — Python+libtorrent minimal personal client; readable protocol walkthrough.
- **Deluge** — https://github.com/deluge-torrent/deluge — Python/GTK libtorrent client; plugin system is the gold standard in OSS torrenting.
- **qBittorrent Enhanced Edition** — https://github.com/c0re100/qBittorrent-Enhanced-Edition — Adds auto-ban for fake-progress peers, IP block-by-ASN; privacy extras worth mirroring.
- **libtorrent-rasterbar** — https://github.com/arvidn/libtorrent — Underlying lib; follow releases for BEP support changes.

### Features to Borrow
- Plugin architecture from `Deluge` — Python hook points for RSS auto-dl, auto-unrar, auto-move, custom trackers.
- Fake-peer / leech-blocker lists from `qBittorrent Enhanced Edition` — ban peers sending invalid progress or known bad client strings.
- ASN/IP block lists (`Enhanced Edition`) — prefilter anti-piracy honeypot nets (IPP2P-style) without relying on VPN.
- Randomized listen port per session (`MacTorrent`) — fingerprinting resistance vs. static port detection.
- Sequential-download + streaming piece-picker tweaks (`libtorrent` session_settings) — expose to UI for video preview.
- Built-in WebUI on a bound port (`qBittorrent`) — headless mode for same-process remote control.
- Anonymous mode toggle — disable DHT, LSD, PEX per-torrent (`qBittorrent` `anonymous_mode` flag).

### Patterns & Architectures Worth Studying
- **libtorrent session + `save_resume_data` checkpoints** (`qBittorrent`): persist resume state every N seconds, not just on shutdown — survives kill-9.
- **QAbstractItemModel over torrent list** (`qBittorrent`): scales to 10k+ torrents with virtualized view; avoid naive `QListWidget`.
- **Per-torrent share-ratio / seed-time stop criteria** (`Deluge`): declarative rules, applied by scheduler; keeps seed-bleeder ratios in check without manual stop.
- **SOCKS5/HTTP proxy with auth per-session** (`qBittorrent`): pipe libtorrent through proxy for IP isolation; I2P support via SAM bridge for the paranoid tier.
```

</details>
