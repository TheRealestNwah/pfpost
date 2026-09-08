"""Ties storage and the API together: registration, authorization, token lifecycle.

The browser leg of OAuth runs a one-shot loopback listener. Both the CLI and the
GUI use `Session`; neither needs to know how tokens are stored or refreshed.
"""

from __future__ import annotations

import json
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

from . import api, store

DEFAULT_PORT = 8080

_CALLBACK_PAGE = b"""<!doctype html><meta charset="utf-8"><title>pfpost</title>
<style>body{font:16px/1.5 system-ui,sans-serif;margin:4rem auto;max-width:32rem;
padding:0 1rem;color:#111}h1{font-size:1.4rem}</style>
<h1>%TITLE%</h1><p>%BODY%</p>"""


class AuthError(Exception):
    pass


class _CallbackHandler(BaseHTTPRequestHandler):
    captured = None

    def do_GET(self):
        import urllib.parse
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.rstrip("/") not in ("/callback", ""):
            self.send_response(404)
            self.end_headers()
            return
        query = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        _CallbackHandler.captured = query
        if "code" in query:
            title, body = "Authorized", "You can close this tab and return to pfpost."
        else:
            title = "Authorization failed"
            body = query.get("error_description") or query.get("error") or "No code returned."
        page = (_CALLBACK_PAGE.replace(b"%TITLE%", title.encode())
                             .replace(b"%BODY%", body.encode()))
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(page)))
        self.end_headers()
        self.wfile.write(page)

    def log_message(self, *args):
        pass


