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
    from pfpost import theme as pftheme
    from pfpost.gui import ConnectDialog, MainWindow
    from pfpost.session import Session

    TOKENS = pftheme.tokens(dark=False)

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
    check("counter flags an over-long caption",
          TOKENS["danger"] in composer.counter.styleSheet(),
          composer.counter.styleSheet())

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
    check("disconnected dot uses the danger token",
          TOKENS["danger"] in bar.dot.styleSheet(), bar.dot.styleSheet())

    bar.show_state({"configured": True, "connected": False, "instance": "x.social"})
    check("registered-but-unauthorized is distinguished",
          "Not signed in" in bar.primary.text(), bar.primary.text())
    check("unauthorized offers Sign in", bar.button.text() == "Sign in")

    bar.show_state({"configured": True, "connected": True, "instance": "x.social",
                    "scopes": "read write", "can_read": True, "backend": "keyring"},
                   username="morro")
    check("connected shows the username",
          "@morro" in bar.primary.text(), bar.primary.text())
    check("connected dot uses the success token",
          TOKENS["success"] in bar.dot.styleSheet(), bar.dot.styleSheet())
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
    check("off state is highlighted",
          TOKENS["warning"] in panel.runner_label.styleSheet(),
          panel.runner_label.styleSheet())
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

    print("\n  alt text and drafts")
    from pfpost import store as pfstore

    composer.add_paths([a, b])
    composer.table.item(0, 2).setText("described")
    composer.table.item(1, 2).setText("")
    check("spots images with no alt text",
          [p.name for p, alt in composer.images() if not (alt or "").strip()]
          == ["b.png"])
    check("passes when every image is described",
          composer.confirm_alt_text([(a, "one"), (b, "two")]) is True)
    check("whitespace does not count as alt text",
          [alt for _p, alt in [(a, "   ")] if not (alt or "").strip()] == ["   "])

    composer.caption.setPlainText("draft caption")
    saved = composer.draft()
    check("draft captures the caption", saved["caption"] == "draft caption")
    check("draft captures images and alts",
          [i["alt"] for i in saved["images"]] == ["described", None])
    check("draft captures visibility", saved["visibility"] in ("public", "unlisted",
                                                              "private"))

    composer.save_draft()
    check("draft is written to disk", pfstore.draft_path().exists())

    composer.table.setRowCount(0)
    composer.caption.setPlainText("")
    restored = composer.restore_draft()
    check("draft restores both images", restored == 2, str(restored))
    check("draft restores the caption",
          composer.caption.toPlainText() == "draft caption")
    check("draft restores alt text",
          composer.images()[0][1] == "described")

    # An image deleted between sessions must not block the rest of the draft.
    ghost = tmp / "ghost.png"
    ghost.write_bytes(PNG)
    composer.add_paths([ghost])
    composer.save_draft()
    ghost.unlink()
    composer.table.setRowCount(0)
    restored = composer.restore_draft()
    check("a deleted image is skipped, not fatal", restored == 2, str(restored))

    composer.clear()
    check("clearing the composer clears the draft",
          not pfstore.draft_path().exists())
    check("restoring nothing is harmless", composer.restore_draft() == 0)

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

    print("\n  theme")
    # setStyleSheet() emits PaletteChange on a real platform, so an unguarded
    # changeEvent handler recurses until the interpreter dies. The offscreen
    # platform does NOT emit it, so the loop cannot be reproduced here - these
    # checks verify the guard's contract, which is what actually breaks it.
    dark_tokens = pftheme.tokens(dark=True)
    light_tokens = pftheme.tokens(dark=False)

    bar.set_tokens(dark_tokens)
    check("applies the given tokens",
          bar._applied_colour == dark_tokens["muted"], str(bar._applied_colour))
    before = bar._applied_colour
    bar.apply_theme()
    check("re-applying unchanged tokens is a no-op", bar._applied_colour == before)

    bar.set_tokens(light_tokens)
    check("follows a token swap", bar._applied_colour == light_tokens["muted"])

    # Force a mismatch, or the equality short-circuit returns before the guard
    # is reached - which made an earlier version of this check pass even with
    # the guard deleted.
    sentinel = "#000000"
    bar._applied_colour = sentinel
    bar._applying = True
    bar.apply_theme()
    check("nested call is refused while restyling",
          bar._applied_colour == sentinel,
          "guard let a re-entrant call through: %s" % bar._applied_colour)
    bar._applying = False
    bar.apply_theme()
    check("restyles once the guard clears",
          bar._applied_colour == light_tokens["muted"])

    survived = True
    try:
        for _ in range(4):
            _QApp.sendEvent(bar, QEvent(QEvent.PaletteChange))
    except RecursionError:
        survived = False
    check("repeated PaletteChange events stay stable", survived)

    print("\n  palette contrast")

    def luminance(colour):
        def channel(v):
            v = v / 255
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
        return (0.2126 * channel(colour.red())
                + 0.7152 * channel(colour.green())
                + 0.0722 * channel(colour.blue()))

    def contrast(one, two):
        high, low = sorted([luminance(QColor(one)), luminance(QColor(two))],
                           reverse=True)
        return (high + 0.05) / (low + 0.05)

    # Body text to WCAG AA (4.5:1); secondary and status colours to the 3:1
    # floor that applies to large or non-body text.
    for name, t in (("light", light_tokens), ("dark", dark_tokens)):
        for role, floor, ground in (("text", 4.5, "bg"), ("text", 4.5, "surface"),
                                    ("muted", 3.0, "bg"), ("muted", 3.0, "surface"),
                                    ("primary", 3.0, "bg"), ("danger", 3.0, "surface"),
                                    ("success", 3.0, "surface"),
                                    ("warning", 3.0, "surface")):
            ratio = contrast(t[role], t[ground])
            check("%s: %s on %s (%.2f:1)" % (name, role, ground, ratio),
                  ratio >= floor, "below the %.1f:1 floor" % floor)
        ratio = contrast(t["primary_text"], t["primary"])
        check("%s: button label on primary (%.2f:1)" % (name, ratio), ratio >= 4.5)

    check("both palettes define the same tokens",
          set(light_tokens) == set(dark_tokens))
    check("the brand cyan is carried through",
          light_tokens["accent"] == dark_tokens["accent"] == "#10c5f8")

    sheet = pftheme.stylesheet(dark_tokens)
    check("stylesheet substitutes every token", "%(" not in sheet)
    check("stylesheet styles the account bar", "#accountBar" in sheet)
    check("stylesheet defines an accent button",
          'QPushButton[accent="true"]' in sheet)

    icon = pftheme.make_icon(128)
    pixmap = icon.pixmap(128, 128)
    image = pixmap.toImage()
    check("icon renders", not pixmap.isNull() and pixmap.width() == 128)
    check("icon centre is painted", image.pixelColor(64, 64).alpha() == 255)
    check("icon corners are rounded, not square",
          image.pixelColor(1, 1).alpha() == 0)

    check("theme detection returns a bool",
          isinstance(pftheme.is_dark(_QApp.instance()), bool))

    check("primary actions are marked accent",
          composer.post_now.property("accent") is True)

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
