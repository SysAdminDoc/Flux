#!/usr/bin/env python3
"""Build script for Flux Torrent Client.

Creates a distributable package using PyInstaller.
Handles dependency checking, OpenSSL DLL bundling, and cleanup.

Usage:
    python build.py          # Full build
    python build.py --clean  # Clean build artifacts only
"""

import os
import sys
import shutil
import subprocess
from pathlib import Path


def check_deps():
    """Verify build dependencies are installed."""
    missing = []
    for mod in ['PyInstaller', 'libtorrent', 'PyQt6']:
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        print(f"Missing dependencies: {', '.join(missing)}")
        print("Install with: pip install " + " ".join(missing))
        return False
    return True


def find_openssl_dlls():
    """Find OpenSSL DLLs that libtorrent needs."""
    import libtorrent
    lt_dir = Path(libtorrent.__file__).parent
    dll_names = [
        'libssl-3-x64.dll', 'libcrypto-3-x64.dll',
        'libssl-1_1-x64.dll', 'libcrypto-1_1-x64.dll',
    ]
    found = []
    search_dirs = [lt_dir, lt_dir.parent, Path(sys.executable).parent]
    for search_dir in search_dirs:
        for name in dll_names:
            dll = search_dir / name
            if dll.exists():
                found.append(str(dll))
    return found


def clean():
    """Remove build artifacts."""
    for d in ['build', 'dist', '__pycache__']:
        if os.path.isdir(d):
            shutil.rmtree(d)
            print(f"  Removed {d}/")
    for f in Path('.').glob('*.spec.bak'):
        f.unlink()


def build():
    """Run PyInstaller build."""
    if not check_deps():
        sys.exit(1)

    print("=== Flux Torrent Client Build ===\n")

    print("[1/4] Checking dependencies...")
    import libtorrent as lt
    print(f"  libtorrent: {lt.__version__}")
    import PyQt6.QtCore
    print(f"  PyQt6: {PyQt6.QtCore.PYQT_VERSION_STR}")

    print("\n[2/4] Finding OpenSSL DLLs...")
    dlls = find_openssl_dlls()
    for dll in dlls:
        print(f"  Found: {Path(dll).name}")
    if not dlls:
        print("  WARNING: No OpenSSL DLLs found. Build may not work on other machines.")

    print("\n[3/4] Running PyInstaller...")
    result = subprocess.run([
        sys.executable, '-m', 'PyInstaller',
        'flux.spec',
        '--noconfirm',
    ], capture_output=False)

    if result.returncode != 0:
        print("\nBuild FAILED!")
        sys.exit(1)

    print("\n[4/4] Build complete!")
    dist_dir = Path('dist/FluxTorrent')
    if dist_dir.exists():
        exe = dist_dir / 'FluxTorrent.exe'
        if exe.exists():
            size_mb = exe.stat().st_size / (1024 * 1024)
            print(f"  Output: {dist_dir}")
            print(f"  Executable: {size_mb:.1f} MB")
        total = sum(f.stat().st_size for f in dist_dir.rglob('*') if f.is_file())
        print(f"  Total size: {total / (1024 * 1024):.1f} MB")
    else:
        print("  Output directory not found - check for errors above.")


if __name__ == '__main__':
    os.chdir(Path(__file__).parent)
    if '--clean' in sys.argv:
        print("Cleaning build artifacts...")
        clean()
        print("Done.")
    else:
        build()
