"""Switch the running recorder: python mode.py listen | silent (applies to the latest session)."""
import glob
import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "sessions")

if len(sys.argv) != 2 or sys.argv[1] not in ("listen", "silent"):
    sys.exit("usage: python mode.py listen|silent")
sessions = sorted(d for d in glob.glob(os.path.join(ROOT, "*")) if os.path.isfile(os.path.join(d, "MODE")))
if not sessions or os.path.exists(os.path.join(sessions[-1], "STOP")):
    sys.exit("no running session with --monitor")
with open(os.path.join(sessions[-1], "MODE"), "w") as f:
    f.write(sys.argv[1])
print(f"{sys.argv[1]} -> {os.path.basename(sessions[-1])}")
