# Flux Torrent Client

![License](https://img.shields.io/badge/license-MIT-green) ![Platform](https://img.shields.io/badge/platform-PowerShell-lightgrey)

A clean, fast, privacy-focused BitTorrent client built with Python, PyQt6, and libtorrent.

## Features

### Core
- Full BitTorrent protocol support via libtorrent 2.0+
- BitTorrent v1, v2, and hybrid torrents with full info-hash pair visibility
- Magnet link and .torrent file support with drag-and-drop
- DHT, PEX, LSD for decentralized peer discovery
- Encryption support (disabled/prefer/require)
- Resume data with SQLite-backed persistence and schema versioning

### Performance
- **Threaded architecture** - libtorrent runs on a dedicated QThread, GUI never blocks on FFI calls
- **Snapshot-based updates** - thread-safe dataclasses cross the thread boundary, no shared mutable state
- **Differential model updates** - adds/removes rows individually, preserves scroll and selection state
- **Protected alert pipeline** - individual alert errors don't crash the event loop
- Per-torrent speed recording with 5-minute rolling history

### UI
- 6 dark themes: Flux Dark, Midnight Blue, Dracula, Nord, Solarized Dark, Monokai Pro
- Real-time speed sparklines in toolbar with peak/average annotations
- Detail panel with Overview, Files, Peers, Trackers, Piece Map, and per-torrent Log tabs
- Sidebar with state filters, categories, and session info
- Search/filter bar for quick torrent lookup
- Column visibility toggle (right-click table header)
- System tray with minimize-to-tray and speed tooltip
- Speed in title bar (optional)

### Torrent Management
- Per-torrent speed limits
- File priority editing (High / Normal / Low / Skip)
- Tracker add/remove in detail panel
- Queue position controls (Top / Up / Down / Bottom)
- Force recheck, force reannounce, sequential download
- Configurable on-complete actions (pause, remove, seed to ratio)

### Tools
- **Create Torrent** - build .torrent files from local files/folders with configurable piece size
- **RSS Feed Manager** - poll RSS/Atom feeds for new torrents with regex filtering and auto-download
- **IP Blocklist** - import PeerGuardian-format blocklists
- **Bandwidth Scheduling** - time-based upload/download limits
- **Peer Filtering** - auto-ban peers by client name patterns
- **Remote Web UI / API** - optional HTTP/WebSocket control surface with qBittorrent-compatible
  endpoints, bearer-token authentication, and TLS/mTLS support
- **Remote desktop mode** - connect the native UI to a headless Flux daemon while retaining the
  same torrent snapshot and detail models
- **Per-tracker proxy routing** - send HTTP(S) tracker announces through individual SOCKS5 or
  HTTP(S) proxies while keeping unrelated trackers direct
- **Label automation** - apply category/tag-scoped completion paths, tracker lists, ratio limits,
  and upload limits
- **Script hooks** - run configured shell commands on add, finish, delete, and error events with
  JSON payloads
- **RSS episode automation** - match `S01E05`/`1x05` releases with per-show quality and group
  rules, plus optional TMDB/TVDB title lookup
- **Cross-seed helper** - scan torrent metadata, compare info hashes/piece hashes, and verify
  candidates against a downloaded library before reusing content
- **Private-tracker profile** - disable DHT/PEX/LSD and cap unchoke slots with one setting
- **Creation presets** - save named piece-size, tracker, private, comment, and web-seed options
- **Column profiles** - keep focused Downloading, Seeding, and Completed table layouts
- **Activity Heatmap** - review accumulated download/upload volume across local weekday/hour cells
- **Settings search** - fuzzy-filter tabs and setting groups without changing their stored values

## Requirements

- Python 3.10+
- PyQt6
- libtorrent (python bindings, 2.0+)
- Optional I2P SAM bridge for I2P outbound transport (default `127.0.0.1:7656`)

## Installation

```bash
pip install PyQt6 libtorrent
```

On Windows, if libtorrent fails to load DLLs:
```bash
python fix_libtorrent.py
```

I2P transport is configured in Settings > Connection and requires a running local SAM bridge.
Per-tracker proxy rules are configured in the same tab, one per line as
`tracker URL | proxy URL`. Flux supports `socks5://`, `http://`, and `https://` proxy endpoints;
configured HTTP(S) tracker announces are routed through the selected proxy and returned peers are
added to the torrent. UDP trackers remain on libtorrent's direct announce path. Right-click any
tracker in the detail panel to run a one-shot announce test and inspect its response class and peer
counts.
Label automation rules are configured in Settings > Behavior as a JSON array. Each rule uses a
`label` matching a category or tag and can set `move_completed_path`, `tracker_overrides`,
`ratio_limit`, and `upload_limit` (bytes/s).
Conditional auto-delete is separately configurable in Settings > Behavior. It uses an OR between
the ratio and seeded-days thresholds, skips the configured exclusion label (default `archive`),
and can remove the downloaded files with the torrent.
Per-torrent start/stop schedules are also available as an info-hash keyed JSON object in the
Behavior tab. Schedules use local time, Monday=0 through Sunday=6, and support windows that cross
midnight.
Lifecycle script hooks are configured in Settings > Behavior as a JSON array. Each hook selects
an event (`on_add`, `on_finish`, `on_delete`, or `on_error`), a shell command, and optional timeout
and JSON stdin/argument delivery settings. Hooks run asynchronously so they do not block torrent
transfers or the UI.
RSS feeds are managed from Tools > RSS Feed Manager. A feed can use a JSON show-rule array with
aliases, season/episode constraints, resolution and codec requirements, and a release-group
allowlist. TMDB or TVDB credentials are optional; the Lookup button runs the selected provider in
the RSS worker and stores the chosen show identity in the rule.
Tools > Cross-seed Helper scans a metadata folder in the background. Exact info-hash and piece-hash
matches are high-confidence; file-list/piece-size matches are shown as lower-confidence candidates.
Optionally select the downloaded library to verify v1 piece hashes before acting on a candidate.
The private-tracker profile is configured in Settings > Connection. Enabling it disables DHT, PEX,
and LSD for existing and newly added torrents and applies the selected session-wide unchoke cap.
Create Torrent includes named presets with Load, Save As, and Delete controls; presets are stored in
the application settings and can be reused for public or private release templates.
The torrent table saves independent column visibility, order, and widths for the Downloading,
Seeding, Completed, and default sidebar filters. Right-click the header to toggle a column or reset
the active profile.
Tools > Activity Heatmap groups persisted session traffic into a 7 x 24 local-time grid. Each cell
shows separate download and upload intensity, with hover details for the exact recorded byte totals.
Settings includes a fuzzy search field above the tabs; matching group titles, labels, setting keys,
and control placeholders keep only the relevant tabs and groups visible while editing remains intact.

## Usage

```bash
# Launch the GUI
python -m flux.main

# Open a .torrent file directly
python -m flux.main path/to/file.torrent

# Open a magnet link directly
python -m flux.main "magnet:?xt=urn:btih:..."
```

The Remote tab in Settings can enable the embedded Web UI/API or connect this desktop to another
Flux daemon. Use a bearer token for API access; TLS client-certificate authentication is available
when the server is bound beyond localhost.

## Building

### PyInstaller (Windows)

```powershell
# PowerShell build script
.\Build-FluxTorrent.ps1

# Or manually
pyinstaller flux-torrent.spec
```

## Running Tests

```bash
python -m pytest tests/ -v
```

## Architecture

```
MainWindow (GUI thread)
    |
    |-- ThreadedSession
    |       |-- QThread ("FluxSessionThread")
    |       |       |-- SessionWorker (owns libtorrent session)
    |       |               |-- alert processing (500ms timer)
    |       |               |-- stats snapshots (1s timer)
    |       |               |-- resume data save (5min timer)
    |       |               |-- bandwidth schedule (1min timer)
    |       |
    |       |-- GUI -> Worker: queued pyqtSlot calls
    |       |-- Worker -> GUI: pyqtSignal emissions
    |
    |-- TorrentListModel (reads TorrentSnapshot dataclasses)
    |-- DetailPanel (reads DetailData dataclasses)
    |-- RSSMonitor (ThreadPoolExecutor for HTTP, GUI thread for signals)
```

All libtorrent FFI calls happen on the worker thread. The GUI thread only reads
pure Python dataclasses (`TorrentSnapshot`, `SessionStats`, `DetailData`) that
cross the thread boundary via Qt's signal/slot mechanism.

## License

MIT
