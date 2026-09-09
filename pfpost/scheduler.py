"""Windows Task Scheduler integration.

The queue is inert on its own: the GUI and CLI write `queue.json`, and something
has to come along and publish what is due. That something is a scheduled task
running `pfpost queue run`.

Command construction and output parsing are pure functions so they can be
tested without touching the real Task Scheduler.
"""

from __future__ import annotations

import json
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


class SchedulerError(Exception):
    pass


def available() -> bool:
    return IS_WINDOWS


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
        "interval = [string]$t.Triggers[0].Repetition.Interval } "
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
    return {
        "registered": True,
        "state": data.get("state") or "",
        "last_run": data.get("lastRun") or "",
        "last_result": data.get("lastResult"),
        "next_run": data.get("nextRun") or "",
        "interval": data.get("interval") or "",
    }


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


def status(name: str = TASK_NAME) -> dict:
    if not available():
        return {"registered": False, "unsupported": True}
    try:
        return parse_status(_powershell(build_status_command(name)))
    except SchedulerError:
        return {"registered": False}


def register(every: int = DEFAULT_INTERVAL, name: str = TASK_NAME) -> dict:
    executable, arguments, workdir = runner_command()
    _powershell(build_register_command(executable, arguments, workdir, every, name))
    return status(name)


def unregister(name: str = TASK_NAME) -> None:
    _powershell(build_unregister_command(name))


def run_now(name: str = TASK_NAME) -> None:
    _powershell(build_run_now_command(name))
