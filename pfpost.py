#!/usr/bin/env python3
"""Launcher so `python pfpost.py ...` keeps working from a checkout."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pfpost.cli import main

if __name__ == "__main__":
    main()
