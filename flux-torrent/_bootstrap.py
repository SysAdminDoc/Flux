"""Bootstrap: fix DLLs then launch Flux Torrent."""
import sys
import os

if sys.platform == "win32":
    os.system("")

root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, root)
os.chdir(root)

from flux import dll_fix
from flux.main import main
main()
