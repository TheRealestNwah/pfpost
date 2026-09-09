"""Command line interface. All presentation lives here; logic lives in the
sibling modules so the GUI can reuse it."""

from __future__ import annotations

import argparse
import sys
import textwrap
from pathlib import Path

from . import api, queue as pfqueue, scheduler as pfscheduler, store
from .session import AuthError, DEFAULT_PORT, Session

VISIBILITIES = ["public", "unlisted", "private"]


def die(message) -> None:
    print("\nError: %s" % message, file=sys.stderr)
    sys.exit(1)


def note(text: str) -> None:
    print(text)


def collect_images(paths, alts):
    if alts and len(alts) not in (0, 1, len(paths)):
        die("Give either one --alt for all images, or one per image "
            "(%d given for %d images)." % (len(alts), len(paths)))
    images = []
    for index, raw in enumerate(paths):
        path = Path(raw).expanduser().resolve()
        if not path.exists():
            die("File not found: %s" % path)
        if not path.is_file():
            die("Not a file: %s" % path)
        alt = None if not alts else (alts[0] if len(alts) == 1 else alts[index])
        images.append((path, alt))
    return images


# --------------------------------------------------------------------------

def cmd_register(args):
    session = Session()
    try:
        result = session.register(
            args.instance or session.instance, name=args.name,
            port=args.port or DEFAULT_PORT, scopes=args.scopes, on_note=note)
    except (AuthError, api.ApiError, store.StorageError) as exc:
        die(exc)
    print("\nRegistered on %s" % session.instance)
    print("  client_id     %s" % result["client_id"])
    print("  client_secret %s" % ("stored in %s" % store.backend_description()
                                  if result["has_secret"] else "none - will use PKCE"))
    print("  scopes        %s" % result["scopes"])
    print("  redirect_uri  %s" % result["redirect_uri"])
    print("\nThe client's Redirect URL must match that exactly.")
    print("\nNext:  pfpost auth")


def cmd_auth(args):
    session = Session()
    try:
        token = session.authorize(on_note=note)
    except (AuthError, api.ApiError, store.StorageError) as exc:
        die(exc)
    print("\nToken stored in %s (%d chars)." % (store.backend_description(), len(token)))
    if not session.can_read:
        print("Scope is `%s`, so `whoami` will not work - posting will." % session.scopes)
        advice = session.header_limit_advice()
        if advice:
            print("\n" + advice)
        return
    try:
        me = session.client().verify_credentials()
        print("Authorized as @%s" % me.get("username", "?"))
    except api.ApiError as exc:
        if exc.is_header_too_large:
            print("\nThe token works but this host rejects the header it travels in.")
            print("Re-run:  pfpost register --scopes write")
        else:
            print("Token stored, but the identity check failed:\n  %s" % exc)


def cmd_whoami(args):
    session = Session()
    info = session.status()

    if not info["configured"]:
        print("Status     not connected")
        print("\nRun:  pfpost register --instance <domain>")
        return
    if not info["connected"]:
        print("Status     configured but not authorized")
        print("Instance   %s" % info["instance"])
        print("\nRun:  pfpost auth")
        return

    print("Status     connected")
    print("Instance   %s" % info["instance"])
    print("Client     %s" % info["client_id"])
    print("Scopes     %s" % info["scopes"])
    print("Storage    %s" % store.backend_description())
    print("Token      %s" % ("expires " + pfqueue.local_str(info["expires_at"])
                             if info["expires_at"] else "no expiry reported"))

    if not info["can_read"]:
        print("Account    unavailable - this token has no `read` scope")
        advice = session.header_limit_advice()
        if advice:
            print("\n" + advice)
        return
    try:
        me = session.client().verify_credentials()
        print("Account    @%s (%s)"
              % (me.get("username", "?"), me.get("display_name") or ""))
        print("Posts      %s" % me.get("statuses_count", "?"))
    except api.ApiError as exc:
        print("Account    lookup failed: %s" % exc)


