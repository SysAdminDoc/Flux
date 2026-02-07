#!/usr/bin/env python3
"""Flux Torrent Client - entry point.

Handles both normal Python execution and frozen PyInstaller bundles.
"""

import sys
import os
import logging
import traceback
from pathlib import Path


def _is_frozen() -> bool:
    return getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS')


def _app_root() -> Path:
    """Return the application root directory."""
    if _is_frozen():
        return Path(sys._MEIPASS)
    return Path(__file__).parent.parent


def _crash_dialog(title: str, message: str):
    """Show a crash dialog. Works even if Qt isn't fully loaded."""
    # Write to crash log first
    try:
        crash_dir = Path.home() / ".flux-torrent" / "logs"
        crash_dir.mkdir(parents=True, exist_ok=True)
        crash_log = crash_dir / "crash.log"
        crash_log.write_text(f"{title}\n\n{message}", encoding="utf-8")
    except Exception:
        pass

    # Try Qt message box
    try:
        from PyQt6.QtWidgets import QApplication, QMessageBox
        app = QApplication.instance()
        if app is None:
            app = QApplication(sys.argv)
        QMessageBox.critical(None, title, message)
        return
    except Exception:
        pass

    # Fallback: Windows message box
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, message, title, 0x10)
            return
        except Exception:
            pass

    # Last resort: stderr
    print(f"FATAL: {title}\n{message}", file=sys.stderr)


def setup_logging():
    log_dir = Path.home() / ".flux-torrent" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    handlers = [logging.FileHandler(log_dir / "flux.log", encoding="utf-8")]
    if not _is_frozen():
        handlers.append(logging.StreamHandler(sys.stdout))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
    )


def main():
    setup_logging()
    logger = logging.getLogger("flux")
    logger.info("Starting Flux Torrent Client...")

    if not _is_frozen():
        sys.path.insert(0, str(_app_root()))

    # Fix Windows DLL loading before any libtorrent import
    try:
        from flux import dll_fix
    except Exception:
        pass

    try:
        import libtorrent as lt
        logger.info(f"libtorrent version: {lt.__version__}")
    except ImportError as e:
        err = str(e)
        logger.error(f"libtorrent import failed: {err}")
        _crash_dialog(
            "Flux Torrent - Missing Dependency",
            f"Failed to load libtorrent:\n{err}\n\n"
            "Make sure libtorrent and its OpenSSL DLLs are installed.\n"
            "Run fix_libtorrent.py or reinstall."
        )
        sys.exit(1)

    try:
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtGui import QFont, QPalette, QColor
        from PyQt6.QtCore import Qt
    except ImportError as e:
        logger.error(f"PyQt6 import failed: {e}")
        _crash_dialog(
            "Flux Torrent - Missing Dependency",
            f"Failed to load PyQt6:\n{e}\n\nReinstall the application."
        )
        sys.exit(1)

    from flux.gui.main_window import MainWindow
    from flux.gui.themes import get_stylesheet, get_palette, set_current
    from flux.core.settings import Settings

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName("Flux Torrent")
    app.setApplicationVersion("1.0.0")
    app.setOrganizationName("Flux")

    try:
        settings = Settings()
        theme_key = settings.get("theme", "dark")
        settings.close()
    except Exception:
        theme_key = "dark"

    set_current(theme_key)
    app.setStyleSheet(get_stylesheet(theme_key))

    p = get_palette(theme_key)
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(p["bg"]))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(p["text"]))
    palette.setColor(QPalette.ColorRole.Base, QColor(p["bg"]))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(p["bg_alt"]))
    palette.setColor(QPalette.ColorRole.Text, QColor(p["text"]))
    palette.setColor(QPalette.ColorRole.Button, QColor(p["bg_hover"]))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(p["text"]))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(p["accent"]))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(p["bg_card"]))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(p["text"]))
    app.setPalette(palette)

    default_font = QFont("Segoe UI", 10)
    default_font.setStyleHint(QFont.StyleHint.SansSerif)
    default_font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    app.setFont(default_font)

    window = MainWindow()
    window.show()

    if len(sys.argv) > 1:
        arg = sys.argv[1]
        if arg.endswith(".torrent") and os.path.isfile(arg):
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(500, lambda: window._add_torrent_with_dialog(torrent_path=arg))
        elif arg.startswith("magnet:"):
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(500, lambda: window._add_torrent_with_dialog(magnet_uri=arg))

    logger.info("Flux Torrent Client is ready.")
    sys.exit(app.exec())


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        tb = traceback.format_exc()
        _crash_dialog("Flux Torrent - Fatal Error", tb)
        sys.exit(1)
