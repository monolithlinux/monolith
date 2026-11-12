#!/usr/bin/env python3
"""Check that the apps in Monolith's catalog can still be installed.

Monolith's tests run the catalog against stand-ins, so they can't notice a
publisher changing what it releases. This runs the catalog's own code against
the real publishers, as far as a new install goes before it changes anything.

An AppImage (every app the Gear Lever helper installs): the helper reads the
releases, picks one, downloads it and checks its size, SHA256, AppImage header
and embedded update source. The helper passes over a release it can't use
without a word and installs an older one, so the release it picks also has to
be the one GitHub calls the latest.

Ghostty Themes: the themes helper downloads its pinned archive and checks it.

Every other app: `monolith install APP` runs in a home directory made for the
check, with check-app-downloads.sh standing in for the programs that would
install something. So the catalog itself asks the publisher, downloads, and
compares checksums where it has them. Where it stops, the check looks at what
it would have installed:

  A program (Tea, Superfile, Oh My Pi), whose checksum matched the publisher's
  list, has to be a program for x86_64 computers.

  An install script (Claude Code, Codex CLI, OpenCode, Herdr) has to be a shell
  script that still mentions every setting and argument the catalog gives it,
  and the address `monolith update` asks for the newest version has to answer
  with one. What the script would download isn't checked.

  A .flatpakref (Prism Launcher, Sunshine, Fluxer, Fluxer Canary) has to name
  the app the catalog expects. Its repository has to list that app, not as
  end-of-life, and the app's runtime. That is read from the files Flatpak looks
  an app up in: the repository's summary.idx and the summary for x86_64 that
  names. The app's files aren't downloaded and no signature is checked.

  A Flatpak bundle (Moonfin, Plezy, the Crafting Apps), whose SHA256 matched the
  one GitHub lists, has to hold the app the catalog expects, and Flathub has to
  list its runtime. The runner has no Flatpak to install it with.

Nothing that was downloaded is run: not a program, not an install script.

    python3 .github/workflows/check-app-downloads.py [APP...]

Without arguments it checks every app in the catalog, and any other app the
Gear Lever helper installs. It exits 0 when each one passes, or fails only the
way KNOWN says it does, and 1 otherwise. With GITHUB_TOKEN set, requests to
GitHub's API are signed in: GitHub's runners share their addresses, and with
them the small allowance for anonymous requests.
"""

import argparse
import functools
import gzip
import hashlib
import html
import http.client
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import struct
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

sys.dont_write_bytecode = True  # no __pycache__ beside the helpers

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

# The other files the check reads. The workflow runs for a pull request that changes one of them.
CATALOG = HELPER.with_name("software")
COMMON = HELPER.with_name("common.sh")
THEMES = HELPER.with_name("ghostty-themes")
STAND_INS = Path(__file__).resolve().with_suffix(".sh")

GHOSTTY_THEMES = "ghostty-themes"
# The catalog's last line runs it. Without that line, sourcing it only defines its functions.
RUNS_THE_CATALOG = '\nmain "$@"\n'
# How check-app-downloads.sh ends when the catalog got as far as installing.
STOPPED = 120
# The catalog's own downloads give up when they stall. This many seconds are for anything else that hangs.
TIME_LIMIT = 600
# Monolith is for x86_64 computers only.
ARCH = "x86_64"
AGENT = "monolith-app-downloads/1 (+https://github.com/monolithlinux/monolith)"
MAX_ANSWER = 64 * 1024 * 1024

# The formats OSTree and Flatpak keep a repository's summary and a bundle in.
SUMMARY = "(a(s(taya{sv}))a{sv})"
SUMMARY_INDEX = "(a{s(ayaaya{sv})}a{sv})"
BUNDLE = "(a{sv}tayay(a{sv}aya(say)sstayay)aya(uayttay)a(yaytt))"

# One request for each address in a run.
ANSWERS = {}
REPOSITORIES = {}


class Problem(Exception):
    """Why an app can't be installed, worded for the results."""


class NotThere(Problem):
    """An address that answers 404."""


def load(name, path):
    """One of the catalog's Python helpers, as a module."""
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module  # dataclasses look their module up by name
    loader.exec_module(module)
    return module


