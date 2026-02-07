"""Flux Torrent - Complete libtorrent DLL fixer.

Auto-detects OpenSSL version needed (1.1 vs 3.x) from PE import table.
Downloads correct version. Fixes everything.
"""
import sys
import os
import struct
import ctypes
import importlib.util
import shutil
import subprocess
import urllib.request
import tempfile

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Referer": "https://www.firedaemon.com/download-firedaemon-openssl",
}

OPENSSL_11_ZIPS = [
    "https://download.firedaemon.com/FireDaemon-OpenSSL/openssl-1.1.1w.zip",
]
OPENSSL_11_DLLS = ["libcrypto-1_1-x64.dll", "libssl-1_1-x64.dll"]

OPENSSL_3X_ZIPS = [
    "https://download.firedaemon.com/FireDaemon-OpenSSL/openssl-3.6.1.zip",
    "https://download.firedaemon.com/FireDaemon-OpenSSL/openssl-3.5.5.zip",
]
OPENSSL_3X_DLLS = ["libcrypto-3-x64.dll", "libssl-3-x64.dll"]

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


def ok(msg):    print(f"  \033[92m[+]\033[0m {msg}")
def warn(msg):  print(f"  \033[93m[!]\033[0m {msg}")
def err(msg):   print(f"  \033[91m[X]\033[0m {msg}")
def step(msg):  print(f"  \033[90m[*]\033[0m {msg}")
def header(msg):
    print()
    print(f"  \033[96m{msg}\033[0m")
    print(f"  \033[96m{'-' * len(msg)}\033[0m")


def ensure_pefile():
    try:
        import pefile
        return pefile
    except ImportError:
        step("Installing pefile...")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "pefile", "--quiet"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        import pefile
        return pefile


def get_pe_imports(filepath):
    pefile = ensure_pefile()
    pe = pefile.PE(filepath, fast_load=False)
    names = []
    if hasattr(pe, "DIRECTORY_ENTRY_IMPORT"):
        for e in pe.DIRECTORY_ENTRY_IMPORT:
            names.append(e.dll.decode("utf-8", errors="replace"))
    pe.close()
    return names


def get_pe_arch(filepath):
    try:
        pefile = ensure_pefile()
        pe = pefile.PE(filepath, fast_load=True)
        m = pe.FILE_HEADER.Machine
        pe.close()
        return {0x14c: "x86", 0x8664: "x64", 0xaa64: "ARM64"}.get(m, hex(m))
    except Exception:
        return "unknown"


def try_load(dll_name, lt_dir):
    try:
        ctypes.WinDLL(dll_name)
        return "system", None
    except OSError:
        pass
    local = os.path.join(lt_dir, dll_name)
    if os.path.exists(local):
        try:
            ctypes.WinDLL(local)
            return "local", local
        except OSError:
            return "broken", local
    for d in SEARCH_DIRS:
        if not d or not os.path.isdir(d):
            continue
        fp = os.path.join(d, dll_name)
        if os.path.exists(fp):
            try:
                ctypes.WinDLL(fp)
                return "found", fp
            except OSError:
                pass
    return "missing", None


def download_file(url):
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        return urllib.request.urlopen(req, timeout=60).read()
    except Exception as e:
        warn(str(e))
        return None


def detect_openssl(imports):
    for dll in imports:
        dl = dll.lower()
        if "libcrypto-1_1" in dl or "libssl-1_1" in dl:
            return "1.1", OPENSSL_11_DLLS, OPENSSL_11_ZIPS
        if "libcrypto-3" in dl or "libssl-3" in dl:
            return "3.x", OPENSSL_3X_DLLS, OPENSSL_3X_ZIPS
    return None, [], []


