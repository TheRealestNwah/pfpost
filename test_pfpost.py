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


class MemoryKeyring:
    """Test double for the system credential store.

    The production path deliberately prefers Windows Credential Manager.  That
    API cannot be used from CI or other non-interactive Windows sessions
    (CredWrite returns WinError 1312), so core tests must not touch it.
    """
    def __init__(self):
        self.values = {}

    def set_password(self, service, username, password):
        self.values[service, username] = password

    def get_password(self, service, username):
        return self.values.get((service, username))

    def delete_password(self, service, username):
        try:
            del self.values[service, username]
        except KeyError:
            # Matches keyring's practical contract for a missing old secret:
            # forgetting credentials should remain safe to retry.
            pass


# Keep the production backend selection intact. Only this test process uses an
# in-memory store, so tests cannot create, read or require real credentials.
TEST_KEYRING = MemoryKeyring()
store._keyring = TEST_KEYRING
store._HAS_KEYRING = True

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


class FakeScheduler:
    """Stands in for launchctl, systemctl and crontab.

    Records every command and answers the few that return output, so the
    macOS and Linux backends can be driven end to end on any OS - CI runs on
    Windows, where none of these programs exist.
    """
    def __init__(self, sched):
        self.sched = sched
        self.calls = []
        self.crontab = None            # None = the user has no crontab yet
        self.crontab_error = ""        # set to make `crontab -l` fail
        self.launchd_loaded = False
        self.show = {}

    def run(self, argv, input=None, timeout=30):
        self.calls.append(list(argv))
        if argv[:2] == ["crontab", "-l"]:
            if self.crontab_error:
                raise self.sched.SchedulerError(self.crontab_error)
            if self.crontab is None:
                raise self.sched.SchedulerError("no crontab for pat")
            return self.crontab
        if argv[:2] == ["crontab", "-"]:
            self.crontab = input
            return ""
        if argv[:2] == ["launchctl", "bootstrap"]:
            self.launchd_loaded = True
        if argv[:2] == ["launchctl", "bootout"]:
            if not self.launchd_loaded:
                raise self.sched.SchedulerError("Boot-out failed: 3: No such process")
            self.launchd_loaded = False
        if argv[:2] == ["launchctl", "print"]:
            if not self.launchd_loaded:
                raise self.sched.SchedulerError("Could not find service")
            return ("gui/501/pfpost.pixelfed-poster = {\n\tactive count = 0\n"
                    "\tstate = not running\n\truns = 2\n\tlast exit code = 0\n"
                    "\tendpoints = {\n\t\tstate = active\n\t}\n}\n")
        if argv[:3] == ["systemctl", "--user", "show"]:
            return self.show.get(argv[3], "")
        return ""

    def ran(self, *prefix):
        return [c for c in self.calls if c[:len(prefix)] == list(prefix)]