def cmd_logout(args):
    session = Session()
    info = session.status()
    if not info["configured"]:
        print("Nothing to disconnect.")
        return
    session.disconnect(forget_client=not args.keep_client)
    print("Disconnected from %s." % info["instance"])
    if args.keep_client:
        print("The registered client was kept; run `pfpost auth` to sign in again.")
    else:
        print("The client registration was removed too; run `pfpost register` "
              "to reconnect.")
    print("\nPixelfed has no token-revocation endpoint, so the token is only gone "
          "from this machine.\nTo revoke it on the server as well, visit:\n  %s"
          % ("https://%s/settings/applications" % info["instance"]))


def cmd_info(args):
    session = Session()
    if not session.configured:
        die("Not configured. Run:  pfpost register --instance <domain>")
    info = session.public_client().instance_info()
    if not info:
        die("Could not read /api/v1/instance on %s." % session.instance)
    limits = session.public_client().limits()
    print("Title           %s" % info.get("title", "?"))
    print("Version         %s" % info.get("version", "?"))
    print("Max characters  %s" % (limits["max_characters"] or "unreported"))
    print("Max attachments %s" % (limits["max_media_attachments"] or "unreported"))
    if limits["image_size_limit"]:
        print("Max image size  %.1f MB" % (limits["image_size_limit"] / 1048576))
    if limits["supported_mime_types"]:
        print("Accepted types  %s" % ", ".join(limits["supported_mime_types"]))
    advice = session.header_limit_advice()
    if advice:
        print("\n" + advice)


def cmd_post(args):
    session = Session()
    images = collect_images(args.images, args.alt)
    try:
        limits = session.public_client().limits()
        api.validate(images, args.caption, limits)
    except api.ValidationError as exc:
        die(exc)

    if args.dry_run:
        print("DRY RUN - nothing was uploaded.")
        for path, alt in images:
            print("  would upload %s (%.0f KB) alt=%r"
                  % (path.name, path.stat().st_size / 1024, alt))
        print("  would post caption (%d chars), visibility=%s"
              % (len(args.caption or ""), args.visibility))
        return

    missing_alt = [p.name for p, alt in images if not (alt or "").strip()]
    if missing_alt:
        # Not a prompt: scripts and scheduled runs must not block on stdin.
        print("Note: no alt text for %s. Pixelfed cannot add it after posting."
              % ", ".join(missing_alt), file=sys.stderr)

    def progress(index, total, name):
        print("[%d/%d] uploading %s ..." % (index, total, name), flush=True)

    try:
        result = session.client().publish(images, args.caption, args.visibility,
                                          on_progress=progress)
    except (AuthError, api.ApiError, store.StorageError) as exc:
        die(exc)
    print("\nPosted: %s" % (result.get("url") or result.get("uri") or "(no url returned)"))


def cmd_queue_add(args):
    images = collect_images(args.images, args.alt)
    try:
        when = pfqueue.parse_when(args.at)
        item = pfqueue.add(images, args.caption, args.visibility, when)
    except pfqueue.QueueError as exc:
        die(exc)
    print("Queued %s for %s (%d image%s)."
          % (item["id"], pfqueue.local_str(item["post_at"]), len(images),
             "" if len(images) == 1 else "s"))


def cmd_queue_list(args):
    rows = pfqueue.items(include_done=args.all)
    if not rows:
        print("Queue is empty." if args.all else "Nothing pending.")
        return
    print("%-9s %-17s %-8s %-5s %s" % ("ID", "POST AT", "STATUS", "IMGS", "CAPTION"))
    for item in rows:
        caption = (item.get("caption") or "").replace("\n", " ")
        if len(caption) > 40:
            caption = caption[:37] + "..."
        print("%-9s %-17s %-8s %-5d %s"
              % (item["id"], pfqueue.local_str(item["post_at"]), item["status"],
                 len(item["images"]), caption))


def cmd_queue_remove(args):
    try:
        pfqueue.remove(args.id)
    except pfqueue.QueueError as exc:
        die(exc)
    print("Removed %s." % args.id)


