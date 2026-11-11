#!/usr/bin/env python3
"""check-app-downloads.py, against a made-up GitHub and Epic.

The check reads the real publishers once a day. Here they are stand-ins, so the
tests can change what a publisher releases and see what the check makes of it.

    python3 tests/check-app-downloads.py
"""

import contextlib
import email.message
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import unittest
from unittest import mock
import urllib.request
import urllib.response
import warnings

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github/workflows"


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    loader.exec_module(module)
    return module


script = load("check_app_downloads", WORKFLOWS / "check-app-downloads.py")
# The helper's own tests have the stand-in for GitHub and Epic, and AppImages that readelf can read.
fixtures = load("gear_lever_fixtures", ROOT / "tests/gear-lever-apps.py")

WOWUP_CF, R2MODMAN, SIDRA, EPIC = fixtures.WOWUP_CF, fixtures.R2MODMAN, fixtures.SIDRA, fixtures.EPIC
EPIC_BUILD = "0.114.0+g04485d6"
JSON = {"Content-Type": "application/json; charset=utf-8"}
EPIC_IS_GONE = {"epic-games": ("HTTP Error 404", "Epic's link is gone.")}


def appimage(app, version, update_info=None):
    """The app's AppImage as its publisher builds it. WowUp-CF's has named its own stable releases as its
    update source since 2.25.5."""
    if update_info is None:
        update_info = fixtures.OWN_RELEASES if app is WOWUP_CF else b""
    return fixtures.make_appimage(version, name=app.name, update_info=update_info)


def latest_url(app):
    return f"https://api.github.com/repos/{app.repository}/releases/latest"


def publish(web, app, versions, *, data=None, **newest):
    """The app's releases on GitHub, newest first, with their AppImages to download. GitHub calls the first
    one its latest. data is the first one's AppImage when it isn't the usual one, and newest changes how
    its release lists it (see fixtures.release)."""
    files = [appimage(app, version) for version in versions]
    if data is not None:
        files[0] = data
    releases = [fixtures.release(version, app=app, data=file, **(newest if number == 0 else {}))
                for number, (version, file) in enumerate(zip(versions, files))]
    web.publish(releases, app)
    web.add(latest_url(app), json.dumps(releases[0]).encode(), headers=JSON)
    for version, file in zip(versions, files):
        web.asset(version, file, app=app)


def serve_epic(web, version):
    """Epic's link leads to the build's AppImage on its content server."""
    data = appimage(EPIC, version)
    build = EPIC.download_url(version)
    web.add(fixtures.EPIC_LINK, headers={"Content-Length": str(len(data))},
            final_url=f"{build}?launcherfilename={fixtures.EPIC_ASSET}")
    web.add(build, data, headers={"Content-Length": str(len(data))})


def results(out):
    """The check's closing list, as {app name: (result, details)}."""
    rows = {}
    for line in out.split("\nResults:\n", 1)[1].splitlines():
        if not line.startswith("  "):
            break
        state, name, detail = re.split(r" {2,}", line.strip(), maxsplit=2)
        rows[name] = (state, detail)
    return rows


