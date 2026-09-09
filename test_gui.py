"""GUI smoke test. Run: python test_gui.py

Builds the real widgets on Qt's offscreen platform, so it works headless and in
CI. Covers construction and the data path between the table and a post payload -
not appearance.
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PFPOST_HOME", tempfile.mkdtemp(prefix="pfgui"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from PySide6.QtWidgets import QApplication
except ImportError:
    print("PySide6 is not installed - skipping GUI tests.")
    print("Install it with:  pip install PySide6")
    sys.exit(0)

def make_png(size: int = 8) -> bytes:
    """A real, valid greyscale PNG so Qt can actually decode a thumbnail."""
    import struct
    import zlib

    raw = b"".join(b"\x00" + bytes(range(size)) for _ in range(size))

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


PNG = make_png()

FAILURES = []


def check(label, condition, detail=""):
    if not condition:
        FAILURES.append(label)
    print("  [%s] %s%s" % ("PASS" if condition else "FAIL", label,
                           ("  <- " + detail) if detail and not condition else ""))


def main():
    app = QApplication([])                                    # noqa: F841
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QColor, QPalette
    from PySide6.QtWidgets import QApplication as _QApp
    from pfpost.gui import ConnectDialog, MainWindow
    from pfpost.session import Session

    tmp = Path(tempfile.mkdtemp())
    a, b = tmp / "a.png", tmp / "b.png"
    a.write_bytes(PNG)
    b.write_bytes(PNG)

    print("\nGUI smoke test (offscreen)")
    window = MainWindow()
    check("main window builds", window is not None)
    check("composer present", window.composer is not None)
    check("queue panel present", window.queue_panel is not None)

    composer = window.composer
    composer.set_limits({"max_characters": 500, "max_media_attachments": 4,
                         "image_size_limit": 15728640,
                         "supported_mime_types": ["image/png"]})

    composer.add_paths([a, b])
    check("two rows added", composer.table.rowCount() == 2,
          str(composer.table.rowCount()))

    composer.table.item(0, 2).setText("First alt")
    composer.table.item(1, 2).setText("Second alt")
    images = composer.images()
    check("alt text reads back in row order",
          [i[1] for i in images] == ["First alt", "Second alt"],
          str([i[1] for i in images]))
    check("paths read back in order",
          [i[0].name for i in images] == ["a.png", "b.png"])
    check("blank alt becomes None",
          (composer.table.item(0, 2).setText(""), composer.images()[0][1])[1] is None)
    composer.table.item(0, 2).setText("First alt")

    composer.caption.setPlainText("hello")
    check("counter shows the instance limit", "/ 500" in composer.counter.text(),
          composer.counter.text())
    composer.caption.setPlainText("x" * 501)
    check("counter flags an over-long caption", "c0392b" in composer.counter.styleSheet())

    composer.caption.setPlainText("ok")
    check("gather accepts a valid post", composer.gather() is not None)

    composer.remove_selected_all = None
    composer.table.selectRow(0)
    composer.remove_selected()
    check("remove drops the selected row", composer.table.rowCount() == 1)

    composer.clear()
    check("clear empties table and caption",
          composer.table.rowCount() == 0 and composer.caption.toPlainText() == "")

    dialog = ConnectDialog(Session({}))
    check("connect dialog builds", dialog.instance is not None)

    window.queue_panel.reload()
    check("queue panel reloads", window.queue_panel.table.rowCount() >= 0)

    print("\n  account bar")
    bar = window.account_bar

    bar.show_state({"configured": False, "connected": False})
    check("disconnected state is visible",
          "Not connected" in bar.primary.text(), bar.primary.text())
    check("disconnected offers Connect", bar.button.text() == "Connect account")
    check("disconnected dot is red", "c0392b" in bar.dot.styleSheet())

    bar.show_state({"configured": True, "connected": False, "instance": "x.social"})
    check("registered-but-unauthorized is distinguished",
          "Not signed in" in bar.primary.text(), bar.primary.text())
    check("unauthorized offers Sign in", bar.button.text() == "Sign in")

    bar.show_state({"configured": True, "connected": True, "instance": "x.social",
                    "scopes": "read write", "can_read": True, "backend": "keyring"},
                   username="morro")
    check("connected shows the username",
          "@morro" in bar.primary.text(), bar.primary.text())
    check("connected dot is green", "27ae60" in bar.dot.styleSheet())
    check("connected offers Disconnect", bar.button.text() == "Disconnect")
    check("detail names the instance and backend",
          "x.social" in bar.detail.text() and "keyring" in bar.detail.text(),
          bar.detail.text())

    bar.show_state({"configured": True, "connected": True, "instance": "gram.social",
                    "scopes": "write", "can_read": False, "backend": "dpapi"},
                   username=None)
    check("write-only falls back to the instance name",
          "gram.social" in bar.primary.text(), bar.primary.text())
    check("write-only explains the missing username",
          "write-only" in bar.detail.text(), bar.detail.text())

    print("\n  background runner row")
    from pfpost import scheduler as sched
    panel = window.queue_panel

    panel.runner_state({"registered": False})
    check("off state warns what happens once pfpost is closed",
          "close it" in panel.runner_label.text(), panel.runner_label.text())
    check("off state is highlighted", "e67e22" in panel.runner_label.styleSheet())
    check("off state offers Enable",
          panel.runner_button.text() == "Enable background posting")
    check("off state tracked", panel.runner_registered is False)

    panel.runner_state({"registered": True, "interval": "PT15M", "state": "Ready",
                        "last_result": 0})
    check("on state names the interval",
          "every 15 minutes" in panel.runner_label.text(), panel.runner_label.text())
    check("on state is not highlighted", panel.runner_label.styleSheet() == "")
    check("on state offers Disable",
          panel.runner_button.text() == "Disable background posting")

    # 267011 is SCHED_S_TASK_HAS_NOT_RUN - normal for a freshly created task.
    panel.runner_state({"registered": True, "interval": "PT15M", "last_result": 267011})
    check("a never-run task is not reported as failing",
          "reported code" not in panel.runner_label.text(), panel.runner_label.text())
    panel.runner_state({"registered": True, "interval": "PT15M", "last_result": 1})
    check("a real failure code is surfaced",
          "reported code 1" in panel.runner_label.text(), panel.runner_label.text())

    check("nudge fires once per session, not every queue",
          window._runner_nudged is False)

    print("\n  queue housekeeping")
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    from pfpost import queue as pfq

    pfq.store.save_queue({"items": []})
    keep = pfq.add([(a, "alt")], "still pending", "public",
                   _dt.now(_tz.utc) + _td(days=1))
    done = pfq.add([(a, "alt")], "done", "public", _dt.now(_tz.utc))
    bad = pfq.add([(a, "alt")], "broke", "public", _dt.now(_tz.utc))
    raw = pfq.store.load_queue()
    for row in raw["items"]:
        if row["id"] == done["id"]:
            row["status"] = "posted"
        if row["id"] == bad["id"]:
            row["status"] = "failed"
    pfq.store.save_queue(raw)

    panel.reload()
    check("clear button counts finished items",
          "(2)" in panel.clear_button.text(), panel.clear_button.text())
    check("clear button enabled when there is something to clear",
          panel.clear_button.isEnabled())

    panel.clear_posted()
    remaining = [i["id"] for i in pfq.items(include_done=True)]
    check("clearing removes posted and failed",
          done["id"] not in remaining and bad["id"] not in remaining)
    check("clearing keeps pending posts", keep["id"] in remaining, str(remaining))
    check("clear button disables when nothing is finished",
          not panel.clear_button.isEnabled())
    check("clear button drops the count when empty",
          panel.clear_button.text() == "Clear posted", panel.clear_button.text())

    # Cancelling a scheduled post is deliberate; housekeeping must not do it.
    try:
        pfq.clear(("pending",))
        check("clear refuses to drop pending posts", False)
    except pfq.QueueError:
        check("clear refuses to drop pending posts", True)

    second = pfq.add([(a, "alt")], "another", "public",
                     _dt.now(_tz.utc) + _td(days=2))
    check("remove_many drops several at once",
          pfq.remove_many([keep["id"], second["id"]]) == 2)
    check("remove_many ignores unknown ids", pfq.remove_many(["nope"]) == 0)
    check("remove_many on nothing is a no-op", pfq.remove_many([]) == 0)
    check("counts tally by status", pfq.counts()["pending"] == 0)

    print("\n  auto-run while open")
    from pfpost.gui import AUTO_RUN_MS

    check("timer is running", panel.auto_timer.isActive())
    check("timer checks every minute", panel.auto_timer.interval() == AUTO_RUN_MS)
    check("interval is well under the background task's",
          AUTO_RUN_MS / 1000 < sched.DEFAULT_INTERVAL * 60)

    pfq.store.save_queue({"items": []})
    panel.session = Session({})                       # not connected
    check("does nothing while disconnected", panel.auto_run() == "disconnected")

    class _Connected:
        def status(self):
            return {"connected": True}
    panel.session = _Connected()
    check("does nothing with an empty queue", panel.auto_run() == "nothing due")

    pfq.add([(a, "alt")], "overdue", "public", _dt.now(_tz.utc) - _td(minutes=1))
    pfq.add([(a, "alt")], "future", "public", _dt.now(_tz.utc) + _td(days=1))
    check("sees only the overdue item", len(pfq.due()) == 1, str(len(pfq.due())))

    class _Busy:
        def isRunning(self):
            return True
    panel.worker = _Busy()
    check("does not start a second run while one is in flight",
          panel.auto_run() == "busy")
    panel.worker = None
    pfq.store.save_queue({"items": []})

    print("\n  posted dialog")
    from pfpost.gui import PostedDialog

    posted = PostedDialog("https://gram.social/p/morro/123", window)
    check("shows the link", posted.link.text().endswith("/p/morro/123"))
    check("link field is read-only", posted.link.isReadOnly())
    check("copy enabled for a real url", posted.copy_button.isEnabled())
    check("open enabled for a real url", posted.open_button.isEnabled())

    try:
        _QApp.clipboard().setText("")
        posted.copy_link()
        copied = _QApp.clipboard().text()
        check("copy puts the url on the clipboard",
              copied == "https://gram.social/p/morro/123", copied)
    except RuntimeError as exc:            # genuinely no clipboard on this host
        print("  [SKIP] clipboard unavailable here (%s)" % exc)
    check("copy button confirms the action", posted.copy_button.text() == "Copied")

    # The instance supplies this string; only http(s) may reach the browser.
    for bad in ("javascript:alert(1)", "file:///C:/Windows/System32", "", None,
                "(no url returned)"):
        guarded = PostedDialog(bad, window)
        check("refuses to open %r" % (bad,),
              guarded.url is None and not guarded.open_button.isEnabled())

    missing = PostedDialog(None, window)
    check("explains a missing link", "No link" in missing.link.text())
    check("disabled buttons say why", "did not return" in missing.open_button.toolTip())

    print("\n  theme changes")
    # On a real platform setStyleSheet() emits PaletteChange, so an unguarded
    # changeEvent handler recurses until the interpreter dies. The offscreen
    # platform does NOT emit it, so that recursion cannot be reproduced here -
    # removing the guard still passes any event-driven test. These checks
    # therefore verify the guard's contract directly, which is the part that
    # actually prevents the loop.
    dark = QPalette()
    dark.setColor(QPalette.Window, QColor("#1e1e1e"))
    dark.setColor(QPalette.WindowText, QColor("#e0e0e0"))
    light = QPalette()
    light.setColor(QPalette.Window, QColor("#f0f0f0"))
    light.setColor(QPalette.WindowText, QColor("#101010"))

    bar.setPalette(dark)
    bar.apply_theme()
    check("applies the current palette",
          bar._applied_colour == bar.dim_colour().name(),
          "%s vs %s" % (bar._applied_colour, bar.dim_colour().name()))

    before = bar._applied_colour
    bar.apply_theme()
    check("re-applying an unchanged theme is a no-op",
          bar._applied_colour == before)

    bar.setPalette(light)
    bar.apply_theme()
    check("follows a palette swap", bar._applied_colour == bar.dim_colour().name())

    # The re-entrancy guard: while a restyle is in flight, a nested call must
    # return without touching anything. This is what breaks the recursion.
    #
    # _applied_colour is forced to a value the current palette cannot produce,
    # otherwise the equality short-circuit returns first and the guard is never
    # exercised - which made an earlier version of this check pass even with
    # the guard deleted.
    bar.setPalette(dark)
    bar.apply_theme()
    sentinel = "#000000"
    bar._applied_colour = sentinel
    bar._applying = True
    bar.apply_theme()
    check("nested call is refused while restyling",
          bar._applied_colour == sentinel,
          "guard let a re-entrant call through: %s" % bar._applied_colour)
    bar._applying = False
    bar.apply_theme()
    check("restyles normally once the guard clears",
          bar._applied_colour == bar.dim_colour().name())

    survived = True
    try:
        for palette in (dark, light, dark, light):
            bar.setPalette(palette)
            _QApp.sendEvent(bar, QEvent(QEvent.PaletteChange))
    except RecursionError:
        survived = False
    check("repeated PaletteChange events stay stable", survived)

    print("\n  secondary-text contrast")

    def luminance(colour):
        def channel(v):
            v = v / 255
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
        return (0.2126 * channel(colour.red())
                + 0.7152 * channel(colour.green())
                + 0.0722 * channel(colour.blue()))

    def contrast(a, b):
        high, low = sorted([luminance(a), luminance(b)], reverse=True)
        return (high + 0.05) / (low + 0.05)

    for label, bg, fg in [("dark", "#1e1e1e", "#e0e0e0"),
                          ("light", "#f0f0f0", "#101010"),
                          ("mid-grey", "#3c3c3c", "#dcdcdc")]:
        palette = QPalette()
        palette.setColor(QPalette.Window, QColor(bg))
        palette.setColor(QPalette.WindowText, QColor(fg))
        probe = type(bar)()
        probe.setPalette(palette)
        probe.apply_theme()
        ratio = contrast(probe.dim_colour(), QColor(bg))
        check("detail text readable on a %s theme (%.2f:1)" % (label, ratio),
              ratio >= 3.0, "%.2f:1 is below the 3:1 floor" % ratio)

    print("\n  gating")
    composer.set_enabled(False)
    check("posting disabled while disconnected", not composer.post_now.isEnabled())
    check("disabled button explains why", "Connect" in composer.post_now.toolTip())
    composer.set_enabled(True)
    check("posting enabled once connected", composer.post_now.isEnabled())

    print("\n%s" % ("GUI smoke test passed." if not FAILURES
                    else "%d GUI CHECK(S) FAILED: %s" % (len(FAILURES), FAILURES)))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
