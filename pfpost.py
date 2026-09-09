#!/usr/bin/env python3
"""Launcher so `python pfpost.py ...` keeps working from a checkout, and the
same entry point can be frozen into an executable."""
import os
import sys
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)

# Which binary is this? Not "is stdout None" - a windowed build launched from a
# terminal inherits that terminal's pipes, so the stream test reports the wrong
# answer in exactly the situation where it is easiest to test. The filename
# build.py gives it is unambiguous.
GUI_BUILD = FROZEN and Path(sys.executable).stem.lower().endswith("-gui")

# A windowed build usually has no console at all, in which case sys.stdout and
# sys.stderr are None and any print() raises AttributeError.
if sys.stdout is None or sys.stderr is None:
    _sink = open(os.devnull, "w", encoding="utf-8")
    sys.stdout = sys.stdout or _sink
    sys.stderr = sys.stderr or _sink

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from pfpost.cli import main

def report_crash(exc: BaseException) -> None:
    """Surface a startup failure that would otherwise be invisible.

    A windowed build has nowhere to print, so an unhandled exception looks
    exactly like the program declining to start for no reason.
    """
    import traceback
    detail = "".join(traceback.format_exception(exc))[-3000:]
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
        app = QApplication.instance() or QApplication([])   # noqa: F841
        box = QMessageBox()
        box.setIcon(QMessageBox.Critical)
        box.setWindowTitle("pfpost could not start")
        box.setText("pfpost hit an error while starting.")
        box.setInformativeText(
            "Run pfpost.exe from a terminal for the full output.\n\n%s"
            % str(exc)[:300])
        box.setDetailedText(detail)
        box.exec()
    except Exception:
        # Qt itself may be what failed. Leave something on disk either way.
        try:
            log = Path(os.environ.get("TEMP", ".")) / "pfpost-crash.log"
            log.write_text(detail, encoding="utf-8")
        except OSError:
            pass


if __name__ == "__main__":
    # Double-clicking the windowed build passes no arguments. Without this it
    # reaches argparse, is told a subcommand is required, and exits silently -
    # there is no console for the error to appear in, so it looks like nothing
    # happened at all.
    argv = ["gui"] if GUI_BUILD and len(sys.argv) == 1 else None
    if not GUI_BUILD:
        main(argv)
    else:
        try:
            main(argv)
        except SystemExit:
            raise
        except BaseException as exc:                        # noqa: BLE001
            report_crash(exc)
            sys.exit(1)
