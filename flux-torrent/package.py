#!/usr/bin/env python3
"""Build unsigned portable and Linux distribution artifacts.

The Windows PowerShell build remains the canonical Windows entry point. This
script adds a versioned portable archive and Linux-native .deb, Flatpak, and
AUR source archives without requiring signing credentials.

Usage:
    python package.py --portable
    python package.py --linux
    python package.py --linux --skip-build
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
DIST_ROOT = PROJECT_ROOT / "dist"
APP_DIR = DIST_ROOT / "FluxTorrent"
APP_ID = "com.sysadmindoc.FluxTorrent"


def project_version() -> str:
    """Read the application version without importing optional dependencies."""
    init_text = (PROJECT_ROOT / "flux" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*[\"\']([^\"\']+)', init_text, re.MULTILINE)
    if not match:
        raise RuntimeError("Could not determine application version")
    return match.group(1)


def run(command: list[str]) -> None:
    print("+", " ".join(command))
    subprocess.run(command, cwd=PROJECT_ROOT, check=True)


def build_pyinstaller() -> None:
    """Build the platform-native application directory."""
    run([
        sys.executable,
        "-m",
        "PyInstaller",
        "flux-torrent.spec",
        "--noconfirm",
        "--clean",
    ])
    executable = APP_DIR / ("FluxTorrent.exe" if sys.platform == "win32" else "FluxTorrent")
    if not executable.is_file():
        raise RuntimeError(f"PyInstaller did not produce {executable}")


def require_app_dir() -> None:
    if not APP_DIR.is_dir():
        raise RuntimeError(f"Missing {APP_DIR}; run without --skip-build first")


def make_portable_archive(version: str) -> Path:
    require_app_dir()
    archive_base = DIST_ROOT / f"FluxTorrent-{version}-portable"
    archive_path = Path(shutil.make_archive(
        str(archive_base), "zip", root_dir=DIST_ROOT, base_dir=APP_DIR.name
    ))
    print(f"Created {archive_path}")
    return archive_path


def make_linux_source_archive(version: str) -> Path:
    """Create the self-contained tree consumed by the AUR recipe."""
    require_app_dir()
    archive_base = DIST_ROOT / f"FluxTorrent-{version}-linux-x86_64"
    with tempfile.TemporaryDirectory(prefix="flux-linux-archive-") as temp_dir:
        root = Path(temp_dir) / archive_base.name
        shutil.copytree(APP_DIR, root / APP_DIR.name)
        shutil.copy2(PROJECT_ROOT / "packaging" / "linux" / "flux-torrent.desktop", root)
        shutil.copy2(PROJECT_ROOT.parent / "icon.png", root)
        archive_path = Path(shutil.make_archive(
            str(archive_base), "gztar", root_dir=root.parent, base_dir=root.name
        ))
    print(f"Created {archive_path}")
    return archive_path


def make_deb(version: str) -> Path:
    """Build an amd64 .deb around the native PyInstaller directory."""
    require_app_dir()
    dpkg_deb = shutil.which("dpkg-deb")
    if not dpkg_deb:
        raise RuntimeError("dpkg-deb is required for --linux")

    output = DIST_ROOT / f"flux-torrent_{version}_amd64.deb"
    with tempfile.TemporaryDirectory(prefix="flux-deb-") as temp_dir:
        stage = Path(temp_dir)
        opt_dir = stage / "opt" / "flux-torrent"
        shutil.copytree(APP_DIR, opt_dir / "FluxTorrent")

        launcher = stage / "usr" / "bin" / "flux-torrent"
        launcher.parent.mkdir(parents=True, exist_ok=True)
        launcher.write_text(
            "#!/bin/sh\nexec /opt/flux-torrent/FluxTorrent/FluxTorrent \"$@\"\n",
            encoding="utf-8",
        )
        launcher.chmod(0o755)

        desktop = stage / "usr" / "share" / "applications" / f"{APP_ID}.desktop"
        desktop.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT_ROOT / "packaging" / "linux" / "flux-torrent.desktop", desktop)

        icon = stage / "usr" / "share" / "icons" / "hicolor" / "512x512" / "apps" / f"{APP_ID}.png"
        icon.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT_ROOT.parent / "icon.png", icon)

        control = stage / "DEBIAN" / "control"
        control.parent.mkdir(parents=True, exist_ok=True)
        control.write_text(
            """Package: flux-torrent
Version: {version}
Section: net
Priority: optional
Architecture: amd64
Maintainer: SysAdminDoc <matt_parker@outlook.com>
Depends: libglib2.0-0, libx11-6, libxcb1
Description: Flux Torrent client
 A privacy-focused PyQt6 BitTorrent client backed by libtorrent.
""".format(version=version),
            encoding="utf-8",
        )
        run([
            dpkg_deb,
            "--build",
            "--root-owner-group",
            "-Zgzip",
            "-z6",
            str(stage),
            str(output),
        ])
    print(f"Created {output}")
    return output


def make_flatpak(version: str) -> Path:
    """Build a Flatpak using the checked-in local-source manifest."""
    require_app_dir()
    flatpak_builder = shutil.which("flatpak-builder")
    flatpak = shutil.which("flatpak")
    if not flatpak_builder or not flatpak:
        raise RuntimeError("flatpak-builder and flatpak are required for --linux")

    output = DIST_ROOT / f"FluxTorrent-{version}-linux-x86_64.flatpak"
    manifest = PROJECT_ROOT / "packaging" / "linux" / f"{APP_ID}.yml"
    with tempfile.TemporaryDirectory(prefix="flux-flatpak-") as temp_dir:
        temp_root = Path(temp_dir)
        build_dir = temp_root / "build"
        repo_dir = temp_root / "repo"
        state_dir = temp_root / "state"
        run([
            flatpak_builder,
            "--force-clean",
            "--disable-rofiles-fuse",
            "--state-dir",
            str(state_dir),
            "--repo",
            str(repo_dir),
            str(build_dir),
            str(manifest),
        ])
        run([flatpak, "build-bundle", str(repo_dir), str(output), APP_ID])
    print(f"Created {output}")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--portable", action="store_true", help="build a versioned portable zip")
    parser.add_argument("--linux", action="store_true", help="build Linux zip, source tarball, .deb, and Flatpak")
    parser.add_argument("--skip-build", action="store_true", help="package the existing dist/FluxTorrent directory")
    args = parser.parse_args()
    if not args.portable and not args.linux:
        parser.error("choose --portable or --linux")

    version = project_version()
    if not args.skip_build:
        build_pyinstaller()
    require_app_dir()
    make_portable_archive(version)
    if args.linux:
        make_linux_source_archive(version)
        make_deb(version)
        make_flatpak(version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
