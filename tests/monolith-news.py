#!/usr/bin/env python3
"""Offline checks for Monolith News feeds, read state, and the login check."""

import configparser
import datetime
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import urllib.error

sys.dont_write_bytecode = True


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "files/kde/usr/bin/monolith-news"


def entry(identifier, date="2026-01-01", title="Title", body="Body.", links=None, **fields):
    """Return one [[announcements]] table; date is raw TOML so tests can pass date-times."""
    lines = ["[[announcements]]", f"id = {json.dumps(identifier)}", f"date = {date}",
             f"title = {json.dumps(title)}", f"body = {json.dumps(body)}"]
    # JSON strings, numbers, and arrays are also valid TOML values.
    lines.extend(f"{key} = {json.dumps(value)}" for key, value in fields.items())
    if links is not None:
        tables = (f"{{ label = {json.dumps(label)}, url = {json.dumps(url)} }}" for label, url in links)
        lines.append(f"links = [{', '.join(tables)}]")
    return "\n".join(lines) + "\n\n"


def feed(*entries):
    return "".join(entries).encode()


class NewsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="monolith news ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = {
            "HOME": str(self.root / "home with spaces"),
            "XDG_STATE_HOME": str(self.root / "state with spaces"),
            "XDG_CACHE_HOME": str(self.root / "cache with spaces"),
            "XDG_CONFIG_HOME": str(self.root / "config with spaces"),
            "XDG_RUNTIME_DIR": str(self.root / "runtime with spaces"),
        }
        patch = mock.patch.dict(os.environ, self.env)
        patch.start()
        self.addCleanup(patch.stop)
        # Nothing here may open a real window.
        os.environ.pop("DISPLAY", None)
        os.environ.pop("WAYLAND_DISPLAY", None)
        loader = importlib.machinery.SourceFileLoader("monolith_news_test", str(APP))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        self.app = importlib.util.module_from_spec(spec)
        loader.exec_module(self.app)
        self.gui = mock.Mock(return_value=0)
        self.app.run_gui = self.gui
        self.responses = []
        self.opener = self.patch("urllib.request.urlopen", side_effect=self.respond)
        self.sleep = self.patch("time.sleep")
        # Keep expected warnings out of the test output.
        self.patch("sys.stderr", new_callable=io.StringIO)
        self.state = Path(self.env["XDG_STATE_HOME"]) / "monolith/news/state.json"
        self.cache = Path(self.env["XDG_CACHE_HOME"]) / "monolith/news/announcements.toml"
        self.override = Path(self.env["XDG_CONFIG_HOME"]) / "autostart/monolith-news.desktop"

    def patch(self, target, **kwargs):
        patcher = mock.patch(target, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def respond(self, request, timeout):
        self.assertEqual(request.full_url, self.app.FEED_URL)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return io.BytesIO(response)

    def login(self, *responses):
        """Run the login check; return (ids, source, dismissed) for an opened window, else None."""
        self.responses = list(responses)
        for recorder in (self.gui, self.opener, self.sleep):
            recorder.reset_mock()
        self.assertEqual(self.app.main(["--autostart"]), 0)
        self.assertEqual(self.responses, [])
        if not self.gui.called:
            return None
        result, state = self.gui.call_args.args
        self.assertEqual(self.gui.call_args.kwargs, {"refresh": False, "record": True})
        return [announcement.id for announcement in result.announcements], result.source, state.dismissed

    def test_repository_feed_is_valid(self):
        self.assertEqual(self.app.parse_feed((ROOT / "news/announcements.toml").read_bytes())[1], [])

    def test_new_ids_reopen_but_edits_stay_dismissed(self):
        original = feed(entry("a", "2026-01-01", title="First", body="Body."))
        self.assertEqual(self.login(original), (["a"], "network", set()))
        self.app.save_state({"a"}, set())
        self.assertIsNone(self.login(original))
        edited = entry("a", "2026-05-01", title="Edited", body="New body.",
                       links=[("Details", "https://example.com/details")])
        self.assertIsNone(self.login(feed(edited)))
        self.assertEqual(self.login(feed(entry("b", "2026-06-01"), edited)), (["b", "a"], "network", {"a"}))
        # Dismissals accumulate, so ids missing from the current feed stay dismissed.
        self.app.save_state({"b"}, set())
        self.assertIsNone(self.login(feed(entry("b", "2026-06-01"), edited)))

    def test_dismissals_are_per_user(self):
        data = feed(entry("a"))
        self.assertEqual(self.login(data), (["a"], "network", set()))
        self.app.save_state({"a"}, set())
        self.assertIsNone(self.login(data))
        other = self.root / "second user"
        with mock.patch.dict(os.environ, {"HOME": str(other / "home"), "XDG_STATE_HOME": str(other / "state")}):
            self.assertEqual(self.login(data), (["a"], "network", set()))

    def test_offline_login_retries_then_uses_cache(self):
        offline = [urllib.error.URLError("offline")] * self.app.LOGIN_ATTEMPTS
        retries = [mock.call(self.app.LOGIN_RETRY_SECONDS)] * (self.app.LOGIN_ATTEMPTS - 1)
        # Without a saved feed there is nothing to show.
        self.assertIsNone(self.login(*offline))
        self.assertEqual(self.opener.call_count, self.app.LOGIN_ATTEMPTS)
        self.assertEqual(self.sleep.call_args_list, retries)
        self.assertEqual(self.login(feed(entry("a"))), (["a"], "network", set()))
        self.assertEqual(self.login(*offline), (["a"], "cache", set()))
        self.assertEqual(self.opener.call_count, self.app.LOGIN_ATTEMPTS)
        self.assertEqual(self.sleep.call_args_list, retries)

    def test_network_recovery_during_retries(self):
        data = feed(entry("a"))
        offline = urllib.error.URLError("offline")
        self.assertEqual(self.login(offline, offline, data), (["a"], "network", set()))
        self.assertEqual(self.sleep.call_count, 2)
        self.assertEqual(self.cache.read_bytes(), data)

    def test_http_error_is_not_retried(self):
        missing = urllib.error.HTTPError(self.app.FEED_URL, 404, "Not Found", {}, None)
        self.assertIsNone(self.login(missing))
        self.assertEqual(self.opener.call_count, 1)
        self.sleep.assert_not_called()

    def test_bad_download_keeps_last_good_cache(self):
        good = feed(entry("a"))
        self.assertEqual(self.login(good), (["a"], "network", set()))
        # Valid TOML if cut to the size limit, so only the size check can reject it.
        oversized = good + b"#" * (self.app.MAX_FEED_BYTES + 1 - len(good))
        downloads = {
            "invalid TOML": b"not toml [",
            "oversized": oversized,
            "no announcements array": b'title = "Monolith"\n',
        }
        for name, data in downloads.items():
            with self.subTest(name):
                self.assertEqual(self.login(data), (["a"], "cache", set()))
                self.assertEqual(self.cache.read_bytes(), good)

    def test_invalid_entries_are_skipped_and_order_is_newest_first(self):
        docs = ("Docs", "https://example.com/docs")
        announcements, problems = self.app.parse_feed(feed(
            entry("x", "2026-01-01"),
            entry("Bad ID"),
            entry("y", "2026-03-01"),
            # A rejected entry must not claim the id of a later valid one.
            entry("z", "2026-03-01T10:00:00Z"),
            entry("z", "2026-03-01"),
            entry("two-lines", title="First line\nSecond line"),
            entry("plain-http", links=[("Docs", "http://example.com/docs")]),
            entry("four-links", links=[docs] * 4),
            entry("x", "2026-02-01"),
            entry("two-line-category", category="Gaming\nNews"),
            entry("long-category", category="x" * 25),
            entry("path-not-list", install_path="monolith"),
            entry("path-number", install_path=["monolith", 3]),
            entry("long-path", install_path=["step"] * 6),
            entry("two-line-note", note="Reboot\nfirst."),
        ))
        self.assertEqual([(a.id, a.date) for a in announcements], [
            ("y", datetime.date(2026, 3, 1)),
            ("z", datetime.date(2026, 3, 1)),
            ("x", datetime.date(2026, 1, 1)),
        ])
        self.assertEqual(len(problems), 12)
        for problem, index in zip(problems, (2, 4, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15)):
            self.assertTrue(problem.startswith(f"announcement {index}: "), problem)

    def test_optional_display_fields(self):
        announcements, problems = self.app.parse_feed(feed(
            entry("full", category=" Gaming ", install_path=["monolith", " Gaming ", "r2modman"],
                  note=" Reboot first. "),
            entry("bare"),
        ))
        self.assertEqual(problems, [])
        full, bare = announcements
        self.assertEqual((full.category, full.install_path, full.note),
                         ("Gaming", ("monolith", "Gaming", "r2modman"), "Reboot first."))
        self.assertEqual((bare.category, bare.install_path, bare.note), (None, (), None))

    def test_corrupt_state_is_replaced(self):
        self.state.parent.mkdir(parents=True)
        corrupt = {
            "not JSON": "not json",
            "read is not a list": json.dumps({"version": 1, "dismissed": ["a"], "read": "a"}),
        }
        for name, text in corrupt.items():
            with self.subTest(name):
                self.state.write_text(text)
                self.assertEqual(self.login(feed(entry("a"))), (["a"], "network", set()))
        self.app.save_state({"a"}, set())
        self.assertEqual(json.loads(self.state.read_text()),
                         {"version": 1, "dismissed": ["a"], "read": []})

    def test_read_state_merges_and_starts_from_older_dismissals(self):
        # Older releases saved only dismissals, after showing every announcement in full.
        self.state.parent.mkdir(parents=True)
        self.state.write_text(json.dumps({"version": 1, "dismissed": ["a"]}))
        self.assertEqual(self.app.load_state(), self.app.NewsState(frozenset({"a"}), frozenset({"a"})))
        # Saving adds to what is stored and never prunes it.
        self.app.save_state({"b"}, {"c"})
        self.assertEqual(json.loads(self.state.read_text()),
                         {"version": 1, "dismissed": ["a", "b"], "read": ["a", "c"]})
        # Once read state is stored, dismissing no longer marks announcements read.
        self.app.save_state({"d"}, set())
        self.assertEqual(self.app.load_state(),
                         self.app.NewsState(frozenset({"a", "b", "d"}), frozenset({"a", "c"})))

    def test_login_switch_override(self):
        self.assertTrue(self.app.login_check_enabled())
        self.app.set_login_check(False)
        parser = configparser.ConfigParser(interpolation=None)
        parser.optionxform = str
        self.assertEqual(parser.read(self.override, encoding="utf-8"), [str(self.override)])
        self.assertEqual(parser["Desktop Entry"]["Hidden"], "true")
        # Plasma's Autostart page runs this copy once re-enabled, so it must match the system entry.
        system = (ROOT / "files/kde/etc/xdg/autostart/monolith-news.desktop").read_text()
        self.assertEqual(self.override.read_text(), system + "Hidden=true\n")
        self.assertFalse(self.app.login_check_enabled())
        self.assertIsNone(self.login())
        self.opener.assert_not_called()
        # Plasma's Autostart page re-enables an entry by deleting its Hidden key.
        self.override.write_text(self.override.read_text().replace("Hidden=true\n", ""))
        self.assertTrue(self.app.login_check_enabled())
        self.app.set_login_check(False)
        self.app.set_login_check(True)
        self.assertFalse(self.override.exists())

    def test_preview_leaves_user_state_untouched(self):
        path = self.root / "preview feed.toml"
        path.write_bytes(feed(entry("a", "2026-01-01"), entry("b", "2026-02-01")))
        self.assertEqual(self.app.main(["--preview", str(path)]), 0)
        result, state = self.gui.call_args.args
        self.assertEqual([announcement.id for announcement in result.announcements], ["b", "a"])
        self.assertEqual(state, self.app.NewsState(frozenset(), frozenset()))
        self.assertEqual(self.gui.call_args.kwargs, {"refresh": False, "record": False})
        self.opener.assert_not_called()
        for variable in ("XDG_STATE_HOME", "XDG_CACHE_HOME", "XDG_CONFIG_HOME"):
            self.assertFalse(Path(self.env[variable]).exists(), variable)
        path.write_text("not toml [")
        self.gui.reset_mock()
        self.assertEqual(self.app.main(["--preview", str(path)]), 1)
        self.gui.assert_not_called()


if __name__ == "__main__":
    unittest.main()
