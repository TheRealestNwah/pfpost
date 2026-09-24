"""Background runner: Task Scheduler, launchd, a systemd user timer, or cron.

The queue is inert on its own: the GUI and CLI write `queue.json`, and something
has to come along and publish what is due. That something is a scheduled job
running `pfpost queue run`:

  Windows  a Task Scheduler task, registered through PowerShell
  macOS    a launchd agent in ~/Library/LaunchAgents
  Linux    a systemd user timer, or a crontab entry where there is no systemd

Every backend offers the same four operations - status, register, unregister,
run_now - and reports status as the same dict, so the CLI and GUI never need to
know which one is in use. Command, unit-file and crontab construction and output
parsing are pure functions so they can be tested on any OS without touching a
real scheduler.
"""

from __future__ import annotations

import functools
import json
import os
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

TASK_NAME = "Pixelfed Poster"
DESCRIPTION = "Publishes queued Pixelfed posts"
DEFAULT_INTERVAL = 15
# A large finite duration rather than TimeSpan::MaxValue, which fails to
# serialise on some Windows builds. Without any duration the trigger gets an
# empty Duration plus StopAtDurationEnd=True and can stop repeating after a day.
DURATION_DAYS = 3650

IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

# Task Scheduler's result when the program a task runs cannot be found:
# 0x80070002 (file not found) and 0x80070003 (path not found). Seen in practice
# when the Python a source-checkout task pointed at was uninstalled.
MISSING_PROGRAM_CODES = (0x80070002, 0x80070003)


class SchedulerError(Exception):
    pass


# Names for each backend in messages such as "Updating launchd ...".
BACKEND_LABELS = {
    "taskscheduler": "Windows Task Scheduler",
    "launchd": "launchd",
    "systemd": "the systemd user timer",
    "cron": "your crontab",
}


def _systemd_user_running() -> bool:
    """True when this login has a systemd user manager to hold a timer.

    /run/systemd/system is systemd's own test for "booted with systemd"; the
    user manager can still be missing (containers, some WSL setups, su shells),
    so ask it directly.
    """
    if not Path("/run/systemd/system").is_dir() or not shutil.which("systemctl"):
        return False
    try:
        return subprocess.run(["systemctl", "--user", "show-environment"],
                              capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


@functools.lru_cache(maxsize=1)
def backend() -> str | None:
    """Which scheduler this system offers, or None if none was found."""
    if IS_WINDOWS:
        return "taskscheduler"
    if IS_MAC:
        return "launchd" if shutil.which("launchctl") else None
    if _systemd_user_running():
        return "systemd"
    if shutil.which("crontab"):
        return "cron"
    return None


def available() -> bool:
    return backend() is not None


def backend_label() -> str:
    return BACKEND_LABELS.get(backend(), "the system scheduler")


def ps_quote(value) -> str:
    """Quote for a PowerShell single-quoted string."""
    return "'%s'" % str(value).replace("'", "''")


def runner_command() -> tuple[str, str, str]:
    """(executable, arguments, working directory) for `pfpost queue run`.

    Frozen builds run themselves; a source checkout runs the launcher under
    pythonw.exe so no console window appears every interval.
    """
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable).resolve()
        # Prefer the windowed build for the scheduled task: the console build
        # flashes a terminal every interval when the user is logged in.
        windowed = exe.with_name("pfpost-gui.exe")
        runner = windowed if windowed.exists() else exe
        return str(runner), "queue run", str(exe.parent)

    root = Path(__file__).resolve().parent.parent
    launcher = root / "pfpost.py"
    if not launcher.exists():                      # installed rather than cloned
        launcher = Path(sys.argv[0]).resolve()
        root = launcher.parent
    python = Path(sys.executable).resolve()
    pythonw = python.with_name("pythonw.exe")
    runner = pythonw if pythonw.exists() else python
    return str(runner), '"%s" queue run' % launcher, str(root)


