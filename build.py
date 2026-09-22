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

import os
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


def build_environment() -> dict[str, str]:
    """Return a minimal PATH for the PyInstaller child process.

    PyInstaller examines PATH while resolving native dependencies. Developer
    shells can prepend unrelated Qt, OpenSSL, and compiler directories there;
    collecting one of those DLLs produces a valid-looking executable that
    fails only when Qt is imported. Keep the rest of the environment, but make
    native DLL resolution deterministic on Windows.
    """
    env = os.environ.copy()
    if os.name != "nt":
        return env

    python_scripts = Path(sys.executable).resolve().parent
    python_home = Path(sys.base_prefix).resolve()
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows")).resolve()
    candidates = (
        python_scripts,
        python_home,
        python_home / "Scripts",
        system_root / "System32",
        system_root,
    )
    # Preserve order while avoiding duplicate directories on case-insensitive
    # Windows filesystems.
    clean_path: list[str] = []
    seen: set[str] = set()
    for path in candidates:
        key = os.path.normcase(str(path))
        if key not in seen:
            seen.add(key)
            clean_path.append(str(path))
    env["PATH"] = os.pathsep.join(clean_path)
    return env


def run(args: list[str], name: str) -> None:
    print("$ " + " ".join(args), flush=True)
    result = subprocess.run(
        args, cwd=ROOT, env=build_environment(), capture_output=True, text=True)
    print(result.stdout, end="")
    print(result.stderr, end="", file=sys.stderr)
    if result.returncode != 0:
        target = ROOT / "dist" / (name + ".exe")
        if target.exists() and ("PermissionError" in result.stderr
                                or "Access is denied" in result.stderr):
            raise SystemExit(
                "Cannot replace %s because it is still running. Close it, then run build.py again."
                % target.name)
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
    # The font and its licence. theme.install_font() finds them next to
    # theme.py, which in a frozen build is pfpost/ inside the unpacked bundle.
    # Missing, the app would quietly fall back to Segoe UI.
    args += ["--add-data", "%s%spfpost/fonts" % (ROOT / "pfpost" / "fonts", os.pathsep)]
    args += ["--windowed"] if windowed else ["--console"]
    args.append(str(ENTRY))
    run(args, name)


def main() -> int:
    # PyInstaller bundles whatever this interpreter has. A missing PySide6
    # does not fail the build - it yields a pfpost-gui.exe that dies on launch,
    # and a missing keyring silently drops to DPAPI. So check them all first.
    missing = []
    for module, package in (("PyInstaller", "pyinstaller"), ("PySide6", "PySide6"),
                            ("keyring", "keyring")):
        try:
            __import__(module)
        except ImportError:
            missing.append(package)
    if missing:
        print("Not installed for %s: %s" % (sys.executable, ", ".join(missing)))
        print("Set up the project environment and build from it:")
        print("  python -m venv .venv")
        print("  .venv\\Scripts\\python.exe -m pip install -r requirements.txt pyinstaller")
        print("  .venv\\Scripts\\python.exe build.py")
        return 1
    print("Building with %s" % sys.executable)

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
