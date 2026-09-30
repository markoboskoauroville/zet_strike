"""Bridge for installs made before the rename: the command is now zet.
The old updater downloads this file; it creates zet, removes zs and zets, then runs zet."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    import update
    update.migrate()
except Exception:
    pass
print("This app is now called zet. Use: zet   (zet now for the board)")
os.execv(sys.executable, [sys.executable, os.path.join(HERE, "zet.py")] + sys.argv[1:])
