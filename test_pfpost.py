"""Exercise pfpost against a mock Pixelfed instance. Run: python test_pfpost.py

No network access and no real credentials. The mock can simulate an nginx that
caps request header size, which is how the scope-selection logic is tested.
"""
import email
import json
import os
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

os.environ["PFPOST_HOME"] = tempfile.mkdtemp(prefix="pftest")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pfpost import api, queue as pfqueue, store          # noqa: E402
from pfpost.session import Session                        # noqa: E402

PORT = 47311
RECEIVED = []
MEDIA_COUNTER = [0]
HEADER_LIMIT = [None]      # None = unlimited; int = max Bearer token chars
NGINX_400 = b"<html><head><title>400 Request Header Or Cookie Too Large</title></head></html>"

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000100ffff03000006000557bfabd400"
    "00000049454e44ae426082"
)


class MockPixelfed(BaseHTTPRequestHandler):
    def _bearer(self):
        auth = self.headers.get("Authorization") or ""
        return auth[7:] if auth.startswith("Bearer ") else ""

    def _too_large(self):
        limit = HEADER_LIMIT[0]
        return limit is not None and len(self._bearer()) > limit

    def _reject(self):
        self.send_response(400)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(NGINX_400)))
        self.end_headers()
        self.wfile.write(NGINX_400)

    def _json(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _parts(self, body):
        header = "Content-Type: %s\r\nMIME-Version: 1.0\r\n\r\n" % self.headers["Content-Type"]
        msg = email.message_from_bytes(header.encode() + body)
        out = {}
        for part in msg.get_payload():
            disp = part.get("Content-Disposition", "")
            name = None
            for chunk in disp.split(";"):
                chunk = chunk.strip()
                if chunk.startswith("name="):
                    name = chunk[5:].strip('"')
            if name is not None:
                out.setdefault(name, []).append(part.get_payload(decode=True))
        return out

    def do_GET(self):
        if self._too_large():
            return self._reject()
        RECEIVED.append(("GET", self.path, self.headers.get("Authorization")))
        if self.path == "/api/v1/instance":
            return self._json({
                "title": "Mock", "version": "3.5.3 (compatible; Pixelfed 0.12.9)",
                "configuration": {
                    "statuses": {"max_characters": 500, "max_media_attachments": 4},
                    "media_attachments": {
                        "image_size_limit": 15728640,
                        "supported_mime_types": ["image/png", "image/jpeg"]},
                },
            })
        if self.path == "/api/v1/accounts/verify_credentials":
            return self._json({"username": "morro", "display_name": "Morro",
                               "statuses_count": 42})
        self._json({"error": "not found"}, 404)

    def do_POST(self):
        if self._too_large():
            return self._reject()
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        auth = self.headers.get("Authorization")
        if self.path == "/api/v1/apps":
            RECEIVED.append(("POST", self.path, body.decode()))
            return self._json({"client_id": "58045", "client_secret": "s" * 40,
                               "redirect_uri": "http://localhost:8080/callback"})
        if self.path == "/oauth/token":
            RECEIVED.append(("POST", self.path, body.decode()))
            return self._json({"access_token": "tok_" + "a" * 60,
                               "refresh_token": "ref_xyz789",
                               "expires_in": 31536000, "scope": "read write"})
        if self.path == "/api/v1/media":
            parts = self._parts(body)
            RECEIVED.append(("POST", self.path, auth, {
                "description": [p.decode() for p in parts.get("description", [])],
                "file_bytes": len(parts.get("file", [b""])[0]),
            }))
            MEDIA_COUNTER[0] += 1
            return self._json({"id": "media_%d" % MEDIA_COUNTER[0], "type": "image"})
        if self.path == "/api/v1/statuses":
            parts = self._parts(body)
            RECEIVED.append(("POST", self.path, auth, {
                "status": [p.decode() for p in parts.get("status", [])],
                "visibility": [p.decode() for p in parts.get("visibility", [])],
                "media_ids": [p.decode() for p in parts.get("media_ids[]", [])],
            }))
            return self._json({"id": "99", "url": "https://mock.test/p/morro/99"})
        self._json({"error": "not found"}, 404)

    def log_message(self, *args):
        pass


FAILURES = [0]


def check(label, condition, detail=""):
    if not condition:
        FAILURES[0] += 1
    print("  [%s] %s%s" % ("PASS" if condition else "FAIL", label,
                           ("  <- " + detail) if detail and not condition else ""))


def main():
    server = HTTPServer(("127.0.0.1", PORT), MockPixelfed)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    # Exercise the real request path, redirected at the transport layer only.
    original = api.http
    api.http = lambda m, u, **kw: original(m, u.replace("https://", "http://"), **kw)

    host = "127.0.0.1:%d" % PORT
    tmp = Path(tempfile.mkdtemp())
    img_a, img_b = tmp / "a.png", tmp / "b.png"
    img_a.write_bytes(PNG)
    img_b.write_bytes(PNG)

    print("\n1. credential storage")
    print("  backend in use: %s" % store.backend_name())
    secret = "super-secret-value-eu-check"
    ref = store.store_secret("test_secret", secret)
    check("secret is not stored in the clear",
          secret not in ref and not ref.startswith("plain:"), ref[:40])
    check("secret round-trips", store.load_secret(ref) == secret)
    check("empty secret yields empty reference", store.store_secret("empty", "") == "")
    store.forget_secret(ref)

    print("\n2. header-limit probing")
    HEADER_LIMIT[0] = None
    check("no limit detected on a permissive host",
          api.probe_header_limit(host) is None)
    HEADER_LIMIT[0] = 1000
    found = api.probe_header_limit(host)
    check("detects a 1000-char ceiling", found == 1000, str(found))
    scopes, limit = api.choose_scopes(host)
    check("narrows scopes when the ceiling is low",
          scopes == api.NARROW_SCOPES and limit == 1000, "%s / %s" % (scopes, limit))
    HEADER_LIMIT[0] = None
    scopes, limit = api.choose_scopes(host)
    check("keeps read+write when there is headroom",
          scopes == api.DEFAULT_SCOPES and limit is None, str(scopes))

    print("\n3. registration and token lifecycle")
    session = Session({})
    RECEIVED.clear()
    result = session.register(host, name="pfpost-test", port=8080)
    check("registered via /api/v1/apps",
          any(r[1] == "/api/v1/apps" for r in RECEIVED))
    check("chose read+write on a permissive host", result["scopes"] == "read write")
    check("client secret not written to state.json in the clear",
          "s" * 40 not in json.dumps(session.state))

    session._store_token({"access_token": "tok_abc123", "refresh_token": "ref_xyz789",
                          "expires_in": 31536000})
    check("access token round-trips", session.access_token() == "tok_abc123")

    payload = session._token_payload()
    payload["expires_at"] = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    session.state["token"] = store.store_secret("token", json.dumps(payload))
    RECEIVED.clear()
    session.access_token()
    check("auto-refresh fires inside the 7-day margin",
          any(r[1] == "/oauth/token" for r in RECEIVED))
    check("refresh uses grant_type=refresh_token",
          any("grant_type=refresh_token" in str(r[2])
              for r in RECEIVED if r[1] == "/oauth/token"))

    print("\n4. validation")
    limits = session.public_client().limits()
    check("reads max_characters from the instance", limits["max_characters"] == 500)
    for label, images, caption in [
        ("rejects too many attachments", [(img_a, None)] * 5, "x"),
        ("rejects an over-long caption", [(img_a, None)], "x" * 501),
        ("rejects a missing file", [(tmp / "nope.png", None)], "x"),
    ]:
        try:
            api.validate(images, caption, limits)
            check(label, False)
        except api.ValidationError:
            check(label, True)
    try:
        api.validate([(img_a, "alt")], "fine", limits)
        check("accepts a valid post", True)
    except api.ValidationError as exc:
        check("accepts a valid post", False, str(exc))

    print("\n5. publish flow")
    RECEIVED.clear()
    MEDIA_COUNTER[0] = 0
    client = session.client()
    result = client.publish([(img_a, "First photo"), (img_b, "Second photo")],
                            "Hello from pfpost", "unlisted")
    uploads = [r for r in RECEIVED if r[1] == "/api/v1/media"]
    statuses = [r for r in RECEIVED if r[1] == "/api/v1/statuses"]
    check("uploaded both images", len(uploads) == 2, str(len(uploads)))
    check("sent real file bytes", all(u[3]["file_bytes"] == len(PNG) for u in uploads))
    check("alt text sent as `description`",
          [u[3]["description"][0] for u in uploads] == ["First photo", "Second photo"])
    check("created exactly one status", len(statuses) == 1)
    if statuses:
        s = statuses[0][3]
        check("caption sent", s["status"] == ["Hello from pfpost"])
        check("visibility honoured", s["visibility"] == ["unlisted"])
        check("media_ids[] repeated in upload order",
              s["media_ids"] == ["media_1", "media_2"], str(s["media_ids"]))
    check("returns the post url", result["url"] == "https://mock.test/p/morro/99")

    print("\n6. time parsing")
    now = datetime.now(timezone.utc)
    rel = pfqueue.parse_when("+2h")
    check("relative +2h", 7100 < (rel - now).total_seconds() < 7300)
    absolute = pfqueue.parse_when("2026-09-10 17:00")
    check("absolute local time keeps tz",
          absolute.tzinfo is not None and absolute.hour == 17)
    for bad in ("tomorrow", "+5y", ""):
        try:
            pfqueue.parse_when(bad)
            check("rejects %r" % bad, False)
        except pfqueue.QueueError:
            check("rejects %r" % bad, True)

    print("\n7. queue")
    store.save_queue({"items": []})
    pfqueue.add([(img_a, "alt")], "due now", "public", now - timedelta(minutes=5))
    later = pfqueue.add([(img_a, None)], "later", "public", now + timedelta(days=1))
    check("only the due item is selected", [i["id"] for i in pfqueue.due()]
          != [] and later["id"] not in [i["id"] for i in pfqueue.due()])

    RECEIVED.clear()
    MEDIA_COUNTER[0] = 0
    processed = pfqueue.run(session)
    check("ran exactly one item", len(processed) == 1, str(len(processed)))
    check("marked it posted", processed and processed[0]["status"] == "posted")
    check("recorded the result url",
          processed and processed[0]["result_url"] == "https://mock.test/p/morro/99")
    check("future item still pending",
          [i["id"] for i in pfqueue.items()] == [later["id"]])

    print("\n8. failure handling")
    pfqueue.add([(tmp / "gone.png", None)], "orphan", "public", now - timedelta(minutes=1))
    processed = pfqueue.run(session)
    orphan = [i for i in processed if i["caption"] == "orphan"]
    check("vanished file fails without uploading",
          orphan and orphan[0]["status"] == "failed"
          and "missing files" in orphan[0]["error"])

    HEADER_LIMIT[0] = 10
    try:
        session.client().verify_credentials()
        check("surfaces an nginx header rejection", False)
    except api.ApiError as exc:
        check("surfaces an nginx header rejection", exc.is_header_too_large)
    HEADER_LIMIT[0] = None

    print("\n9. connection status and disconnect")
    info = session.status()
    check("reports connected", info["connected"] and info["configured"])
    check("reports the instance", info["instance"] == host)
    check("reports scopes", info["scopes"] == "read write")
    check("reports can_read", info["can_read"] is True)
    check("reports the storage backend", info["backend"] == store.backend_name())
    check("status makes no network call",
          (RECEIVED.clear(), session.status(), len(RECEIVED))[2] == 0)

    fresh = Session({})
    blank = fresh.status()
    check("blank session reports not configured",
          not blank["configured"] and not blank["connected"])

    # Sign out but keep the client registration.
    kept = Session(dict(session.state))
    kept.disconnect(forget_client=True)
    check("full disconnect clears the token", "token" not in kept.state)
    check("full disconnect clears the client", "client_id" not in kept.state)
    check("full disconnect reports not configured", not kept.status()["configured"])

    partial = Session(dict(session.state))
    partial.disconnect(forget_client=False)
    check("keep-client disconnect drops the token", "token" not in partial.state)
    check("keep-client disconnect keeps the client",
          partial.state.get("client_id") == session.state["client_id"])
    check("keep-client stays configured but not connected",
          partial.status()["configured"] and not partial.status()["connected"])

    broken = Session(dict(session.state))
    broken.state["token"] = "dpapi:bm90LWEtcmVhbC1ibG9i"   # unreadable credential
    check("unreadable credential reports disconnected, not a crash",
          broken.status()["connected"] is False)

    server.shutdown()
    print("\n%s" % ("All checks passed." if not FAILURES[0]
                    else "%d CHECK(S) FAILED." % FAILURES[0]))
    return 1 if FAILURES[0] else 0


if __name__ == "__main__":
    sys.exit(main())
