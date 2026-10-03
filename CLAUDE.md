# Pixelfed Poster (pfpost)

A Python 3.13 CLI and PySide6 desktop app for posting images to Pixelfed, with a local scheduling queue and a background runner (Task Scheduler, launchd, systemd or cron). The version lives in `pfpost/__init__.py`.

## Build, test, lint

Use a venv in the project folder (no need to activate it):

```
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.venv\Scripts\python.exe -m ruff check --select F,E9 pfpost build.py pfpost.py test_pfpost.py test_gui.py
.venv\Scripts\python.exe test_pfpost.py
.venv\Scripts\python.exe test_gui.py
.venv\Scripts\python.exe build.py          # dist\pfpost.exe and dist\pfpost-gui.exe
.venv\Scripts\python.exe pfpost.py gui     # run from source
```

CI (Windows, Python 3.13, job "Lint and test") runs ruff, both test files and `build.py`, plus a smoke test of the built exes. No network or credentials are needed for tests.

## Layout

`pfpost/` — `api.py` (Pixelfed client and OAuth, no UI), `store.py`, `session.py`, `queue.py`, `scheduler.py`, `cli.py`, `gui.py`, `theme.py`, `updates.py`, `fonts/`. `pfpost.py` is the checkout launcher. `build.py` makes the two PyInstaller binaries (console and windowed) from the same code.

## Gotchas

- Keep `pfpost.exe` and `pfpost-gui.exe` in the same folder: background posting looks for its sibling so the scheduled task doesn't flash a console.
- `test_gui.py` fails (not skips) without PySide6, so a wrong interpreter can't pass by testing nothing. `PFPOST_SKIP_GUI_TESTS=1` skips on purpose.
- Release binaries come from CI, not a local build: the **Release** workflow (or a `v<version>` tag matching `__version__`) builds and attaches to a draft release; **Publish release** publishes it.
- If the Python version changes, delete `.venv` and recreate it. `build.py` restricts PyInstaller's native-library search path so stray Qt DLLs aren't bundled.
- `.dev/` is gitignored scratch space.