def provision_zip(needed, zip_urls, lt_dir):
    import zipfile, io
    for url in zip_urls:
        fname = url.split("/")[-1]
        step(f"Downloading {fname}...")
        data = download_file(url)
        if not data or len(data) < 50000:
            continue
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                entries = zf.namelist()
                for dll_name in needed:
                    candidates = [e for e in entries
                                  if os.path.basename(e).lower() == dll_name.lower()]
                    x64 = [c for c in candidates if "x64" in c.lower() or "win64" in c.lower()]
                    pick = x64[0] if x64 else (candidates[0] if candidates else None)
                    if pick:
                        dest = os.path.join(lt_dir, dll_name)
                        with zf.open(pick) as src, open(dest, "wb") as dst:
                            dst.write(src.read())
                        ok(f"Extracted {dll_name} ({os.path.getsize(dest):,} bytes)")
            if all(os.path.exists(os.path.join(lt_dir, d)) for d in needed):
                return True
        except Exception as e:
            warn(f"ZIP failed: {e}")
    return False


def provision_pip(needed, lt_dir):
    step("Trying pip install libtorrent-windows-dll...")
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "libtorrent-windows-dll", "--quiet"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        import site
        for sp in site.getsitepackages():
            if not isinstance(sp, str) or not os.path.isdir(sp):
                continue
            for dirpath, _, filenames in os.walk(sp):
                lower = [f.lower() for f in filenames]
                if all(d.lower() in lower for d in needed):
                    for dll_name in needed:
                        actual = next(f for f in filenames if f.lower() == dll_name.lower())
                        shutil.copy2(os.path.join(dirpath, actual), lt_dir)
                        ok(f"Copied {dll_name} from pip package")
                    return True
    except Exception as e:
        warn(f"pip package: {e}")
    return False


def copy_system(needed, lt_dir):
    for d in SEARCH_DIRS:
        if not d or not os.path.isdir(d):
            continue
        if all(os.path.exists(os.path.join(d, n)) for n in needed):
            for n in needed:
                shutil.copy2(os.path.join(d, n), lt_dir)
                ok(f"Copied {n} from {d}")
            return True
    return False