class AppDownloads(unittest.TestCase):
    """Every app can be installed until a test changes what its publisher offers."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="monolith-app-downloads-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.downloads = self.tmp / "downloads"
        self.downloads.mkdir()
        self.page = self.tmp / "summary.md"
        self.pauses = []
        self.web = fixtures.FakeWeb()
        publish(self.web, WOWUP_CF, ["2.25.5", "2.25.4"])
        publish(self.web, R2MODMAN, ["3.2.21", "3.2.20"])
        publish(self.web, SIDRA, ["1.3.0", "1.2.1"])
        serve_epic(self.web, EPIC_BUILD)

    def check(self, *apps, known=None, github=False, token=None):
        """Run the check outside GitHub Actions, or in a run of it with github. Returns its exit code,
        what it printed and its results."""
        env = {key: value for key, value in os.environ.items() if not key.startswith("GITHUB_")}
        if github:
            env.update(GITHUB_ACTIONS="true", GITHUB_STEP_SUMMARY=str(self.page))
        if token:
            env["GITHUB_TOKEN"] = token
        out = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch("urllib.request.urlopen", self.web.urlopen), \
                mock.patch("time.sleep", self.pauses.append), \
                mock.patch.object(tempfile, "tempdir", str(self.downloads)), \
                mock.patch.object(script, "KNOWN", known or {}), \
                contextlib.redirect_stdout(out), warnings.catch_warnings():
            # An HTTP error keeps the server's answer open, and nothing closes it before the check ends.
            warnings.simplefilter("ignore", ResourceWarning)
            code = script.main(list(apps))
        return code, out.getvalue(), results(out.getvalue())

    def test_apps_that_can_be_installed_pass(self):
        code, out, rows = self.check()
        self.assertEqual(code, 0, out)
        self.assertEqual(rows, {"WowUp-CF": ("passed", "2.25.5 (stable channel)"),
                                "r2modman": ("passed", "3.2.21 (stable release)"),
                                "Sidra": ("passed", "1.3.0 (stable release)"),
                                "Epic Games Launcher": ("passed", f"{EPIC_BUILD} (Preview)")})
        self.assertEqual(self.web.unexpected, [])
        self.assertEqual(self.pauses, [])
        for app, version in ((WOWUP_CF, "2.25.5"), (R2MODMAN, "3.2.21"), (SIDRA, "1.3.0"), (EPIC, EPIC_BUILD)):
            self.assertEqual(self.web.requests.count(app.download_url(version)), 1, app.name)
        self.assertEqual(os.listdir(self.downloads), [], "the downloads are deleted")
        self.assertFalse(any(line.startswith("::") for line in out.splitlines()), out)
        self.assertFalse(self.page.exists())

    def test_only_the_named_apps_are_checked(self):
        code, out, rows = self.check("sidra", "r2modman")
        self.assertEqual(code, 0, out)
        self.assertEqual(list(rows), ["Sidra", "r2modman"])
        self.assertFalse([url for url in self.web.requests if "WowUp" in url or "epicgames" in url])
        with self.assertRaises(SystemExit) as stopped, contextlib.redirect_stderr(io.StringIO()) as said:
            script.main(["wowup"])
        self.assertEqual(stopped.exception.code, 2)
        self.assertIn("unknown app: wowup (the catalog has wowup-cf, r2modman, sidra, epic-games)", said.getvalue())

    def test_an_update_source_the_helper_refuses_fails_the_check(self):
        """What WowUp-CF 2.25.5 did on November 10, 2026, with a source the helper still refuses."""
        source = fixtures.EMBEDDED["prereleases"]
        publish(self.web, WOWUP_CF, ["2.25.5", "2.25.4"], data=appimage(WOWUP_CF, "2.25.5", source))
        code, out, rows = self.check()
        self.assertEqual(code, 1, out)
        state, detail = rows.pop("WowUp-CF")
        self.assertEqual(state, "FAILED")
        self.assertIn(f"WowUp-CF 2.25.5 embeds an AppImage update source ({source.decode()})", detail)
        self.assertEqual({state for state, _ in rows.values()}, {"passed"}, "the other apps are still checked")
        self.assertEqual(len(rows), 3)

    def test_a_failure_counts_after_three_tries(self):
        url = SIDRA.download_url("1.3.0")
        self.web.add(url, error="Connection reset by peer")
        code, out, rows = self.check("sidra")
        self.assertEqual(code, 1, out)
        self.assertEqual(rows["Sidra"], ("FAILED", f"Could not download {url}: <urlopen error Connection reset "
                                                   "by peer>. Nothing was changed."))
        self.assertEqual(self.web.requests.count(url), 3)
        self.assertEqual(self.pauses, [15, 60])
        self.assertEqual(os.listdir(self.downloads), [])

    def test_a_failure_that_passes_on_another_try_does_not_count(self):
        url = R2MODMAN.download_url("3.2.21")
        works = self.web.routes[url]
        self.web.add(url, error="Connection reset by peer", on_open=lambda: self.web.routes.update({url: works}))
        code, out, rows = self.check("r2modman")
        self.assertEqual(code, 0, out)
        self.assertEqual(rows["r2modman"], ("passed", "3.2.21 (stable release)"))
        self.assertEqual(self.web.requests.count(url), 2)
        self.assertEqual(self.pauses, [15])
        self.assertIn("Connection reset by peer", out)
        self.assertIn("Trying again in 15 seconds.", out)

    def test_an_error_that_is_not_the_helpers_is_named(self):
        for error, named in ((RuntimeError("no such thing"), "RuntimeError: no such thing"),
                             (TimeoutError(), "TimeoutError")):
            with self.subTest(named), mock.patch.object(script, "check", side_effect=error):
                code, out, rows = self.check("sidra")
                self.assertEqual(code, 1, out)
                self.assertEqual(rows["Sidra"], ("FAILED", named))

    def test_without_readelf_nothing_is_downloaded(self):
        with mock.patch.dict(os.environ, {"PATH": str(self.downloads)}):
            code, out, rows = self.check("sidra")
        self.assertEqual(code, 1, out)
        self.assertIn("readelf (binutils) is required", rows["Sidra"][1])
        self.assertEqual(self.web.requests, [])

    def test_a_newer_release_the_helper_passes_over_fails_the_check(self):
        """A renamed file: the helper goes on installing 3.2.21 and says nothing."""
        publish(self.web, R2MODMAN, ["3.3.0", "3.2.21"], name="r2modman-3.3.0-x86_64.AppImage")
        code, out, rows = self.check("r2modman")
        self.assertEqual(code, 1, out)
        self.assertIn("A new install picks r2modman 3.2.21 (stable release).", out)
        self.assertEqual(rows["r2modman"], (
            "FAILED", "GitHub's latest r2modman release is v3.3.0, but a new install gets 3.2.21. See "
                      "https://github.com/ebkr/r2modmanPlus/releases."))

    def test_an_app_with_no_release_the_helper_can_use_fails_the_check(self):
        publish(self.web, SIDRA, ["1.3.0"], name="Sidra-1.3.0-linux-x86_64.AppImage")
        code, out, rows = self.check("sidra")
        self.assertEqual(code, 1, out)
        self.assertEqual(rows["Sidra"], ("FAILED", "GitHub lists no downloadable Sidra stable release. Nothing was "
                                                   "changed."))

    def test_a_latest_release_that_cannot_be_read_fails_the_check(self):
        url = latest_url(SIDRA)
        answers = {"an error": {"status": 500}, "no release": {"body": b"{}"}, "a list": {"body": b"[]"},
                   "no JSON": {"body": b"<html>"}}
        for name, answer in answers.items():
            with self.subTest(name):
                self.web.add(url, **answer)
                code, out, rows = self.check("sidra")
                self.assertEqual(code, 1, out)
                state, detail = rows["Sidra"]
                self.assertEqual(state, "FAILED")
                self.assertTrue(detail.startswith(f"Could not read Sidra's latest release from {url}: "), detail)
                self.assertNotIn(SIDRA.download_url("1.3.0"), self.web.requests)

    def test_a_known_problem_does_not_fail_the_check(self):
        self.web.add(fixtures.EPIC_LINK, status=404)
        code, out, rows = self.check(known=EPIC_IS_GONE)
        self.assertEqual(code, 0, out)
        self.assertEqual(rows.pop("Epic Games Launcher"), ("known problem", "Epic's link is gone."))
        self.assertEqual({state for state, _ in rows.values()}, {"passed"})
        self.assertIn("Error: Could not read Epic Games Launcher download information", out)
        self.assertEqual(self.web.requests.count(fixtures.EPIC_LINK), 3)

    def test_a_known_problem_that_is_over_fails_the_check(self):
        code, out, rows = self.check(known=EPIC_IS_GONE)
        self.assertEqual(code, 1, out)
        self.assertEqual(rows["Epic Games Launcher"], (
            "FAILED", f"Epic Games Launcher {EPIC_BUILD} (Preview) passes again. Take its entry out of KNOWN in "
                      ".github/workflows/check-app-downloads.py, which says: Epic's link is gone."))

    def test_a_known_app_that_fails_another_way_fails_the_check(self):
        self.web.add(fixtures.EPIC_LINK, error="Temporary failure in name resolution")
        code, out, rows = self.check(known=EPIC_IS_GONE)
        self.assertEqual(code, 1, out)
        state, detail = rows["Epic Games Launcher"]
        self.assertEqual(state, "FAILED")
        self.assertIn("Temporary failure in name resolution", detail)
        self.assertTrue(detail.endswith("This is not the problem KNOWN lists for it in "
                                        ".github/workflows/check-app-downloads.py, which says: Epic's link is gone."))

    def test_known_lists_apps_the_catalog_has(self):
        apps = script.load_helper().APPS
        for name, entry in script.KNOWN.items():
            with self.subTest(name):
                self.assertIn(name, apps)
                words, note = entry
                self.assertTrue(words.strip() and note.strip())

    def test_a_run_on_github_shows_its_results_on_the_runs_page(self):
        source = fixtures.EMBEDDED["prereleases"].decode()
        publish(self.web, WOWUP_CF, ["2.25.5", "2.25.4"], data=appimage(WOWUP_CF, "2.25.5", source.encode()))
        self.web.add(fixtures.EPIC_LINK, status=404)
        code, out, rows = self.check(known=EPIC_IS_GONE, github=True)
        self.assertEqual(code, 1, out)
        lines = out.splitlines()
        marks = [line for line in lines if line.startswith("::group::") or line == "::endgroup::"]
        self.assertEqual(marks, ["::group::WowUp-CF", "::endgroup::", "::group::r2modman", "::endgroup::",
                                 "::group::Sidra", "::endgroup::", "::group::Epic Games Launcher", "::endgroup::"])
        self.assertLess(lines.index("::group::WowUp-CF"),
                        lines.index("Downloading WowUp-CF-2.25.5.AppImage (0.0 MiB)..."))
        self.assertLess(lines.index("::endgroup::"), lines.index("::group::r2modman"))
        refused = rows["WowUp-CF"][1]
        self.assertIn(source, refused)
        self.assertEqual([line for line in lines if line.startswith(("::error", "::warning"))], [
            f"::error title=WowUp-CF%3A FAILED::{refused}",
            "::warning title=Epic Games Launcher%3A known problem::Epic's link is gone."])
        page = self.page.read_text().splitlines()
        self.assertEqual(page[:4], ["### App downloads", "", "| App | Result | Details |", "|---|---|---|"])
        self.assertEqual(page[4], f"| WowUp-CF | FAILED | {refused.replace('|', '&#124;')} |")
        self.assertEqual(page[5:], ["| r2modman | passed | 3.2.21 (stable release) |",
                                    "| Sidra | passed | 1.3.0 (stable release) |",
                                    "| Epic Games Launcher | known problem | Epic's link is gone. |"])

    def test_text_in_a_message_for_github_cannot_end_it_or_start_another(self):
        self.assertEqual(script.command("error", "50% of a, b: done\r\n::warning::more", title="a: b, 100%\n"),
                         "::error title=a%3A b%2C 100%25%0A::50%25 of a, b: done%0D%0A::warning::more")
        self.assertEqual(script.command("endgroup"), "::endgroup::")
        row = type("App", (), {"name": "A|B <i>"})(), "FAILED", "x | y\nz <b>&"
        self.assertEqual(script.summary([row]).splitlines()[4],
                         "| A&#124;B &lt;i&gt; | FAILED | x &#124; y z &lt;b&gt;&amp; |")

    def test_a_token_is_used_when_there_is_one(self):
        with mock.patch.object(urllib.request, "install_opener") as install:
            self.check("sidra")
            install.assert_not_called()
            self.check("sidra", token="made-up-token")
        (opener,), _ = install.call_args
        self.assertEqual([handler.token for handler in opener.handlers if isinstance(handler, script.GitHubToken)],
                         ["made-up-token"])

    def test_the_workflow_runs_the_check_and_watches_the_files_it_reads(self):
        text = (WORKFLOWS / "check-app-downloads.yml").read_text()
        named = set(re.findall(r"(?:\.github|files)/[\w./-]+", text))
        self.assertEqual(named, {".github/workflows/check-app-downloads.yml", str(script.SCRIPT),
                                 str(script.HELPER.relative_to(ROOT))})
        for path in named:
            self.assertTrue((ROOT / path).is_file(), path)
        self.assertIn(f"\n        run: python3 {script.SCRIPT}\n", text)

    def test_nothing_is_left_beside_the_scripts(self):
        self.check()
        for folder in (WORKFLOWS, script.HELPER.parent, ROOT / "tests"):
            self.assertFalse((folder / "__pycache__").exists(), folder)


class Recorder(urllib.request.HTTPSHandler):
    """Answers in place of the network and keeps each request's Authorization header."""

    def __init__(self, redirects):
        super().__init__()
        self.redirects = redirects
        self.seen = []

    def https_open(self, request):
        self.seen.append((request.full_url, request.get_header("Authorization")))
        headers = email.message.Message()
        target = self.redirects.get(request.full_url)
        if target:
            headers["Location"] = target
        response = urllib.response.addinfourl(io.BytesIO(b""), headers, request.full_url, 302 if target else 200)
        response.msg = "Found" if target else "OK"
        return response


class Token(unittest.TestCase):
    def test_the_token_goes_to_githubs_api_and_nowhere_else(self):
        api = "https://api.github.com/repos/WowUp/WowUp.CF/releases"
        asset = "https://api.github.com/repos/WowUp/WowUp.CF/releases/assets/1"
        elsewhere = ["https://github.com/WowUp/WowUp.CF/releases/download/v2.25.5/WowUp-CF-2.25.5.AppImage",
                     "https://release-assets.githubusercontent.com/WowUp-CF-2.25.5.AppImage",
                     "https://api.github.com.example.org/repos/WowUp/WowUp.CF/releases",
                     "https://example.org/api.github.com/", fixtures.EPIC_LINK]
        recorder = Recorder({asset: elsewhere[1]})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), recorder, script.GitHubToken("made-up"))
        for url in (api, asset, *elsewhere):
            opener.open(url, timeout=5).close()
        self.assertEqual(recorder.seen, [(api, "Bearer made-up"), (asset, "Bearer made-up"), (elsewhere[1], None),
                                         *((url, None) for url in elsewhere)])


if __name__ == "__main__":
    unittest.main()