def build_register_command(executable: str, arguments: str, workdir: str,
                           every: int = DEFAULT_INTERVAL,
                           name: str = TASK_NAME) -> str:
    if every < 1:
        raise SchedulerError("Interval must be at least 1 minute.")
    return "; ".join([
        "$a = New-ScheduledTaskAction -Execute %s -Argument %s -WorkingDirectory %s"
        % (ps_quote(executable), ps_quote(arguments), ps_quote(workdir)),
        "$t = New-ScheduledTaskTrigger -Once -At (Get-Date) "
        "-RepetitionInterval (New-TimeSpan -Minutes %d) "
        "-RepetitionDuration (New-TimeSpan -Days %d)" % (every, DURATION_DAYS),
        "$s = New-ScheduledTaskSettingsSet -StartWhenAvailable "
        "-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries",
        "Register-ScheduledTask -TaskName %s -Action $a -Trigger $t -Settings $s "
        "-Description %s -Force | Out-Null" % (ps_quote(name), ps_quote(DESCRIPTION)),
        "'{\"ok\":true}'",
    ])


def build_status_command(name: str = TASK_NAME) -> str:
    return "; ".join([
        "$t = Get-ScheduledTask -TaskName %s -ErrorAction SilentlyContinue" % ps_quote(name),
        "if (-not $t) { '{\"registered\":false}' } else "
        "{ $i = Get-ScheduledTaskInfo -TaskName %s; "
        "[pscustomobject]@{ registered = $true; state = [string]$t.State; "
        "lastRun = [string]$i.LastRunTime; lastResult = $i.LastTaskResult; "
        "nextRun = [string]$i.NextRunTime; "
        "interval = [string]$t.Triggers[0].Repetition.Interval; "
        "program = [string]$t.Actions[0].Execute; "
        "programExists = [bool](Test-Path -LiteralPath "
        "([Environment]::ExpandEnvironmentVariables([string]$t.Actions[0].Execute).Trim('\"'))) } "
        "| ConvertTo-Json -Compress }" % ps_quote(name),
    ])


def build_unregister_command(name: str = TASK_NAME) -> str:
    return ("Unregister-ScheduledTask -TaskName %s -Confirm:$false; '{\"ok\":true}'"
            % ps_quote(name))


def build_run_now_command(name: str = TASK_NAME) -> str:
    return ("Start-ScheduledTask -TaskName %s; '{\"ok\":true}'" % ps_quote(name))


def parse_status(output: str) -> dict:
    """Turn the PowerShell payload into a plain dict.

    Anything unparseable is reported as not-registered rather than raising:
    the caller only needs to know whether a runner exists.
    """
    text = (output or "").strip()
    if not text:
        return {"registered": False}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {"registered": False}
    if not isinstance(data, dict) or not data.get("registered"):
        return {"registered": False}
    exists = data.get("programExists")
    last_result = data.get("lastResult")
    return {
        "registered": True,
        "state": data.get("state") or "",
        "last_run": data.get("lastRun") or "",
        "last_result": last_result,
        "next_run": data.get("nextRun") or "",
        "interval": data.get("interval") or "",
        "program": data.get("program") or "",
        # Unknown (older output, or the check failed) is not evidence of breakage.
        "program_exists": exists is not False,
        "broken": exists is False or last_result in MISSING_PROGRAM_CODES,
    }


def interval_minutes(iso: str) -> int | None:
    """'PT15M' -> 15, 'PT1H' -> 60; None for anything else."""
    text = (iso or "").upper()
    if text.startswith("PT") and text[2:-1].isdigit():
        if text.endswith("M"):
            return int(text[2:-1])
        if text.endswith("H"):
            return int(text[2:-1]) * 60
    return None


def describe_interval(iso: str) -> str:
    """'PT15M' -> 'every 15 minutes'. Falls back to the raw value."""
    text = (iso or "").upper()
    if text.startswith("PT") and text.endswith("M") and text[2:-1].isdigit():
        minutes = int(text[2:-1])
        return "every %d minute%s" % (minutes, "" if minutes == 1 else "s")
    if text.startswith("PT") and text.endswith("H") and text[2:-1].isdigit():
        hours = int(text[2:-1])
        return "every %d hour%s" % (hours, "" if hours == 1 else "s")
    return iso or "on a schedule"