def load_helper():
    """The catalog's Gear Lever helper."""
    return load("gear_lever_app", HELPER)


class GitHubToken(urllib.request.BaseHandler):
    """Signs requests to GitHub's API in, and no others. The header is left
    behind when a request is redirected, so it can't follow one to another host."""

    def __init__(self, token):
        self.token = token

    def https_request(self, request):
        if urllib.parse.urlsplit(request.full_url).hostname == "api.github.com":
            request.add_unredirected_header("Authorization", f"Bearer {self.token}")
        return request


# --- AppImages, through the Gear Lever helper --------------------------------------------------

def latest_on_github(helper, app):
    """The tag of the release GitHub calls the app's latest."""
    url = f"https://api.github.com/repos/{app.repository}/releases/latest"
    try:
        return json.loads(helper.github_get(app, url))["tag_name"]
    except (helper.HelperError, OSError, http.client.HTTPException, ValueError, LookupError, TypeError) as error:
        raise helper.HelperError(f"Could not read {app.name}'s latest release from {url}: {error}.") from error


def check_appimage(helper, app):
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
    return app.describe(target)


# --- Ghostty Themes, through its helper ---------------------------------------------------------

def check_themes():
    """The pinned theme archive, downloaded and checked by the helper that installs it."""
    helper = load("ghostty_themes", THEMES)
    print(f"Downloading {helper.URL}...", flush=True)
    try:
        themes = helper.download_themes()
    except (ValueError, tarfile.TarError) as error:
        raise Problem(str(error)) from error
    except (OSError, http.client.HTTPException) as error:
        raise Problem(f"Could not get {helper.URL}: {error}.") from error
    return f"{helper.RELEASE} ({len(themes)} themes)"


# --- everything else, through the catalog --------------------------------------------------------

def catalog_apps():
    """The catalog's apps as {ID: name}, in its order."""
    listed = subprocess.run(["bash", str(CATALOG), "__ids"], capture_output=True, text=True, timeout=60,
                            stdin=subprocess.DEVNULL, env={"PATH": os.environ.get("PATH", os.defpath), "HOME": "/"})
    apps = dict(line.split("\t", 1) for line in listed.stdout.splitlines() if "\t" in line)
    if listed.returncode != 0 or not apps:
        sys.exit(f"Could not list the catalog's apps with {CATALOG.relative_to(ROOT)} __ids: "
                 f"{(listed.stderr or listed.stdout).strip() or f'exit status {listed.returncode}'}")
    return apps


def every_app(helper):
    """The apps to check, as {ID: name}: the catalog's in its order, then any other the Gear Lever helper
    installs. The helper can keep an app the catalog has stopped offering while its publisher is away,
    and the check goes on asking, so we see the day it can be installed again."""
    apps = catalog_apps()
    for app in helper.APPS.values():
        apps.setdefault(app.id, app.name)
    return apps


class Run:
    """One run of the catalog's code: how it ended, what it printed, and what it asked curl and the
    stand-ins for, each as [command, argument...]."""

    def __init__(self, work, status, output):
        self.status, self.output = status, output
        self.kept = work / "check/kept"
        record = work / "check/record"
        self.record = [line.split("\t") for line in record.read_text(encoding="utf-8").splitlines()] \
            if record.exists() else []
        self.stopped = status == STOPPED and self.record[-1:] == [["stopped"]]

    def asked(self, command):
        return [fields[1:] for fields in self.record if fields[0] == command]

    @property
    def why(self):
        """Why it ended early: the last thing it said."""
        lines = [line.strip() for line in self.output.splitlines() if line.strip()]
        return lines[-1].removeprefix("Error: ") if lines else f"The catalog ended with status {self.status}."


