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
        os.system('icacls "%s" /inheritance:r /grant:r "%%USERNAME%%":F >nul 2>&1' % p)
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
