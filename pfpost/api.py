"""Pixelfed API client and OAuth flow.

No printing, no argparse, no UI. Everything here returns values or raises, so
both the CLI and the GUI can drive it.
"""

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import secrets
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import __version__

USER_AGENT = "pfpost/%s" % __version__
DEFAULT_SCOPES = "read write"
NARROW_SCOPES = "write"
REFRESH_MARGIN = timedelta(days=7)

# A Passport JWT for two scopes runs ~1007 chars. Servers whose nginx caps a
# header line at 1k reject it outright, so we measure before registering.
SAFE_HEADER_MARGIN = 1060


class ApiError(Exception):
    def __init__(self, status: int, body: str, url: str):
        self.status, self.body, self.url = status, body, url
        super().__init__("HTTP %s from %s\n%s" % (status, url, body[:2000]))

    @property
    def is_header_too_large(self) -> bool:
        return self.status == 400 and "Header Or Cookie Too Large" in self.body

    @property
    def oauth_error(self) -> str:
        try:
            return json.loads(self.body).get("error", "")
        except Exception:
            return ""


# --------------------------------------------------------------------------
# transport
# --------------------------------------------------------------------------

def http(method: str, url: str, *, data: bytes | None = None,
         content_type: str | None = None, token: str | None = None,
         timeout: int = 180) -> tuple[int, bytes]:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if content_type:
        headers["Content-Type"] = content_type
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except urllib.error.URLError as exc:
        raise ApiError(0, "network error: %s" % (exc.reason,), url) from exc


def api_json(method: str, url: str, **kw):
    status, body = http(method, url, **kw)
    text = body.decode("utf-8", "replace")
    if status >= 400:
        raise ApiError(status, text, url)
    if not text.strip():
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise ApiError(status, "expected JSON, got:\n%s" % text[:1000], url)


def form(fields: dict) -> tuple[bytes, str]:
    return (urllib.parse.urlencode(fields).encode("utf-8"),
            "application/x-www-form-urlencoded")


def multipart(fields, files) -> tuple[bytes, str]:
    boundary = "----pfpost" + uuid.uuid4().hex
    out = bytearray()
    for name, value in fields:
        out += ("--%s\r\n" % boundary).encode()
        out += ('Content-Disposition: form-data; name="%s"\r\n\r\n' % name).encode()
        out += str(value).encode("utf-8") + b"\r\n"
    for name, filename, ctype, content in files:
        out += ("--%s\r\n" % boundary).encode()
        out += ('Content-Disposition: form-data; name="%s"; filename="%s"\r\n'
                % (name, filename)).encode()
        out += ("Content-Type: %s\r\n\r\n" % ctype).encode()
        out += content + b"\r\n"
    out += ("--%s--\r\n" % boundary).encode()
    return bytes(out), "multipart/form-data; boundary=" + boundary


def normalise_instance(text: str) -> str:
    return (text or "").replace("https://", "").replace("http://", "").strip().strip("/")


# --------------------------------------------------------------------------
# server capability probing
# --------------------------------------------------------------------------

def probe_header_limit(instance: str, ceiling: int = 4096) -> int | None:
    """Largest Bearer token this host accepts, or None if it accepts `ceiling`.

    Some instances sit behind an nginx with `large_client_header_buffers` at 1k,
    which silently rejects normal Passport tokens before Pixelfed sees them.
    Detecting that up front lets us pick a scope set that fits.
    """
    url = "https://%s/api/v1/instance" % instance

    def accepts(n: int) -> bool:
        status, _ = http("GET", url, token="x" * n, timeout=20)
        return status != 400

    if accepts(ceiling):
        return None
    lo, hi = 1, ceiling
    while lo < hi - 1:
        mid = (lo + hi) // 2
        if accepts(mid):
            lo = mid
        else:
            hi = mid
    return lo


def choose_scopes(instance: str) -> tuple[str, int | None]:
    """Pick the widest scope set whose token will fit through this host."""
    limit = probe_header_limit(instance)
    if limit is not None and limit < SAFE_HEADER_MARGIN:
        return NARROW_SCOPES, limit
    return DEFAULT_SCOPES, limit


# --------------------------------------------------------------------------
# oauth
# --------------------------------------------------------------------------

def register_app(instance: str, name: str, redirect_uri: str, scopes: str) -> dict:
    body, ctype = form({"client_name": name, "redirect_uris": redirect_uri,
                        "scopes": scopes})
    result = api_json("POST", "https://%s/api/v1/apps" % instance,
                      data=body, content_type=ctype)
    if not result.get("client_id"):
        raise ApiError(200, "registration returned no client_id: %s"
                       % json.dumps(result)[:500], "/api/v1/apps")
    return result


def make_pkce() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).decode().rstrip("=")
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()).decode().rstrip("=")
    return verifier, challenge


def authorize_url(instance: str, client_id: str, redirect_uri: str, scopes: str,
                  state_token: str, challenge: str | None = None) -> str:
    query = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": scopes,
        "state": state_token,
    }
    if challenge:
        query["code_challenge"] = challenge
        query["code_challenge_method"] = "S256"
    return "https://%s/oauth/authorize?%s" % (instance, urllib.parse.urlencode(query))


def exchange_code(instance: str, client_id: str, client_secret: str,
                  redirect_uri: str, code: str, verifier: str | None = None) -> dict:
    fields = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code": code,
    }
    if client_secret:
        fields["client_secret"] = client_secret
    if verifier:
        fields["code_verifier"] = verifier
    data, ctype = form(fields)
    return api_json("POST", "https://%s/oauth/token" % instance,
                    data=data, content_type=ctype)