# --------------------------------------------------------------------------
# shared by the macOS and Linux backends
# --------------------------------------------------------------------------

LOG_NAME = "background.log"

# Carried into the background job so it reads the same queue and credentials as
# the pfpost that enabled it: launchd, systemd and cron all start jobs with a
# bare environment that lacks anything set in a shell profile.
PASSTHROUGH_ENV = ("PFPOST_HOME", "XDG_CONFIG_HOME")

# systemd's exit statuses for "could not change to WorkingDirectory" and "could
# not execute the program": the POSIX counterparts of MISSING_PROGRAM_CODES.
SYSTEMD_MISSING_CODES = (200, 203)


def iso_interval(minutes: int) -> str:
    """15 -> 'PT15M', 120 -> 'PT2H': the form status() reports on every OS."""
    return "PT%dH" % (minutes // 60) if minutes % 60 == 0 else "PT%dM" % minutes


def _check_interval(every: int) -> None:
    if every < 1:
        raise SchedulerError("Interval must be at least 1 minute.")


def slug(name: str) -> str:
    """'Pixelfed Poster' -> 'pixelfed-poster', safe in a file or unit name."""
    text = re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-")
    return text or "pfpost"


def runner_argv() -> tuple[list[str], str]:
    """(argv, working directory) for `pfpost queue run` on macOS and Linux."""
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable).resolve()
        return [str(exe), "queue", "run"], str(exe.parent)

    root = Path(__file__).resolve().parent.parent
    launcher = root / "pfpost.py"
    if not launcher.exists():                      # installed rather than cloned
        launcher = Path(sys.argv[0]).resolve()
        root = launcher.parent
    # absolute(), not resolve(): a virtual environment's python is a symlink to
    # the base interpreter, and following it would run pfpost without the
    # environment's packages (keyring among them).
    return [str(Path(sys.executable).absolute()), str(launcher), "queue", "run"], str(root)


def runner_env(environ=None) -> dict:
    """Variables the background job needs that its scheduler will not supply."""
    environ = os.environ if environ is None else environ
    env = {key: environ[key] for key in PASSTHROUGH_ENV if environ.get(key)}
    # cron jobs get no session bus, and without one keyring cannot reach Secret
    # Service. Only a fixed socket path is worth keeping; an abstract address is
    # specific to this login.
    bus = environ.get("DBUS_SESSION_BUS_ADDRESS", "")
    if bus.startswith("unix:path="):
        env["DBUS_SESSION_BUS_ADDRESS"] = bus.split(",")[0]
    return env


def log_path() -> Path:
    from . import store
    return store.config_dir() / LOG_NAME


def program_missing(argv) -> str:
    """The first of the job's program and script that no longer exists, or ''."""
    argv = list(argv or [])
    candidates = argv[:1] + [a for a in argv[1:2] if a.endswith(".py")]
    for path in candidates:
        # A bare name is looked up on PATH at run time, so only a path can go
        # missing. Not os.path.isabs: on Windows it rejects "/usr/bin/python3".
        if ("/" in path or os.sep in path) and not Path(path).exists():
            return path
    return ""


def _report(argv, minutes, state="", last_run="", last_result=None, next_run="",
            broken_code=False) -> dict:
    """The status dict every backend returns, in parse_status()'s shape."""
    argv = list(argv or [])
    missing = program_missing(argv)
    return {
        "registered": True,
        "state": state,
        "last_run": last_run,
        "last_result": last_result,
        "next_run": next_run,
        "interval": iso_interval(minutes) if minutes else "",
        "program": argv[0] if argv else "",
        "program_exists": not missing,
        "broken": bool(missing) or broken_code,
    }


def _log_time(path: Path) -> str:
    """When the job last wrote its log, as a stand-in for 'last run'."""
    from datetime import datetime
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    except OSError:
        return ""


# --------------------------------------------------------------------------
# macOS: launchd agent
# --------------------------------------------------------------------------

def launchd_label(name: str = TASK_NAME) -> str:
    return "pfpost." + slug(name)


