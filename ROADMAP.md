# ROADMAP

Backlog for Flux Torrent Client. Target: match qBittorrent's feature density with a cleaner
PyQt6 UI, while staying single-binary-installable.

## Planned Features

### Web / remote
- **Web UI** — HTTP/WebSocket server that mirrors the native UI, served from the same process.
  Biggest gap vs qBittorrent.
- **WebUI API compatibility shim** — implement qBittorrent's REST API endpoints so Sonarr/Radarr
  and the whole *arr stack can talk to Flux unchanged.
- **mTLS + token auth** on the WebUI.
- **Remote desktop client mode** — the GUI connects to a headless Flux daemon (Deluge-style
  client/daemon split), using the same dataclass snapshots the local model uses.

### Protocol coverage
- **BitTorrent v2 hybrid-torrent support** — ensure libtorrent 2.x v2 pieces and info-hash pairs
  render correctly, esp. in Piece Map.
- **I2P outbound support** via libtorrent's `--enable-i2p` wiring.
- **SOCKS5 / HTTPS proxy per-tracker** not just session-wide.
- **Tracker announce test tool** — right-click a tracker, run a synthetic announce and show
  result (error class, peers returned).

### Scheduling / automation
- **Per-label automation rules** — move-on-complete path, tracker overrides, ratio limit, upload
  limit scoped to a label.
- **Conditional auto-delete** — "delete torrent + files when ratio>X OR seeded>Y days, but not if
  label=archive".
- **Scheduled start/stop per torrent** (currently session-wide bandwidth schedule).
- **ScriptHooks on lifecycle events** — on-add / on-finish / on-delete call user-configured
  shell commands with a JSON payload.

### Content tools
- **RSS → episode parser** with tvdb/tmdb lookup for `S01E05`-style matching, per-show rules
  (resolution, codec, group allowlist).
- **Torrent cross-seed helper** — scan a library and cross-seed matched content across trackers
  using file-hash or piece-size matching.
- **Private-tracker profile** with conservative defaults (no DHT/PEX/LSD, cap unchoke slots).
- **Creation presets** — resource sets (piece size, trackers, private flag) savable as named
  templates.

### UI
- **Column profiles** per tab — different columns for Downloading vs Seeding vs Completed views.
- **Details-panel Log tab** showing libtorrent alerts for that torrent only.
- **Activity heatmap** — 24 x 7 calendar of upload/download volume.
- **Settings search** with fuzzy match.

### Safety / integrity
- **VPN bind-to-interface** with kill-switch — if interface disappears, pause all torrents and
  flash UI.
- **Blocklist auto-refresh** on schedule with failover mirror.
- **Smart re-check** — only re-check dirty pieces based on moov/mod time heuristics, not full
  file.
- **Integrity verifier** — optional SHA-256 sidecar manifest generation for completed torrents.

### Distribution
- **Signed Windows installer + portable zip**.
- **macOS universal `.app`** — notarized, with `libtorrent-rasterbar` bundled.
- **Flatpak + `.deb`** for Linux, plus AUR.

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

- **Desktop notification on ratio milestones** (1.0, 2.0, custom) with suggested action.
- **Peer reputation memory** — remember peers that repeatedly hash-fail or disconnect and
  deprioritize them across sessions.
- **Piece map live painting** during download (already a tab; add per-peer color coding).
- **Discord / Telegram webhook** on torrent complete.
- **Stats export** — CSV/JSON of historical session stats for homelab Grafana dashboards.
- **Plugin SDK** (Python entry points) with 3-5 example plugins (auto-extract, post-complete move
  with renaming rules, tracker announce logger).

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