def by_the_catalog(work, action, app):
    """Have the catalog's own code install an app, or look up its newest version, in a home directory of
    its own inside work, with check-app-downloads.sh standing in for the programs that install."""
    text = CATALOG.read_text(encoding="utf-8")
    if not text.endswith(RUNS_THE_CATALOG):
        raise Problem(f"{CATALOG.relative_to(ROOT)} no longer ends with {RUNS_THE_CATALOG.strip()}, so {SCRIPT} "
                      "can't load its functions.")
    for folder in ("catalog", "home", "check"):
        (work / folder).mkdir()
    library = work / "catalog" / CATALOG.name
    library.write_text(text[:1 - len(RUNS_THE_CATALOG)], encoding="utf-8")
    shutil.copy(COMMON, library.parent)
    # Nothing of this computer's own setup reaches the catalog.
    env = {"PATH": os.environ.get("PATH", os.defpath), "HOME": str(work / "home"), "LC_ALL": "C.UTF-8",
           "MONOLITH_CHECK_DIR": str(work / "check")}
    if os.environ.get("GITHUB_TOKEN"):
        env["GITHUB_TOKEN"] = os.environ["GITHUB_TOKEN"]
    process = subprocess.Popen(["bash", str(STAND_INS), str(library), action, app], env=env, text=True,
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               start_new_session=True)
    try:
        output, _ = process.communicate(timeout=TIME_LIMIT)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)  # the catalog and what it started
        process.communicate()
        raise Problem(f"The catalog was still busy after {TIME_LIMIT // 60:g} minutes and was stopped.") from None
    return Run(work, process.returncode, output)


def downloaded(run):
    """The address of the file the catalog downloaded."""
    saved = [url for url, file in run.asked("curl") if file]
    if not saved:
        raise Problem(f"The catalog got as far as installing without a download that {SCRIPT} saw. It sees what "
                      "the catalog downloads with curl.")
    return saved[0]


def release_of(url):
    """The release and the file in a download's address: v1.6.0 (superfile-linux-v1.6.0-amd64.tar.gz)."""
    *_, release, file = urllib.parse.unquote(urllib.parse.urlsplit(url).path).split("/")
    return f"{release} ({file})"


def check_program(run):
    """A program the catalog downloaded and whose checksum it compared. It would run it next."""
    name = Path(run.asked("install")[-1][-2]).name
    with open(run.kept, "rb") as file:
        header = file.read(20)
    if (len(header) < 20 or header[:4] != b"\x7fELF" or header[4] != 2 or header[5] != 1
            or int.from_bytes(header[16:18], "little") not in (2, 3) or int.from_bytes(header[18:20], "little") != 62):
        raise Problem(f"{name} is not a program for {ARCH} computers.")


def check_installer(run, app, name):
    """A publisher's install script, where the catalog would run it. Returns the newest version, as the
    catalog's update would find it."""
    url = downloaded(run)
    shell, *arguments = run.asked("installer")[-1]
    settings = run.asked("settings")[-1]
    command = " ".join([shell, "install.sh", *arguments])
    print(f"The catalog would now run: {command}" + (f", with {', '.join(settings)} set" if settings else ""),
          flush=True)
    script = run.kept.read_text(encoding="utf-8", errors="replace")
    if not script.startswith("#!"):
        raise Problem(f"{url} is no install script: it starts with {script[:40]!r}.")
    # Read by bash, which runs nothing with -n. Monolith's sh is bash too. The runner's sh is another
    # shell, and refuses some of what Monolith's takes.
    parsed = subprocess.run(["bash", "-n", str(run.kept)], capture_output=True, text=True, timeout=60,
                            stdin=subprocess.DEVNULL)
    if parsed.returncode != 0:
        said = parsed.stderr.replace(str(run.kept), "install script").strip().splitlines()
        raise Problem(f"{url} is not a script {shell} can read: {said[-1] if said else 'it says nothing'}")
    for word in settings:
        if not mentions(script, word):
            raise Problem(f"The catalog gives {url} the setting {word}, but the script no longer mentions it.")
    for word in arguments:
        if not mentions(script, word):
            raise Problem(f"The catalog runs {url} with {word}, but the script no longer mentions it.")

    with tempfile.TemporaryDirectory(prefix=f"monolith-{app}-") as work:
        newest = by_the_catalog(Path(work), "newest", app)
    said = newest.output.split()
    if newest.status == 0 and said:
        print(f"The newest {name}, as `monolith update` reads it, is {said[-1]}.", flush=True)
        return said[-1]
    print(newest.output, end="" if newest.output.endswith("\n") else "\n", flush=True)
    asked = [url for url, _ in newest.asked("curl")]
    if not asked:
        raise Problem(f"The catalog can't tell which {name} is the newest, so `monolith update` runs the installer "
                      "every time.")
    reason = f"{newest.why}." if "Could not get" in newest.output else \
        f"{asked[-1]} answered, but not with a version the catalog can read."
    raise Problem(f"{reason} `monolith update` asks there for the newest {name}, and runs the installer every time "
                  "it gets no answer.")


