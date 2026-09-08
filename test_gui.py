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
