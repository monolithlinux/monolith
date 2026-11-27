#!/usr/bin/python3
"""Fail the image build if Monolith News couldn't open its window.

Monolith News draws its window with Qt, through PySide6 or PyQt6, whichever
an edition installs. Neither is on the computers that run Monolith's tests,
so the build is where the window first meets the Python, the Qt and the
fonts an image really has. This opens it off screen on made-up announcements
that use every part of it, goes through them the way a reader would, has
each one painted, and closes it again. Nothing is downloaded and nothing is
kept.

usage: check-monolith-news.py [ROOT]      (ROOT defaults to /)
"""
import faulthandler
import importlib.machinery
import importlib.util
import os
import sys
import tempfile
import traceback

# Loading /usr/bin/monolith-news must not leave a __pycache__ in the image.
sys.dont_write_bytecode = True

FONTS = ("IBM Plex Sans", "IBM Plex Mono")
TIME_LIMIT = 300
# The first announcement has everything an announcement can have.
FEED = b'''
[[announcements]]
id = "build-check-full"
date = 2026-01-02
title = "An announcement with every part the window can show"
category = "Check"
install_path = ["monolith", "System tools", "Status"]
body = """
A paragraph with **bold**, `code` and [a link](https://example.com/).

- a list item
- another, with `more code`
"""
note = "A note under the text."
links = [{ label = "A link button", url = "https://example.com/docs" }]

[[announcements]]
id = "build-check-plain"
date = 2026-01-01
title = "A plain one"
body = "Just text."
'''


class Problem(Exception):
    pass


def load(path):
    loader = importlib.machinery.SourceFileLoader("monolith_news_build_check", path)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    try:
        loader.exec_module(module)
    except OSError as error:
        raise Problem(f"{path} can't be read: {error}") from error
    return module


def check(root):
    news = load(os.path.join(root, "usr/bin/monolith-news"))
    try:
        qt = news.load_qt()
    except ImportError as error:
        raise Problem(str(error)) from error
    QtCore, QtGui, QtWidgets = qt.QtCore, qt.QtGui, qt.QtWidgets
    announcements, problems = news.parse_feed(FEED)
    if problems or len(announcements) != 2:
        raise Problem(f"the check's own announcements aren't valid any more: {problems}")

    # An error raised inside the window's own code doesn't stop the window:
    # both bindings hand it to this hook and carry on, so a window can look
    # fine and still be broken.
    raised = []

    def report(kind, error, trace):
        raised.append("".join(traceback.format_exception(kind, error, trace)).rstrip())
        # Python remembers the last error it reported, and with it whatever
        # the handler had open. A painter must not outlive its painting.
        for name in ("last_exc", "last_type", "last_value", "last_traceback"):
            if hasattr(sys, name):
                delattr(sys, name)

    def turn():
        """One turn of Qt's event loop, which paints what has changed.

        The painting is left to the loop, as on a computer. Asked for from
        here, PySide6 keeps an error raised in a paint handler until the
        painting is over, and letting go of that handler's painter then
        crashes Qt.
        """
        loop = QtCore.QEventLoop()
        QtCore.QTimer.singleShot(0, loop.quit)
        loop.exec()

    seen = {}

    def look(*_ignored):
        """Stands in for the program's event loop: the window is built and shown."""
        app = QtWidgets.QApplication.instance()
        window = next((widget for widget in app.topLevelWidgets()
                       if widget.objectName() == "newsWindow"), None)
        if window is None:
            raise Problem("Monolith News built no window.")
        rows = window.findChild(QtWidgets.QListWidget)
        seen["rows"] = rows.count()
        seen["texts"] = []
        for row in range(rows.count()):
            rows.setCurrentRow(row)
            window.update()
            # Paints the window, and deletes the announcement shown before.
            turn()
            if raised:
                break
            picture = window.grab().toImage()
            seen["texts"] += [view.toPlainText() for view in window.findChildren(QtWidgets.QTextBrowser)]
            # Painted with the window's own colors: the list's side and the bar at the bottom.
            seen[row] = (picture.pixelColor(4, picture.height() // 2).name(),
                         picture.pixelColor(picture.width() // 2, picture.height() - 4).name())
        seen["families"] = set(QtGui.QFontDatabase.families())
        window.close()
        return 0

    QtWidgets.QApplication.exec = look
    reporter, sys.excepthook = sys.excepthook, report
    try:
        # "cache" also shows the note about saved announcements.
        code = news.run_gui(news.FeedResult(tuple(announcements), "cache"),
                            news.NewsState(frozenset(), frozenset()), refresh=False, record=False)
    except Problem:
        raise
    except Exception:  # pylint: disable=broad-exception-caught
        # Raised before the window was up.
        raised.append(traceback.format_exc().rstrip())
        code = None
    finally:
        sys.excepthook = reporter
    if raised:
        raise Problem("Monolith News raised an error while its window was open:\n" + "\n".join(raised))
    if code != 0 or seen.get("rows") != 2:
        raise Problem(f"Monolith News ended with {code} and listed {seen.get('rows')} of 2 announcements.")
    if len(seen["texts"]) != 2 or "more code" not in seen["texts"][0] or seen["texts"][1] != "Just text.":
        raise Problem(f"Monolith News showed other text than the announcements': {seen['texts']}")
    colors = (news.Color.SIDEBAR, news.Color.CHROME)
    for row in range(2):
        if seen[row] != colors:
            raise Problem(f"Monolith News wasn't painted in its own colors: {seen[row]} where "
                          f"{colors} belong.")
    missing = [font for font in FONTS if font not in seen["families"]]
    if missing:
        raise Problem(f"Qt doesn't find {' and '.join(missing)}, which Monolith News is set in.")
    version = getattr(QtCore, "PYQT_VERSION_STR", "") or getattr(sys.modules.get(qt.name), "__version__", "")
    return (f"Monolith News: the window opens with {qt.name} {version} on Qt {QtCore.qVersion()}, "
            f"in {' and '.join(FONTS)}.")


def main(arguments):
    root = arguments[0] if arguments else "/"
    faulthandler.enable()
    # A window that never finishes opening must not hold the build up: after
    # five minutes this prints where the check is stuck and ends it.
    faulthandler.dump_traceback_later(TIME_LIMIT, exit=True)
    with tempfile.TemporaryDirectory(prefix="monolith-news-check-") as home:
        os.environ.update({
            "HOME": home, "XDG_CONFIG_HOME": f"{home}/config", "XDG_STATE_HOME": f"{home}/state",
            "XDG_CACHE_HOME": f"{home}/cache", "XDG_RUNTIME_DIR": f"{home}/run",
            "QT_QPA_PLATFORM": "offscreen",
        })
        os.mkdir(f"{home}/run", 0o700)
        try:
            print(check(root))
        except Problem as problem:
            print(f"check-monolith-news: {problem}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