def cmd_queue_run(args):
    session = Session()

    def on_event(kind, item, detail):
        if kind == "start":
            print("\n--- %s (due %s)" % (item["id"], pfqueue.local_str(item["post_at"])))
        elif kind == "posted":
            print("  posted: %s" % detail)
        elif kind == "retry":
            print("  failed (attempt %d/%d), will retry next run\n  %s"
                  % (item["attempts"], pfqueue.MAX_ATTEMPTS, detail))
        elif kind == "failed":
            print("  failed permanently\n  %s" % detail)

    try:
        processed = pfqueue.run(session, limit=args.limit, on_event=on_event)
    except (AuthError, api.ApiError, store.StorageError) as exc:
        die(exc)
    if not processed:
        print("Nothing due.")


def cmd_schedule(args):
    task, every = args.name, args.every

    if args.status:
        info = pfscheduler.status(task)
        if info.get("unsupported"):
            die("Background posting via Task Scheduler is Windows-only.")
        if not info["registered"]:
            print("Background posting is off. Enable it with:  pfpost schedule --install")
            return
        print("Background posting  on, %s" % pfscheduler.describe_interval(info["interval"]))
        print("State               %s" % info["state"])
        print("Last run            %s (result %s)"
              % (info["last_run"] or "never", info["last_result"]))
        print("Next run            %s" % (info["next_run"] or "unknown"))
        return

    if args.install:
        try:
            info = pfscheduler.register(every, task)
        except pfscheduler.SchedulerError as exc:
            die(exc)
        print("Background posting enabled - the queue runs %s."
              % pfscheduler.describe_interval(info.get("interval", "")))
        print("Turn it off with:  pfpost schedule --remove")
        return

    if args.remove:
        try:
            pfscheduler.unregister(task)
        except pfscheduler.SchedulerError as exc:
            die(exc)
        print("Background posting disabled. Queued posts now publish only when "
              "you run `pfpost queue run`.")
        return

    executable, arguments, workdir = pfscheduler.runner_command()
    runner, script = executable, arguments.split('" ')[0].lstrip('"')
    print("Tip: `pfpost schedule --install` does all of this for you.\n")

    print("Register a task that drains the queue every %d minutes." % every)
    print("Paste into PowerShell - no administrator rights needed:\n")
    print("$action = New-ScheduledTaskAction -Execute '%s' -Argument '\"%s\" queue run'"
          % (runner, script))
    # -RepetitionDuration is not optional in practice: without it the trigger
    # gets an empty Duration alongside StopAtDurationEnd=True, which is the
    # documented cause of a repeating task quietly stopping after a day.
    print("$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) "
          "-RepetitionInterval (New-TimeSpan -Minutes %d) "
          "-RepetitionDuration (New-TimeSpan -Days 3650)" % every)
    print("$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable "
          "-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries")
    print("Register-ScheduledTask -TaskName '%s' -Action $action -Trigger $trigger "
          "-Settings $settings -Description 'Publishes queued Pixelfed posts'" % task)
    print("\n-StartWhenAvailable is what makes it catch up after sleep or a reboot.\n")
    print("Verify    Get-ScheduledTask -TaskName '%s'" % task)
    print("Run now   Start-ScheduledTask -TaskName '%s'" % task)
    print("Remove    Unregister-ScheduledTask -TaskName '%s' -Confirm:$false" % task)


def cmd_gui(args):
    try:
        from .gui import main as gui_main
    except ImportError as exc:
        die("The GUI needs PySide6:\n  pip install PySide6\n\n(%s)" % exc)
    gui_main()


# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pfpost",
        description="Automate Pixelfed posting.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            getting started:
              pfpost register --instance pixelfed.social
              pfpost auth
              pfpost post photo.jpg --caption "Morning fog" --alt "Fog over a valley"

            scheduling:
              pfpost queue add photo.jpg --caption "Later" --at "2026-09-10 17:00"
              pfpost queue run
              pfpost schedule
            """))
    sub = parser.add_subparsers(dest="command", required=True)

    reg = sub.add_parser("register", help="register an OAuth client via /api/v1/apps")
    reg.add_argument("--instance", help="instance domain, e.g. pixelfed.social")
    reg.add_argument("--name", default="pfpost", help="application name")
    reg.add_argument("--port", type=int, help="redirect port (default %d)" % DEFAULT_PORT)
    reg.add_argument("--scopes", help="override scopes; detected automatically by default")
    reg.set_defaults(func=cmd_register)

    sub.add_parser("auth", help="authorize in the browser").set_defaults(func=cmd_auth)
    sub.add_parser("whoami", help="show connection status").set_defaults(func=cmd_whoami)

    out = sub.add_parser("logout", help="remove stored credentials")
    out.add_argument("--keep-client", action="store_true",
                     help="keep the registered OAuth client, drop only the token")
    out.set_defaults(func=cmd_logout)
    sub.add_parser("info", help="show instance limits").set_defaults(func=cmd_info)
    sub.add_parser("gui", help="open the desktop interface").set_defaults(func=cmd_gui)

    post = sub.add_parser("post", help="publish a post now")
    post.add_argument("images", nargs="+", help="image or video files")
    post.add_argument("--caption", "-c", default="", help="post caption")
    post.add_argument("--alt", "-a", action="append", default=[],
                      help="alt text; once for all images or once per image")
    post.add_argument("--visibility", "-v", default="public", choices=VISIBILITIES)
    post.add_argument("--dry-run", action="store_true", help="validate without uploading")
    post.set_defaults(func=cmd_post)

    q = sub.add_parser("queue", help="schedule posts for later")
    qsub = q.add_subparsers(dest="queue_command", required=True)

    qadd = qsub.add_parser("add", help="add a post to the queue")
    qadd.add_argument("images", nargs="+")
    qadd.add_argument("--caption", "-c", default="")
    qadd.add_argument("--alt", "-a", action="append", default=[])
    qadd.add_argument("--visibility", "-v", default="public", choices=VISIBILITIES)
    qadd.add_argument("--at", required=True,
                      help="'2026-09-10 17:00' (local) or relative like +2h")
    qadd.set_defaults(func=cmd_queue_add)

    qlist = qsub.add_parser("list", help="show queued posts")
    qlist.add_argument("--all", action="store_true", help="include posted and failed")
    qlist.set_defaults(func=cmd_queue_list)

    qrm = qsub.add_parser("remove", help="drop a queued post")
    qrm.add_argument("id")
    qrm.set_defaults(func=cmd_queue_remove)

    qrun = qsub.add_parser("run", help="publish everything that is due")
    qrun.add_argument("--limit", type=int, default=0, help="max posts this run")
    qrun.set_defaults(func=cmd_queue_run)

    sched = sub.add_parser("schedule", help="print the Task Scheduler command")
    sched.add_argument("--every", type=int, default=pfscheduler.DEFAULT_INTERVAL,
                       help="minutes between queue checks")
    sched.add_argument("--name", default=pfscheduler.TASK_NAME,
                       help="scheduled task name")
    sched.add_argument("--install", action="store_true",
                       help="register the task instead of printing the commands")
    sched.add_argument("--remove", action="store_true", help="unregister the task")
    sched.add_argument("--status", action="store_true",
                       help="report whether background posting is on")
    sched.set_defaults(func=cmd_schedule)

    return parser


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except api.ApiError as exc:
        hints = {
            401: "Token rejected. Run `pfpost auth` to re-authorize.",
            403: "Token lacks the needed scope. Re-run `pfpost register` then `pfpost auth`.",
            422: "The instance rejected the payload - check `pfpost info`.",
            429: "Rate limited. Wait a minute before retrying.",
        }
        hint = hints.get(exc.status, "")
        if exc.is_header_too_large:
            hint = ("This host caps request header size below your token length.\n"
                    "Re-run:  pfpost register --scopes write")
        die("%s%s" % (exc, "\n\nHint: " + hint if hint else ""))
    except (AuthError, store.StorageError, pfqueue.QueueError) as exc:
        die(exc)
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        sys.exit(130)
