#!/usr/bin/env python3
"""Launcher so `python pfpost.py ...` keeps working from a checkout, and the
same entry point can be frozen into an executable."""
import os
import sys
from pathlib import Path

# A windowed (--windowed) PyInstaller build has no console, so sys.stdout and
# sys.stderr are None and any print() raises AttributeError. Give them
# somewhere to go rather than scattering guards through the CLI.
if sys.stdout is None or sys.stderr is None:
    _sink = open(os.devnull, "w", encoding="utf-8")
    sys.stdout = sys.stdout or _sink
    sys.stderr = sys.stderr or _sink

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from pfpost.cli import main

if __name__ == "__main__":
    main()