def launch_agents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def build_launchd_plist(argv, workdir: str, every: int = DEFAULT_INTERVAL,
                        name: str = TASK_NAME, log: str = "", env=None) -> bytes:
    _check_interval(every)
    job = {
        "Label": launchd_label(name),
        "ProgramArguments": [str(a) for a in argv],
        "WorkingDirectory": str(workdir),
        "StartInterval": every * 60,
        # Also run at login, and so once straight after a reboot rather than a
        # full interval later. An interval that falls while the Mac sleeps is
        # skipped, not queued, so the worst case after waking is one interval.
        "RunAtLoad": True,
        "ProcessType": "Background",
    }
    if env:
        job["EnvironmentVariables"] = dict(env)
    if log:
        job["StandardOutPath"] = job["StandardErrorPath"] = str(log)
    return plistlib.dumps(job)


def parse_launchd_plist(data: bytes) -> dict:
    """What the agent file says it runs: argv and interval in minutes."""
    try:
        job = plistlib.loads(data)
    except Exception:
        return {"argv": [], "minutes": None, "log": ""}
    if not isinstance(job, dict):
        return {"argv": [], "minutes": None, "log": ""}
    seconds = job.get("StartInterval")
    return {
        "argv": [str(a) for a in job.get("ProgramArguments") or []],
        "minutes": seconds // 60 if isinstance(seconds, int) and seconds >= 60 else None,
        "log": job.get("StandardOutPath") or "",
    }


def parse_launchctl_print(output: str) -> dict:
    """Pull state and last exit code out of `launchctl print`.

    The output is a nested, human-oriented listing; only the first occurrence
    of each key belongs to the job itself, later ones to its sub-sections.
    """
    found = {}
    for line in (output or "").splitlines():
        key, sep, value = line.strip().partition(" = ")
        if sep and key in ("state", "last exit code", "runs") and key not in found:
            found[key] = value.strip()
    code = found.get("last exit code", "")
    head = code.split(":")[0].strip()
    return {
        "state": found.get("state", ""),
        "last_result": int(head) if head.lstrip("-").isdigit() else None,
        "runs": int(found["runs"]) if found.get("runs", "").isdigit() else 0,
    }


# --------------------------------------------------------------------------
# Linux: systemd user timer
# --------------------------------------------------------------------------

def systemd_unit(name: str = TASK_NAME) -> str:
    return "pfpost-" + slug(name)


def systemd_user_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "systemd" / "user"


def systemd_quote(value, dollars: bool = True) -> str:
    """Quote one word for a unit file.

    Inside double quotes systemd honours C escapes, so backslashes and quotes
    are escaped; % introduces a specifier everywhere, and in ExecStart= $
    introduces a variable, so both are doubled to mean themselves.
    """
    text = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    if dollars:
        text = text.replace("$", "$$")
    return '"%s"' % text


def build_systemd_service(argv, workdir: str, env=None) -> str:
    lines = [
        "[Unit]",
        "Description=%s" % DESCRIPTION,
        "",
        "[Service]",
        "Type=oneshot",
        "WorkingDirectory=%s" % str(workdir).replace("%", "%%"),
    ]
    for key, value in (env or {}).items():
        lines.append("Environment=%s" % systemd_quote("%s=%s" % (key, value), dollars=False))
    lines.append("ExecStart=%s" % " ".join(systemd_quote(a) for a in argv))
    return "\n".join(lines) + "\n"


def build_systemd_timer(every: int = DEFAULT_INTERVAL, name: str = TASK_NAME) -> str:
    _check_interval(every)
    return "\n".join([
        "[Unit]",
        "Description=%s %s" % (DESCRIPTION, describe_interval(iso_interval(every))),
        "",
        "[Timer]",
        # Monotonic timers pause while the machine sleeps and pick up where they
        # left off on waking, so a missed run is at most one interval late. The
        # first run comes shortly after enabling, or after logging in.
        "OnActiveSec=30s",
        "OnUnitActiveSec=%dmin" % every,
        "AccuracySec=10s",
        "Unit=%s.service" % systemd_unit(name),
        "",
        "[Install]",
        "WantedBy=timers.target",
    ]) + "\n"


