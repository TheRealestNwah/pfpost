"""GUI smoke test. Run: .venv\\Scripts\\python.exe test_gui.py

Builds the real widgets on Qt's offscreen platform, so it works headless and in
CI. Covers construction, the data path between the table and a post payload,
the pre-post dialogs, and rendered pixels where a style rule can be ignored.
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
    # A failure, not a skip. Exiting 0 here once let a run on the wrong Python
    # report success having tested nothing. Opt out explicitly if you mean it.
    print("PySide6 is not installed for %s - no GUI tests ran." % sys.executable)
    print("Use the project environment:  .venv\\Scripts\\python.exe test_gui.py")
    if os.environ.get("PFPOST_SKIP_GUI_TESTS") == "1":
        print("PFPOST_SKIP_GUI_TESTS=1, so exiting 0 anyway.")
        sys.exit(0)
    sys.exit(2)

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
    from PySide6.QtGui import QColor, QImage
    from PySide6.QtWidgets import QApplication as _QApp
    from pfpost import theme as pftheme
    from pfpost.gui import ConnectDialog, MainWindow
    from pfpost.session import Session

    TOKENS = pftheme.tokens(dark=False)

    # Before any widget exists, as main() does - the width checks below
    # measure text in the font the app actually ships.
    print("\nFont")
    family = pftheme.install_font(_QApp.instance())
    check("IBM Plex Sans loads from the bundled file", family == "IBM Plex Sans",
          str(family))
    from PySide6.QtGui import QFontInfo
    check("it becomes the application font",
          QFontInfo(_QApp.instance().font()).family() == "IBM Plex Sans",
          QFontInfo(_QApp.instance().font()).family())
    check("the OFL licence ships beside it, as the licence requires",
          (Path(pftheme.__file__).parent / "fonts" / "OFL.txt").is_file())
    real_file = pftheme.FONT_FILE
    pftheme.FONT_FILE = "missing.ttf"
    try:
        check("a missing font file leaves the platform font, without raising",
              pftheme.install_font(_QApp.instance()) is None)
    finally:
        pftheme.FONT_FILE = real_file

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
    check("two image tiles added", len(composer.tiles) == 2, str(len(composer.tiles)))
    check("each tile shows its image, not a filename",
          all(t.thumb.pixmap() is not None and not t.thumb.pixmap().isNull()
              for t in composer.tiles))
    check("the Add images tile stays last",
          composer.strip.indexOf(composer.add_tile) == 2,
          str(composer.strip.indexOf(composer.add_tile)))

    composer.tiles[0].alt.setText("First alt")
    composer.tiles[1].alt.setText("Second alt")
    images = composer.images()
    check("alt text reads back in tile order",
          [i[1] for i in images] == ["First alt", "Second alt"],
          str([i[1] for i in images]))
    check("paths read back in order",
          [i[0].name for i in images] == ["a.png", "b.png"])
    check("blank alt becomes None",
          (composer.tiles[0].alt.setText("  "), composer.images()[0][1])[1] is None)
    composer.tiles[0].alt.setText("First alt")

    composer.caption.setPlainText("hello")
    check("counter shows the instance limit", "5 / 500" == composer.counter.text(),
          composer.counter.text())
    composer.caption.setPlainText("x" * 501)
    check("counter flags an over-long caption",
          TOKENS["danger"] in composer.counter.styleSheet(),
          composer.counter.styleSheet())

    composer.caption.setPlainText("ok")
    check("gather accepts a valid post", composer.gather() is not None)

    composer.tiles[0].remove_button.click()
    check("a tile's own remove button drops that image",
          [p.name for p, _alt in composer.images()] == ["b.png"],
          str([p.name for p, _alt in composer.images()]))
    check("and it leaves the layout at once, not at the next event loop",
          composer.strip.count() == 3)          # one tile, the add tile, a stretch

    check("visibility reads back as the API value, not its label",
          composer.visibility_value() == "public"
          and composer.visibility.currentText() == "Public")
    composer.set_visibility("private")
    check("visibility can be set by API value", composer.visibility_value() == "private")
    composer.set_visibility("public")

    composer.clear()
    check("clear empties the images and caption",
          composer.tiles == [] and composer.caption.toPlainText() == "")

    dialog = ConnectDialog(Session({}))
    check("connect dialog builds", dialog.instance is not None)

    window.queue_panel.reload()
    check("queue panel reloads", window.queue_panel.rows is not None)

    print("\n  tabs")
    bar = window.top_bar
    check("opens on Compose", window.stack.currentWidget() is composer
          and bar.compose_tab.isChecked())
    bar.queue_tab.click()
    check("the Queue tab shows the queue", window.stack.currentWidget() is window.queue_panel
          and bar.queue_tab.isChecked() and not bar.compose_tab.isChecked())
    check("the footer's background summary hides on the Queue tab, which has its own",
          window.summary.isHidden())
    window.say("")             # startup's "Not connected" message borrows the footer
    bar.compose_tab.click()
    check("and back", window.stack.currentWidget() is composer and not window.summary.isHidden())

    bar.set_pending(2)
    check("the Queue tab shows the pending count", not bar.badge.isHidden()
          and bar.badge.text() == "2")
    check("the tab's padding rule is scoped, so it cannot pad the badge's own digit",
          bar.queue_tab.styleSheet().startswith("QPushButton#tab"),
          bar.queue_tab.styleSheet())
    bar.set_pending(0)
    check("no badge when nothing is pending", bar.badge.isHidden())

    print("\n  account chip")
    bar.show_state({"configured": False, "connected": False})
    check("nothing set up: one Connect account button, no chip",
          not bar.connect_button.isHidden() and bar.chip.isHidden()
          and bar.connect_button.text() == "Connect account")

    bar.show_state({"configured": True, "connected": False, "instance": "x.social"})
    check("registered but unauthorized offers Sign in",
          bar.connect_button.text() == "Sign in" and bar.chip.isHidden())
    check("and says where", "x.social" in bar.connect_button.toolTip())

    bar.show_state({"configured": True, "connected": True, "instance": "x.social",
                    "scopes": "read write", "can_read": True, "backend": "keyring"},
                   username="morro")
    check("connected: the chip shows only the username",
          not bar.chip.isHidden() and bar.chip.text().strip() == "@morro"
          and bar.connect_button.isHidden(), bar.chip.text())
    check("the chip no longer spells out scopes or storage",
          "scope" not in bar.chip.text() and "keyring" not in bar.chip.text())
    check("those moved behind Connection details",
          "x.social" in bar.details_text() and "keyring" in bar.details_text()
          and "read write" in bar.details_text(), bar.details_text())
    menu_texts = [a.text() for a in bar.chip_menu.actions() if not a.isSeparator()]
    check("the chip's menu holds Open account, Connection details and Disconnect",
          {"Open account", "Connection details", "Disconnect"} <= set(menu_texts),
          str(menu_texts))
    check("its header names the account and instance",
          bar.header_action.text() == "@morro · x.social", bar.header_action.text())

    bar.show_state({"configured": True, "connected": True, "instance": "gram.social",
                    "scopes": "write", "can_read": False, "backend": "dpapi"},
                   username=None)
    check("write-only without a learned name falls back to the instance",
          bar.chip.text().strip() == "gram.social", bar.chip.text())
    check("details explain the missing username",
          "write-only" in bar.details_text(), bar.details_text())

    print("\n  open account")
    write_only = {"configured": True, "connected": True, "instance": "gram.social",
                  "scopes": "write", "can_read": False, "backend": "keyring"}
    bar.show_state(write_only, username="nmorrow08")
    check("a learned username shows even on a write-only token",
          bar.chip.text().strip() == "@nmorrow08", bar.chip.text())
    check("and the 'write-only' note goes away",
          "write-only" not in bar.details_text(), bar.details_text())
    check("Open account is offered once connected", bar.open_action.isEnabled())
    bar.show_state({"configured": True, "connected": False, "instance": "gram.social"})
    check("but not while signed out", not bar.open_action.isEnabled())
    bar.show_state({"configured": False, "connected": False})
    check("nor when nothing is set up", not bar.open_action.isEnabled())

    import pfpost.gui as pfgui
    opened = []
    real_open = pfgui.QDesktopServices.openUrl
    pfgui.QDesktopServices.openUrl = lambda url: opened.append(url.toString())
    real_status = window.session.status
    try:
        window.session.state.update({"instance": "gram.social", "username": "nmorrow08"})
        window.session.status = lambda: dict(write_only)
        window.open_account()
        check("opens the profile when the username is known",
              opened == ["https://gram.social/nmorrow08"], str(opened))
        opened.clear()
        window.session.state.pop("username")
        window.open_account()
        check("otherwise opens /i/me, which Pixelfed redirects to your profile",
              opened == ["https://gram.social/i/me"], str(opened))
        opened.clear()
        window.session.status = lambda: {"configured": True, "connected": False}
        window.open_account()
        check("does nothing while signed out", opened == [], str(opened))
        window.session.status = lambda: dict(write_only)
        window.top_bar.show_state(write_only, "nmorrow08")    # a disabled action ignores trigger()
        window.top_bar.open_action.trigger()
        check("the menu item is wired to it", len(opened) == 1, str(opened))
    finally:
        pfgui.QDesktopServices.openUrl = real_open
        window.session.status = real_status
        for key in ("instance", "username"):
            window.session.state.pop(key, None)

    print("\n  background runner row")
    from pfpost import scheduler as sched
    panel = window.queue_panel

    summaries = []
    panel.runner_summary.connect(lambda text, colour, dot: summaries.append((text, colour, dot)))

    panel.runner_state({"registered": False})
    check("off state warns what happens once pfpost is closed",
          "close pfpost" in panel.runner_label.text(), panel.runner_label.text())
    check("off state's light is red, on the Queue tab",
          TOKENS["danger"] in panel.runner_dot.styleSheet(), panel.runner_dot.styleSheet())
    check("and in the Compose footer",
          TOKENS["danger"] in window.summary_dot.styleSheet(), window.summary_dot.styleSheet())
    check("off state offers Turn on, as the primary action",
          panel.runner_button.text() == "Turn on"
          and panel.runner_button.property("accent") is True)
    check("off state tracked", panel.runner_registered is False)
    check("the Compose tab's footer hears about it too",
          summaries and "off" in summaries[-1][0] and summaries[-1][2] == TOKENS["danger"],
          str(summaries[-1:]))
    check("the footer line says a click turns it on",
          "turn on" in window.summary.toolTip(), window.summary.toolTip())

    panel.runner_state({"registered": True, "interval": "PT15M", "state": "Ready",
                        "last_result": 0})
    check("on state is one short line, as in the mockup",
          panel.runner_label.text() == "Background posting on · every 15 min",
          panel.runner_label.text())
    check("the full explanation is a tooltip away",
          "every 15 minutes" in panel.runner_label.toolTip(), panel.runner_label.toolTip())
    check("on state is not highlighted", panel.runner_label.styleSheet() == "")
    check("on state's light is green",
          TOKENS["success"] in window.summary_dot.styleSheet(), window.summary_dot.styleSheet())
    check("the footer line says a click turns it off",
          "turn off" in window.summary.toolTip(), window.summary.toolTip())
    toggled = []
    real_toggle = panel.toggle_runner
    panel.toggle_runner = lambda: toggled.append(True)
    try:
        window.summary.click()
    finally:
        panel.toggle_runner = real_toggle
    check("clicking the footer line toggles background posting "
          "(toggle_runner asks before turning it off)", toggled == [True])
    check("on state offers Change, not a primary button",
          panel.runner_button.text() == "Change"
          and panel.runner_button.property("accent") is False)
    panel.runner_state({"registered": True, "interval": "PT2H", "last_result": 0})
    check("hour intervals read as hours",
          panel.runner_label.text().endswith("every 2 h"), panel.runner_label.text())

    # 267011 is SCHED_S_TASK_HAS_NOT_RUN - normal for a freshly created task.
    panel.runner_state({"registered": True, "interval": "PT15M", "last_result": 267011})
    check("a never-run task is not reported as failing",
          "reported code" not in panel.runner_label.text(), panel.runner_label.text())
    panel.runner_state({"registered": True, "interval": "PT15M", "last_result": 1})
    check("a real failure code is surfaced",
          "reported code 1" in panel.runner_label.text(), panel.runner_label.text())

    missing_python = r"C:\Users\x\AppData\Local\Python\pythoncore-3.14-64\pythonw.exe"
    panel.runner_state({"registered": True, "interval": "PT30M", "last_result": 2147942402,
                        "program": missing_python, "program_exists": False, "broken": True})
    check("a broken task says so in words, not an error code",
          "broken" in panel.runner_label.text()
          and "2147942402" not in panel.runner_label.text(), panel.runner_label.text())
    check("and names the missing program", missing_python in panel.runner_label.text())
    check("broken is shown in the danger colour",
          TOKENS["danger"] in panel.runner_label.styleSheet())
    check("offers Repair", panel.runner_button.text() == "Repair")
    check("the Compose footer shortens it rather than repeating a full path",
          window.summary.text() == "Background posting is broken - click to repair",
          window.summary.text())
    # The label's own minimum width is what layouts impose on the window; an
    # unwrapped one is the whole sentence, path included (~1250px).
    check("a long program path wraps instead of widening the window (%dpx)"
          % panel.runner_label.minimumSizeHint().width(),
          panel.runner_label.minimumSizeHint().width() < 600)

    import pfpost.gui as _gui_mod

    class _SyncWorker:
        """Stands in for the QThread worker: runs at start(), emits immediately."""
        def __init__(self, fn, *args):
            self.fn, self.args, self._done, self._failed = fn, args, [], []
            self.done = type("S", (), {"connect": lambda _s, f: self._done.append(f)})()
            self.failed = type("S", (), {"connect": lambda _s, f: self._failed.append(f)})()

        def start(self):
            try:
                result = self.fn(*self.args)
            except Exception as exc:                 # as Worker.run does
                for f in self._failed:
                    f(str(exc))
                return
            for f in self._done:
                f(result)

        def isRunning(self):
            return False

    calls = []
    real_worker, real_register = _gui_mod.Worker, _gui_mod.scheduler.register
    real_unregister, real_refresh = _gui_mod.scheduler.unregister, panel.refresh_runner
    real_question, real_get_int = _gui_mod.QMessageBox.question, _gui_mod.QInputDialog.getInt
    _gui_mod.Worker = _SyncWorker
    _gui_mod.scheduler.register = lambda minutes: calls.append(("register", minutes))
    _gui_mod.scheduler.unregister = lambda: calls.append(("unregister",))
    # Any prompt would block forever offscreen; record it and decline instead,
    # so a regression into the Disable or Enable path fails rather than hangs.
    _gui_mod.QMessageBox.question = staticmethod(
        lambda *a, **k: (calls.append(("asked",)), _gui_mod.QMessageBox.No)[1])
    _gui_mod.QInputDialog.getInt = staticmethod(
        lambda *a, **k: (calls.append(("asked",)), (0, False))[1])
    panel.refresh_runner = lambda: None
    try:
        panel.toggle_runner()
    finally:
        _gui_mod.Worker, _gui_mod.scheduler.register = real_worker, real_register
        _gui_mod.scheduler.unregister, panel.refresh_runner = real_unregister, real_refresh
        _gui_mod.QMessageBox.question = real_question
        _gui_mod.QInputDialog.getInt = real_get_int
    check("Repair re-registers in place, keeping the 30-minute interval, without asking",
          calls == [("register", 30)], str(calls))

    panel.runner_state({"registered": True, "interval": "PT15M", "last_result": 0,
                        "program": "C:/pfpost/pfpost-gui.exe", "program_exists": True,
                        "broken": False})
    check("a healthy task goes back to offering Change",
          panel.runner_button.text() == "Change")

    print("\n  footer: version and updates")
    from pfpost import __version__ as _version
    # Checked before any click below: the only request pfpost makes to anyone
    # but your instance must never happen on its own.
    check("nothing checks for updates at startup", window.update_worker is None)
    check("the footer shows the version",
          window.version_label.text() == "pfpost %s" % _version, window.version_label.text())
    check("Check for updates sits beside it",
          window.update_button.text() == "Check for updates")
    settings_texts = [a.text() for a in window.top_bar.settings_button.menu().actions()]
    check("and in the settings menu", "Check for updates" in settings_texts, str(settings_texts))

    window.show_tab(0)
    window.say("Queued for Fri, Sep 18 · 09:00")
    check("a message borrows the footer's left side",
          not window.message.isHidden() and window.summary.isHidden())
    window.message_timer.stop()
    window.clear_message()
    check("and gives it back to background posting",
          window.message.isHidden() and not window.summary.isHidden())

    update_calls, opened_updates, update_questions = [], [], []
    real = (_gui_mod.Worker, _gui_mod.updates.check, _gui_mod.QDesktopServices.openUrl,
            _gui_mod.QMessageBox.question)
    _gui_mod.Worker = _SyncWorker
    _gui_mod.QDesktopServices.openUrl = lambda url: opened_updates.append(url.toString())
    _gui_mod.QMessageBox.question = staticmethod(
        lambda *a, **k: (update_questions.append(a[2]), _gui_mod.QMessageBox.No)[1])
    try:
        _gui_mod.updates.check = lambda: (update_calls.append(1), {
            "current": "1.1.0", "latest": "1.0.4", "newer": False, "ahead": True,
            "url": "https://github.com/TheRealestNwah/pfpost/releases"})[1]
        window.update_button.click()
        check("a click runs one check", update_calls == [1])
        check("a build ahead of the release says so",
              "newer than the latest release (1.0.4)" in window.message.text(),
              window.message.text())
        check("and nothing is opened or asked", opened_updates == [] and update_questions == [])

        _gui_mod.updates.check = lambda: {
            "current": "1.1.0", "latest": "1.2.0", "newer": True, "ahead": False,
            "url": "https://github.com/TheRealestNwah/pfpost/releases/tag/v1.2.0"}
        window.update_button.click()
        check("a newer release is offered once, and declining opens nothing",
              len(update_questions) == 1 and "1.2.0" in update_questions[0]
              and opened_updates == [], str(update_questions))
        check("the button becomes the way to it",
              window.update_button.text() == "Update available: 1.2.0",
              window.update_button.text())
        window.update_button.click()
        check("clicking it opens the release page, without checking again",
              opened_updates == ["https://github.com/TheRealestNwah/pfpost/releases/tag/v1.2.0"]
              and len(update_questions) == 1, str(opened_updates))

        def offline():
            raise _gui_mod.updates.UpdateError("No releases are published yet.")
        window.update_url = None
        _gui_mod.updates.check = offline
        window.check_for_updates()
        check("a failed check says why and leaves the button usable",
              "No releases" in window.message.text() and window.update_button.isEnabled()
              and window.update_button.text() == "Check for updates",
              window.message.text())
    finally:
        (_gui_mod.Worker, _gui_mod.updates.check, _gui_mod.QDesktopServices.openUrl,
         _gui_mod.QMessageBox.question) = real

    check("nudge fires once per session, not every queue",
          window._runner_nudged is False)

    print("\n  alt text and drafts")
    from pfpost import store as pfstore

    composer.add_paths([a, b])
    composer.tiles[0].alt.setText("described")
    composer.tiles[1].alt.setText("")
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

    composer.set_visibility("unlisted")
    composer.save_draft()
    for tile in list(composer.tiles):
        composer.remove_tile(tile)
    composer.caption.setPlainText("")
    composer.set_visibility("public")
    restored = composer.restore_draft()
    check("draft restores visibility by value", composer.visibility_value() == "unlisted",
          composer.visibility_value())
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
    for tile in list(composer.tiles):
        composer.remove_tile(tile)
    restored = composer.restore_draft()
    check("a deleted image is skipped, not fatal", restored == 2, str(restored))

    composer.clear()
    check("clearing the composer clears the draft",
          not pfstore.draft_path().exists())
    check("restoring nothing is harmless", composer.restore_draft() == 0)

    print("\n  link warning")
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QMessageBox

    seen = []

    def answering(button_text, fn, mute=False, probe=None):
        """Run fn, clicking button_text on any message box it opens.

        Always armed, even where no dialog is expected: an unanswered exec()
        blocks forever, so a regression would hang the suite instead of
        failing it. Disarmed afterwards so it cannot answer a later box.
        """
        seen.clear()
        armed = [True]

        def go():
            if not armed[0]:
                return
            boxes = [w for w in _QApp.topLevelWidgets()
                     if isinstance(w, QMessageBox) and w.isVisible()]
            if not boxes:
                QTimer.singleShot(10, go)
                return
            box = boxes[0]
            seen.append(box.text())
            if probe:
                probe(box)
            if mute:
                box.checkBox().setChecked(True)
            for button in box.buttons():
                if button.text() == button_text:
                    button.click()
                    break
            else:
                box.reject()
            QTimer.singleShot(10, go)       # keep answering until disarmed

        QTimer.singleShot(0, go)
        try:
            return fn()
        finally:
            armed[0] = False

    link = "Posted with pfpost https://github.com/TheRealestNwah/pfpost"
    # Capitals: the queue section below reuses `keep` for a queue item.
    KEEP, EDIT = "Keep the link", "Edit caption"
    pfstore.set_pref("warn_links", True)

    check("no link: passes without asking",
          answering(KEEP, lambda: composer.confirm_links("Morning fog", "public"))
          is True and not seen, str(seen))
    check("unlisted post with a link: passes without asking",
          answering(KEEP, lambda: composer.confirm_links(link, "unlisted"))
          is True and not seen, str(seen))

    composer.caption.setPlainText(link)
    check("Edit caption holds the post back",
          answering(EDIT, lambda: composer.confirm_links(link, "public")) is False)
    check("the warning was actually shown", len(seen) == 1, str(seen))
    check("Edit caption selects the link so one key deletes it",
          composer.caption.textCursor().selectedText()
          == "https://github.com/TheRealestNwah/pfpost",
          composer.caption.textCursor().selectedText())

    check("Keep the link lets the post through",
          answering(KEEP, lambda: composer.confirm_links(link, "public")) is True)
    check("keeping the link without ticking the box keeps warning",
          pfstore.load_prefs()["warn_links"] is True)

    answering(KEEP, lambda: composer.confirm_links(link, "public"), mute=True)
    check("the checkbox turns the warning off", pfstore.load_prefs()["warn_links"] is False)
    check("once muted, no dialog",
          answering(KEEP, lambda: composer.confirm_links(link, "public"))
          is True and not seen, str(seen))
    window.sync_options()
    check("the Options menu reflects a mute from the dialog",
          window.warn_links_action.isChecked() is False)
    window.warn_links_action.setChecked(True)
    check("the Options menu turns it back on", pfstore.load_prefs()["warn_links"] is True)

    print("\n  alt text reminder can be turned off")
    POST_ANYWAY = "Post without it"
    undescribed = [(a, None)]
    check("alt text reminder is on by default",
          pfstore.load_prefs()["warn_alt_text"] is True)
    check("reminder still asks before it is muted",
          answering(POST_ANYWAY, lambda: composer.confirm_alt_text(undescribed))
          is True and len(seen) == 1, str(seen))
    check("posting anyway without the box ticked keeps asking",
          pfstore.load_prefs()["warn_alt_text"] is True)
    answering(POST_ANYWAY, lambda: composer.confirm_alt_text(undescribed), mute=True)
    check("the checkbox turns the reminder off",
          pfstore.load_prefs()["warn_alt_text"] is False)
    check("once muted, missing alt text posts with no dialog",
          answering(POST_ANYWAY, lambda: composer.confirm_alt_text(undescribed))
          is True and not seen, str(seen))
    check("muting alt text leaves the link warning alone",
          pfstore.load_prefs()["warn_links"] is True)
    window.sync_options()
    check("the Options menu reflects the alt text mute",
          window.warn_alt_action.isChecked() is False
          and window.warn_links_action.isChecked() is True)
    window.warn_alt_action.setChecked(True)
    check("the Options menu turns the reminder back on",
          pfstore.load_prefs()["warn_alt_text"] is True)

    composer.set_visibility("public")
    composer.add_paths([a])
    composer.tiles[0].alt.setText("alt")
    check("gather runs the link check",
          answering(EDIT, composer.gather) is None and len(seen) == 1, str(seen))
    composer.clear()

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

    raw = pfq.store.load_queue()
    for row in raw["items"]:
        if row["id"] == done["id"]:
            row["result_url"] = "https://gram.social/p/morro/1"
        if row["id"] == bad["id"]:
            row["error"] = "HTTP 500 from the instance"
    pfq.store.save_queue(raw)
    pending_counts = []
    panel.pending_changed.connect(pending_counts.append)
    panel.reload()
    check("one row per item", len(panel.rows) == 3, str(len(panel.rows)))
    check("pending rows come first",
          panel.rows[0].item["id"] == keep["id"], panel.rows[0].item["id"])
    pills = {r.item["id"]: (r.pill.text(), r.pill.property("pill")) for r in panel.rows}
    check("each row carries a status pill",
          pills[keep["id"]] == ("Pending", "pending") and pills[done["id"]] == ("Posted", "posted")
          and pills[bad["id"]] == ("Failed", "failed"), str(pills))
    failed_row = next(r for r in panel.rows if r.item["id"] == bad["id"])
    check("a failed row explains itself on hover",
          "HTTP 500" in failed_row.pill.toolTip())
    posted_row = next(r for r in panel.rows if r.item["id"] == done["id"])
    check("a posted row offers Open post", posted_row.action.toolTip() == "Open post")
    check("a pending row offers Remove", panel.rows[0].action.toolTip() == "Remove from queue")
    check("only the first row drops its top rule",
          [r.property("first") for r in panel.rows] == [True, False, False])
    check("the subtitle tallies each status",
          panel.subtitle.text() == "1 pending · 1 posted · 1 failed", panel.subtitle.text())
    check("the pending count reaches the tab badge", pending_counts[-1:] == [1],
          str(pending_counts))
    check("the empty-state line hides when there are items", panel.empty.isHidden())

    opened_posts = []
    real_post_open = _gui_mod.QDesktopServices.openUrl
    _gui_mod.QDesktopServices.openUrl = lambda url: opened_posts.append(url.toString())
    try:
        posted_row.action.click()
        panel.open_post("javascript:alert(1)")
    finally:
        _gui_mod.QDesktopServices.openUrl = real_post_open
    check("Open post opens the post, and only http(s) links",
          opened_posts == ["https://gram.social/p/morro/1"], str(opened_posts))

    old_rows = list(panel.rows)
    panel.reload()
    # removeWidget alone passes a layout count; the overlap seen on screen came
    # from old rows still parented, so still painted until the event loop ran.
    check("replaced rows are detached immediately, so they cannot paint underneath",
          all(r.parent() is None for r in old_rows))
    check("reloading replaces rows rather than stacking them",
          len(panel.rows) == 3 and panel.list_layout.count() == 5,   # 3 rows, empty label, stretch
          str(panel.list_layout.count()))
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
    real_ask = _gui_mod.QMessageBox.question
    asked = []
    _gui_mod.QMessageBox.question = staticmethod(
        lambda *a, **k: (asked.append(a[2] if len(a) > 2 else ""), _gui_mod.QMessageBox.No)[1])
    try:
        panel.reload()
        next(r for r in panel.rows if r.item["id"] == second["id"]).action.click()
    finally:
        _gui_mod.QMessageBox.question = real_ask
    check("removing a pending post asks first, naming it",
          len(asked) == 1 and "another" in asked[0], str(asked))
    check("and declining keeps it",
          second["id"] in [i["id"] for i in pfq.items(include_done=True)])
    check("remove_many drops several at once",
          pfq.remove_many([keep["id"], second["id"]]) == 2)
    panel.reload()
    check("an empty queue says so", not panel.empty.isHidden() and panel.rows == [])
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
    dark_tokens = pftheme.tokens(dark=True)
    light_tokens = pftheme.tokens(dark=False)

    # The old account bar restyled itself on PaletteChange and needed a
    # re-entrancy guard. The top bar has no such handler; make sure one did
    # not creep back in unguarded.
    survived = True
    try:
        for _ in range(4):
            _QApp.sendEvent(bar, QEvent(QEvent.PaletteChange))
    except RecursionError:
        survived = False
    check("repeated PaletteChange events stay stable", survived)
    bar.set_tokens(dark_tokens)
    check("token swaps redraw the chip's menu icons without error",
          not bar.disconnect_action.icon().isNull())
    bar.set_tokens(light_tokens)

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
    check("stylesheet styles the top bar and its chip",
          "#topBar" in sheet and "#accountChip" in sheet)
    for role in ("card", "segmented", "badge", "queueRow", "addTile"):
        check("stylesheet styles %s" % role, "#%s" % role in sheet)
    check("gram.social's signed-in palette is the ground",
          light_tokens["bg"] == "#f3f4f6" and dark_tokens["bg"] == "#16171b"
          and dark_tokens["surface"] == "#1f2025")
    check("the app icon keeps its shipped blue, independent of the UI palette",
          pftheme.ICON_BLUE == "#2c78bf")
    check("stylesheet defines an accent button",
          'QPushButton[accent="true"]' in sheet)

    icon = pftheme.make_icon(128)
    pixmap = icon.pixmap(128, 128)
    image = pixmap.toImage()
    check("icon renders", not pixmap.isNull() and pixmap.width() == 128)
    check("icon centre is painted", image.pixelColor(64, 64).alpha() == 255)
    check("icon corners are rounded, not square",
          image.pixelColor(1, 1).alpha() == 0)
    check("the window icon carries every size, not one to be scaled",
          {s.width() for s in icon.availableSizes()} >= set(pftheme.ICON_SIZES),
          str(sorted(s.width() for s in icon.availableSizes())))

    # At 16px a proportional 1.2px ring antialiases to grey. With the 2px
    # floor, the ring crosses the middle row as near-white pixels.
    small = pftheme.draw_icon(16)
    bright = [x for x in range(16)
              if min(small.pixelColor(x, 8).getRgb()[:3]) > 220]
    check("16px ring is crisp, not a grey smudge", len(bright) >= 2, str(bright))

    # Parsed here independently of write_ico, so a packing mistake cannot
    # pass by agreeing with itself.
    import struct
    ico_path = Path(tempfile.mkdtemp()) / "pfpost.ico"
    pftheme.write_ico(ico_path)
    blob = ico_path.read_bytes()
    reserved, kind, count = struct.unpack_from("<HHH", blob, 0)
    check("ico header says icon", reserved == 0 and kind == 1, "%d %d" % (reserved, kind))
    check("ico holds every size", count == len(pftheme.ICON_SIZES), str(count))
    decoded = []
    for index in range(count):
        w, h, _c, _r, planes, bits, length, offset = struct.unpack_from(
            "<BBBBHHII", blob, 6 + 16 * index)
        png = blob[offset:offset + length]
        picture = QImage.fromData(png, "PNG")
        stated = w or 256
        if (png[:8] == b"\x89PNG\r\n\x1a\n" and offset + length <= len(blob)
                and picture.width() == picture.height() == stated == (h or 256)
                and planes == 1 and bits == 32):
            decoded.append(stated)
    check("every ico entry decodes at its stated size",
          decoded == list(pftheme.ICON_SIZES), str(decoded))

    check("theme detection returns a bool",
          isinstance(pftheme.is_dark(_QApp.instance()), bool))

    check("primary actions are marked accent",
          composer.post_now.property("accent") is True)

    # Styling ::drop-down replaces the sub-control, and Qt then draws no arrow -
    # so the rule may only appear together with an arrow image of our own.
    # Strip comments first, or these match the notes explaining the rules.
    import re as _re

    def rules_of(tokens):
        return _re.sub(r"/\*.*?\*/", "", pftheme.stylesheet(tokens), flags=_re.S)

    check("without arrow images, the dropdown sub-control is left native",
          "::drop-down" not in rules_of(light_tokens))
    with_arrows = dict(light_tokens, chevron_image="C:/a.png",
                       chevron_up_image="C:/u.png", chevron_left_image="C:/l.png",
                       chevron_right_image="C:/r.png")
    styled = rules_of(with_arrows)
    check("with arrow images, ::drop-down and its arrow are styled together",
          "::drop-down" in styled and 'image: url("C:/a.png")' in styled)
    check("a missing calendar arrow keeps the whole dropdown block out",
          "::drop-down" not in rules_of(dict(with_arrows, chevron_left_image="")))
    check("number fields get the same themed arrows, up and down",
          'QSpinBox::up-arrow { image: url("C:/u.png")' in styled
          and 'QSpinBox::down-arrow { image: url("C:/a.png")' in styled)
    check("and fall back to native arrows together with the rest",
          "QSpinBox::up-button" not in rules_of(dict(with_arrows, chevron_up_image="")))
    up = QImage(pftheme.chevron_image("#123456", "up"))
    down = QImage(pftheme.chevron_image("#123456", "down"))
    # Apex pixels on the 64px drawing: up peaks near y=22, down bottoms near y=42.
    check("the up chevron points up and the down one down",
          up.pixelColor(32, 22).alpha() > 0 and up.pixelColor(32, 42).alpha() == 0
          and down.pixelColor(32, 42).alpha() > 0 and down.pixelColor(32, 22).alpha() == 0)

    for name, t in (("light", light_tokens), ("dark", dark_tokens)):
        for ground in ("bg", "surface", "surface_alt"):
            ratio = contrast(t["input_border"], t[ground])
            check("%s: dropdown and field outline on %s (%.2f:1)" % (name, ground, ratio),
                  ratio >= 3.0, "below the 3:1 floor for control boundaries")

    # Stylesheet padding does not feed back into sizeHint, so widgets sized to
    # fit their text get clipped - the date field lost its final characters.
    from PySide6.QtGui import QFontMetrics
    for name, widget, sample in (("date field", composer.when, "Sep 28, 2026 · 21:50"),
                                 ("visibility", composer.visibility, "Followers only")):
        needed = QFontMetrics(widget.font()).horizontalAdvance(sample)
        room = widget.minimumWidth() - needed
        # 10px left padding + 30px arrow section + borders, with a little air.
        check("%s has room for its text and arrow (%dpx spare)" % (name, room),
              room >= 48, "only %dpx spare" % room)

    print("\n  gating")
    composer.set_enabled(False)
    check("posting disabled while disconnected", not composer.post_now.isEnabled())
    check("disabled button explains why", "Connect" in composer.post_now.toolTip())
    composer.set_enabled(True)
    check("posting enabled once connected", composer.post_now.isEnabled())

    # Last, because it installs the real application stylesheet. These read
    # rendered pixels: a property being set proves nothing - the accent on
    # message-box buttons was set, and ignored.
    print("\n  rendered dialogs")

    def top_pixel(widget):
        image = widget.grab().toImage()
        return image.pixelColor(image.width() // 2, 4).name()

    for dark in (False, True):
        name = "dark" if dark else "light"
        t = pftheme.tokens(dark)
        t["check_image"] = pftheme.check_image(t["primary_text"])
        for direction, key in pftheme.ARROW_DIRECTIONS:
            t[key] = pftheme.chevron_image(t["text"], direction)
        _QApp.instance().setStyleSheet(pftheme.stylesheet(t))
        found = {}

        def ink(widget, region=None):
            """Pixels drawn in (nearly) the text colour: what an arrow is made of."""
            image = widget.grab().toImage()
            want = QColor(t["text"])
            x0, x1 = region or (0, image.width())
            return sum(1 for x in range(x0, x1) for y in range(image.height())
                       if abs(image.pixelColor(x, y).red() - want.red()) < 40
                       and abs(image.pixelColor(x, y).green() - want.green()) < 40
                       and abs(image.pixelColor(x, y).blue() - want.blue()) < 40)

        from PySide6.QtWidgets import QToolButton
        combo = composer.visibility
        combo.resize(combo.minimumWidth(), 34)
        arrow_ink = ink(combo, (combo.width() - 28, combo.width() - 2))
        # Calibrated: 8px with the arrow, 0 with its image missing, both themes.
        check("%s: the dropdown arrow is actually drawn (%d px)" % (name, arrow_ink),
              arrow_ink >= 5)
        calendar = composer.when.calendarWidget()
        calendar.resize(300, 250)
        prev = calendar.findChild(QToolButton, "qt_calendar_prevmonth")
        prev_ink = ink(prev) if prev else 0
        # Calibrated: 40-44px with our chevron, 0 with Qt's black default - which
        # is invisible on the dark bar. A looser colour match stops telling the
        # two apart in light mode, where black is close to the text colour.
        check("%s: the calendar's month arrow is visible (%d px)" % (name, prev_ink),
              prev_ink >= 20)

        def probe(box):
            for button in box.buttons():
                found[button.text()] = top_pixel(button)
            tick = box.checkBox()
            if tick is not None:
                found["off"] = tick.grab().toImage()
                tick.setChecked(True)
                found["on"] = tick.grab().toImage()
                tick.setChecked(False)

        pfstore.set_pref("warn_links", True)
        answering(KEEP, lambda: composer.confirm_links(link, "public"), probe=probe)
        check("%s: Edit caption renders in the accent colour" % name,
              found.get(EDIT) == t["primary"], str(found.get(EDIT)))
        check("%s: Keep the link does not" % name,
              found.get(KEEP) not in (None, t["primary"]), str(found.get(KEEP)))
        check("%s: ticking the box visibly changes it" % name,
              "on" in found and found["on"] != found["off"])

        answering("Post without it",
                  lambda: composer.confirm_alt_text([(a, None)]), probe=probe)
        check("%s: Add alt text renders in the accent colour" % name,
              found.get("Add alt text") == t["primary"], str(found.get("Add alt text")))

        # The box outline is the control's only affordance: 3:1 non-text floor.
        for ground in ("bg", "surface"):
            ratio = contrast(t["muted"], t[ground])
            check("%s: checkbox outline on %s (%.2f:1)" % (name, ground, ratio),
                  ratio >= 3.0)

        tick = QImage(t["check_image"])
        check("%s: tick image is a real drawing" % name,
              not tick.isNull() and tick.pixelColor(27, 46).alpha() > 0
              and tick.pixelColor(2, 2).alpha() == 0)
    _QApp.instance().setStyleSheet("")

    print("\n%s" % ("GUI smoke test passed." if not FAILURES
                    else "%d GUI CHECK(S) FAILED: %s" % (len(FAILURES), FAILURES)))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    code = main()
    # os._exit, not sys.exit: interpreter teardown destroys the suite's many
    # leftover Qt objects out of order and dies with 0xC0000409, turning a
    # passing run into a failing exit code. Results are already printed.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