def mentions(script, word):
    """Whether a script has a word as a whole: CODEX_HOME, not MY_CODEX_HOME or CODEX_HOME_DIR."""
    return re.search(rf"(?<![\w-]){re.escape(word)}(?![\w-])", script) is not None


def keyfile(text, group):
    """The keys of one group in a file such as a .flatpakref, or None when it has no such group."""
    found, inside = None, False
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("["):
            inside = line == f"[{group}]"
            if inside and found is None:
                found = {}
        elif inside and "=" in line and not line.startswith("#"):
            key, _, value = line.partition("=")
            found[key.strip()] = value.strip()
    return found


def fetch(url):
    """What an address answers. Each one is asked once in a run."""
    if url not in ANSWERS:
        request = urllib.request.Request(url, headers={"User-Agent": AGENT})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                answer = response.read(MAX_ANSWER + 1)
        except urllib.error.HTTPError as error:
            raise (NotThere if error.code == 404 else Problem)(f"Could not get {url}: {error}.") from error
        except (OSError, http.client.HTTPException) as error:
            raise Problem(f"Could not get {url}: {error}.") from error
        if len(answer) > MAX_ANSWER:
            raise Problem(f"{url} answers with more than {MAX_ANSWER // 1048576} MiB.")
        ANSWERS[url] = answer
    return ANSWERS[url]


def group_at(url, group):
    """One group of the keyfile at an address."""
    keys = keyfile(fetch(url).decode("utf-8", "replace"), group)
    if keys is None:
        raise Problem(f"{url} has no [{group}] group.")
    return keys


FIXED = {"b": "?", "y": "B", "n": "h", "q": "H", "i": "i", "u": "I", "h": "i", "x": "q", "t": "Q", "d": "d"}


@functools.cache
def members(types):
    """The types in a GVariant type string, one after another: s(tay) is s and (tay)."""
    found = []
    while types:
        end, depth = 0, 0
        while types[end] in "am":
            end += 1
        for end in range(end, len(types)):
            depth += (types[end] in "({") - (types[end] in ")}")
            if depth == 0:
                break
        found.append(types[:end + 1])
        types = types[end + 1:]
    return tuple(found)


@functools.cache
def alignment(kind):
    if kind[0] in FIXED:
        return struct.calcsize(FIXED[kind[0]])
    if kind[0] in "am":
        return alignment(kind[1:])
    if kind[0] in "({":
        return max((alignment(member) for member in members(kind[1:-1])), default=1)
    return 8 if kind[0] == "v" else 1


