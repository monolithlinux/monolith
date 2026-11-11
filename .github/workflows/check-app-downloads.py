#!/usr/bin/env python3
"""Check that the apps Monolith installs through Gear Lever can still be installed.

Monolith's tests run the catalog against stand-ins, so they can't notice a
publisher changing what it releases. This runs the catalog helper's own code
against the real thing. For each app it reads the releases, picks the one a new
install would, downloads it, and makes the checks an install makes before it
changes anything: size, SHA256, AppImage header and embedded update source.

The helper passes over a release it can't use without a word, and installs an
older one. So for an app on GitHub, the release it picks also has to be the one
GitHub calls the latest.

    python3 .github/workflows/check-app-downloads.py [APP...]

Without arguments it checks every app the helper knows. It exits 0 when each
one passes, or fails only the way KNOWN says it does, and 1 otherwise. With
GITHUB_TOKEN set, requests to GitHub's API are signed in: GitHub's runners
share their addresses, and with them the small allowance for anonymous requests.
"""

import argparse
import html
import http.client
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import urllib.parse
import urllib.request

sys.dont_write_bytecode = True  # no __pycache__ beside the helper

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "files/system/usr/libexec/monolith/gear-lever-app"
SCRIPT = Path(__file__).resolve().relative_to(ROOT)

# Problems we already know about, so the daily check stays quiet until they
# change: APP: (words its error contains, what is known). The check fails again
# when the app passes or fails another way, so an entry can't outlive its problem.
KNOWN = {
    "epic-games": ("HTTP Error 404", "Epic's AppImage link has answered 404 since at least November 11, 2026. "
                                     "New installs fail; installed copies are left alone."),
}

# A failure has to happen three times in a row to count, with these pauses in
# seconds between the tries, so one dropped connection doesn't fail the check.
PAUSES = (15, 60)

PASSED, KNOWN_PROBLEM, FAILED = "passed", "known problem", "FAILED"


def load_helper():
    """The catalog's Gear Lever helper, as a module."""
    loader = importlib.machinery.SourceFileLoader("gear_lever_app", str(HELPER))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module  # dataclasses look their module up by name
    loader.exec_module(module)
    return module


class GitHubToken(urllib.request.BaseHandler):
    """Signs requests to GitHub's API in, and no others. The header is left
    behind when a request is redirected, so it can't follow one to another host."""

    def __init__(self, token):
        self.token = token

    def https_request(self, request):
        if urllib.parse.urlsplit(request.full_url).hostname == "api.github.com":
            request.add_unredirected_header("Authorization", f"Bearer {self.token}")
        return request


def latest_on_github(helper, app):
    """The tag of the release GitHub calls the app's latest."""
    url = f"https://api.github.com/repos/{app.repository}/releases/latest"
    try:
        return json.loads(helper.github_get(app, url))["tag_name"]
    except (helper.HelperError, OSError, http.client.HTTPException, ValueError, LookupError, TypeError) as error:
        raise helper.HelperError(f"Could not read {app.name}'s latest release from {url}: {error}.") from error


def check(helper, app):
    """What a new install does before it changes anything, and a look at GitHub's
    latest release. Returns the release an install would get; anything it raises
    is why it can't, or why that isn't the newest."""
    helper.require_readelf(app)
    target = app.select(helper.releases_for(app), None)
    print(f"A new install picks {app.name} {app.describe(target)}.", flush=True)
    if app.repository:
        latest = latest_on_github(helper, app)
        if latest != f"{app.tag_prefix}{target.version}":
            raise helper.HelperError(f"GitHub's latest {app.name} release is {latest}, but a new install gets "
                                     f"{target.version}. See https://github.com/{app.repository}/releases.")
    with tempfile.TemporaryDirectory(prefix=f"monolith-{app.id}-") as stage:
        source, verified = helper.download(app, target, Path(stage))
        helper.check_embedded(app, source, verified)
    return target


def check_patiently(helper, app):
    """check(), tried again after each pause while it fails. Returns the release
    and None, or None and the last error as the catalog would word it."""
    reason = ""
    for pause in (0, *PAUSES):
        if pause:
            print(f"Trying again in {pause} seconds.", flush=True)
            time.sleep(pause)
        try:
            return check(helper, app), None
        except Exception as error:  # the helper's own errors are worded for the catalog; name any other
            reason = str(error)
            if not reason or not isinstance(error, helper.HelperError):
                reason = f"{type(error).__name__}: {reason}".removesuffix(": ")
            print(f"Error: {reason}", flush=True)
    return None, reason


def judge(app, release, error):
    """How an app's check ended: PASSED, KNOWN_PROBLEM or FAILED, and what to say about it."""
    known = KNOWN.get(app.id)
    if known is None:
        return (PASSED, app.describe(release)) if error is None else (FAILED, error)
    words, note = known
    if error is None:
        return FAILED, (f"{app.name} {app.describe(release)} passes again. Take its entry out of KNOWN in "
                        f"{SCRIPT}, which says: {note}")
    if words in error:
        return KNOWN_PROBLEM, note
    return FAILED, f"{error} This is not the problem KNOWN lists for it in {SCRIPT}, which says: {note}"


def command(name, text="", **properties):
    """A GitHub Actions workflow command, such as an error shown on the run's page."""
    def escaped(value, more=""):
        for char in "%\r\n" + more:
            value = value.replace(char, f"%{ord(char):02X}")
        return value
    listed = ",".join(f"{key}={escaped(value, ':,')}" for key, value in properties.items())
    return f"::{name}{' ' if listed else ''}{listed}::{escaped(text)}"


def summary(rows):
    """The results as a Markdown table for the run's page."""
    def cell(text):
        return html.escape(text, quote=False).replace("|", "&#124;").replace("\n", " ")
    lines = ["### App downloads", "", "| App | Result | Details |", "|---|---|---|"]
    lines += [f"| {cell(app.name)} | {state} | {cell(detail)} |" for app, state, detail in rows]
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Check that the apps Monolith installs through Gear Lever can still be installed.")
    parser.add_argument("apps", nargs="*", metavar="APP", help="the apps to check, such as wowup-cf (default: all)")
    arguments = parser.parse_args(argv)
    helper = load_helper()
    unknown = [name for name in arguments.apps if name not in helper.APPS]
    if unknown:
        parser.error(f"unknown app: {', '.join(unknown)} (the catalog has {', '.join(helper.APPS)})")
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        urllib.request.install_opener(urllib.request.build_opener(GitHubToken(token)))
    on_github = os.environ.get("GITHUB_ACTIONS") == "true"

    rows = []
    for name in arguments.apps or helper.APPS:
        app = helper.APPS[name]
        print(command("group", app.name) if on_github else f"\n== {app.name}", flush=True)
        release, error = check_patiently(helper, app)
        if on_github:
            print(command("endgroup"), flush=True)
        rows.append((app, *judge(app, release, error)))

    width = max(len(app.name) for app, _, _ in rows)
    print("\nResults:")
    for app, state, detail in rows:
        print(f"  {state:<13}  {app.name:<{width}}  {detail}")
    if on_github:
        for app, state, detail in rows:
            if state != PASSED:
                print(command("error" if state == FAILED else "warning", detail, title=f"{app.name}: {state}"))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as file:
            file.write(summary(rows))
    return 1 if any(state == FAILED for _, state, _ in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
