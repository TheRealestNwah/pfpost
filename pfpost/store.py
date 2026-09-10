"""Configuration and credential storage.

Secrets are never written in the clear. Three backends, in order of preference:

  keyring  Credential Manager / Keychain / Secret Service. Correct everywhere,
           needs the `keyring` package.
  dpapi    Windows only, no dependency. Bound to the current Windows user.
  none     Refuse to store. Better than pretending base64 is encryption.

Non-secret configuration always lives in state.json; only the secret material
is routed through a backend.
"""

from __future__ import annotations

import base64
import ctypes
import json
import os
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path

APP_NAME = "PixelfedPoster"
KEYRING_SERVICE = "pfpost"
IS_WINDOWS = sys.platform == "win32"

try:
    import keyring as _keyring
    try:  # a backend that only pretends to store is worse than none
        from keyring.backends.fail import Keyring as _FailKeyring
        _HAS_KEYRING = not isinstance(_keyring.get_keyring(), _FailKeyring)
    except Exception:
        _HAS_KEYRING = True
except ImportError:
    _keyring = None
    _HAS_KEYRING = False


class StorageError(Exception):
    pass


def config_dir() -> Path:
    base = os.environ.get("PFPOST_HOME") or os.environ.get("APPDATA") \
        or os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    d = Path(base)
    if d.name != APP_NAME:
        d = d / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def state_path() -> Path:
    return config_dir() / "state.json"


def queue_path() -> Path:
    return config_dir() / "queue.json"


def draft_path() -> Path:
    return config_dir() / "draft.json"


def prefs_path() -> Path:
    # Separate from state.json: Session holds that in memory and rewrites it
    # whole on every token save, which would drop a key written beside it.
    return config_dir() / "prefs.json"


def staged_dir(item_id: str | None = None) -> Path:
    """Where queued images are copied to.

    A queued post may sit for days. Referencing the user's original file means
    it can be moved, renamed, edited or deleted in the meantime - and a
    same-path-different-content edit would post the wrong image with no error
    at all. Copying at queue time closes that window.
    """
    base = config_dir() / "staged"
    path = base / item_id if item_id else base
    path.mkdir(parents=True, exist_ok=True)
    return path


def backend_name() -> str:
    if _HAS_KEYRING:
        return "keyring"
    if IS_WINDOWS:
        return "dpapi"
    return "none"


def backend_description() -> str:
    return {
        "keyring": "system keyring (Credential Manager / Keychain / Secret Service)",
        "dpapi": "Windows DPAPI, bound to your Windows user account",
        "none": "unavailable",
    }[backend_name()]


# --------------------------------------------------------------------------
# DPAPI
# --------------------------------------------------------------------------

_ENTROPY = b"pfpost.v1"


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char))]


def _to_blob(data: bytes):
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def _from_blob(blob: _Blob) -> bytes:
    return ctypes.string_at(blob.pbData, blob.cbData)


def _dpapi(func_name: str, data: bytes) -> bytes:
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    func = getattr(crypt32, func_name)
    pin, _k1 = _to_blob(data)
    ent, _k2 = _to_blob(_ENTROPY)
    out = _Blob()
    if not func(ctypes.byref(pin), None, ctypes.byref(ent), None, None, 0,
                ctypes.byref(out)):
        raise StorageError(
            "%s failed (Windows error %d). DPAPI secrets are bound to the "
            "Windows user that created them." % (func_name, ctypes.get_last_error()))
    try:
        return _from_blob(out)
    finally:
        kernel32.LocalFree(out.pbData)


# --------------------------------------------------------------------------
# public interface
# --------------------------------------------------------------------------

def store_secret(name: str, value: str) -> str:
    """Persist a secret and return the reference to record in state.json."""
    if value == "":
        return ""  # public client: nothing to protect
    backend = backend_name()
    if backend == "keyring":
        _keyring.set_password(KEYRING_SERVICE, name, value)
        return "keyring:" + name
    if backend == "dpapi":
        blob = _dpapi("CryptProtectData", value.encode("utf-8"))
        return "dpapi:" + base64.b64encode(blob).decode("ascii")
    raise StorageError(
        "No secure credential store is available on this platform.\n"
        "Install one with:  pip install keyring\n"
        "pfpost will not write credentials to disk unencrypted."
    )