def parse_systemd_timer(text: str) -> int | None:
    match = re.search(r"^OnUnitActiveSec=(\d+)min\s*$", text or "", re.M)
    return int(match.group(1)) if match else None


def parse_systemd_service(text: str) -> list[str]:
    """The argv ExecStart= runs, undoing systemd_quote()."""
    match = re.search(r"^ExecStart=(.*)$", text or "", re.M)
    if not match:
        return []
    try:
        words = shlex.split(match.group(1))
    except ValueError:
        return []
    return [w.replace("%%", "%").replace("$$", "$") for w in words]


def parse_systemctl_show(output: str) -> dict:
    """`systemctl show -p A,B` prints A=... lines; unset values are empty."""
    values = {}
    for line in (output or "").splitlines():
        key, sep, value = line.partition("=")
        if sep:
            values[key.strip()] = value.strip()
    return values


# --------------------------------------------------------------------------
# Linux without systemd: crontab entry
# --------------------------------------------------------------------------

CRON_MARKER = "# pfpost: "


def cron_schedule(every: int = DEFAULT_INTERVAL) -> str:
    """Five cron fields for `every` minutes.

    cron counts from the top of the hour and midnight rather than from when the
    job was added, so only intervals that divide an hour or a day repeat evenly.
    Anything else is refused rather than silently run at uneven gaps.
    """
    _check_interval(every)
    if every == 1:
        return "* * * * *"
    if every < 60 and 60 % every == 0:
        return "*/%d * * * *" % every
    if every % 60 == 0 and 24 % (every // 60) == 0:
        hours = every // 60
        if hours == 1:
            return "0 * * * *"
        return "0 0 * * *" if hours == 24 else "0 */%d * * *" % hours
    raise SchedulerError(
        "cron can only repeat every 1, 2, 3, 4, 5, 6, 10, 12, 15, 20 or 30 minutes, "
        "or every 1, 2, 3, 4, 6, 8, 12 or 24 hours - not every %d minutes." % every)


def parse_cron_schedule(fields: str) -> int | None:
    """Inverse of cron_schedule(): minutes between runs, or None."""
    parts = (fields or "").split()
    if len(parts) != 5 or parts[2:] != ["*", "*", "*"]:
        return None
    minute, hour = parts[0], parts[1]
    if hour == "*":
        if minute == "*":
            return 1
        if minute.startswith("*/") and minute[2:].isdigit():
            return int(minute[2:])
        return 60 if minute == "0" else None
    if minute == "0":
        if hour == "0":
            return 1440
        if hour.startswith("*/") and hour[2:].isdigit():
            return int(hour[2:]) * 60
    return None


def _cron_escape(text: str) -> str:
    # An unescaped % ends the command in a crontab and turns the rest into stdin.
    return text.replace("%", "\\%")


def build_cron_line(argv, workdir: str, every: int = DEFAULT_INTERVAL,
                    log: str = "", env=None) -> str:
    words = ["cd", shlex.quote(str(workdir)), "&&"]
    if env:
        words.append("env")
        words += [shlex.quote("%s=%s" % item) for item in env.items()]
    words += [shlex.quote(str(a)) for a in argv]
    if log:
        words += [">>", shlex.quote(str(log)), "2>&1"]
    return "%s %s" % (cron_schedule(every), _cron_escape(" ".join(words)))


def cron_marker(name: str = TASK_NAME) -> str:
    return CRON_MARKER + " ".join(str(name).split())


def update_crontab(current: str, name: str = TASK_NAME, line: str | None = None) -> str:
    """Drop this job's entry from a crontab and, given a line, add it back.

    Each entry is a marker comment followed by the job, so everything else in
    the user's crontab is left exactly as it was.
    """
    marker = cron_marker(name)
    kept, skip = [], False
    for row in (current or "").splitlines():
        if skip:
            skip = False
            continue
        if row.strip() == marker:
            skip = True
            continue
        kept.append(row)
    if line:
        kept += [marker, line]
    return "\n".join(kept) + "\n" if kept else ""


def parse_crontab(text: str, name: str = TASK_NAME) -> dict:
    """Find this job's entry: its interval, the argv it runs and its log."""
    rows = (text or "").splitlines()
    marker = cron_marker(name)
    for i, row in enumerate(rows):
        if row.strip() == marker and i + 1 < len(rows):
            entry = rows[i + 1].split(None, 5)
            if len(entry) < 6:
                break
            try:
                words = shlex.split(entry[5].replace("\\%", "%"))
            except ValueError:
                words = []
            workdir, log = "", ""
            if "&&" in words:
                at = words.index("&&")
                workdir = words[1] if at == 2 and words[0] == "cd" else ""
                words = words[at + 1:]
            if words[:1] == ["env"]:
                words = words[1:]
                while words and "=" in words[0] and not words[0].startswith("/"):
                    words = words[1:]
            if ">>" in words:
                at = words.index(">>")
                log = words[at + 1] if at + 1 < len(words) else ""
                words = words[:at]
            return {"registered": True,
                    "minutes": parse_cron_schedule(" ".join(entry[:5])),
                    "argv": words, "workdir": workdir, "log": log}
    return {"registered": False}


def manual_instructions(kind: str, argv, workdir: str, every: int = DEFAULT_INTERVAL,
                        name: str = TASK_NAME, log: str = "", env=None) -> str:
    """What `schedule --install` would do on macOS or Linux, as copyable steps."""
    if kind == "launchd":
        label = launchd_label(name)
        path = "~/Library/LaunchAgents/%s.plist" % label
        target = "gui/$(id -u)/%s" % label
        plist = build_launchd_plist(argv, workdir, every, name, log, env).decode()
        return "\n".join([
            "Save this as %s:\n" % path,
            plist,
            "Load      launchctl bootstrap gui/$(id -u) %s" % path,
            "Verify    launchctl print %s" % target,
            "Run now   launchctl kickstart %s" % target,
            "Remove    launchctl bootout %s; rm %s" % (target, path),
        ])
    if kind == "systemd":
        unit = systemd_unit(name)
        return "\n".join([
            "Save this as ~/.config/systemd/user/%s.service:\n" % unit,
            build_systemd_service(argv, workdir, env),
            "and this as ~/.config/systemd/user/%s.timer:\n" % unit,
            build_systemd_timer(every, name),
            "Enable    systemctl --user daemon-reload; "
            "systemctl --user enable --now %s.timer" % unit,
            "Verify    systemctl --user list-timers %s.timer" % unit,
            "Run now   systemctl --user start %s.service" % unit,
            "Remove    systemctl --user disable --now %s.timer" % unit,
            "",
            "The timer runs while you are logged in. To keep it running after you "
            "log out:  loginctl enable-linger",
        ])
    if kind == "cron":
        return "\n".join([
            "Run `crontab -e` and add these two lines:\n",
            cron_marker(name),
            build_cron_line(argv, workdir, every, log, env),
            "",
            "Remove them again with `crontab -e`, or run  pfpost schedule --remove",
        ])
    raise SchedulerError(unsupported_message())


def unsupported_message() -> str:
    return ("Background posting needs Windows Task Scheduler, launchd, a systemd "
            "user session or cron, and none was found on this system.")

# --------------------------------------------------------------------------
# execution
# --------------------------------------------------------------------------

def _powershell(command: str, timeout: int = 60) -> str:
    if not IS_WINDOWS:
        raise SchedulerError("Background posting is only supported on Windows.")
    try:
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, text=True, timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except FileNotFoundError:
        raise SchedulerError("powershell.exe was not found on this system.")
    except subprocess.TimeoutExpired:
        raise SchedulerError("Task Scheduler did not respond within %ds." % timeout)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise SchedulerError(detail.splitlines()[0] if detail
                             else "PowerShell exited with %d." % completed.returncode)
    return completed.stdout


def _run(argv, input: str | None = None, timeout: int = 30) -> str:
    """Run a scheduler command on macOS or Linux; stdout, or SchedulerError."""
    try:
        completed = subprocess.run(argv, capture_output=True, text=True,
                                   input=input, timeout=timeout)
    except FileNotFoundError:
        raise SchedulerError("%s was not found on this system." % argv[0])
    except subprocess.TimeoutExpired:
        raise SchedulerError("%s did not respond within %ds." % (argv[0], timeout))
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise SchedulerError(detail.splitlines()[0] if detail
                             else "%s exited with %d." % (argv[0], completed.returncode))
    return completed.stdout


def _uid() -> int:
    return os.getuid()


def _write(path: Path, data) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, bytes):
            path.write_bytes(data)
        else:
            path.write_text(data, encoding="utf-8")
    except OSError as exc:
        raise SchedulerError("Could not write %s: %s" % (path, exc))


