"""Allow running as: python -m flux"""
import sys
import traceback

from flux.main import main, _crash_dialog

if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        tb = traceback.format_exc()
        _crash_dialog("Flux Torrent - Fatal Error", tb)
        sys.exit(1)