def posix_scheduler_checks(sched, cli):
    import io
    from contextlib import redirect_stdout

    print("\n  macOS and Linux runners (no job is created)")
    root = Path(tempfile.mkdtemp(prefix="pfsched"))
    python = str(Path(sys.executable).absolute())
    launcher = str(Path(__file__).resolve().parent / "pfpost.py")
    argv = [python, launcher, "queue", "run"]
    workdir = str(Path(__file__).resolve().parent)

    check("names become safe slugs", sched.slug("Pixelfed Poster") == "pixelfed-poster")
    check("an unusable name still gets a slug", sched.slug("!!!") == "pfpost")
    check("launchd label", sched.launchd_label() == "pfpost.pixelfed-poster")
    check("systemd unit", sched.systemd_unit() == "pfpost-pixelfed-poster")
    for minutes, iso in ((15, "PT15M"), (60, "PT1H"), (90, "PT90M"), (1440, "PT24H")):
        check("%d minutes reports as %s" % (minutes, iso), sched.iso_interval(minutes) == iso)
        check("which reads back as %d minutes" % minutes,
              sched.interval_minutes(sched.iso_interval(minutes)) == minutes)

    run_argv, run_dir = sched.runner_argv()
    check("POSIX runner runs `queue run`", run_argv[-2:] == ["queue", "run"], str(run_argv))
    check("POSIX runner points at a real program", Path(run_argv[0]).exists(), run_argv[0])
    check("POSIX runner has a working directory", Path(run_dir).is_dir(), run_dir)
    # A venv's python is a symlink to the base interpreter; following it would
    # run the job without the venv's packages.
    check("POSIX runner keeps a virtual environment's python",
          run_argv[0] == str(Path(sys.executable).absolute()), run_argv[0])

    env = sched.runner_env({"PFPOST_HOME": "/home/pat/pf", "PATH": "/usr/bin",
                            "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus,guid=ab"})
    check("the job keeps PFPOST_HOME", env.get("PFPOST_HOME") == "/home/pat/pf", str(env))
    check("the job keeps a fixed session bus, for keyring",
          env.get("DBUS_SESSION_BUS_ADDRESS") == "unix:path=/run/user/1000/bus", str(env))
    check("but not the rest of the environment", "PATH" not in env, str(env))
    check("an abstract session bus is left out",
          "DBUS_SESSION_BUS_ADDRESS" not in sched.runner_env(
              {"DBUS_SESSION_BUS_ADDRESS": "unix:abstract=/tmp/dbus-x"}))

    check("present program is not missing", sched.program_missing(argv) == "")
    check("a missing program is found",
          sched.program_missing(["/nonexistent/python3", launcher]) == "/nonexistent/python3")
    check("a missing script is found",
          sched.program_missing([python, "/gone/pfpost.py", "queue", "run"]) == "/gone/pfpost.py")
    check("the log lives with pfpost's config",
          sched.log_path().parent == store.config_dir(), str(sched.log_path()))

    # -- launchd builders
    import plistlib
    plist = plistlib.loads(sched.build_launchd_plist(
        ["/Apps/Pixelfed Poster/pfpost", "queue", "run"], "/Apps/Pixelfed Poster", 20,
        log="/tmp/pf.log", env={"PFPOST_HOME": "/x"}))
    check("plist runs the program with separate arguments",
          plist["ProgramArguments"] == ["/Apps/Pixelfed Poster/pfpost", "queue", "run"])
    check("plist interval is in seconds", plist["StartInterval"] == 1200)
    check("plist also runs at login, to catch up after a reboot", plist["RunAtLoad"] is True)
    check("plist keeps the working directory",
          plist["WorkingDirectory"] == "/Apps/Pixelfed Poster")
    check("plist logs output", plist["StandardOutPath"] == "/tmp/pf.log"
          and plist["StandardErrorPath"] == "/tmp/pf.log")
    check("plist carries the environment", plist["EnvironmentVariables"] == {"PFPOST_HOME": "/x"})
    # plistlib writes XML, so markup in a path cannot break out of its string.
    odd = sched.build_launchd_plist(["/o'b <&> \"q\"/python3"], "/", 15)
    check("plist escapes markup in paths",
          plistlib.loads(odd)["ProgramArguments"] == ["/o'b <&> \"q\"/python3"])
    try:
        sched.build_launchd_plist(argv, workdir, every=0)
        check("plist rejects a zero interval", False)
    except sched.SchedulerError:
        check("plist rejects a zero interval", True)
    job = sched.parse_launchd_plist(sched.build_launchd_plist(argv, workdir, 30, log="/l"))
    check("plist reads back", job == {"argv": argv, "minutes": 30, "log": "/l"}, str(job))
    check("an unreadable plist reads as empty",
          sched.parse_launchd_plist(b"not a plist")["argv"] == [])

    printed = sched.parse_launchctl_print(
        "gui/501/pfpost.x = {\n\tstate = running\n\truns = 4\n"
        "\tlast exit code = 78: EX_CONFIG\n\tendpoints = {\n\t\tstate = active\n\t}\n}")
    check("launchctl state is the job's, not a sub-section's", printed["state"] == "running")
    check("launchctl exit code", printed["last_result"] == 78, str(printed))
    check("a job that has not exited has no result",
          sched.parse_launchctl_print("\tlast exit code = (never exited)")["last_result"] is None)

    # -- systemd builders
    service = sched.build_systemd_service(
        ["/home/pat/My Stuff/python3", "/home/pat/100% \"real\"/pfpost.py", "queue", "run"],
        "/home/pat/100% real", {"PFPOST_HOME": "/home/pat/$pf"})
    check("service is a oneshot", "Type=oneshot" in service)
    check("service quotes paths with spaces",
          'ExecStart="/home/pat/My Stuff/python3"' in service, service)
    check("service escapes %, quotes and $",
          '"/home/pat/100%% \\"real\\"/pfpost.py"' in service, service)
    check("working directory escapes %", "WorkingDirectory=/home/pat/100%% real" in service)
    check("environment keeps a literal $", 'Environment="PFPOST_HOME=/home/pat/$pf"' in service)
    check("ExecStart reads back as the same argv",
          sched.parse_systemd_service(service)
          == ["/home/pat/My Stuff/python3", '/home/pat/100% "real"/pfpost.py', "queue", "run"],
          str(sched.parse_systemd_service(service)))
    timer = sched.build_systemd_timer(15)
    check("timer repeats on the interval", "OnUnitActiveSec=15min" in timer)
    check("timer starts soon after enabling or login", "OnActiveSec=" in timer)
    check("timer activates its service", "Unit=pfpost-pixelfed-poster.service" in timer)
    check("timer is enabled by timers.target", "WantedBy=timers.target" in timer)
    check("timer interval reads back", sched.parse_systemd_timer(timer) == 15)
    try:
        sched.build_systemd_timer(0)
        check("timer rejects a zero interval", False)
    except sched.SchedulerError:
        check("timer rejects a zero interval", True)
    shown = sched.parse_systemctl_show("ActiveState=active\nSubState=waiting\nEmpty=\n")
    check("systemctl show parses", shown == {"ActiveState": "active", "SubState": "waiting",
                                             "Empty": ""}, str(shown))

    # -- cron builders
    for minutes, fields in ((1, "* * * * *"), (15, "*/15 * * * *"), (30, "*/30 * * * *"),
                            (60, "0 * * * *"), (120, "0 */2 * * *"), (1440, "0 0 * * *")):
        check("cron every %d minutes is %r" % (minutes, fields),
              sched.cron_schedule(minutes) == fields)
        check("and %r reads back" % fields, sched.parse_cron_schedule(fields) == minutes)
    for minutes in (0, 7, 45, 90, 300):
        try:
            sched.cron_schedule(minutes)
            check("cron refuses an uneven %d-minute interval" % minutes, False)
        except sched.SchedulerError:
            check("cron refuses an uneven %d-minute interval" % minutes, True)
    check("unknown cron fields read as no interval",
          sched.parse_cron_schedule("5 4 * * 1") is None)

    line = sched.build_cron_line(["/home/pat/it's here/python3", "/p/pfpost.py", "queue", "run"],
                                 "/home/pat/50% off", 15, "/home/pat/pf.log",
                                 {"PFPOST_HOME": "/home/pat/pf"})
    check("cron line starts with its schedule", line.startswith("*/15 * * * * cd "), line)
    check("cron line quotes apostrophes for the shell", "'/home/pat/it'\"'\"'s here/python3'" in line,
          line)
    check("cron line escapes %", "50\\% off" in line and "50% off" not in line, line)
    check("cron line appends to the log", line.endswith(">> /home/pat/pf.log 2>&1"), line)
    check("cron line sets the environment", " env PFPOST_HOME=/home/pat/pf " in line, line)

    mine = "MAILTO=pat\n# backups\n0 3 * * * /usr/local/bin/backup\n"
    added = sched.update_crontab(mine, "Pixelfed Poster", line)
    check("adding keeps the user's own entries", added.startswith(mine), added)
    check("adding marks its entry", "# pfpost: Pixelfed Poster\n" + line in added, added)
    again = sched.update_crontab(added, "Pixelfed Poster", line.replace("*/15", "*/30"))
    check("re-adding replaces rather than duplicates",
          again.count("# pfpost: Pixelfed Poster") == 1 and "*/15" not in again, again)
    check("removing restores the crontab exactly",
          sched.update_crontab(again, "Pixelfed Poster") == mine)
    check("removing leaves other pfpost jobs",
          "# pfpost: Other" in sched.update_crontab(
              sched.update_crontab(added, "Other", line), "Pixelfed Poster"))
    entry = sched.parse_crontab(added)
    check("crontab entry reads back", entry["registered"] and entry["minutes"] == 15, str(entry))
    check("crontab entry's program reads back",
          entry["argv"] == ["/home/pat/it's here/python3", "/p/pfpost.py", "queue", "run"],
          str(entry))
    check("crontab entry's working directory and log read back",
          entry["workdir"] == "/home/pat/50% off" and entry["log"] == "/home/pat/pf.log",
          str(entry))
    check("no entry reads as not registered", sched.parse_crontab(mine)["registered"] is False)
    check("a name with a newline cannot add a crontab line",
          sched.cron_marker("a\n* * * * * rm -rf ~") == "# pfpost: a * * * * * rm -rf ~")

    # -- the three backends end to end, against a fake scheduler
    saved = {k: getattr(sched, k) for k in (
        "_run", "_uid", "backend", "launch_agents_dir", "systemd_user_dir", "runner_argv")}
    fake = FakeScheduler(sched)
    sched._run, sched._uid = fake.run, lambda: 501
    sched.launch_agents_dir = lambda: root / "LaunchAgents"
    sched.systemd_user_dir = lambda: root / "systemd"
    sched.runner_argv = lambda: (argv, workdir)
    try:
        print("\n  launchd, end to end")
        sched.backend = lambda: "launchd"
        check("launchd counts as available", sched.available())
        check("launchd is named in messages", sched.backend_label() == "launchd")
        check("nothing is registered at first", sched.status()["registered"] is False)
        info = sched.register(20)
        agent = root / "LaunchAgents" / "pfpost.pixelfed-poster.plist"
        check("register writes the agent", agent.exists())
        check("register loads it into the login session",
              fake.ran("launchctl", "bootstrap", "gui/501", str(agent)), str(fake.calls))
        check("status reports it on", info["registered"] and info["interval"] == "PT20M",
              str(info))
        check("status reads launchctl", info["state"] == "not running"
              and info["last_result"] == 0, str(info))
        check("a healthy agent is not broken", info["broken"] is False, str(info))
        fake.calls.clear()
        sched.register(30)
        check("re-registering unloads the old agent before loading the new one",
              [c[1] for c in fake.calls if c[0] == "launchctl"][:2] == ["bootout", "bootstrap"]
              and fake.launchd_loaded, str(fake.calls))
        check("and changes the interval", sched.status()["interval"] == "PT30M")
        sched.run_now()
        check("run now kicks the job",
              fake.ran("launchctl", "kickstart", "gui/501/pfpost.pixelfed-poster"))
        agent.write_bytes(sched.build_launchd_plist(["/gone/python3", launcher, "queue", "run"],
                                                    workdir, 30))
        gone = sched.status()
        check("an agent whose python was removed is broken",
              gone["broken"] is True and gone["program"] == "/gone/python3", str(gone))
        sched.unregister()
        check("unregister unloads and deletes the agent",
              not agent.exists() and not fake.launchd_loaded)
        check("and it reads as off", sched.status()["registered"] is False)
        sched.unregister()
        check("unregistering twice is harmless", True)

        print("\n  systemd, end to end")
        sched.backend = lambda: "systemd"
        fake.calls.clear()
        check("nothing is registered at first", sched.status()["registered"] is False)
        fake.show = {"pfpost-pixelfed-poster.timer": "ActiveState=active\nSubState=waiting\n",
                     "pfpost-pixelfed-poster.service":
                         "ExecMainStatus=0\nExecMainExitTimestamp=\n"}
        info = sched.register(15)
        unit = root / "systemd" / "pfpost-pixelfed-poster"
        check("register writes the service and timer",
              unit.with_suffix(".service").exists() and unit.with_suffix(".timer").exists())
        check("register reloads, enables and restarts the timer",
              [c[2:] for c in fake.calls if c[2] in ("daemon-reload", "enable", "restart")]
              == [["daemon-reload"], ["enable", "pfpost-pixelfed-poster.timer"],
                  ["restart", "pfpost-pixelfed-poster.timer"]], str(fake.calls))
        check("status reports it on", info["registered"] and info["interval"] == "PT15M",
              str(info))
        check("status shows the timer waiting", info["state"] == "waiting", str(info))
        check("a timer that has not fired yet has no result",
              info["last_result"] is None and info["last_run"] == "", str(info))
        check("the unit runs this pfpost", info["program"] == python and not info["broken"])
        fake.show["pfpost-pixelfed-poster.service"] = (
            "ExecMainStatus=203\nExecMainExitTimestamp=Thu 2026-09-24 01:30:00 UTC\n")
        failed = sched.status()
        check("systemd's could-not-execute status marks it broken",
              failed["broken"] is True and failed["last_result"] == 203, str(failed))
        check("and reports when it ran",
              failed["last_run"] == "Thu 2026-09-24 01:30:00 UTC", str(failed))
        sched.run_now()
        check("run now starts the service without waiting",
              fake.ran("systemctl", "--user", "start", "--no-block",
                       "pfpost-pixelfed-poster.service"))
        sched.unregister()
        check("unregister disables the timer",
              fake.ran("systemctl", "--user", "disable", "--now", "pfpost-pixelfed-poster.timer"))
        check("and deletes both units",
              not unit.with_suffix(".service").exists() and not unit.with_suffix(".timer").exists())
        check("and it reads as off", sched.status()["registered"] is False)

        print("\n  cron, end to end")
        sched.backend = lambda: "cron"
        check("nothing is registered at first", sched.status()["registered"] is False)
        fake.crontab = mine
        info = sched.register(30)
        check("register adds the entry", "# pfpost: Pixelfed Poster" in fake.crontab)
        check("keeping the user's own entries", fake.crontab.startswith(mine), fake.crontab)
        check("status reports it on", info["registered"] and info["interval"] == "PT30M",
              str(info))
        check("a healthy entry is not broken", info["broken"] is False, str(info))
        before = fake.crontab
        try:
            sched.register(45)
            check("an uneven interval is refused", False)
        except sched.SchedulerError as exc:
            check("an uneven interval is refused, naming the ones that work",
                  "every 1, 2, 3" in str(exc), str(exc))
        check("and the crontab is untouched", fake.crontab == before)
        fake.crontab_error = "crontab: cannot open /var/spool/cron: Permission denied"
        try:
            sched.register(15)
            check("an unreadable crontab stops the install", False)
        except sched.SchedulerError:
            check("an unreadable crontab stops the install", True)
        check("rather than replacing the user's crontab", fake.crontab == before)
        fake.crontab_error = ""
        sched.unregister()
        check("unregister restores the crontab", fake.crontab == mine, fake.crontab)
        writes = len(fake.ran("crontab", "-"))
        sched.unregister()
        check("unregistering again does not rewrite the crontab",
              len(fake.ran("crontab", "-")) == writes)

        print("\n  schedule command on macOS and Linux")
        for kind, needles in (
                ("launchd", ("<key>StartInterval</key>", "launchctl bootstrap",
                             "launchctl bootout")),
                ("systemd", ("OnUnitActiveSec=15min", "ExecStart=", "enable --now",
                             "enable-linger")),
                ("cron", ("*/15 * * * *", "# pfpost: Pixelfed Poster", "crontab -e"))):
            sched.backend = lambda kind=kind: kind
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                cli.cmd_schedule(cli.build_parser().parse_args(["schedule", "--every", "15"]))
            out = buffer.getvalue()
            check("plain schedule prints the %s steps" % kind,
                  all(n in out for n in needles), out)
            check("and no PowerShell", "ScheduledTask" not in out)

        sched.backend = lambda: "cron"
        fake.crontab = ""
        sched.register(15)
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            cli.cmd_schedule(cli.build_parser().parse_args(["schedule", "--status"]))
        out = buffer.getvalue()
        check("--status reports it on", "Background posting  on, every 15 minutes" in out, out)
        check("--status leaves out a result cron does not have", "result None" not in out, out)
        sched.unregister()

        sched.backend = lambda: None
        check("no scheduler reads as unsupported", sched.status().get("unsupported") is True)
        try:
            sched.register(15)
            check("and cannot be registered", False)
        except sched.SchedulerError as exc:
            check("and cannot be registered", "none was found" in str(exc), str(exc))
    finally:
        for key, value in saved.items():
            setattr(sched, key, value)


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
    check("uses the in-memory keyring, not the Windows credential store",
          store._keyring is TEST_KEYRING)
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
    check("alt text reminder is on by default",
          store.load_prefs()["warn_alt_text"] is True)
    store.prefs_path().write_text('{"warn_links": false}', encoding="utf-8")
    check("a prefs file from 1.0.1 gains the new key, switched on",
          store.load_prefs() == {"warn_links": False, "warn_alt_text": True},
          str(store.load_prefs()))
    store.prefs_path().unlink()
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

    print("\n5b. learning the account name (for Open account)")
    check("username from the status's account",
          api.status_username({"account": {"username": "nmorrow08"},
                               "url": "https://x/p/other/1"}) == "nmorrow08")
    check("falls back to Pixelfed's /p/<username>/<id> post URL",
          api.status_username(result) == "morro", str(api.status_username(result)))
    for label, junk in (("no url", {}), ("not a post url", {"url": "https://x/i/web/post/9"}),
                        ("empty username segment", {"url": "https://x/p//9"}), ("None", None)):
        check("no username from %s" % label, api.status_username(junk) is None)

    learner = Session({"instance": "gram.social", "scopes": "write"})
    learner.save = lambda: None                    # keep the real state file out of it
    check("before any post, Open account goes to /i/me",
          learner.profile_url() == "https://gram.social/i/me")
    check("a write-only token has no name yet", learner.identity() is None)
    learner.learn_from_status({"url": "https://gram.social/p/nmorrow08/1003756385650833832"})
    check("a post teaches it the username", learner.state.get("username") == "nmorrow08")
    check("write-only identity() now answers without a network call",
          (RECEIVED.clear(), learner.identity(), len(RECEIVED))[1:] == ("nmorrow08", 0))
    check("Open account goes straight to the profile",
          learner.profile_url() == "https://gram.social/nmorrow08")
    for hostile in ("../settings", "a/b", "x?y=1", "name with space", "@nmorrow08", "a" * 65):
        learner.remember_username(hostile)
        check("refuses a username that would bend the URL: %r" % hostile[:12],
              learner.state["username"] == "nmorrow08")

    queued_learner = Session(dict(session.state))
    queued_learner.save = lambda: None
    queued_learner.state.pop("username", None)
    pfqueue.store.save_queue({"items": []})
    pfqueue.add([(img_a, "alt")], "learn from the queue", "public",
                datetime.now(timezone.utc) - timedelta(minutes=1))
    pfqueue.run(queued_learner)
    check("a queued post teaches it too", queued_learner.state.get("username") == "morro",
          str(queued_learner.state.get("username")))
    pfqueue.store.save_queue({"items": []})

    signed_out = Session({"instance": "gram.social", "username": "nmorrow08"})
    signed_out.save = lambda: None
    signed_out.disconnect(forget_client=False)
    check("disconnecting forgets the username - the next sign-in may be someone else",
          "username" not in signed_out.state)

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

    print("\n9d. reproducible frozen-build environment")
    import build as build_script
    from unittest.mock import patch

    if os.name == "nt":
        contaminated = os.pathsep.join([
            r"C:\unrelated-qt\bin", r"C:\unrelated-openssl\bin"])
        with patch.dict(os.environ, {"PATH": contaminated}):
            frozen_env = build_script.build_environment()
        clean_parts = frozen_env["PATH"].split(os.pathsep)
        check("build excludes unrelated native DLL directories",
              all("unrelated" not in part for part in clean_parts))
        check("build keeps its Python interpreter directory",
              str(Path(sys.executable).resolve().parent) in clean_parts)
        check("build keeps Windows system DLL directories",
              any(part.lower().endswith(r"windows\system32")
                  for part in clean_parts))
    else:
        check("non-Windows builds retain PATH",
              build_script.build_environment()["PATH"] == os.environ["PATH"])

    print("\n10. schedule command output")
    import io
    from contextlib import redirect_stdout
    from pfpost import cli

    # Parse real arguments rather than hand-building a Namespace, so new flags
    # and changed defaults are picked up instead of silently diverging.
    from pfpost import scheduler as sched
    parsed_args = cli.build_parser().parse_args(["schedule", "--every", "15"])
    buffer = io.StringIO()
    # The PowerShell output is what this section checks, on whichever OS runs it.
    real_backend = sched.backend
    sched.backend = lambda: "taskscheduler"
    try:
        with redirect_stdout(buffer):
            cli.cmd_schedule(parsed_args)
    finally:
        sched.backend = real_backend
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

    check("a healthy task is not broken", parsed["broken"] is False)
    check("status command checks the task's program exists",
          "Test-Path -LiteralPath" in sched.build_status_command()
          and "Actions[0].Execute" in sched.build_status_command())
    # The real case: the task ran a Python 3.14 pythonw.exe that was uninstalled.
    gone = sched.parse_status(
        '{"registered":true,"state":"Ready","lastResult":2147942402,"interval":"PT15M",'
        '"program":"C:\\\\Python\\\\pythoncore-3.14-64\\\\pythonw.exe","programExists":false}')
    check("a missing program marks the task broken", gone["broken"] is True)
    check("and names the program", gone["program"].endswith("pythonw.exe"), gone["program"])
    check("a missing program is broken even before it has failed a run",
          sched.parse_status('{"registered":true,"lastResult":0,"programExists":false}')
          ["broken"] is True)
    for code in (2147942402, 2147942403):
        check("result 0x%X alone marks it broken" % code,
              sched.parse_status('{"registered":true,"lastResult":%d}' % code)["broken"])
    check("an ordinary failure code is not called broken",
          sched.parse_status('{"registered":true,"lastResult":1,"programExists":true}')
          ["broken"] is False)
    check("output without the program check is not assumed broken",
          sched.parse_status('{"registered":true,"lastResult":0}')["broken"] is False)
    for iso, minutes in (("PT15M", 15), ("PT1H", 60), ("PT2H", 120), ("", None), ("P1D", None)):
        check("interval %r is %s minutes" % (iso, minutes),
              sched.interval_minutes(iso) == minutes)

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

    posix_scheduler_checks(sched, cli)

    print("\n12. update check (GitHub is faked; nothing leaves the machine)")
    from pfpost import updates, __version__
    for text, parsed in (("1.0.4", (1, 0, 4)), ("v1.1.0", (1, 1, 0)), ("2", (2, 0, 0)),
                         ("1.2", (1, 2, 0)), ("1.1.0-dev", (1, 1, 0))):
        check("parses %r" % text, updates.parse_version(text) == parsed,
              str(updates.parse_version(text)))
    check("compares numerically, not as text: 1.10 > 1.9",
          updates.is_newer("1.10.0", "1.9.0"))
    check("an equal version is not newer", not updates.is_newer("v1.1.0", "1.1.0"))
    try:
        updates.parse_version("latest")
        check("rejects a non-version", False)
    except updates.UpdateError:
        check("rejects a non-version", True)

    requests = []
    reply = [200, b""]

    def fake_http(method, url, **kw):
        requests.append((method, url, kw))
        return reply[0], reply[1]

    real_http = api.http
    api.http = fake_http
    try:
        reply[:] = [200, json.dumps({"tag_name": "v9.0.0", "html_url":
                    "https://github.com/TheRealestNwah/pfpost/releases/tag/v9.0.0"}).encode()]
        info = updates.check("1.1.0")
        check("finds a newer release", info["newer"] and info["latest"] == "9.0.0", str(info))
        check("links to that release",
              info["url"].endswith("/releases/tag/v9.0.0"), info["url"])
        check("asks GitHub's public releases API, and nothing else",
              [(m, u) for m, u, _kw in requests] == [("GET", updates.LATEST_API)])
        check("sends no token or body", all("token" not in kw and "data" not in kw
                                            for _m, _u, kw in requests))
        check("says a newer one is available",
              updates.describe(info) == "pfpost 9.0.0 is available (you have 1.1.0).")

        reply[1] = json.dumps({"tag_name": "v1.1.0", "html_url": "x"}).encode()
        info = updates.check("1.1.0")
        check("same version: up to date", not info["newer"] and not info["ahead"])
        check("says so", updates.describe(info) == "pfpost 1.1.0 is the latest version.")
        check("a link that is not this repo's release page is replaced",
              info["url"] == updates.RELEASES_PAGE, info["url"])

        reply[1] = json.dumps({"tag_name": "v1.0.4",
                               "html_url": "https://evil.example/releases/"}).encode()
        info = updates.check("1.1.0")
        check("a local build ahead of the release says so, not 'latest'",
              updates.describe(info)
              == "pfpost 1.1.0 is newer than the latest release (1.0.4).",
              updates.describe(info))
        check("a foreign link never reaches the browser", info["url"] == updates.RELEASES_PAGE)

        for status, body, expect in ((404, b"", "No releases"), (403, b"", "rate-limiting"),
                                     (500, b"", "HTTP 500"), (200, b"<html>", "not a release"),
                                     (200, b'{"name": "x"}', "not a release")):
            reply[:] = [status, body]
            try:
                updates.check("1.1.0")
                check("HTTP %d %r is reported, not raised raw" % (status, body[:8]), False)
            except updates.UpdateError as exc:
                check("HTTP %d %r explains itself" % (status, body[:8]), expect in str(exc),
                      str(exc))
    finally:
        api.http = real_http

    import contextlib
    import io
    from pfpost import cli as pfcli
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            pfcli.main(["--version"])
    except SystemExit:
        pass
    check("pfpost --version prints the version",
          out.getvalue().strip() == "pfpost %s" % __version__, out.getvalue())

    server.shutdown()
    print("\n%s" % ("All checks passed." if not FAILURES[0]
                    else "%d CHECK(S) FAILED." % FAILURES[0]))
    return 1 if FAILURES[0] else 0


if __name__ == "__main__":
    sys.exit(main())