# -- launchd ----------------------------------------------------------------

def _launchd_paths(name):
    label = launchd_label(name)
    return label, launch_agents_dir() / (label + ".plist"), "gui/%d/%s" % (_uid(), label)


def _launchd_status(name):
    label, path, target = _launchd_paths(name)
    if not path.exists():
        return {"registered": False}
    job = parse_launchd_plist(path.read_bytes())
    try:
        loaded = parse_launchctl_print(_run(["launchctl", "print", target]))
    except SchedulerError:
        # The file is there but launchd has not loaded it; it will at next login.
        loaded = {"state": "not loaded", "last_result": None}
    return _report(job["argv"], job["minutes"], state=loaded["state"],
                   last_run=_log_time(Path(job["log"])) if job["log"] else "",
                   last_result=loaded["last_result"])


def _launchd_register(argv, workdir, every, name, log, env):
    label, path, target = _launchd_paths(name)
    plist = build_launchd_plist(argv, workdir, every, name, log, env)
    try:
        _run(["launchctl", "bootout", target])     # replace, as -Force does
    except SchedulerError:
        pass                                        # nothing loaded yet
    _write(path, plist)
    _run(["launchctl", "bootstrap", "gui/%d" % _uid(), str(path)])


def _launchd_unregister(name):
    label, path, target = _launchd_paths(name)
    try:
        _run(["launchctl", "bootout", target])
    except SchedulerError:
        pass
    path.unlink(missing_ok=True)