def refresh_access_token(instance: str, client_id: str, client_secret: str,
                         refresh_token: str, scopes: str) -> dict:
    fields = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "refresh_token": refresh_token,
        "scope": scopes,
    }
    if client_secret:
        fields["client_secret"] = client_secret
    data, ctype = form(fields)
    return api_json("POST", "https://%s/oauth/token" % instance,
                    data=data, content_type=ctype)


def token_expiry(token_response: dict) -> str | None:
    seconds = int(token_response.get("expires_in") or 0)
    if not seconds:
        return None
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def needs_refresh(expires_at: str | None) -> bool:
    if not expires_at:
        return False
    try:
        return datetime.fromisoformat(expires_at) - datetime.now(timezone.utc) < REFRESH_MARGIN
    except ValueError:
        return False


# --------------------------------------------------------------------------
# resources
# --------------------------------------------------------------------------

class Pixelfed:
    """Authenticated client. `token` may be None for public endpoints."""

    def __init__(self, instance: str, token: str | None = None):
        self.instance = instance
        self.token = token

    def _url(self, path: str) -> str:
        return "https://%s%s" % (self.instance, path)

    def instance_info(self) -> dict:
        try:
            return api_json("GET", self._url("/api/v1/instance"))
        except ApiError:
            return {}

    def limits(self) -> dict:
        cfg = self.instance_info().get("configuration") or {}
        statuses = cfg.get("statuses") or {}
        media = cfg.get("media_attachments") or {}
        return {
            "max_characters": statuses.get("max_characters"),
            "max_media_attachments": statuses.get("max_media_attachments"),
            "image_size_limit": media.get("image_size_limit"),
            "supported_mime_types": media.get("supported_mime_types"),
        }

    def verify_credentials(self) -> dict:
        return api_json("GET", self._url("/api/v1/accounts/verify_credentials"),
                        token=self.token)

    def upload_media(self, path: Path, alt: str | None = None) -> str:
        content = path.read_bytes()
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        fields = [("description", alt)] if alt else []
        body, boundary = multipart(fields, [("file", path.name, ctype, content)])
        result = api_json("POST", self._url("/api/v1/media"),
                          data=body, content_type=boundary, token=self.token)
        media_id = result.get("id")
        if not media_id:
            raise ApiError(200, "upload returned no id: %s" % json.dumps(result)[:500],
                           "/api/v1/media")
        return str(media_id)

    def create_status(self, caption: str, media_ids: list[str],
                      visibility: str = "public") -> dict:
        fields = [("status", caption or ""), ("visibility", visibility)]
        fields += [("media_ids[]", mid) for mid in media_ids]
        body, ctype = multipart(fields, [])
        return api_json("POST", self._url("/api/v1/statuses"),
                        data=body, content_type=ctype, token=self.token)

    def publish(self, images, caption: str, visibility: str = "public",
                on_progress=None) -> dict:
        """images: sequence of (Path, alt-or-None). on_progress(index, total, name)."""
        media_ids = []
        total = len(images)
        for index, (path, alt) in enumerate(images, 1):
            if on_progress:
                on_progress(index, total, path.name)
            media_ids.append(self.upload_media(path, alt))
        return self.create_status(caption, media_ids, visibility)


# Pixelfed's anti-spam check (app/Util/Sentiment/Bouncer.php) quietly turns a
# public post unlisted when the account is under six months old or has no
# public posts, has 100 followers or fewer, and the caption contains any of
# these. Copied verbatim, and matched the way Laravel's Str::contains does -
# case-sensitive substrings - so the warning fires exactly when Pixelfed would
# even look. Account age and followers need the `read` scope, which a
# header-capped instance may not grant, so those two halves are not checked.
LINK_MARKERS = ("https://", "http://", "hxxps://", "hxxp://",
                "www.", ".com", ".net", ".org")


def link_triggers(caption: str, visibility: str) -> list[str]:
    """Words in the caption that could get a public post made unlisted.

    Empty for anything but a public post: Pixelfed skips the check otherwise.
    """
    if visibility != "public" or not caption:
        return []
    return [word for word in caption.split()
            if any(marker in word for marker in LINK_MARKERS)]


class ValidationError(Exception):
    pass


def validate(images, caption: str, limits: dict) -> None:
    """Check a post against instance limits before uploading anything."""
    max_media = limits.get("max_media_attachments")
    max_chars = limits.get("max_characters")
    size_limit = limits.get("image_size_limit")
    types = limits.get("supported_mime_types")

    if max_media and len(images) > max_media:
        raise ValidationError("%d attachments exceeds this instance's limit of %d."
                              % (len(images), max_media))
    if max_chars and caption and len(caption) > max_chars:
        raise ValidationError("Caption is %d characters; the instance allows %d."
                              % (len(caption), max_chars))
    for path, _alt in images:
        if not path.exists():
            raise ValidationError("File not found: %s" % path)
        if size_limit and path.stat().st_size > size_limit:
            raise ValidationError("%s is %.1f MB; the instance allows %.1f MB."
                                  % (path.name, path.stat().st_size / 1048576,
                                     size_limit / 1048576))
        if types:
            ctype = mimetypes.guess_type(path.name)[0]
            if ctype and ctype not in types:
                raise ValidationError("%s is %s, which this instance does not accept."
                                      % (path.name, ctype))