def load_secret(reference: str) -> str:
    """Resolve a reference produced by store_secret back to the secret."""
    if not reference:
        return ""
    scheme, _, rest = reference.partition(":")
    if scheme == "keyring":
        if not _HAS_KEYRING:
            raise StorageError(
                "This credential is in the system keyring, but the `keyring` "
                "package is not installed. Run:  pip install keyring")
        value = _keyring.get_password(KEYRING_SERVICE, rest)
        if value is None:
            raise StorageError(
                "Credential %r not found in the system keyring. Re-run "
                "`pfpost register`." % rest)
        return value
    if scheme == "dpapi":
        if not IS_WINDOWS:
            raise StorageError("This credential was encrypted with Windows DPAPI "
                               "and cannot be read on this platform.")
        return _dpapi("CryptUnprotectData", base64.b64decode(rest)).decode("utf-8")
    if scheme == "plain":
        # Written by an older build. Readable, but not secure - warn and migrate.
        return base64.b64decode(rest).decode("utf-8")
    raise StorageError("Unknown credential reference %r" % scheme)


def forget_secret(reference: str) -> None:
    scheme, _, rest = reference.partition(":")
    if scheme == "keyring" and _HAS_KEYRING:
        try:
            _keyring.delete_password(KEYRING_SERVICE, rest)
        except Exception:
            pass


def load_state() -> dict:
    p = state_path()
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def save_state(state: dict) -> None:
    p = state_path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(p)
    if IS_WINDOWS:
        # subprocess, not os.system: os.system spawns cmd.exe, which flashes a
        # console window under pythonw.exe - and this runs on every token save.
        try:
            subprocess.run(
                ["icacls", str(p), "/inheritance:r", "/grant:r",
                 "%s:F" % os.environ.get("USERNAME", "")],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                timeout=15, check=False)
        except (OSError, subprocess.SubprocessError):
            pass       # tightening the ACL is best-effort; DPAPI is the real guard
    else:
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass


def load_queue() -> dict:
    p = queue_path()
    if not p.exists():
        return {"items": []}
    return json.loads(p.read_text(encoding="utf-8"))


def save_queue(queue: dict) -> None:
    p = queue_path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(queue, indent=2), encoding="utf-8")
    tmp.replace(p)


def discard_staged(item_id: str) -> None:
    """Delete one item's staged copies. Never raises - this is cleanup."""
    import shutil
    try:
        shutil.rmtree(staged_dir(item_id), ignore_errors=True)
    except OSError:
        pass


def staged_bytes() -> int:
    total = 0
    for path in staged_dir().rglob("*"):
        if path.is_file():
            try:
                total += path.stat().st_size
            except OSError:
                pass
    return total


def load_draft() -> dict:
    p = draft_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_draft(draft: dict) -> None:
    p = draft_path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(draft, indent=2), encoding="utf-8")
    tmp.replace(p)


def clear_draft() -> None:
    try:
        draft_path().unlink(missing_ok=True)
    except OSError:
        pass


PREF_DEFAULTS = {"warn_links": True}


def load_prefs() -> dict:
    """User preferences, always with every key present."""
    prefs = dict(PREF_DEFAULTS)
    try:
        saved = json.loads(prefs_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return prefs
    if isinstance(saved, dict):
        prefs.update({k: v for k, v in saved.items() if k in PREF_DEFAULTS})
    return prefs


def set_pref(key: str, value) -> None:
    if key not in PREF_DEFAULTS:
        raise KeyError(key)
    prefs = load_prefs()
    prefs[key] = value
    p = prefs_path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(prefs, indent=2), encoding="utf-8")
    tmp.replace(p)