class Session:
    def __init__(self, state: dict | None = None):
        self.state = state if state is not None else store.load_state()

    # -- configuration ----------------------------------------------------

    @property
    def configured(self) -> bool:
        return bool(self.state.get("instance") and self.state.get("client_id"))

    @property
    def authorized(self) -> bool:
        return bool(self.state.get("token"))

    @property
    def instance(self) -> str:
        return self.state.get("instance", "")

    @property
    def scopes(self) -> str:
        return self.state.get("scopes", api.DEFAULT_SCOPES)

    @property
    def can_read(self) -> bool:
        return "read" in self.scopes.split()

    def redirect_uri(self) -> str:
        port = int(self.state.get("redirect_port", DEFAULT_PORT))
        return self.state.get("redirect_uri") or "http://localhost:%d/callback" % port

    def save(self) -> None:
        store.save_state(self.state)

    # -- registration -----------------------------------------------------

    def register(self, instance: str, name: str = "pfpost",
                 port: int = DEFAULT_PORT, scopes: str | None = None,
                 on_note=None) -> dict:
        """Register an OAuth client via /api/v1/apps.

        Scopes are chosen automatically unless given: some hosts cap request
        header size below a normal two-scope token, and a narrower scope
        produces a shorter JWT that fits.
        """
        instance = api.normalise_instance(instance)
        if not instance:
            raise AuthError("No instance given.")
        redirect = "http://localhost:%d/callback" % port

        header_limit = None
        if scopes is None:
            if on_note:
                on_note("Checking %s for request-header limits ..." % instance)
            scopes, header_limit = api.choose_scopes(instance)
            if header_limit is not None and on_note:
                on_note("This host caps Bearer tokens at %d characters, so pfpost is "
                        "requesting `%s` scope only.\nA read+write token would be "
                        "rejected by its web server before Pixelfed sees it."
                        % (header_limit, scopes))

        result = api.register_app(instance, name, redirect, scopes)
        secret = result.get("client_secret") or ""

        old = self.state.get("client_secret")
        if old:
            store.forget_secret(old)

        self.state.update({
            "instance": instance,
            "client_id": str(result["client_id"]),
            "client_secret": store.store_secret("client_secret", secret),
            "redirect_port": port,
            "redirect_uri": redirect,
            "scopes": scopes,
            "header_limit": header_limit,
        })
        self.state.pop("token", None)
        self.save()
        return {"client_id": result["client_id"], "has_secret": bool(secret),
                "scopes": scopes, "redirect_uri": redirect,
                "header_limit": header_limit}

    # -- authorization ----------------------------------------------------

    def authorize(self, timeout: int = 300, on_note=None) -> str:
        if not self.configured:
            raise AuthError("Not configured. Run `pfpost register` first.")
        client_id = self.state["client_id"]
        client_secret = store.load_secret(self.state.get("client_secret", ""))
        redirect = self.redirect_uri()
        port = int(self.state.get("redirect_port", DEFAULT_PORT))
        csrf = uuid.uuid4().hex

        verifier = challenge = None
        if not client_secret:
            verifier, challenge = api.make_pkce()
            if on_note:
                on_note("No client secret - authenticating with PKCE.")

        url = api.authorize_url(self.instance, client_id, redirect,
                                self.scopes, csrf, challenge)

        try:
            server = HTTPServer(("127.0.0.1", port), _CallbackHandler)
        except OSError as exc:
            raise AuthError(
                "Could not listen on port %d (%s).\nSomething else is using it. "
                "Re-register with --port and update the client's Redirect URL."
                % (port, exc))
        server.timeout = 5
        _CallbackHandler.captured = None

        if on_note:
            on_note("Opening your browser to authorize on %s" % self.instance)
            on_note(url)
        try:
            webbrowser.open(url)
        except Exception:
            pass

        deadline = time.monotonic() + timeout
        while _CallbackHandler.captured is None and time.monotonic() < deadline:
            server.handle_request()
        server.server_close()

        captured = _CallbackHandler.captured
        if not captured:
            raise AuthError("Timed out waiting for the authorization redirect.")
        if captured.get("state") != csrf:
            raise AuthError("State mismatch - response discarded. Try again.")
        if "code" not in captured:
            raise AuthError("Authorization denied: %s" % (
                captured.get("error_description") or captured.get("error") or "unknown"))

        try:
            token = api.exchange_code(self.instance, client_id, client_secret,
                                      redirect, captured["code"], verifier)
        except api.ApiError as exc:
            if exc.oauth_error == "invalid_client":
                raise AuthError(
                    "%s rejected the client credentials (invalid_client).\n"
                    "The authorization itself worked, so this is the client, not "
                    "your login.\nRe-run `pfpost register` to issue a fresh client "
                    "via the API." % self.instance)
            raise
        self._store_token(token)
        return token["access_token"]

    def _store_token(self, token_response: dict) -> None:
        payload = {
            "access_token": token_response["access_token"],
            "refresh_token": token_response.get("refresh_token"),
            "expires_at": api.token_expiry(token_response),
        }
        self.state["token"] = store.store_secret("token", json.dumps(payload))
        self.save()

    def _token_payload(self) -> dict:
        if not self.state.get("token"):
            raise AuthError("Not authorized yet. Run `pfpost auth`.")
        return json.loads(store.load_secret(self.state["token"]))

    def access_token(self) -> str:
        payload = self._token_payload()
        if api.needs_refresh(payload.get("expires_at")):
            if not payload.get("refresh_token"):
                raise AuthError("Token expired and no refresh token stored. Run `pfpost auth`.")
            fresh = api.refresh_access_token(
                self.instance, self.state["client_id"],
                store.load_secret(self.state.get("client_secret", "")),
                payload["refresh_token"], self.scopes)
            self._store_token(fresh)
            payload = self._token_payload()
        return payload["access_token"]

    def token_expiry(self) -> str | None:
        return self._token_payload().get("expires_at")

    def client(self) -> api.Pixelfed:
        return api.Pixelfed(self.instance, self.access_token())

    def public_client(self) -> api.Pixelfed:
        return api.Pixelfed(self.instance)

    # -- convenience ------------------------------------------------------

    def header_limit_advice(self) -> str:
        limit = self.state.get("header_limit")
        if not limit:
            return ""
        return (
            "%s's web server caps request headers at about %d characters, which "
            "is why pfpost requested `%s` scope only.\n"
            "The permanent fix is on the server:  large_client_header_buffers 4 16k;\n"
            "Until then there is little headroom - if posting suddenly fails after "
            "a token refresh, this is the first thing to check."
            % (self.instance, limit, self.scopes))
