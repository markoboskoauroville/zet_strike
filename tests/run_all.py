#!/usr/bin/env python3
"""The four tests (MANTRA_MANIFEST four-tests.md), each in its own process so one cannot lean on
another's state. Run before every push:  python3 tests/run_all.py"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TESTS = ["test1_mechanism.py", "test2_real.py", "test3_ugly.py", "test4_upgrade.py"]
failed = []
for t in TESTS:
    print("=" * 60 + "\n" + t, flush=True)
    r = subprocess.run([sys.executable, os.path.join(HERE, t)], timeout=600)
    if r.returncode != 0:
        failed.append(t)
print("=" * 60)
print("all four green" if not failed else "RED: " + ", ".join(failed))
sys.exit(1 if failed else 0)
