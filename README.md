# Flux Torrent Client

A clean, fast, privacy-focused BitTorrent client built with Python, PyQt6, and libtorrent.

## Features

### Core
- Full BitTorrent protocol support via libtorrent 2.0+
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
- Detail panel with Overview, Files, Peers, Trackers, and Piece Map tabs
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

## Requirements

- Python 3.10+
- PyQt6
- libtorrent (python bindings, 2.0+)

## Installation

```bash
pip install PyQt6 libtorrent
```

On Windows, if libtorrent fails to load DLLs:
```bash
python fix_libtorrent.py
```

## Usage

```bash
# Launch the GUI
python -m flux.main

# Open a .torrent file directly
python -m flux.main path/to/file.torrent

# Open a magnet link directly
python -m flux.main "magnet:?xt=urn:btih:..."
```

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