def _launchd_run_now(name):
    _run(["launchctl", "kickstart", _launchd_paths(name)[2]])


# -- systemd ----------------------------------------------------------------

def _systemd_paths(name):
    unit = systemd_unit(name)
    folder = systemd_user_dir()
    return unit, folder / (unit + ".service"), folder / (unit + ".timer")


def _systemctl(*args) -> str:
    return _run(["systemctl", "--user"] + list(args))


def _systemd_status(name):
    unit, service, timer = _systemd_paths(name)
    if not (service.exists() and timer.exists()):
        return {"registered": False}
    argv = parse_systemd_service(service.read_text(encoding="utf-8"))
    minutes = parse_systemd_timer(timer.read_text(encoding="utf-8"))
    try:
        t = parse_systemctl_show(_systemctl(
            "show", unit + ".timer", "--property=ActiveState,SubState"))
        s = parse_systemctl_show(_systemctl(
            "show", unit + ".service",
            "--property=ExecMainStatus,ExecMainExitTimestamp"))
    except SchedulerError:
        t, s = {}, {}
    state = t.get("SubState") or t.get("ActiveState") or ""
    if t.get("ActiveState") == "inactive":
        state = "not enabled"
    ran = s.get("ExecMainExitTimestamp", "") not in ("", "n/a")
    code = s.get("ExecMainStatus", "")
    last_result = int(code) if ran and code.isdigit() else None
    return _report(argv, minutes, state=state,
                   last_run=s.get("ExecMainExitTimestamp", "") if ran else "",
                   last_result=last_result,
                   broken_code=last_result in SYSTEMD_MISSING_CODES)