def main():
    if sys.platform == "win32":
        os.system("")

    print()
    print("  \033[96mFlux Torrent - libtorrent DLL Fixer\033[0m")
    print("  \033[96m====================================\033[0m")
    print()
    print(f"  Python: {sys.version}")
    print(f"  Arch:   {struct.calcsize('P') * 8}-bit")

    # 1. Find libtorrent
    header("1. Locate libtorrent")
    spec = importlib.util.find_spec("libtorrent")
    if not spec or not spec.origin:
        err("libtorrent not found. Run: pip install libtorrent")
        return 1

    pyd = spec.origin
    lt_dir = os.path.dirname(pyd)
    ok(pyd)
    step(f"{os.path.getsize(pyd):,} bytes, {get_pe_arch(pyd)}")
    step("Files:")
    for f in sorted(os.listdir(lt_dir)):
        step(f"  {f} ({os.path.getsize(os.path.join(lt_dir, f)):,} bytes)")

    # 2. Read PE imports
    header("2. Read .pyd import table")
    direct_imports = get_pe_imports(pyd)
    if not direct_imports:
        err("Could not read PE import table")
        return 1

    for d in direct_imports:
        print(f"      {d}")

    ssl_ver, ssl_dlls, ssl_zips = detect_openssl(direct_imports)
    print()
    if ssl_ver:
        ok(f"Needs OpenSSL {ssl_ver} ({', '.join(ssl_dlls)})")
    else:
        ok("No OpenSSL dependency detected")

    # 3. Test each
    header("3. Test all dependencies")
    if hasattr(os, "add_dll_directory"):
        try:
            os.add_dll_directory(lt_dir)
        except OSError:
            pass
    os.environ["PATH"] = lt_dir + ";" + os.environ.get("PATH", "")

    all_missing = []
    for dll in direct_imports:
        status, path = try_load(dll, lt_dir)
        if status == "system":     ok(f"{dll} (system)")
        elif status == "local":    ok(f"{dll} (libtorrent dir)")
        elif status == "found":    ok(f"{dll} ({path})")
        elif status == "broken":   warn(f"{dll} (exists, won't load)"); all_missing.append(dll)
        else:                      err(f"{dll} NOT FOUND"); all_missing.append(dll)

    # Transitive
    local_dlls = [f for f in os.listdir(lt_dir) if f.lower().endswith(".dll")]
    if local_dlls:
        print()
        step("Transitive dependencies...")
        for dll_file in local_dlls:
            for sub in get_pe_imports(os.path.join(lt_dir, dll_file)):
                s, _ = try_load(sub, lt_dir)
                if s in ("missing", "broken"):
                    err(f"  {dll_file} -> {sub} NOT FOUND")
                    if sub not in all_missing:
                        all_missing.append(sub)
        if not any(s in all_missing for s in all_missing if s not in direct_imports):
            ok("Transitive OK")

    # 4. Fix
    if all_missing:
        header("4. Fix missing DLLs")
        step(f"Missing: {', '.join(all_missing)}")

        openssl_missing = [d for d in all_missing if "ssl" in d.lower() or "crypto" in d.lower()]
        other_missing = [d for d in all_missing if d not in openssl_missing]

        if other_missing:
            err(f"Non-OpenSSL: {', '.join(other_missing)}")
            print(f"    VC++ Redistributable: https://aka.ms/vs/17/release/vc_redist.x64.exe")

        if openssl_missing:
            step(f"Provisioning OpenSSL {ssl_ver}: {', '.join(openssl_missing)}")

            # Remove wrong-version DLLs
            wrong = OPENSSL_3X_DLLS if ssl_ver == "1.1" else OPENSSL_11_DLLS
            for w in wrong:
                wp = os.path.join(lt_dir, w)
                if os.path.exists(wp):
                    os.remove(wp)
                    warn(f"Removed wrong-version {w}")

            fixed = copy_system(openssl_missing, lt_dir)
            if not fixed:
                fixed = provision_zip(openssl_missing, ssl_zips, lt_dir)
            if not fixed and ssl_ver == "1.1":
                fixed = provision_pip(openssl_missing, lt_dir)
            if not fixed:
                err("All automatic methods failed.")
                print()
                print(f"    Manual fix:")
                print(f"    1. Go to: https://www.firedaemon.com/download-firedaemon-openssl")
                if ssl_ver == "1.1":
                    print(f"    2. Scroll to bottom, download 'OpenSSL 1.1.1w ZIP'")
                    print(f"    3. Extract x64/bin DLLs to: {lt_dir}")
                else:
                    print(f"    2. Download 'EXE Installer x64' and run it")
                    print(f"    3. Copy DLLs to: {lt_dir}")
                return 1
    else:
        header("4. No missing DLLs")
        ok("Everything present")

    # 5. Verify
    header("5. Final verification")
    if hasattr(os, "add_dll_directory"):
        try:
            os.add_dll_directory(lt_dir)
        except OSError:
            pass
    os.environ["PATH"] = lt_dir + ";" + os.environ.get("PATH", "")

    step("Preloading DLLs...")
    for f in sorted(os.listdir(lt_dir)):
        if f.lower().endswith(".dll"):
            fp = os.path.join(lt_dir, f)
            for loader in [ctypes.CDLL, ctypes.WinDLL]:
                try:
                    loader(fp)
                    ok(f"Preloaded {f}")
                    break
                except OSError:
                    pass

    print()
    step("import libtorrent...")
    try:
        import libtorrent
        ok(f"SUCCESS! libtorrent v{libtorrent.__version__}")
        print()
        print("  \033[92m==============================\033[0m")
        print("  \033[92m  libtorrent is working!      \033[0m")
        print("  \033[92m  Run Flux.bat to launch.     \033[0m")
        print("  \033[92m==============================\033[0m")
        print()
        return 0
    except ImportError as e:
        err(f"FAILED: {e}")
        step("Architecture check:")
        for f in os.listdir(lt_dir):
            if f.lower().endswith((".dll", ".pyd")):
                step(f"  {f}: {get_pe_arch(os.path.join(lt_dir, f))}")
        err("Try: pip install --force-reinstall libtorrent")
        return 1


if __name__ == "__main__":
    try:
        code = main()
        if code != 0:
            input("\n  Press Enter to exit...")
        sys.exit(code)
    except KeyboardInterrupt:
        print("\n  Interrupted.")
    except Exception as e:
        print(f"\n  Error: {e}")
        import traceback
        traceback.print_exc()
        input("\n  Press Enter to exit...")
        sys.exit(1)
