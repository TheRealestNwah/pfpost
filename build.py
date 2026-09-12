#!/usr/bin/env python3
"""Build standalone Windows executables.

    python build.py

Produces two binaries in dist/, from the same code:

    pfpost.exe      console build - the CLI, and what the scheduled task runs
    pfpost-gui.exe  windowed build - no console window on launch

Two rather than one because the choice is baked in at build time: a console
build flashes a terminal every time the GUI or the scheduled task starts, and a
windowed build has nowhere to print CLI output.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENTRY = ROOT / "pfpost.py"

# keyring finds its backends through entry points at runtime, which static
# analysis cannot see. In practice pyinstaller-hooks-contrib ships a keyring
# hook that already handles this - a build without these was verified to work -
# so they are insurance against that hook being absent or changed, not a fix
# for a failure seen today. If store.backend_name() ever reports "none" in a
# frozen build while working from source, this list is the first thing to check.
HIDDEN = [
    "keyring.backends.Windows",
    "keyring.backends.chainer",
    "keyring.backends.fail",
    "win32timezone",
]


def run(args: list[str]) -> None:
    print("$ " + " ".join(args), flush=True)
    result = subprocess.run(args, cwd=ROOT)
    if result.returncode != 0:
        raise SystemExit("Build failed: %s" % " ".join(args))


def build(name: str, windowed: bool, icon: Path) -> None:
    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onefile",
        "--name", name,
        "--icon", str(icon),
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(ROOT / "build"),
        "--specpath", str(ROOT / "build"),
    ]
    for module in HIDDEN:
        args += ["--hidden-import", module]
    args += ["--windowed"] if windowed else ["--console"]
    args.append(str(ENTRY))
    run(args)


def main() -> int:
    try:
        import PyInstaller                                   # noqa: F401
    except ImportError:
        print("PyInstaller is not installed. Run:  pip install pyinstaller")
        return 1

    # Without --icon, PyInstaller stamps its default Python icon on both
    # binaries. Drawn from the same code as the in-app icon, so the two
    # cannot drift, and written to a temp dir so no .ico lives in the repo.
    sys.path.insert(0, str(ROOT))
    from pfpost import theme

    with tempfile.TemporaryDirectory(prefix="pfpost-build-") as scratch:
        icon = Path(scratch) / "pfpost.ico"
        theme.write_ico(icon)
        build("pfpost", windowed=False, icon=icon)
        build("pfpost-gui", windowed=True, icon=icon)

    shutil.rmtree(ROOT / "build", ignore_errors=True)
    print("\nBuilt:")
    for exe in sorted((ROOT / "dist").glob("*.exe")):
        print("  %-16s %.1f MB" % (exe.name, exe.stat().st_size / 1048576))
    print("\nVerify the build itself - passing tests do not prove a frozen")
    print("binary works, because the failure modes are import-time:")
    print("  dist\\pfpost.exe whoami")
    print("  dist\\pfpost-gui.exe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
