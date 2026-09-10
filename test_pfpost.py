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

    print("\n4b. link warning (mirrors Pixelfed's Bouncer)")
    real = "Posted with pfpost https://github.com/TheRealestNwah/pfpost"
    check("flags the caption that was actually made unlisted",
          api.link_triggers(real, "public") == ["https://github.com/TheRealestNwah/pfpost"],
          str(api.link_triggers(real, "public")))
    check("ignores a caption with no link",
          api.link_triggers("Morning fog #landscape @friend", "public") == [])
    for vis in ("unlisted", "private"):
        check("silent for %s posts - Bouncer only checks public" % vis,
              api.link_triggers(real, vis) == [])
    check("empty caption is fine", api.link_triggers("", "public") == [])
    for word in ("http://x.io", "hxxps://x", "hxxp://x", "www.x.io",
                 "example.com", "example.net", "example.org"):
        check("flags %s" % word, api.link_triggers("see " + word, "public") == [word])
    check("case-sensitive like Str::contains, so EXAMPLE.COM passes Pixelfed too",
          api.link_triggers("EXAMPLE.COM", "public") == [])
    check("an email address trips it, as it does on the server",
          api.link_triggers("mail me@site.com", "public") == ["me@site.com"])
    check("marker list matches upstream Bouncer.php",
          api.LINK_MARKERS == ("https://", "http://", "hxxps://", "hxxp://",
                               "www.", ".com", ".net", ".org"))

    check("warning is on by default", store.load_prefs()["warn_links"] is True)
    store.set_pref("warn_links", False)
    check("preference persists", store.load_prefs()["warn_links"] is False)
    store.prefs_path().write_text("{not json", encoding="utf-8")
    check("a corrupt prefs file falls back to defaults",
          store.load_prefs()["warn_links"] is True)
    store.prefs_path().write_text('{"warn_links": true, "junk": 1}', encoding="utf-8")
    check("unknown keys in the file are ignored", "junk" not in store.load_prefs())
    try:
        store.set_pref("typo", 1)
        check("unknown preference names are rejected", False)
    except KeyError:
        check("unknown preference names are rejected", True)
    check("prefs live outside state.json, which Session rewrites whole",
          store.prefs_path() != store.state_path())

    import contextlib
    import io
    from pfpost import cli as pfcli
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        pfcli.note_links(real, "public")
    check("CLI notes the link on stderr", "github.com" in err.getvalue(), err.getvalue())
    err = io.StringIO()
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(err):
        pfcli.note_links(real, "unlisted")
        store.set_pref("warn_links", False)
        pfcli.note_links(real, "public")
    check("CLI stays quiet when unlisted or muted", err.getvalue() == "", err.getvalue())
    store.set_pref("warn_links", True)

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

    print("\n8. staging and failure handling")
    try:
        pfqueue.add([(tmp / "gone.png", None)], "orphan", "public",
                    now - timedelta(minutes=1))
        check("queueing a missing file is refused up front", False)
    except pfqueue.QueueError:
        check("queueing a missing file is refused up front", True)

    # The point of staging: what happens to the user's original afterwards
    # stops mattering. Previously this scenario failed the post.
    store.save_queue({"items": []})
    movable = tmp / "movable.png"
    movable.write_bytes(PNG)
    staged_item = pfqueue.add([(movable, "alt")], "staged", "public",
                              now - timedelta(minutes=1))
    check("images are copied out of the original location",
          Path(staged_item["images"][0]["path"]) != movable
          and Path(staged_item["images"][0]["path"]).exists())
    check("the original path is remembered for display",
          staged_item["images"][0]["original"] == str(movable))

    movable.unlink()                       # user deletes or moves the original
    check("the staged copy survives the original being deleted",
          Path(staged_item["images"][0]["path"]).exists())

    RECEIVED.clear()
    MEDIA_COUNTER[0] = 0
    processed = pfqueue.run(session)
    check("a post still publishes after its original is gone",
          processed and processed[0]["status"] == "posted",
          processed[0]["status"] if processed else "not run")
    check("staged copies are cleaned up once posted",
          not (store.staged_dir() / staged_item["id"]).exists()
          or not any((store.staged_dir() / staged_item["id"]).iterdir()))

    # A staged copy can still be lost to disk cleanup or a wiped profile.
    store.save_queue({"items": []})
    doomed = tmp / "doomed.png"
    doomed.write_bytes(PNG)
    item = pfqueue.add([(doomed, None)], "orphan", "public",
                       now - timedelta(minutes=1))
    Path(item["images"][0]["path"]).unlink()
    processed = pfqueue.run(session)
    orphan = [i for i in processed if i["caption"] == "orphan"]
    check("a missing staged copy fails without uploading",
          orphan and orphan[0]["status"] == "failed"
          and "missing files" in orphan[0]["error"])

    store.save_queue({"items": []})
    leaked = store.staged_dir("deadbeef")
    (leaked / "x.png").write_bytes(PNG)
    # Count first: earlier sections leave their own folders behind, so a bare
    # `== 1` would be asserting the test's history, not prune_staged's job.
    orphans = sum(1 for d in store.staged_dir().iterdir() if d.is_dir())
    check("prune_staged reclaims every orphaned copy",
          pfqueue.prune_staged() == orphans, "expected %d" % orphans)
    check("pruning leaves nothing behind", not leaked.exists())

    kept = pfqueue.add([(img_a, None)], "keep me", "public",
                       now + timedelta(days=1))
    check("pruning spares copies a queue item still needs",
          pfqueue.prune_staged() == 0
          and Path(kept["images"][0]["path"]).exists())
    store.save_queue({"items": []})

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

    print("\n9b. retry backoff")
    # A one-minute runner must not burn all three attempts in three minutes.
    store.save_queue({"items": []})
    overdue = pfqueue.add([(img_a, None)], "will fail", "public",
                          now - timedelta(minutes=5))
    raw = store.load_queue()
    raw["items"][0]["attempts"] = 1
    raw["items"][0]["next_attempt_at"] = (
        now + timedelta(minutes=2)).isoformat()
    store.save_queue(raw)
    check("an item inside its backoff is not due", pfqueue.due() == [])

    raw = store.load_queue()
    raw["items"][0]["next_attempt_at"] = (now - timedelta(seconds=1)).isoformat()
    store.save_queue(raw)
    check("an item past its backoff is due again", len(pfqueue.due()) == 1)

    raw = store.load_queue()
    raw["items"][0]["next_attempt_at"] = "not a timestamp"
    store.save_queue(raw)
    check("a corrupt backoff stamp does not hide the item",
          len(pfqueue.due()) == 1)

    check("backoff grows with each attempt",
          list(pfqueue.RETRY_BACKOFF_SECONDS)
          == sorted(pfqueue.RETRY_BACKOFF_SECONDS)
          and pfqueue.RETRY_BACKOFF_SECONDS[0] >= 60)
    check("first retry waits longer than the one-minute runner",
          pfqueue.RETRY_BACKOFF_SECONDS[0] > 60)
    pfqueue.remove(overdue["id"])

    print("\n9c. frozen launcher argument defaults")
    # Double-clicking pfpost-gui.exe passes no arguments. Reaching argparse
    # there means "a subcommand is required" printed to a console that does not
    # exist, and the program appears to do nothing at all.
    launcher = Path(__file__).resolve().parent / "pfpost.py"
    source = launcher.read_text(encoding="utf-8")
    check("the GUI build is identified by filename, not by stdout",
          'stem.lower().endswith("-gui")' in source
          and "GUI_BUILD = FROZEN" in source)
    check("no arguments means the GUI on that build",
          'argv = ["gui"] if GUI_BUILD and len(sys.argv) == 1 else None' in source)
    check("startup failures are surfaced, not swallowed",
          "def report_crash" in source and "QMessageBox" in source)
    check("SystemExit is left alone so exit codes survive",
          "except SystemExit:" in source)
    check("missing streams get somewhere to write",
          "os.devnull" in source)

    print("\n10. schedule command output")
    import io
    from contextlib import redirect_stdout
    from pfpost import cli

    # Parse real arguments rather than hand-building a Namespace, so new flags
    # and changed defaults are picked up instead of silently diverging.
    parsed_args = cli.build_parser().parse_args(["schedule", "--every", "15"])
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        cli.cmd_schedule(parsed_args)
    text = buffer.getvalue()

    check("plain schedule neither installs nor removes",
          parsed_args.install is False and parsed_args.remove is False
          and parsed_args.status is False)

    check("names the interval", "-Minutes 15" in text)
    # Without an explicit duration the trigger gets an empty Duration and
    # StopAtDurationEnd=True, and repetition can stop after a day.
    check("sets an explicit repetition duration",
          "-RepetitionDuration" in text, "repetition would expire")
    check("survives sleep and reboot", "-StartWhenAvailable" in text)
    check("quotes the script path for spaces", '\\"' in text or '"' in text)
    check("offers a removal command", "Unregister-ScheduledTask" in text)

    print("\n11. background runner (no task is created)")
    from pfpost import scheduler as sched

    cmd = sched.build_register_command(
        r"C:\Python\pythonw.exe", r'"D:\Pixelfed Poster\pfpost.py" queue run',
        r"D:\Pixelfed Poster", every=15, name="Pixelfed Poster")
    check("registers an action, trigger and settings",
          all(k in cmd for k in ("New-ScheduledTaskAction", "New-ScheduledTaskTrigger",
                                 "New-ScheduledTaskSettingsSet",
                                 "Register-ScheduledTask")))
    check("keeps the repetition duration", "-RepetitionDuration" in cmd)
    check("survives sleep and reboot", "-StartWhenAvailable" in cmd)
    check("overwrites an existing task", "-Force" in cmd)
    check("passes the working directory", "-WorkingDirectory" in cmd)

    # Paths with an apostrophe would otherwise break out of the PowerShell
    # string and execute whatever followed.
    nasty = sched.build_register_command(
        r"C:\o'brien\pythonw.exe", "queue run", r"C:\o'brien", 15, "It's Mine")
    check("escapes apostrophes in paths", "'C:\\o''brien\\pythonw.exe'" in nasty)
    check("escapes apostrophes in the task name", "'It''s Mine'" in nasty)

    try:
        sched.build_register_command("x", "y", "z", every=0)
        check("rejects a zero interval", False)
    except sched.SchedulerError:
        check("rejects a zero interval", True)

    check("status command tolerates a missing task",
          "-ErrorAction SilentlyContinue" in sched.build_status_command())
    check("unregister does not prompt",
          "-Confirm:$false" in sched.build_unregister_command())

    parsed = sched.parse_status(
        '{"registered":true,"state":"Ready","lastRun":"9/8/2026 8:00:00 PM",'
        '"lastResult":0,"nextRun":"9/8/2026 8:15:00 PM","interval":"PT15M"}')
    check("parses a registered task",
          parsed["registered"] and parsed["interval"] == "PT15M")
    check("parses the last result", parsed["last_result"] == 0)
    check("parses an absent task",
          sched.parse_status('{"registered":false}')["registered"] is False)
    for junk in ("", "   ", "not json", "null", "[]", "Get-ScheduledTask : error"):
        check("treats %r as no task" % junk[:20],
              sched.parse_status(junk)["registered"] is False)

    check("describes minutes", sched.describe_interval("PT15M") == "every 15 minutes")
    check("singular minute", sched.describe_interval("PT1M") == "every 1 minute")
    check("describes hours", sched.describe_interval("PT2H") == "every 2 hours")
    check("falls back on an odd interval",
          sched.describe_interval("P1DT3H") == "P1DT3H")
    check("handles an empty interval",
          sched.describe_interval("") == "on a schedule")

    executable, arguments, workdir = sched.runner_command()
    check("runner points at a real executable", Path(executable).exists(), executable)
    check("runner asks for `queue run`", arguments.endswith("queue run"), arguments)
    check("runner has a working directory", Path(workdir).is_dir(), workdir)

    server.shutdown()
    print("\n%s" % ("All checks passed." if not FAILURES[0]
                    else "%d CHECK(S) FAILED." % FAILURES[0]))
    return 1 if FAILURES[0] else 0


if __name__ == "__main__":
    sys.exit(main())