@functools.cache
def fixed_size(kind):
    """How many bytes every value of a type takes, or None when that depends on the value."""
    if kind[0] in FIXED:
        return struct.calcsize(FIXED[kind[0]])
    if kind[0] not in "({":
        return None
    size = 0
    for member in members(kind[1:-1]):
        if fixed_size(member) is None:
            return None
        size = -(-size // alignment(member)) * alignment(member) + fixed_size(member)
    return -(-size // alignment(kind)) * alignment(kind) or 1


def unpack(kind, data):
    """A value in GVariant's little-endian binary form, which OSTree and Flatpak keep their summaries and
    bundles in. Byte arrays come back as views of data, everything else as Python values."""
    data = memoryview(data)
    size = len(data)
    if kind[0] in FIXED:
        return struct.unpack("<" + FIXED[kind[0]], data)[0]
    if kind[0] in "sog":
        if not size or data[-1]:
            raise ValueError("a string has no end")
        return bytes(data[:-1]).decode("utf-8")
    if kind[0] == "v":
        # The value, a zero byte, then the value's type.
        tail = bytes(data[-256:])
        if b"\0" not in tail:
            raise ValueError("a value of any type doesn't say which type")
        return unpack(tail[tail.rindex(b"\0") + 1:].decode("ascii"), data[:size - len(tail) + tail.rindex(b"\0")])
    if kind == "ay":
        return data
    # A member whose size varies ends where a table at the end of its container says. The table's
    # numbers are as wide as the container's size needs.
    width = 1 if size <= 0xFF else 2 if size <= 0xFFFF else 4 if size <= 0xFFFFFFFF else 8

    def table(number):
        """An entry of the table, counted from its end."""
        position = size - number * width
        if position < 0:
            raise ValueError("a table of ends is cut off")
        return int.from_bytes(data[position:position + width], "little")

    if kind[0] == "a":
        if not size:
            return []
        step = fixed_size(kind[1:])
        if step:
            if size % step:
                raise ValueError("an array ends inside one of its values")
            ends = range(step, size + 1, step)
        else:
            # The last value ends where the table begins.
            count, rest = divmod(size - table(1), width)
            if count < 1 or rest:
                raise ValueError("an array's table of ends doesn't fit")
            ends = [table(number) for number in range(count, 0, -1)]
        types = (kind[1:],) * len(ends)
    elif kind[0] in "({":
        # The table has the ends in reverse, and none for the last member, which ends where the table begins.
        types = members(kind[1:-1])
        loose = [index for index, member in enumerate(types[:-1]) if not fixed_size(member)]
        ends = [None] * len(types)
        for number, index in enumerate(loose, 1):
            ends[index] = table(number)
        if types and not fixed_size(types[-1]):
            ends[-1] = size - len(loose) * width
    else:
        raise ValueError(f"values of type {kind} aren't supported")
    values, start = [], 0
    for member, end in zip(types, ends):
        start = -(-start // alignment(member)) * alignment(member)
        end = start + fixed_size(member) if end is None else end
        if not start <= end <= size:
            raise ValueError("a value ends outside its container")
        values.append(unpack(member, data[start:end]))
        start = end
    return values if kind[0] == "a" else tuple(values)


def repository(url):
    """What a Flatpak repository offers, as {ref: (commit, the app's metadata, why it is end-of-life,
    what replaces it)}. Read the way Flatpak finds an app: summary.idx names the summary for x86_64 by
    its SHA256, and a repository without that index has everything in summary."""
    if url in REPOSITORIES:
        return REPOSITORIES[url]
    base = url.rstrip("/")
    source = f"{base}/summary.idx"
    try:
        try:
            index = dict(unpack(SUMMARY_INDEX, fetch(source))[0])
        except NotThere:
            source = f"{base}/summary"
            listed, about = unpack(SUMMARY, fetch(source))
        else:
            if ARCH not in index:
                raise Problem(f"{source} names no summary for {ARCH}.")
            digest = bytes(index[ARCH][0]).hex()
            source = f"{base}/summaries/{digest}.gz"
            summary = gzip.decompress(fetch(source))
            if hashlib.sha256(summary).hexdigest() != digest:
                raise Problem(f"{source} is not the summary {base}/summary.idx names: its SHA256 differs.")
            listed, about = unpack(SUMMARY, summary)
        about = dict(about)
        version = about.get("xa.summary-version", 0)
        if version not in (0, 1):
            raise Problem(f"{source} is a summary of version {version}, which this check can't read.")
        # Older summaries keep what Flatpak needs to know about every ref in two tables of their own.
        cache = dict(about.get("xa.cache") or ())
        sparse = dict(about.get("xa.sparse-cache") or ())
        refs = {}
        for ref, (_, commit, details) in listed:
            details = dict(details) if version else dict(sparse.get(ref) or ())
            metadata = details.get("xa.data") if version else cache.get(ref)
            refs[ref] = (bytes(commit).hex(), metadata[2] if metadata else "", details.get("eol"), details.get("eolr"))
    except (ValueError, LookupError, TypeError, struct.error, zlib.error, EOFError, OSError) as error:
        raise Problem(f"{source} is not a summary this check can read: {type(error).__name__}: {error}") from error
    REPOSITORIES[url] = refs
    return refs


def described_by(url):
    """The address of the repository a .flatpakrepo file is for."""
    address = group_at(url, "Flatpak Repo").get("Url")
    if not address:
        raise Problem(f"{url} names no Url.")
    return address


def need_runtime(metadata, what, repositories):
    """The runtime an app's metadata names has to be in one of the repositories Flatpak would look in.
    Returns the runtime and the repository that has it."""
    runtime = (keyfile(metadata, "Application") or {}).get("runtime")
    if not runtime:
        raise Problem(f"{what} doesn't say which runtime it needs.")
    for url in repositories:
        if f"runtime/{runtime}" in repository(url):
            return runtime, url
    raise Problem(f"{what} needs the runtime {runtime}, which isn't in {' or '.join(repositories)}.")


def flatpak_app(run):
    """The Flatpak ID the catalog expects, which it asks Flatpak about before it installs."""
    asked = [arguments[-1] for arguments in run.asked("flatpak") if arguments[0] == "info"]
    if not asked:
        raise Problem(f"The catalog no longer asks Flatpak whether the app is installed, and {SCRIPT} takes the "
                      "app's ID from that question.")
    return asked[0]


def check_flatpakref(run):
    """What `flatpak install URL` needs from a .flatpakref and its repository, short of the app's files."""
    url = run.asked("flatpak")[-1][-1]
    app_id = flatpak_app(run)
    print(f"The catalog would now run: flatpak {' '.join(run.asked('flatpak')[-1])}", flush=True)
    about = group_at(url, "Flatpak Ref")
    for key in ("Name", "Url"):
        if not about.get(key):
            raise Problem(f"{url} names no {key}.")
    if about.get("Version", "1") != "1":
        raise Problem(f"{url} is a .flatpakref of version {about['Version']}; Flatpak reads only version 1.")
    if about["Name"] != app_id:
        raise Problem(f"{url} installs {about['Name']}, but the catalog expects {app_id}.")
    branch = about.get("Branch", "master")
    ref = f"app/{app_id}/{ARCH}/{branch}"
    offered = repository(about["Url"])
    if ref not in offered:
        raise Problem(f"{about['Url']} doesn't list {ref}, so Flatpak can't install it from {url}.")
    commit, metadata, why, replaced_by = offered[ref]
    if replaced_by:
        raise Problem(f"{ref} is end-of-life in {about['Url']} and replaced by {replaced_by}. A new install would "
                      "get that app instead, which the catalog doesn't know.")
    if why is not None:
        raise Problem(f"{ref} is end-of-life in {about['Url']}: {why}")
    # Flatpak looks for the runtime in the app's repository and in the one the .flatpakref names for it.
    repositories = [about["Url"]]
    if about.get("RuntimeRepo"):
        repositories.append(described_by(about["RuntimeRepo"]))
    runtime, where = need_runtime(metadata, ref, list(dict.fromkeys(repositories)))
    print(f"{about['Url']} lists {ref} at commit {commit}. {where} has its runtime, {runtime}.", flush=True)
    return f"{branch} ({urllib.parse.urlsplit(about['Url']).hostname}, commit {commit[:12]})"


def check_bundle(run):
    """A Flatpak bundle the catalog downloaded and compared with GitHub's SHA256. Flatpak would install it
    next, and look for its runtime in the repository the catalog adds for that."""
    file = Path(run.asked("flatpak")[-1][-1]).name
    app_id = flatpak_app(run)
    added = [arguments[-1] for arguments in run.asked("flatpak") if arguments[0] == "remote-add"]
    print(f"The catalog would now run: flatpak install --bundle {file}", flush=True)
    try:
        about = dict(unpack(BUNDLE, run.kept.read_bytes())[0])
        ref, metadata = about["ref"], about.get("metadata") or ""
    except (ValueError, LookupError, TypeError, struct.error) as error:
        raise Problem(f"{file} is not a Flatpak bundle this check can read: {type(error).__name__}: {error}") \
            from error
    if ref.split("/")[:3] != ["app", app_id, ARCH]:
        raise Problem(f"{file} installs {ref}, but the catalog expects app/{app_id}/{ARCH}.")
    if not added:
        raise Problem(f"The catalog no longer adds a repository for {file}'s runtime, so {SCRIPT} can't tell "
                      "where Flatpak would look for it.")
    runtime, where = need_runtime(metadata, file, [described_by(url) for url in added])
    print(f"It holds {ref}. {where} has its runtime, {runtime}.", flush=True)


def check_install(app, name):
    """A new install by the catalog, up to the step that would install something, and a look at what it
    would install. Returns what that is; anything it raises is why the install would fail."""
    with tempfile.TemporaryDirectory(prefix=f"monolith-{app}-") as work:
        run = by_the_catalog(Path(work), "install", app)
        print(run.output, end="" if run.output.endswith("\n") else "\n", flush=True)
        if not run.stopped:
            if run.status == 0:
                raise Problem(f"The catalog's install of {name} ended without installing anything. {SCRIPT} "
                              "expects it to get as far as a program, an install script or Flatpak.")
            raise Problem(run.why)
        if run.asked("installer"):
            return f"{check_installer(run, app, name)} (install script read, not run)"
        if run.asked("install"):
            release = release_of(downloaded(run))
            print("The catalog would now install this program.", flush=True)
            check_program(run)
            return release
        if run.kept.exists():
            release = release_of(downloaded(run))
            check_bundle(run)
            return release
        return check_flatpakref(run)


# --- the run ----------------------------------------------------------------------------------------

def check(app, name, helper):
    """One app's check. Returns what a new install would get; anything it raises is why it can't."""
    if app in helper.APPS:
        return check_appimage(helper, helper.APPS[app])
    if app == GHOSTTY_THEMES:
        return check_themes()
    return check_install(app, name)


def check_patiently(app, name, helper):
    """check(), tried again after each pause while it fails. Returns what an install would get and None,
    or None and the last error as the catalog would word it."""
    reason = ""
    for pause in (0, *PAUSES):
        if pause:
            print(f"Trying again in {pause} seconds.", flush=True)
            time.sleep(pause)
        try:
            return check(app, name, helper), None
        except Exception as error:  # the catalog's and the check's own errors are worded; name any other
            reason = str(error)
            if not reason or not isinstance(error, (helper.HelperError, Problem)):
                reason = f"{type(error).__name__}: {reason}".removesuffix(": ")
            print(f"Error: {reason}", flush=True)
    return None, reason


def judge(app, name, found, error):
    """How an app's check ended: PASSED, KNOWN_PROBLEM or FAILED, and what to say about it."""
    known = KNOWN.get(app)
    if known is None:
        return (PASSED, found) if error is None else (FAILED, error)
    words, note = known
    if error is None:
        return FAILED, (f"{name} {found} passes again. Take its entry out of KNOWN in {SCRIPT}, which says: {note}")
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
    lines += [f"| {cell(name)} | {state} | {cell(detail)} |" for name, state, detail in rows]
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Check that the apps in Monolith's catalog can still be installed.")
    parser.add_argument("apps", nargs="*", metavar="APP", help="the apps to check, such as wowup-cf (default: all)")
    arguments = parser.parse_args(argv)
    helper = load_helper()
    apps = every_app(helper)
    unknown = [app for app in arguments.apps if app not in apps]
    if unknown:
        parser.error(f"unknown app: {', '.join(unknown)} (the check knows {', '.join(apps)})")
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        urllib.request.install_opener(urllib.request.build_opener(GitHubToken(token)))
    on_github = os.environ.get("GITHUB_ACTIONS") == "true"
    ANSWERS.clear()
    REPOSITORIES.clear()

    rows = []
    for app in arguments.apps or apps:
        name = apps[app]
        print(command("group", name) if on_github else f"\n== {name}", flush=True)
        found, error = check_patiently(app, name, helper)
        if on_github:
            print(command("endgroup"), flush=True)
        rows.append((name, *judge(app, name, found, error)))

    width = max(len(name) for name, _, _ in rows)
    print("\nResults:")
    for name, state, detail in rows:
        print(f"  {state:<13}  {name:<{width}}  {detail}")
    if on_github:
        for name, state, detail in rows:
            if state != PASSED:
                print(command("error" if state == FAILED else "warning", detail, title=f"{name}: {state}"))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as file:
            file.write(summary(rows))
    return 1 if any(state == FAILED for _, state, _ in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
