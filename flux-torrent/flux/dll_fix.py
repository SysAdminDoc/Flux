"""Windows DLL fix for libtorrent. Import before libtorrent.

Auto-detects OpenSSL DLLs and adds them to the search path.
Works in both normal Python and frozen PyInstaller bundles.
"""
import sys
import os


def _is_frozen() -> bool:
    return getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS')


SEARCH_DIRS = [
    r"C:\Program Files\FireDaemon OpenSSL 3\bin",
    r"C:\Program Files\FireDaemon OpenSSL 3\x64\bin",
    r"C:\Program Files\OpenSSL-Win64\bin",
    r"C:\Program Files\OpenSSL\bin",
    r"C:\OpenSSL-Win64\bin",
    r"C:\Program Files\Git\mingw64\bin",
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Git", "mingw64", "bin"),
    r"C:\msys64\mingw64\bin",
    r"C:\Strawberry\c\bin",
]


def setup():
    if sys.platform != "win32":
        return True

    import ctypes

    dirs = []

    # In frozen app, DLLs should be bundled alongside the exe
    if _is_frozen():
        exe_dir = os.path.dirname(sys.executable)
        dirs.append(exe_dir)
        # Also check libtorrent subdirectory (PyInstaller COLLECT layout)
        lt_subdir = os.path.join(exe_dir, 'libtorrent')
        if os.path.isdir(lt_subdir):
            dirs.append(lt_subdir)
        # And the _MEIPASS temp dir
        dirs.append(sys._MEIPASS)
    else:
        # Normal Python: find libtorrent via importlib
        try:
            import importlib.util
            spec = importlib.util.find_spec("libtorrent")
            if spec and spec.origin:
                dirs.append(os.path.dirname(spec.origin))
        except Exception:
            pass

        try:
            import site
            for sp in site.getsitepackages():
                if os.path.isdir(sp):
                    dirs.append(sp)
        except Exception:
            pass

    dirs.extend(SEARCH_DIRS)

    # Register with os.add_dll_directory (Python 3.8+)
    if hasattr(os, "add_dll_directory"):
        for d in dirs:
            if d and os.path.isdir(d):
                try:
                    os.add_dll_directory(d)
                except OSError:
                    pass

    # Add to PATH
    current_path = os.environ.get("PATH", "")
    additions = [d for d in dirs if d and os.path.isdir(d) and d not in current_path]
    if additions:
        os.environ["PATH"] = ";".join(additions) + ";" + current_path

    # Preload OpenSSL DLLs (both 1.1 and 3.x variants)
    dll_pairs = [
        ("libcrypto-1_1-x64.dll", "libssl-1_1-x64.dll"),
        ("libcrypto-3-x64.dll", "libssl-3-x64.dll"),
    ]

    for d in dirs:
        if not d or not os.path.isdir(d):
            continue
        for crypto_name, ssl_name in dll_pairs:
            crypto = os.path.join(d, crypto_name)
            ssl_dll = os.path.join(d, ssl_name)
            if os.path.exists(crypto) and os.path.exists(ssl_dll):
                try:
                    ctypes.CDLL(crypto)
                    ctypes.CDLL(ssl_dll)
                    return True
                except OSError:
                    pass
                try:
                    ctypes.WinDLL(crypto)
                    ctypes.WinDLL(ssl_dll)
                    return True
                except OSError:
                    pass
    return False


setup()