def _systemd_register(argv, workdir, every, name, log, env):
    unit, service, timer = _systemd_paths(name)
    timer_text = build_systemd_timer(every, name)
    _write(service, build_systemd_service(argv, workdir, env))
    _write(timer, timer_text)
    _systemctl("daemon-reload")
    _systemctl("enable", unit + ".timer")
    # restart rather than start, so a changed interval takes effect now.
    _systemctl("restart", unit + ".timer")


def _systemd_unregister(name):
    unit, service, timer = _systemd_paths(name)
    try:
        _systemctl("disable", "--now", unit + ".timer")
    except SchedulerError:
        pass
    service.unlink(missing_ok=True)
    timer.unlink(missing_ok=True)
    _systemctl("daemon-reload")


def _systemd_run_now(name):
    _systemctl("start", "--no-block", systemd_unit(name) + ".service")


# -- cron -------------------------------------------------------------------

def _read_crontab() -> str:
    try:
        return _run(["crontab", "-l"])
    except SchedulerError as exc:
        # An empty crontab is an error to `crontab -l`. Anything else must stop
        # here: carrying on would install a crontab without the user's entries.
        if "no crontab" in str(exc).lower():
            return ""
        raise


def _cron_status(name):
    entry = parse_crontab(_read_crontab(), name)
    if not entry["registered"]:
        return {"registered": False}
    return _report(entry["argv"], entry["minutes"], state="scheduled",
                   last_run=_log_time(Path(entry["log"])) if entry["log"] else "")


def _cron_register(argv, workdir, every, name, log, env):
    line = build_cron_line(argv, workdir, every, log, env)
    _run(["crontab", "-"], input=update_crontab(_read_crontab(), name, line))


def _cron_unregister(name):
    current = _read_crontab()
    if cron_marker(name) in current:
        _run(["crontab", "-"], input=update_crontab(current, name))


def _cron_run_now(name):
    entry = parse_crontab(_read_crontab(), name)
    if not entry["registered"]:
        raise SchedulerError("Background posting is not set up in your crontab.")
    # Run what the entry runs, as Start-ScheduledTask would, not this process.
    with open(entry["log"] or os.devnull, "a", encoding="utf-8") as log:
        subprocess.Popen(entry["argv"], cwd=entry["workdir"] or None,
                         env={**os.environ, **runner_env()},
                         stdout=log, stderr=subprocess.STDOUT, start_new_session=True)


POSIX_BACKENDS = {
    "launchd": (_launchd_status, _launchd_register, _launchd_unregister, _launchd_run_now),
    "systemd": (_systemd_status, _systemd_register, _systemd_unregister, _systemd_run_now),
    "cron": (_cron_status, _cron_register, _cron_unregister, _cron_run_now),
}


def _require_backend() -> str:
    kind = backend()
    if kind is None:
        raise SchedulerError(unsupported_message())
    return kind


# -- public entry points ------------------------------------------------------

def status(name: str = TASK_NAME) -> dict:
    kind = backend()
    if kind is None:
        return {"registered": False, "unsupported": True}
    try:
        if kind == "taskscheduler":
            return parse_status(_powershell(build_status_command(name)))
        return POSIX_BACKENDS[kind][0](name)
    except (SchedulerError, OSError):
        return {"registered": False}


def register(every: int = DEFAULT_INTERVAL, name: str = TASK_NAME) -> dict:
    kind = _require_backend()
    if kind == "taskscheduler":
        executable, arguments, workdir = runner_command()
        _powershell(build_register_command(executable, arguments, workdir, every, name))
    else:
        argv, workdir = runner_argv()
        POSIX_BACKENDS[kind][1](argv, workdir, every, name, str(log_path()), runner_env())
    return status(name)


def unregister(name: str = TASK_NAME) -> None:
    kind = _require_backend()
    if kind == "taskscheduler":
        _powershell(build_unregister_command(name))
    else:
        POSIX_BACKENDS[kind][2](name)


def run_now(name: str = TASK_NAME) -> None:
    kind = _require_backend()
    if kind == "taskscheduler":
        _powershell(build_run_now_command(name))
    else:
        POSIX_BACKENDS[kind][3](name)
