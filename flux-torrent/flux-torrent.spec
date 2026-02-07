# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec file for Flux Torrent Client.

Build with:  pyinstaller flux-torrent.spec --noconfirm
Output:      dist/FluxTorrent/FluxTorrent.exe
"""

import os
import sys

# Find libtorrent DLLs
lt_path = None
try:
    import libtorrent
    lt_dir = os.path.dirname(libtorrent.__file__)
    lt_path = lt_dir
except ImportError:
    pass

# Collect libtorrent binaries
lt_binaries = []
if lt_path:
    for f in os.listdir(lt_path):
        if f.endswith(('.dll', '.pyd', '.so')):
            lt_binaries.append((os.path.join(lt_path, f), 'libtorrent'))

# Also collect OpenSSL DLLs from common locations
openssl_dlls = []
for search_dir in [sys.prefix, os.path.join(sys.prefix, 'Library', 'bin'),
                   os.path.join(sys.prefix, 'DLLs')]:
    if os.path.isdir(search_dir):
        for f in os.listdir(search_dir):
            if f.lower().startswith(('libssl', 'libcrypto', 'ssleay', 'libeay')):
                openssl_dlls.append((os.path.join(search_dir, f), '.'))

a = Analysis(
    ['flux/__main__.py'],
    pathex=[],
    binaries=lt_binaries + openssl_dlls,
    datas=[
        ('resources', 'resources'),
    ],
    hiddenimports=[
        'libtorrent',
        'PyQt6',
        'PyQt6.QtCore',
        'PyQt6.QtGui',
        'PyQt6.QtWidgets',
        'PyQt6.sip',
        'concurrent.futures',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'matplotlib',
        'numpy',
        'scipy',
        'pandas',
        'PIL',
        'cv2',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='FluxTorrent',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,  # Set to 'resources/icons/flux.ico' when icon available
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='FluxTorrent',
)
