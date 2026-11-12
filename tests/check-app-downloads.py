#!/usr/bin/env python3
"""check-app-downloads.py, against made-up publishers.

The check reads the real publishers once a day. Here they are stand-ins, so the
tests can change what a publisher releases and see what the check makes of it.
The catalog, its helpers and check-app-downloads.sh are the real ones. One
made-up web answers them all: FakeWeb from the Gear Lever helper's tests for
what Python asks, and a stand-in curl that reads the same answers from files
for what the catalog asks.

    python3 tests/check-app-downloads.py
"""

import contextlib
import email.message
import gzip
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import struct
import sys
import tarfile
import tempfile
import time
import types
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


# How the check loads a helper, before a test changes that.
LOAD = script.load
# The apps Gear Lever installs. A test in AppDownloads that names no app checks these.
APPIMAGES = tuple(script.load_helper().APPS)
NOT_RUN = "(install script read, not run)"
CHECK = ".github/workflows/check-app-downloads.py"

# The programs the catalog and the stand-ins need. A test's PATH has these and its stand-ins, and
# nothing else of the computer it runs on.
PROGRAMS = ("awk basename bash cat chmod comm cp cut dirname flock grep gzip mkdir mktemp mv python3 readelf "
            "readlink rm sha256sum sleep sort tail tar").split()
BASICS = None


def setUpModule():
    global BASICS
    BASICS = Path(tempfile.mkdtemp(prefix="monolith-app-downloads-programs-"))
    unittest.addModuleCleanup(shutil.rmtree, BASICS, True)
    for program in PROGRAMS:
        found = shutil.which(program)
        if found is None:
            raise FileNotFoundError(f"these tests need {program}")
        (BASICS / program).symlink_to(found)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


# --- what the made-up publishers offer --------------------------------------------------------------

# The start of a program for x86_64 computers, which is all the check reads of one.
PROGRAM = b"\x7fELF\x02\x01\x01" + bytes(9) + (2).to_bytes(2, "little") + (62).to_bytes(2, "little") + bytes(44)

TEA_API = "https://gitea.com/api/v1/repos/gitea/tea/releases/latest"
TEA = "https://dl.gitea.com/tea/0.16.0/tea-0.16.0-linux-amd64"
SUPERFILE_API = "https://api.github.com/repos/yorukot/superfile/releases/latest"
SUPERFILE = "https://github.com/yorukot/superfile/releases/download/v1.6.0/superfile-linux-v1.6.0-amd64.tar.gz"
SUPERFILE_SUMS = "https://github.com/yorukot/superfile/releases/download/v1.6.0/superfile-v1.6.0-checksums.txt"
OMP_API = "https://api.github.com/repos/can1357/oh-my-pi/releases/latest"
OMP = "https://github.com/can1357/oh-my-pi/releases/download/v18.18.9/omp-linux-x64"
OMP_SUMS = "https://github.com/can1357/oh-my-pi/releases/download/v18.18.9/SHA256SUMS.txt"

# APP: (its install script's address, the script, where its newest version is asked, the answer, that version)
INSTALLERS = {
    "claude": ("https://claude.ai/install.sh",
               '#!/bin/bash\nTARGET="$1"\ncase "$TARGET" in stable|latest) ;; *) exit 2 ;; esac\n',
               "https://downloads.claude.ai/claude-code-releases/stable", b"2.1.311\n", "2.1.311"),
    "codex": ("https://chatgpt.com/codex/install.sh",
              '#!/bin/sh\nRELEASE="${CODEX_RELEASE:-latest}"\nBIN_DIR="${CODEX_INSTALL_DIR:-$HOME/.local/bin}"\n'
              'STATE_DIR="${CODEX_HOME:-$HOME/.codex}"\n[ "${CODEX_NON_INTERACTIVE:-false}" = true ] || read -r _\n',
              "https://releases.openai.com/codex/channels/latest",
              json.dumps({"assets": [], "tag_name": "rust-v0.167.2"}).encode(), "0.167.2"),
    "opencode": ("https://opencode.ai/install",
                 '#!/usr/bin/env bash\nrequested_version=${VERSION:-}\nfor argument in "$@"; do\n'
                 '    case "$argument" in --no-modify-path) no_modify_path=true ;; esac\ndone\n',
                 "https://api.github.com/repos/anomalyco/opencode/releases/latest",
                 json.dumps({"tag_name": "v1.19.5"}).encode(), "1.19.5"),
    "herdr": ("https://herdr.dev/install.sh",
              '#!/bin/sh\nset -eu\nINSTALL_DIR="${HERDR_INSTALL_DIR:-$HOME/.local/bin}"\n',
              "https://herdr.dev/latest.json", json.dumps({"version": "0.9.5", "assets": {}}).encode(), "0.9.5"),
}

FLATHUB = "https://dl.flathub.org/repo/"
FLATHUB_REPO = f"{FLATHUB}flathub.flatpakrepo"
FLUXER = "https://pkgs.fluxer.com/flatpak/"
KDE, KDE_NEXT, GNOME = "org.kde.Platform/x86_64/6.10", "org.kde.Platform/x86_64/6.11", "org.gnome.Platform/x86_64/50"
FREEDESKTOP, FREEDESKTOP_NEXT = "org.freedesktop.Platform/x86_64/25.08", "org.freedesktop.Platform/x86_64/26.08"
RUNTIMES = (KDE, KDE_NEXT, GNOME, FREEDESKTOP, FREEDESKTOP_NEXT)

# APP: (Flatpak ID, the address of its .flatpakref, the repository that names, its runtime)
FLATPAKS = {
    "prismlauncher": ("org.prismlauncher.PrismLauncher",
                      f"{FLATHUB}appstream/org.prismlauncher.PrismLauncher.flatpakref", FLATHUB, KDE),
    "sunshine": ("dev.lizardbyte.app.Sunshine", f"{FLATHUB}appstream/dev.lizardbyte.app.Sunshine.flatpakref",
                 FLATHUB, KDE_NEXT),
    "fluxer": ("app.fluxer.Fluxer", f"{FLATHUB}appstream/app.fluxer.Fluxer.flatpakref", FLATHUB, FREEDESKTOP_NEXT),
    "fluxer-canary": ("app.fluxer.FluxerCanary", f"{FLUXER}fluxer-canary.flatpakref", FLUXER, FREEDESKTOP),
}

CRAFTING = re.search(r"^CRAFTING_TOOLS=\((.+)\)$", script.CATALOG.read_text(encoding="utf-8"), re.MULTILINE)[1].split()
# APP: (GitHub repository, the tag of its latest release, the bundle in it, Flatpak ID, its runtime)
BUNDLES = {
    "moonfin": ("Moonfin-Client/Moonfin-Core", "3.0.0", "Moonfin_Linux_v3.0.0.flatpak", "org.moonfin.linux", GNOME),
    "plezy": ("edde746/plezy", "2.26.0", "plezy-linux-x64.flatpak", "com.edde746.plezy", GNOME),
    **{app: (f"storytold/{app}", "v0.23.0", f"{app}-0.23.0-linux-x86_64.flatpak", f"ai.storyteller.{app}",
             FREEDESKTOP) for app in CRAFTING},
}


def pack(kind, value):
    """A value in GVariant's binary form, as GLib writes it and the check's unpack() reads it. A value of
    any type (v) is given as (its type, the value)."""
    if kind[0] in script.FIXED:
        return struct.pack("<" + script.FIXED[kind[0]], value)
    if kind[0] in "sog":
        return value.encode() + b"\0"
    if kind[0] == "v":
        return pack(*value) + b"\0" + value[0].encode()
    if kind == "ay":
        return bytes(value)
    types = [kind[1:]] * len(value) if kind[0] == "a" else script.members(kind[1:-1])
    body, ends = b"", []
    for member, item in zip(types, value, strict=True):
        body += bytes(-len(body) % script.alignment(member)) + pack(member, item)
        ends.append(len(body))
    if kind[0] == "a":
        if script.fixed_size(kind[1:]):
            return body
    elif script.fixed_size(kind):
        return body + bytes(-len(body) % script.alignment(kind)) or b"\0"
    else:
        # A table of where the members end, last one first, without the last member and those of a fixed size.
        ends = [end for member, end in zip(types[:-1], ends) if not script.fixed_size(member)][::-1]
    width = next(width for width in (1, 2, 4, 8) if len(body) + len(ends) * width < 1 << 8 * width)
    return body + b"".join(end.to_bytes(width, "little") for end in ends)


def app_metadata(app_id, runtime):
    return (f"[Application]\nname={app_id}\nruntime={runtime}\nsdk={runtime.replace('Platform', 'Sdk')}\n\n"
            "[Context]\nshared=network;ipc;\n")


def offered(*apps, runtimes=()):
    """What a repository has, as {ref: what its summary says about it}, for apps given as (Flatpak ID, runtime)."""
    refs = {f"app/{app_id}/x86_64/stable": {"metadata": app_metadata(app_id, runtime)} for app_id, runtime in apps}
    refs.update({f"runtime/{runtime}": {"metadata": f"[Runtime]\nname={runtime.split('/')[0]}\n"}
                 for runtime in runtimes})
    return refs


def commit_of(ref):
    return sha256(ref.encode())


def summary_of(refs, arch="x86_64"):
    """The summary for one kind of computer, as Flatpak has written them since 1.10."""
    listed = []
    for ref, about in sorted(refs.items()):
        details = [("xa.data", ("(tts)", (1, 1, about["metadata"])))] if "metadata" in about else []
        details += [(key, ("s", about[key])) for key in ("eol", "eolr") if key in about]
        listed.append((ref.replace("x86_64", arch), (1388, bytes.fromhex(commit_of(ref)), details)))
    return pack(script.SUMMARY, (listed, [("xa.summary-version", ("u", 1))]))


def old_summary_of(refs):
    """The one summary of a repository without an index, which keeps what it knows of the refs in two
    tables of its own."""
    listed = [(ref, (1388, bytes.fromhex(commit_of(ref)), [])) for ref in sorted(refs)]
    cache = [(ref, (1, 1, about["metadata"])) for ref, about in sorted(refs.items()) if "metadata" in about]
    sparse = [(ref, [(key, ("s", about[key])) for key in ("eol", "eolr") if key in about])
              for ref, about in sorted(refs.items()) if "eol" in about or "eolr" in about]
    return pack(script.SUMMARY, (listed, [("xa.cache-version", ("u", 2)),
                                          ("xa.cache", ("v", ("a{s(tts)}", cache))),
                                          ("xa.sparse-cache", ("a{sa{sv}}", sparse))]))


def flatpakref(app_id, repository, **changes):
    keys = {"Name": app_id, "Branch": "stable", "Title": f"{app_id} from a made-up repository", "IsRuntime": "false",
            "Url": repository, "GPGKey": "bWFkZS11cCBrZXkgZm9yIHRoZSB0ZXN0cw==", "RuntimeRepo": FLATHUB_REPO}
    keys.update(changes)
    return "[Flatpak Ref]\n" + "".join(f"{key}={value}\n" for key, value in keys.items() if value is not None)


def bundle_of(app_id, runtime, arch="x86_64", metadata=None):
    """A Flatpak bundle: one file that says what it holds and has the app's files, as flatpak build-bundle
    writes it."""
    about = [("flatpak", ("u", 1)), ("ref", ("s", f"app/{app_id}/{arch}/stable")),
             ("metadata", ("s", app_metadata(app_id, runtime) if metadata is None else metadata)),
             ("runtime-repo", ("s", FLATHUB_REPO)), ("ostree.endianness", ("y", ord("l"))),
             ("deltas/ab/cdef/0", ("(uay)", (0, app_id.encode() * 40)))]
    commit = ([], b"", [], f"Export {app_id}", f"Name: {app_id}\nArch: {arch}\nBranch: stable\n", 0, bytes(32),
              bytes(32))
    return pack(script.BUNDLE, (about, 0, b"", bytes(32), commit, b"", [(0, bytes(32), 1, 1, b"")], []))


def tarball(files):
    packed = io.BytesIO()
    with tarfile.open(fileobj=packed, mode="w:gz") as archive:
        for name, content in files.items():
            member = tarfile.TarInfo(name)
            member.size, member.mode = len(content), 0o755
            archive.addfile(member, io.BytesIO(content))
    return packed.getvalue()


THEMES = tarball({f"ghostty/Made-up Theme {number}": f"background = {number:06x}\n".encode() for number in range(8)})
THEMES_URL = LOAD("ghostty_themes", script.THEMES).URL


# The catalog's curl. It answers from the files Checks.export() writes: the answer is in a file named by
# the SHA256 of the address, and files beside it say why asking fails, or that it never ends.
CURL = r"""#!/bin/bash
output='' url='' header=''
while [ "$#" -gt 0 ]; do
    case "$1" in
        --output) output="$2"; shift 2 ;;
        --header) header="$2"; shift 2 ;;
        --connect-timeout|--max-time|--speed-limit|--speed-time) shift 2 ;;
        --fail|--location|--silent|--show-error) shift ;;
        https://*) url="$1"; shift ;;
        *) echo "curl: the test's curl doesn't know $1" >&2; exit 2 ;;
    esac
done
printf 'curl\t%s\t%s\t%s\n' "$url" "$header" "${GITHUB_TOKEN:+has the token}" >>'@LOG@'
answer='@SITE@'/"$(printf %s "$url" | sha256sum | cut -c 1-64)"
if [ -e "$answer.hangs" ]; then
    sleep 2
    : >'@SITE@/went on after it was stopped'
fi
if [ -e "$answer.fails" ]; then
    read -r status why <"$answer.fails"
    if [ -e "$answer.once" ]; then rm -f -- "$answer.fails" "$answer.once"; fi
    echo "curl: ($status) $why" >&2
    exit "$status"
fi
if [ ! -e "$answer" ]; then
    echo "curl: (6) unexpected request in test: $url" >&2
    exit 6
fi
if [ -n "$output" ]; then cp -- "$answer" "$output"; else cat -- "$answer"; fi
"""


class Checks(unittest.TestCase):
    """Every app can be installed until a test changes what its publisher offers."""

    maxDiff = None

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="monolith-app-downloads-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.downloads = self.tmp / "downloads"
        self.downloads.mkdir()
        self.page = self.tmp / "summary.md"
        self.site = self.tmp / "web"
        # Every request, in the order they were made: who asked, the address, the header it came with
        # (curl's Authorization, Python's User-Agent), and whether curl's environment had the token.
        self.log = self.tmp / "requests"
        self.logged = 0
        self.agents = {}
        # What a made-up install script or program leaves behind when something runs it.
        self.ran = self.tmp / "something that was downloaded ran"
        self.pauses = []
        self.headers = {}
        self.told = False
        self.fails_once = {}
        self.hangs = set()
        self.pins = (sha256(THEMES), 8)
        self.web = fixtures.FakeWeb()
        self.stand_ins = self.tmp / "bin"
        self.stand_ins.mkdir()
        for name, text in (("uname", '#!/bin/bash\nif [ "${1-}" = -m ]; then echo x86_64; else echo Linux; fi\n'),
                           ("curl", CURL.replace("@LOG@", str(self.log)).replace("@SITE@", str(self.site)))):
            (self.stand_ins / name).write_text(text)
            (self.stand_ins / name).chmod(0o755)

        publish(self.web, WOWUP_CF, ["2.25.5", "2.25.4"])
        publish(self.web, R2MODMAN, ["3.2.21", "3.2.20"])
        publish(self.web, SIDRA, ["1.3.0", "1.2.1"])
        serve_epic(self.web, EPIC_BUILD)
        self.serve_tea()
        self.serve_superfile()
        self.serve_omp()
        for app in INSTALLERS:
            self.serve_installer(app)
        self.repositories = {
            FLATHUB: offered(*((app_id, runtime) for app_id, _, repository, runtime in FLATPAKS.values()
                               if repository == FLATHUB), runtimes=RUNTIMES),
            FLUXER: offered((FLATPAKS["fluxer-canary"][0], FREEDESKTOP)),
        }
        self.web.add(FLATHUB_REPO, f"[Flatpak Repo]\nTitle=Flathub\nUrl={FLATHUB}\nGPGKey=bWFkZS11cA==\n".encode())
        for repository in self.repositories:
            self.serve_repository(repository)
        for app_id, ref, repository, _ in FLATPAKS.values():
            self.web.add(ref, flatpakref(app_id, repository).encode())
        for app in BUNDLES:
            self.serve_bundle(app)
        self.web.add(THEMES_URL, THEMES)

    # --- the publishers ---

    def serve_tea(self, *, program=PROGRAM, sums=None):
        self.web.add(TEA_API, json.dumps({"name": "0.16.0", "tag_name": "v0.16.0"}).encode())
        self.web.add(TEA, program)
        self.web.add(f"{TEA}.sha256",
                     (f"{sha256(program)}  tea-0.16.0-linux-amd64\n" if sums is None else sums).encode())

    def serve_superfile(self, *, program=PROGRAM, inside="dist/superfile-linux-v1.6.0-amd64/spf", sums=None):
        archive = tarball({inside: program})
        self.web.add(SUPERFILE_API, json.dumps({"tag_name": "v1.6.0", "assets": []}).encode())
        self.web.add(SUPERFILE, archive)
        self.web.add(SUPERFILE_SUMS, (f"{'0' * 64}  superfile-linux-v1.6.0-arm64.tar.gz\n"
                                      f"{sha256(archive)}  superfile-linux-v1.6.0-amd64.tar.gz\n"
                                      if sums is None else sums).encode())

    def serve_omp(self, *, program=PROGRAM, sums=None):
        self.web.add(OMP_API, json.dumps({"tag_name": "v18.18.9"}).encode())
        self.web.add(OMP, program)
        self.web.add(OMP_SUMS, (f"{'1' * 64}  omp-linux-arm64\n{sha256(program)}  omp-linux-x64\n"
                                if sums is None else sums).encode())

    def serve_installer(self, app, text=None):
        """An app's install script, and the answer to which version is its newest. Run, the script would
        leave self.ran behind."""
        url, usual, newest, answer, _ = INSTALLERS[app]
        self.web.add(url, ((usual if text is None else text) + f': >"{self.ran}"\n').encode())
        self.web.add(newest, answer)

    def serve_repository(self, url, *, index=True):
        """A Flatpak repository that has self.repositories[url]: its index and the summaries that names,
        or without index only the one summary older repositories have."""
        refs = self.repositories[url]
        for route in [route for route in self.web.routes if route.startswith(f"{url}summar")]:
            del self.web.routes[route]
        if not index:
            self.web.add(f"{url}summary.idx", status=404)
            self.web.add(f"{url}summary", old_summary_of(refs))
            return
        summaries = {arch: summary_of(refs, arch) for arch in ("aarch64", "x86_64")}
        self.web.add(f"{url}summary.idx", pack(script.SUMMARY_INDEX, (
            [(arch, (hashlib.sha256(summary).digest(), [], [])) for arch, summary in summaries.items()],
            [("xa.cache-version", ("u", 2))])))
        for summary in summaries.values():
            self.web.add(f"{url}summaries/{sha256(summary)}.gz", gzip.compress(summary))

    def summary_url(self, url):
        """Where a repository's summary for x86_64 is."""
        return f"{url}summaries/{sha256(summary_of(self.repositories[url]))}.gz"

    def serve_bundle(self, app, *, data=None, **changes):
        """An app's latest release on GitHub with its Flatpak bundle. changes are to how GitHub lists the
        bundle. Returns the bundle's address."""
        repository, tag, name, app_id, runtime = BUNDLES[app]
        data = bundle_of(app_id, runtime) if data is None else data
        url = f"https://github.com/{repository}/releases/download/{tag}/{name}"
        asset = {"name": name, "state": "uploaded", "size": len(data), "digest": f"sha256:{sha256(data)}",
                 "browser_download_url": url, **changes}
        signature = {"name": f"{name}.asc", "state": "uploaded", "size": 3, "digest": f"sha256:{sha256(b'asc')}",
                     "browser_download_url": f"{url}.asc"}
        self.web.add(f"https://api.github.com/repos/{repository}/releases/latest",
                     json.dumps({"tag_name": tag, "prerelease": False, "assets": [signature, asset]}).encode())
        self.web.add(url, data)
        return url

    def export(self):
        """The made-up web as files, for the catalog's curl."""
        shutil.rmtree(self.site, True)
        self.site.mkdir()
        for url, route in self.web.routes.items():
            answer = self.site / sha256(url.encode())
            answer.write_bytes(route["body"])
            why = self.fails_once.get(url)
            if route["error"]:
                why = f"6 {route['error']}"
            elif route["status"] != 200:
                why = f"22 The requested URL returned error: {route['status']}"
            if why:
                Path(f"{answer}.fails").write_text(f"{why}\n")
            if url in self.fails_once:
                Path(f"{answer}.once").touch()
            if url in self.hangs:
                Path(f"{answer}.hangs").touch()

    # --- the check ---

    def load(self, name, path):
        """The catalog's helpers, as the check loads them. The themes helper pins the made-up themes
        unless a test wants its own pins."""
        module = LOAD(name, path)
        if path == script.THEMES and self.pins:
            module.SHA256, module.EXPECTED_COUNT = self.pins
        return module

    def urlopen(self, request, *arguments, **options):
        """What Python asks goes into the same list as what curl asks."""
        with self.log.open("a") as log:
            log.write(f"urllib\t{request.full_url}\t{request.get_header('User-agent', '')}\t\n")
        return self.web.urlopen(request, *arguments, **options)

    def read_log(self):
        """Everything that was asked so far becomes self.web.requests, in the order it was asked."""
        lines = [line.split("\t") for line in self.log.read_text().splitlines()] if self.log.exists() else []
        for who, url, header, told in lines[self.logged:]:
            if who == "curl":
                self.headers[url] = header
                self.told = self.told or bool(told)
                if url not in self.web.routes:
                    self.web.unexpected.append(url)
            else:
                self.agents[url] = header
        self.logged = len(lines)
        self.web.requests[:] = [url for _, url, _, _ in lines]

    def check(self, *apps, known=None, github=False, token=None, path=None, patient=True, **environment):
        """Run the check outside GitHub Actions, or in a run of it with github. Without patient a failure
        counts at once. Returns its exit code, what it printed and its results."""
        self.export()
        env = {key: value for key, value in os.environ.items() if not key.startswith("GITHUB_")}
        env["PATH"] = path or f"{self.stand_ins}{os.pathsep}{BASICS}"
        env.update(environment)
        if github:
            env.update(GITHUB_ACTIONS="true", GITHUB_STEP_SUMMARY=str(self.page))
        if token:
            env["GITHUB_TOKEN"] = token
        out = io.StringIO()
        try:
            # Only the check's own pauses are skipped: subprocess sleeps too, while it waits for a program.
            with mock.patch.dict(os.environ, env, clear=True), \
                    mock.patch("urllib.request.urlopen", self.urlopen), \
                    mock.patch.object(script, "time", types.SimpleNamespace(sleep=self.pauses.append)), \
                    mock.patch.object(script, "PAUSES", script.PAUSES if patient else ()), \
                    mock.patch.object(tempfile, "tempdir", str(self.downloads)), \
                    mock.patch.object(script, "KNOWN", known or {}), \
                    mock.patch.object(script, "load", self.load), \
                    contextlib.redirect_stdout(out), warnings.catch_warnings():
                # An HTTP error keeps the server's answer open, and nothing closes it before the check ends.
                warnings.simplefilter("ignore", ResourceWarning)
                code = script.main(list(apps))
        finally:
            self.read_log()
        return code, out.getvalue(), results(out.getvalue())

    def failure(self, app):
        """An app's check fails on its first try. Returns why."""
        code, out, rows = self.check(app, patient=False)
        self.assertEqual(code, 1, out)
        (state, detail), = rows.values()
        self.assertEqual(state, "FAILED", out)
        self.assertEqual(os.listdir(self.downloads), [], "what was downloaded is deleted")
        self.assertFalse(self.ran.exists(), "something that was downloaded was run")
        return detail

    def passes(self, app):
        """An app's check passes. Returns what it says an install would get."""
        code, out, rows = self.check(app)
        (state, detail), = rows.values()
        self.assertEqual((code, state), (0, "passed"), out)
        self.assertEqual(self.web.unexpected, [])
        self.assertEqual(self.pauses, [])
        self.assertEqual(os.listdir(self.downloads), [], "what was downloaded is deleted")
        self.assertFalse(self.ran.exists(), "something that was downloaded was run")
        return detail

    def changed_catalog(self, old, new):
        """The check with a catalog in which one thing is different."""
        copy = Path(tempfile.mkdtemp(dir=self.tmp)) / "libexec"
        shutil.copytree(script.CATALOG.parent, copy)
        text = (copy / "software").read_text(encoding="utf-8")
        self.assertEqual(text.count(old), 1, old)
        (copy / "software").write_text(text.replace(old, new), encoding="utf-8")
        return mock.patch.multiple(script, CATALOG=copy / "software", ROOT=copy.parent)


class AppDownloads(Checks):
    """The tests the check had when the AppImages were all it knew. One that names no app checks those.
    What they show holds for every app: how often it is tried, which problems are known, and how a run
    on GitHub shows its results."""

    def check(self, *apps, **options):
        return super().check(*(apps or APPIMAGES), **options)

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
        known = ", ".join(script.every_app(script.load_helper()))
        self.assertIn(f"unknown app: wowup (the check knows {known})", said.getvalue())

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
        without = self.tmp / "no readelf"
        without.mkdir()
        for program in PROGRAMS:
            if program != "readelf":
                (without / program).symlink_to(BASICS / program)
        code, out, rows = self.check("sidra", path=f"{self.stand_ins}{os.pathsep}{without}")
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
        apps = script.every_app(script.load_helper())
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
        row = "A|B <i>", "FAILED", "x | y\nz <b>&"
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
        reads = (script.HELPER, script.CATALOG, script.COMMON, script.THEMES, script.STAND_INS)
        self.assertEqual(named, {".github/workflows/check-app-downloads.yml", str(script.SCRIPT),
                                 *(str(path.relative_to(ROOT)) for path in reads)})
        for path in named:
            self.assertTrue((ROOT / path).is_file(), path)
        self.assertIn(f"\n        run: python3 {script.SCRIPT}\n", text)
        self.assertEqual(str(script.SCRIPT), CHECK)

    def test_nothing_is_left_beside_the_scripts(self):
        self.check()
        for folder in (WORKFLOWS, script.HELPER.parent, ROOT / "tests"):
            self.assertFalse((folder / "__pycache__").exists(), folder)


class EveryApp(Checks):
    """Which apps a run checks: the catalog's, and any other the Gear Lever helper installs."""

    def test_a_run_without_names_checks_every_app(self):
        code, out, rows = self.check()
        self.assertEqual(code, 0, out)
        names = script.every_app(script.load_helper())
        found = {
            "tea": "0.16.0 (tea-0.16.0-linux-amd64)",
            "superfile": "v1.6.0 (superfile-linux-v1.6.0-amd64.tar.gz)",
            "omp": "v18.18.9 (omp-linux-x64)",
            **{app: f"{version} {NOT_RUN}" for app, (*_, version) in INSTALLERS.items()},
            **{app: f"stable ({'pkgs.fluxer.com' if repository == FLUXER else 'dl.flathub.org'}, commit "
                    f"{commit_of(f'app/{app_id}/x86_64/stable')[:12]})"
               for app, (app_id, _, repository, _) in FLATPAKS.items()},
            **{app: f"{tag} ({file})" for app, (_, tag, file, _, _) in BUNDLES.items()},
            "ghostty-themes": "20260216-151611-fc73ce3 (8 themes)",
        }
        self.assertEqual(list(rows), list(names.values()), "the catalog's apps in its order, then the helper's")
        self.assertEqual({state for state, _ in rows.values()}, {"passed"})
        # What an AppImage's row says is in AppDownloads.test_apps_that_can_be_installed_pass.
        self.assertEqual({app: rows[name][1] for app, name in names.items() if app not in APPIMAGES}, found)
        self.assertEqual(self.web.unexpected, [])
        self.assertEqual(self.pauses, [])
        self.assertEqual(sorted(self.web.requests), sorted(set(self.web.requests)), "each address is asked once")
        # GitHub's API answers the runner's token 1,000 times an hour, and someone without a token 60 times.
        # A run by hand has to fit the second.
        self.assertLess(len([url for url in self.web.requests if url.startswith("https://api.github.com/")]), 60)
        self.assertEqual(os.listdir(self.downloads), [], "the downloads are deleted")
        self.assertFalse(self.ran.exists(), "something that was downloaded was run")
        self.assertFalse(self.told, "curl's environment had the token")
        self.assertEqual(set(self.headers.values()), {""}, "nothing is signed in without a token")
        # Each publisher can tell who is asking.
        self.assertEqual({agent.partition("/")[0] for agent in self.agents.values()}, {
            "monolith-app-downloads", "monolith-ghostty-themes", *(f"monolith-{app}" for app in APPIMAGES)})
        self.assertEqual(self.agents[FLATHUB_REPO], "monolith-app-downloads/1 (+https://github.com/monolithlinux/"
                                                    "monolith)")
        self.assertFalse(any(line.startswith("::") for line in out.splitlines()), out)
        self.assertFalse(self.page.exists())
        for folder in (WORKFLOWS, script.HELPER.parent, ROOT / "tests"):
            self.assertFalse((folder / "__pycache__").exists(), folder)

    def test_every_app_has_made_up_publishers(self):
        self.assertEqual(sorted([*Programs.APPS, *INSTALLERS, *FLATPAKS, *BUNDLES, *APPIMAGES, "ghostty-themes"]),
                         sorted(script.every_app(script.load_helper())))

    def test_an_app_the_catalog_does_not_offer_is_checked_while_the_helper_installs_it(self):
        """An app can leave the catalog while its publisher is away. The helper keeps it, and the check
        goes on asking, so we see the day it can be installed again."""
        apps = list(script.load_helper().APPS.values())
        offered = {"tea": "Tea", apps[0].id: "As the catalog names it"}
        with mock.patch.object(script, "catalog_apps", side_effect=lambda: dict(offered)):
            code, out, rows = self.check()
            self.assertEqual(code, 0, out)
            self.assertEqual(list(rows), ["Tea", "As the catalog names it", *(app.name for app in apps[1:])])
            code, out, rows = self.check(apps[-1].id)
            self.assertEqual((code, list(rows)), (0, [apps[-1].name]), out)
            with self.assertRaises(SystemExit) as stopped, contextlib.redirect_stderr(io.StringIO()) as said:
                self.check("superfile")
        self.assertEqual(stopped.exception.code, 2)
        self.assertIn(f"unknown app: superfile (the check knows tea, {', '.join(app.id for app in apps)})",
                      said.getvalue())

    def test_apps_of_any_kind_are_checked_in_the_order_they_are_named(self):
        code, out, rows = self.check("ghostty-themes", "sidra", "tea", "plezy", "herdr", "fluxer")
        self.assertEqual(code, 0, out)
        self.assertEqual(list(rows), ["Ghostty Themes", "Sidra", "Tea", "Plezy", "Herdr", "Fluxer"])
        self.assertFalse([url for url in self.web.requests if "WowUp" in url or "superfile" in url or "claude" in url])


class ByTheCatalog(Checks):
    """The apps the catalog installs with bash: its own code runs, up to the step that would install."""

    def test_the_catalog_gets_a_home_of_its_own(self):
        mine = self.tmp / "mine"
        places = {name: str(mine / name) for name in ("HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CONFIG_HOME",
                                                        "CODEX_HOME", "CODEX_INSTALL_DIR", "HERDR_INSTALL_DIR")}
        code, out, rows = self.check("tea", "codex", "opencode", "herdr", "plezy", "sunshine", VERSION="9.9.9",
                                     CODEX_RELEASE="9.9.9", MONOLITH_PROGRESS="1", **places)
        self.assertEqual(code, 0, out)
        self.assertFalse(mine.exists(), "the catalog wrote where this computer's own settings point")
        self.assertEqual(rows["Codex CLI"], ("passed", f"0.167.2 {NOT_RUN}"))
        self.assertIn("flatpak install --user --noninteractive --assumeyes "
                      "https://dl.flathub.org/repo/appstream/dev.lizardbyte.app.Sunshine.flatpakref\n", out)

    def test_the_catalog_has_to_end_with_the_line_that_runs_it(self):
        with self.changed_catalog('\nmain "$@"\n', '\nmain "$@"\n# something after it\n'):
            self.assertEqual(self.failure("tea"), 'libexec/software no longer ends with main "$@", so '
                             f"{CHECK} can't load its functions.")
        self.assertEqual(self.web.requests, [])

    def test_an_install_that_installs_nothing_fails(self):
        with self.changed_catalog("\ninstall_tea() {\n", "\ninstall_tea() {\n    return 0\n"):
            self.assertEqual(self.failure("tea"), "The catalog's install of Tea ended without installing anything. "
                             f"{CHECK} expects it to get as far as a program, an install script or Flatpak.")

    def test_a_step_the_check_has_no_stand_in_for_fails(self):
        ref = FLATPAKS["prismlauncher"][1]
        with self.changed_catalog('flatpak install --user "${FLATPAK_QUIET[@]}" --assumeyes "$ref" 9>&- ||',
                                  'flatpak upgrade --user "$ref" 9>&- ||'):
            self.assertEqual(self.failure("prismlauncher"),
                             f"The check has no stand-in for: flatpak upgrade --user {ref}")
        with self.changed_catalog('        sh "$tmp/install.sh" 9>&-', '        sudo sh "$tmp/install.sh" 9>&-'):
            self.assertRegex(self.failure("herdr"), r"^The check has no stand-in for: sudo sh /\S+/install\.sh$")
        with self.changed_catalog('        bash "$ACTIVE_TEMP/install.sh" --no-modify-path 9>&-',
                                  '        nice bash "$ACTIVE_TEMP/install.sh" --no-modify-path 9>&-'):
            self.assertRegex(self.failure("opencode"),
                             r"^The check has no stand-in for: env nice bash /\S+/install\.sh --no-modify-path$")
        with self.changed_catalog('    bash "$ACTIVE_TEMP/install.sh" stable 9>&-', '    bash -c "exit 0" 9>&-'):
            self.assertEqual(self.failure("claude"), "The check has no stand-in for: bash -c exit 0")
        self.assertFalse(self.ran.exists())

    def test_an_install_that_downloads_nothing_the_check_sees_fails(self):
        with self.changed_catalog('    curl_fetch download "$url" "$tmp/$filename"\n'
                                  '    curl_fetch metadata "$url.sha256" "$tmp/$filename.sha256"\n'
                                  '    verify_listed_checksum "$tmp" "$filename.sha256" "$filename"\n',
                                  '    : >"$tmp/$filename"\n'):
            self.assertEqual(self.failure("tea"), "The catalog got as far as installing without a download that "
                             f"{CHECK} saw. It sees what the catalog downloads with curl.")

    def test_a_catalog_that_cannot_list_its_apps_ends_the_check(self):
        with self.changed_catalog('        __ids) for tool in', '        __ids) die "no list today"; for tool in'), \
                self.assertRaises(SystemExit) as stopped:
            self.check("tea")
        self.assertEqual(stopped.exception.code, "Could not list the catalog's apps with libexec/software __ids: "
                                                 "Error: no list today")
        self.assertEqual(self.web.requests, [])

    def test_a_failure_of_the_catalogs_counts_after_three_tries(self):
        self.web.add(TEA, status=503)
        code, out, rows = self.check("tea")
        self.assertEqual(code, 1, out)
        self.assertEqual(rows["Tea"], ("FAILED", f"Could not get {TEA}: curl: (22) The requested URL returned "
                                                 "error: 503"))
        self.assertEqual(self.web.requests, [TEA_API, TEA] * 3)
        self.assertEqual(self.pauses, [15, 60])
        self.assertEqual(out.count("Error: Could not get"), 3)
        self.assertEqual(os.listdir(self.downloads), [], "each try's home directory is deleted")

    def test_a_failure_of_the_catalogs_that_passes_on_another_try_does_not_count(self):
        self.fails_once[TEA] = "56 Recv failure: Connection reset by peer"
        code, out, rows = self.check("tea")
        self.assertEqual(code, 0, out)
        self.assertEqual(rows["Tea"], ("passed", "0.16.0 (tea-0.16.0-linux-amd64)"))
        self.assertEqual(self.web.requests, [TEA_API, TEA, TEA_API, TEA, f"{TEA}.sha256"])
        self.assertEqual(self.pauses, [15])
        self.assertIn(f"Error: Could not get {TEA}: curl: (56) Recv failure: Connection reset by peer\n"
                      "Trying again in 15 seconds.\n", out)

    def test_a_known_problem_of_an_app_the_catalog_installs_is_judged_the_same(self):
        moved = {"tea": ("returned error: 404", "Tea's download moved.")}
        code, out, rows = self.check("tea", known=moved)
        self.assertEqual((code, rows["Tea"]), (1, (
            "FAILED", f"Tea 0.16.0 (tea-0.16.0-linux-amd64) passes again. Take its entry out of KNOWN in {CHECK}, "
                      "which says: Tea's download moved.")), out)
        self.web.add(TEA, status=404)
        code, out, rows = self.check("tea", known=moved)
        self.assertEqual((code, rows["Tea"]), (0, ("known problem", "Tea's download moved.")), out)
        self.web.add(TEA, status=500)
        code, out, rows = self.check("tea", known=moved)
        self.assertEqual((code, rows["Tea"]), (1, (
            "FAILED", f"Could not get {TEA}: curl: (22) The requested URL returned error: 500 This is not the "
                      f"problem KNOWN lists for it in {CHECK}, which says: Tea's download moved.")), out)

    def test_the_catalog_signs_in_to_githubs_api_and_nowhere_else(self):
        with mock.patch.object(urllib.request, "install_opener"):
            code, out, _ = self.check("superfile", "tea", "opencode", "herdr", "photocraft", token="made-up-token")
        self.assertEqual(code, 0, out)
        api = {SUPERFILE_API, INSTALLERS["opencode"][2], "https://api.github.com/repos/storytold/photocraft/"
                                                         "releases/latest"}
        self.assertEqual({url for url in self.headers if url.startswith("https://api.github.com/")}, api)
        self.assertEqual({url: header for url, header in self.headers.items() if header},
                         dict.fromkeys(api, "Authorization: Bearer made-up-token"))
        self.assertEqual(len(self.headers), 12, "and nine addresses that aren't GitHub's API")
        self.assertFalse(self.told, "curl's environment had the token")
        self.assertNotIn("made-up-token", out)
        self.headers.clear()
        code, out, _ = self.check("superfile", "opencode")
        self.assertEqual(code, 0, out)
        self.assertEqual(set(self.headers.values()), {""}, "nothing is signed in without a token")

    def test_a_run_on_github_shows_what_the_catalog_said_in_the_apps_group(self):
        gone = f"Could not get {TEA}: curl: (22) The requested URL returned error: 404"
        self.web.add(TEA, status=404)
        code, out, rows = self.check("tea", "superfile", github=True, patient=False)
        self.assertEqual(code, 1, out)
        lines = out.splitlines()
        self.assertEqual([line for line in lines if line.startswith("::group::") or line == "::endgroup::"],
                         ["::group::Tea", "::endgroup::", "::group::Superfile", "::endgroup::"])
        self.assertLess(lines.index("::group::Tea"), lines.index("==> Installing Tea"))
        self.assertLess(lines.index(f"Error: {gone}"), lines.index("::endgroup::"))
        self.assertEqual([line for line in lines if line.startswith(("::error", "::warning"))],
                         [f"::error title=Tea%3A FAILED::{gone}"])
        self.assertEqual(self.page.read_text().splitlines()[4:], [
            f"| Tea | FAILED | {gone} |", "| Superfile | passed | v1.6.0 (superfile-linux-v1.6.0-amd64.tar.gz) |"])

    def test_a_catalog_that_never_ends_is_stopped(self):
        self.hangs.add(TEA)
        with mock.patch.object(script, "TIME_LIMIT", 0.5):
            self.assertEqual(self.failure("tea"), "The catalog was still busy after 0 minutes and was stopped.")
        # The curl that hangs would go on after two seconds and say so.
        time.sleep(2.2)
        self.assertEqual([path.name for path in self.site.iterdir() if "stopped" in path.name], [],
                         "what the catalog started is stopped with it")


class Programs(Checks):
    """Tea, Superfile and Oh My Pi: a program from a release, with the publisher's list of checksums."""

    # APP: (how its publisher is served, where its release is asked, the download, its checksums, the file)
    APPS = {"tea": ("serve_tea", TEA_API, TEA, f"{TEA}.sha256", "tea-0.16.0-linux-amd64"),
            "superfile": ("serve_superfile", SUPERFILE_API, SUPERFILE, SUPERFILE_SUMS,
                          "superfile-linux-v1.6.0-amd64.tar.gz"),
            "omp": ("serve_omp", OMP_API, OMP, OMP_SUMS, "omp-linux-x64")}

    def each(self):
        """Every app of this kind, each with the publishers as they were before the test changed them."""
        for app, (serve, *rest) in self.APPS.items():
            self.setUp()
            yield app, getattr(self, serve), *rest

    def test_the_catalog_asks_for_the_release_downloads_it_and_compares_its_checksum(self):
        for app, _, api, download, sums, file in self.each():
            with self.subTest(app):
                self.assertTrue(self.passes(app).endswith(f"({file})"))
                self.assertEqual(self.web.requests, [api, download, sums])

    def test_a_file_that_is_gone_fails(self):
        for app, _, api, download, sums, _ in self.each():
            with self.subTest(app):
                self.web.add(download, status=404)
                self.assertEqual(self.failure(app),
                                 f"Could not get {download}: curl: (22) The requested URL returned error: 404")
                self.assertEqual(self.web.requests, [api, download])

    def test_a_checksum_that_does_not_match_fails(self):
        for app, _, _, download, _, file in self.each():
            with self.subTest(app):
                self.web.add(download, self.web.routes[download]["body"] + b"something else")
                self.assertEqual(self.failure(app), f"{file} failed its published SHA256 check; nothing was installed")

    def test_a_list_of_checksums_without_the_file_fails(self):
        for app, serve, _, _, sums, file in self.each():
            with self.subTest(app):
                serve(sums=f"{'2' * 64}  another-file\n")
                saved_as = "checksums.txt" if app == "superfile" else Path(sums).name
                self.assertEqual(self.failure(app), f"{saved_as} does not list exactly one checksum for {file}; "
                                 "nothing was installed")
        self.setUp()
        self.serve_omp(sums=f"{sha256(PROGRAM)}  omp-linux-x64\n" * 2)
        self.assertEqual(self.failure("omp"), "SHA256SUMS.txt does not list exactly one checksum for omp-linux-x64; "
                         "nothing was installed")

    def test_an_answer_without_a_release_fails(self):
        whose = {"tea": "the latest Tea release", "superfile": "the latest release for yorukot/superfile",
                 "omp": "the latest release for can1357/oh-my-pi"}
        for app, _, api, _, _, _ in self.each():
            for answer in (b"{}", b"<html>Not here</html>", b'{"tag_name": "../../etc"}'):
                with self.subTest(app, answer=answer):
                    self.web.add(api, answer)
                    self.assertEqual(self.failure(app), f"could not resolve {whose[app]}")
            self.assertEqual(set(self.web.requests), {api})

    def test_a_download_that_is_not_a_program_fails_and_is_not_run(self):
        instead = f'#!/bin/sh\n: >"{self.ran}"\necho 1.0.0\n'
        for app, serve, _, _, _, file in self.each():
            with self.subTest(app):
                serve(program=instead.replace(str(self.ran.parent), str(self.tmp)).encode())
                self.assertEqual(self.failure(app), f"{'spf' if app == 'superfile' else file} is not a program for "
                                 "x86_64 computers.")

    def test_only_a_program_for_x86_64_computers_will_do(self):
        def changed(at, value):
            return PROGRAM[:at] + value + PROGRAM[at + len(value):]
        others = {"for ARM computers": changed(18, (183).to_bytes(2, "little")), "32-bit": changed(4, b"\x01"),
                  "big-endian": changed(5, b"\x02"), "a library's part": changed(16, (1).to_bytes(2, "little")),
                  "no program": changed(0, b"\x7fELG"), "cut off": PROGRAM[:19], "empty": b""}
        for name, program in others.items():
            with self.subTest(name):
                self.setUp()
                self.serve_tea(program=program)
                self.assertEqual(self.failure("tea"), "tea-0.16.0-linux-amd64 is not a program for x86_64 computers.")
        self.setUp()
        self.serve_tea(program=changed(16, (3).to_bytes(2, "little")))  # one that can be loaded anywhere
        self.passes("tea")

    def test_an_archive_without_the_program_fails(self):
        self.serve_superfile(inside="superfile-linux-v1.6.0-amd64/spf")
        self.assertRegex(self.failure("superfile"), r"^install: cannot stat '/\S+/dist/superfile-linux-v1\.6\.0-amd64/"
                                                    r"spf': No such file or directory$")


class InstallScripts(Checks):
    """Claude Code, Codex CLI, OpenCode and Herdr: the publisher's install script, read and not run."""

    NAMES = {"claude": "Claude Code", "codex": "Codex CLI", "opencode": "OpenCode", "herdr": "Herdr"}

    def each(self):
        """Every app of this kind, each with the publishers as they were before the test changed them."""
        for app, name in self.NAMES.items():
            self.setUp()
            yield app, name, *INSTALLERS[app]

    def test_the_script_is_downloaded_and_read_and_the_newest_version_asked(self):
        for app, _, url, _, newest, _, version in self.each():
            with self.subTest(app):
                self.assertEqual(self.passes(app), f"{version} {NOT_RUN}")
                self.assertEqual(self.web.requests, [url, newest])

    def test_the_check_sees_what_the_catalog_gives_each_script(self):
        code, out, _ = self.check(*self.NAMES, CODEX_RELEASE="9.9.9", VERSION="9.9.9")
        self.assertEqual(code, 0, out)
        self.assertEqual([line for line in out.splitlines() if line.startswith("The catalog would now run")], [
            "The catalog would now run: bash install.sh stable",
            "The catalog would now run: bash install.sh, with CODEX_HOME, CODEX_INSTALL_DIR, CODEX_NON_INTERACTIVE set",
            "The catalog would now run: bash install.sh --no-modify-path",
            "The catalog would now run: sh install.sh, with HERDR_INSTALL_DIR set"])

    def test_a_script_that_is_gone_fails(self):
        for app, _, url, _, newest, _, _ in self.each():
            with self.subTest(app):
                self.web.add(url, status=404)
                self.assertEqual(self.failure(app),
                                 f"Could not get {url}: curl: (22) The requested URL returned error: 404")
                self.assertEqual(self.web.requests, [url])

    def test_a_page_in_place_of_the_script_fails(self):
        for app, _, url, _, _, _, _ in self.each():
            with self.subTest(app):
                self.web.add(url, b"<!DOCTYPE html>\n<html><body>Just a moment...</body></html>\n")
                self.assertEqual(self.failure(app), f"{url} is no install script: it starts with "
                                 "'<!DOCTYPE html>\\n<html><body>Just a momen'.")

    def test_a_script_that_is_cut_off_fails(self):
        for app, _, url, text, _, _, _ in self.each():
            with self.subTest(app):
                self.serve_installer(app, text + 'if [ -n "$HOME" ]; then\n    echo "half of the script"\n')
                shell = "sh" if app == "herdr" else "bash"
                self.assertRegex(self.failure(app), rf"^{re.escape(url)} is not a script {shell} can read: "
                                 r"install script: .*(?i:syntax error)")

    def test_a_script_for_sh_is_read_the_way_monoliths_sh_reads_it(self):
        """Monolith's sh is bash. Where the check runs, sh may be a shell that refuses what bash takes."""
        self.serve_installer("herdr", INSTALLERS["herdr"][1] + 'parts=(a b)\nfor ((n = 0; n < 2; n++)); do\n'
                             '    echo "${parts[n]}"\ndone\n')
        self.assertEqual(self.passes("herdr"), f"0.9.5 {NOT_RUN}")

    def test_a_script_that_no_longer_knows_a_setting_of_the_catalogs_fails(self):
        given = {"codex": ("CODEX_HOME", "CODEX_INSTALL_DIR", "CODEX_NON_INTERACTIVE"), "herdr": ("HERDR_INSTALL_DIR",)}
        for app, settings in given.items():
            url, text = INSTALLERS[app][:2]
            for setting in settings:
                with self.subTest(setting):
                    self.setUp()
                    self.assertIn(setting, text)
                    self.serve_installer(app, text.replace(setting, f"{setting}_2"))
                    self.assertEqual(self.failure(app), f"The catalog gives {url} the setting {setting}, but the "
                                     "script no longer mentions it.")

    def test_a_script_that_no_longer_knows_an_argument_of_the_catalogs_fails(self):
        for app, argument, instead in (("claude", "stable", "release"),
                                       ("opencode", "--no-modify-path", "--keep-path")):
            with self.subTest(app):
                url, text = INSTALLERS[app][:2]
                self.assertIn(argument, text)
                self.serve_installer(app, text.replace(argument, instead))
                self.assertEqual(self.failure(app), f"The catalog runs {url} with {argument}, but the script no "
                                 "longer mentions it.")

    def test_only_whole_words_count_as_mentioned(self):
        for text, mentioned in (('DIR="${CODEX_HOME:-x}"', True), ("CODEX_HOME=1", True), ("$CODEX_HOME/bin", True),
                                ("MY_CODEX_HOME=1", False), ("CODEX_HOME_DIR=1", False), ("CODEX_HOMES", False),
                                ("x-CODEX_HOME", False), ("", False)):
            self.assertEqual(script.mentions(text, "CODEX_HOME"), mentioned, text)
        self.assertTrue(script.mentions("case $1 in --no-modify-path) ;; esac", "--no-modify-path"))
        self.assertFalse(script.mentions("--no-modify-path-at-all", "--no-modify-path"))
        self.assertTrue(script.mentions("stable|latest", "stable"))
        self.assertFalse(script.mentions("unstable", "stable"))

    def test_an_app_whose_newest_version_the_catalog_cannot_ask_for_fails(self):
        with self.changed_catalog("        herdr)\n            # Herdr's installer and",
                                  "        herdr-next)\n            # Herdr's installer and"):
            self.assertEqual(self.failure("herdr"), "The catalog can't tell which Herdr is the newest, so `monolith "
                             "update` runs the installer every time.")
        self.assertEqual(self.web.requests, [INSTALLERS["herdr"][0]])

    def test_a_newest_version_the_catalog_cannot_read_fails(self):
        for app, name, _, _, newest, _, _ in self.each():
            then = f"`monolith update` asks there for the newest {name}, and runs the installer every time it gets " \
                   "no answer."
            for answer in (b"<html>Not here</html>", b'{"tag_name": "nightly", "version": "nightly"}', b"2.1\n"):
                with self.subTest(app, answer=answer):
                    self.web.add(newest, answer)
                    self.assertEqual(self.failure(app), f"{newest} answered, but not with a version the catalog "
                                     f"can read. {then}")
            with self.subTest(app, answer=404):
                self.web.add(newest, status=404)
                self.assertEqual(self.failure(app), f"Could not get {newest}: curl: (22) The requested URL returned "
                                 f"error: 404. {then}")


class FlatpakRefs(Checks):
    """Prism Launcher, Sunshine, Fluxer and Fluxer Canary: a .flatpakref and the repository it names."""

    def each(self):
        """Every app of this kind, each with the publishers as they were before the test changed them."""
        for app, (app_id, url, repository, runtime) in FLATPAKS.items():
            self.setUp()
            yield app, app_id, url, repository, runtime, f"app/{app_id}/x86_64/stable"

    def test_the_ref_and_the_summary_flatpak_looks_an_app_up_in_are_read(self):
        for app, _, url, repository, _, ref in self.each():
            with self.subTest(app):
                host = "pkgs.fluxer.com" if app == "fluxer-canary" else "dl.flathub.org"
                self.assertEqual(self.passes(app), f"stable ({host}, commit {commit_of(ref)[:12]})")
                asked = [url, f"{repository}summary.idx", self.summary_url(repository), FLATHUB_REPO]
                if app == "fluxer-canary":
                    asked += [f"{FLATHUB}summary.idx", self.summary_url(FLATHUB)]
                self.assertEqual(self.web.requests, asked)

    def test_a_repository_is_read_once_for_all_its_apps(self):
        code, out, _ = self.check(*FLATPAKS, "photocraft", "plezy")
        self.assertEqual(code, 0, out)
        for url in (f"{FLATHUB}summary.idx", self.summary_url(FLATHUB), f"{FLUXER}summary.idx", FLATHUB_REPO):
            self.assertEqual(self.web.requests.count(url), 1, url)

    def test_a_ref_that_is_gone_fails(self):
        for app, _, url, _, _, _ in self.each():
            with self.subTest(app):
                self.web.add(url, status=404)
                self.assertEqual(self.failure(app), f"Could not get {url}: HTTP Error 404: Error.")
                self.assertEqual(self.web.requests, [url])

    def test_an_answer_that_is_too_long_is_refused(self):
        url = FLATPAKS["sunshine"][1]
        with mock.patch.object(script, "MAX_ANSWER", len(self.web.routes[url]["body"]) - 1):
            self.assertEqual(self.failure("sunshine"), f"{url} answers with more than 0 MiB.")
        with mock.patch.object(script, "MAX_ANSWER", len(self.web.routes[self.summary_url(FLATHUB)]["body"])):
            self.passes("sunshine")

    def test_a_ref_for_another_app_fails(self):
        for app, app_id, url, repository, _, _ in self.each():
            with self.subTest(app):
                self.web.add(url, flatpakref(f"{app_id}.Next", repository).encode())
                self.assertEqual(self.failure(app), f"{url} installs {app_id}.Next, but the catalog expects {app_id}.")

    def test_a_ref_flatpak_would_not_take_fails(self):
        app_id, url, repository, _ = FLATPAKS["sunshine"]
        for text, why in ((b"<html>Not here</html>", f"{url} has no [Flatpak Ref] group."),
                          (flatpakref(app_id, None).encode(), f"{url} names no Url."),
                          (flatpakref(None, repository).encode(), f"{url} names no Name."),
                          (flatpakref(app_id, repository, Version="2").encode(),
                           f"{url} is a .flatpakref of version 2; Flatpak reads only version 1.")):
            with self.subTest(why):
                self.web.add(url, text)
                self.assertEqual(self.failure("sunshine"), why)
        self.web.add(url, flatpakref(app_id, repository, Version="1").encode())
        self.passes("sunshine")

    def test_an_app_the_repository_no_longer_has_fails(self):
        for app, _, url, repository, _, ref in self.each():
            with self.subTest(app):
                del self.repositories[repository][ref]
                self.serve_repository(repository)
                self.assertEqual(self.failure(app),
                                 f"{repository} doesn't list {ref}, so Flatpak can't install it from {url}.")

    def test_a_branch_the_repository_does_not_have_fails(self):
        app_id, url, repository, _ = FLATPAKS["fluxer-canary"]
        self.web.add(url, flatpakref(app_id, repository, Branch="canary").encode())
        self.assertEqual(self.failure("fluxer-canary"), f"{repository} doesn't list app/{app_id}/x86_64/canary, so "
                         f"Flatpak can't install it from {url}.")
        self.web.add(url, flatpakref(app_id, repository, Branch=None).encode())
        self.assertEqual(self.failure("fluxer-canary"), f"{repository} doesn't list app/{app_id}/x86_64/master, so "
                         f"Flatpak can't install it from {url}.")

    def test_an_app_that_is_end_of_life_fails(self):
        for index in (True, False):
            for app, _, _, repository, _, ref in self.each():
                with self.subTest(app, index=index):
                    self.repositories[repository][ref]["eol"] = "This app is no longer maintained."
                    self.serve_repository(repository, index=index)
                    self.assertEqual(self.failure(app),
                                     f"{ref} is end-of-life in {repository}: This app is no longer maintained.")

    def test_an_app_that_was_replaced_by_another_fails(self):
        for index in (True, False):
            for app, app_id, _, repository, _, ref in self.each():
                with self.subTest(app, index=index):
                    self.repositories[repository][ref].update(eol="It has a new name.",
                                                              eolr=f"app/{app_id}.Next/x86_64/stable")
                    self.serve_repository(repository, index=index)
                    self.assertEqual(self.failure(app), f"{ref} is end-of-life in {repository} and replaced by "
                                     f"app/{app_id}.Next/x86_64/stable. A new install would get that app instead, "
                                     "which the catalog doesn't know.")

    def test_a_runtime_no_repository_has_fails(self):
        for app, _, _, repository, runtime, ref in self.each():
            with self.subTest(app):
                del self.repositories[FLATHUB][f"runtime/{runtime}"]
                self.serve_repository(FLATHUB)
                where = FLATHUB if repository == FLATHUB else f"{FLUXER} or {FLATHUB}"
                self.assertEqual(self.failure(app), f"{ref} needs the runtime {runtime}, which isn't in {where}.")

    def test_a_runtime_in_the_apps_own_repository_will_do(self):
        app_id, url, repository, runtime = FLATPAKS["fluxer-canary"]
        self.repositories[FLUXER][f"runtime/{runtime}"] = {"metadata": ""}
        self.serve_repository(FLUXER)
        self.web.add(url, flatpakref(app_id, repository, RuntimeRepo=None).encode())
        self.passes("fluxer-canary")
        self.assertEqual(self.web.requests, [url, f"{FLUXER}summary.idx", self.summary_url(FLUXER)])

    def test_an_app_whose_runtime_the_repository_does_not_tell_fails(self):
        for index in (True, False):
            for app, app_id, _, repository, _, ref in self.each():
                for metadata in (None, f"[Application]\nname={app_id}\n"):
                    with self.subTest(app, index=index, metadata=metadata):
                        self.repositories[repository][ref] = {} if metadata is None else {"metadata": metadata}
                        self.serve_repository(repository, index=index)
                        self.assertEqual(self.failure(app), f"{ref} doesn't say which runtime it needs.")

    def test_a_repository_without_an_index_is_read_from_its_one_summary(self):
        for app, _, url, repository, _, ref in self.each():
            with self.subTest(app):
                self.serve_repository(repository, index=False)
                self.assertIn(commit_of(ref)[:12], self.passes(app))
                self.assertEqual(self.web.requests[:3], [url, f"{repository}summary.idx", f"{repository}summary"])

    def test_a_repository_whose_summary_cannot_be_had_fails(self):
        gone = {f"{FLUXER}summary.idx": 503, self.summary_url(FLUXER): 404}
        for url, status in gone.items():
            with self.subTest(url):
                self.setUp()
                self.web.add(url, status=status)
                self.assertEqual(self.failure("fluxer-canary"), f"Could not get {url}: HTTP Error {status}: Error.")
                self.assertNotIn(f"{FLUXER}summary", self.web.requests, "only a missing index is made up for")
        self.setUp()
        self.serve_repository(FLUXER, index=False)
        self.web.add(f"{FLUXER}summary", status=404)
        self.assertEqual(self.failure("fluxer-canary"), f"Could not get {FLUXER}summary: HTTP Error 404: Error.")

    def test_a_summary_that_is_not_the_one_the_index_names_fails(self):
        url = self.summary_url(FLUXER)
        self.web.add(url, gzip.compress(summary_of(offered(("app.fluxer.FluxerCanary", FREEDESKTOP_NEXT)))))
        self.assertEqual(self.failure("fluxer-canary"),
                         f"{url} is not the summary {FLUXER}summary.idx names: its SHA256 differs.")

    def test_a_summary_that_cannot_be_read_fails(self):
        index, summary = f"{FLUXER}summary.idx", self.summary_url(FLUXER)
        cut_off = gzip.compress(summary_of(self.repositories[FLUXER]))[:-9]
        for url, body in ((index, b"<html>Not here</html>"), (index, b""), (summary, b"<html>Not here</html>"),
                          (summary, cut_off)):
            with self.subTest(url, body=body[:12]):
                self.setUp()
                self.web.add(url, body)
                why = self.failure("fluxer-canary")
                self.assertTrue(why.startswith(f"{url} is not a summary this check can read: "), why)
        for body in (gzip.compress(b"<html>Not here</html>"), b""):
            with self.subTest(body=body[:12]):
                self.setUp()
                self.web.add(summary, body)
                self.assertEqual(self.failure("fluxer-canary"),
                                 f"{summary} is not the summary {FLUXER}summary.idx names: its SHA256 differs.")
        self.setUp()
        self.web.add(f"{FLUXER}summary.idx", pack(script.SUMMARY_INDEX, ([("aarch64", (bytes(32), [], []))], [])))
        self.assertEqual(self.failure("fluxer-canary"), f"{FLUXER}summary.idx names no summary for x86_64.")

    def test_a_summary_of_a_version_the_check_does_not_know_fails(self):
        newer = pack(script.SUMMARY, ([], [("xa.summary-version", ("u", 2))]))
        url = f"{FLUXER}summaries/{sha256(newer)}.gz"
        self.web.add(f"{FLUXER}summary.idx", pack(script.SUMMARY_INDEX, (
            [("x86_64", (hashlib.sha256(newer).digest(), [], []))], [])))
        self.web.add(url, gzip.compress(newer))
        self.assertEqual(self.failure("fluxer-canary"),
                         f"{url} is a summary of version 2, which this check can't read.")


class Bundles(Checks):
    """Moonfin, Plezy and the Crafting Apps: a Flatpak bundle from the latest release on GitHub."""

    APPS = ("moonfin", "plezy", "photocraft", "cadcraft")

    def each(self):
        """Some apps of this kind, each with the publishers as they were before the test changed them."""
        for app in self.APPS:
            self.setUp()
            repository, tag, file, app_id, runtime = BUNDLES[app]
            yield (app, repository, tag, file, app_id, runtime,
                   f"https://github.com/{repository}/releases/download/{tag}/{file}")

    def test_the_catalog_asks_github_downloads_the_bundle_and_compares_its_sha256(self):
        for app, repository, tag, file, _, _, url in self.each():
            with self.subTest(app):
                self.assertEqual(self.passes(app), f"{tag} ({file})")
                self.assertEqual(self.web.requests, [
                    f"https://api.github.com/repos/{repository}/releases/latest", url, FLATHUB_REPO,
                    f"{FLATHUB}summary.idx", self.summary_url(FLATHUB)])

    def test_a_release_without_the_bundle_fails(self):
        for app, repository, tag, file, _, _, url in self.each():
            with self.subTest(app):
                self.serve_bundle(app, name=file.replace(".flatpak", "-x86_64.flatpak"))
                self.assertEqual(self.failure(app), f"{repository} {tag} does not provide exactly one {file} asset")
                self.assertNotIn(url, self.web.requests)

    def test_a_bundle_github_does_not_vouch_for_fails(self):
        for app, repository, tag, file, _, _, url in self.each():
            for changes, why in (({"digest": None}, f"publishes no SHA256 digest for {file}; refusing an unverified "
                                                    "download"),
                                 ({"digest": "sha512:" + "0" * 128}, f"publishes no SHA256 digest for {file}; "
                                                                     "refusing an unverified download"),
                                 ({"state": "starter"}, f"does not provide an uploaded {file} at {url}"),
                                 ({"browser_download_url": f"https://example.org/{file}"},
                                  f"does not provide an uploaded {file} at {url}")):
                with self.subTest(app, changes=changes):
                    self.serve_bundle(app, **changes)
                    self.assertEqual(self.failure(app), f"{repository} {tag} {why}")
                    self.assertNotIn(url, self.web.requests)

    def test_a_bundle_that_is_not_the_one_github_lists_fails(self):
        for app, _, _, file, _, _, url in self.each():
            with self.subTest(app):
                self.web.add(url, self.web.routes[url]["body"] + b"something else")
                self.assertEqual(self.failure(app), f"{file} failed its SHA256 check; nothing was installed")

    def test_a_bundle_that_is_gone_fails(self):
        for app, _, _, _, _, _, url in self.each():
            with self.subTest(app):
                self.web.add(url, status=404)
                self.assertEqual(self.failure(app),
                                 f"Could not get {url}: curl: (22) The requested URL returned error: 404")

    def test_an_answer_from_github_the_catalog_cannot_read_fails(self):
        repository = BUNDLES["plezy"][0]
        url = f"https://api.github.com/repos/{repository}/releases/latest"
        for answer, why in ((b"<html>Not here</html>", f"the latest {repository} release metadata is not valid JSON"),
                            (b"[]", f"the latest {repository} release metadata is malformed"),
                            (b'{"assets": []}', f"the latest {repository} release has no valid tag"),
                            (b'{"tag_name": "2.26.0"}', f"{repository} 2.26.0 lists no assets")):
            with self.subTest(answer):
                self.web.add(url, answer)
                self.assertEqual(self.failure("plezy"), why)
        self.web.add(url, status=403)
        self.assertEqual(self.failure("plezy"),
                         f"Could not get {url}: curl: (22) The requested URL returned error: 403")

    def test_a_bundle_of_another_app_fails(self):
        for app, _, _, file, app_id, runtime, _ in self.each():
            with self.subTest(app):
                self.serve_bundle(app, data=bundle_of(f"{app_id}.Next", runtime))
                self.assertEqual(self.failure(app), f"{file} installs app/{app_id}.Next/x86_64/stable, but the "
                                 f"catalog expects app/{app_id}/x86_64.")
        self.setUp()
        self.serve_bundle("plezy", data=bundle_of("com.edde746.plezy", GNOME, arch="aarch64"))
        self.assertEqual(self.failure("plezy"), "plezy-linux-x64.flatpak installs app/com.edde746.plezy/aarch64/"
                         "stable, but the catalog expects app/com.edde746.plezy/x86_64.")

    def test_a_bundle_whose_runtime_flathub_does_not_have_fails(self):
        for app, _, _, file, app_id, _, _ in self.each():
            with self.subTest(app):
                self.serve_bundle(app, data=bundle_of(app_id, "org.freedesktop.Platform/x86_64/27.08"))
                self.assertEqual(self.failure(app), f"{file} needs the runtime org.freedesktop.Platform/x86_64/27.08, "
                                 f"which isn't in {FLATHUB}.")

    def test_a_bundle_that_does_not_tell_its_runtime_fails(self):
        self.serve_bundle("plezy", data=bundle_of("com.edde746.plezy", GNOME, metadata="[Application]\nname=x\n"))
        self.assertEqual(self.failure("plezy"), "plezy-linux-x64.flatpak doesn't say which runtime it needs.")

    def test_a_file_that_is_no_bundle_fails(self):
        whole = bundle_of("com.edde746.plezy", GNOME)
        nameless = pack(script.BUNDLE, ([("metadata", ("s", app_metadata("com.edde746.plezy", GNOME)))], 0, b"", b"",
                                        ([], b"", [], "", "", 0, b"", b""), b"", [], []))
        for name, data in (("a page", b"<html>Not here</html>" * 40), ("cut off", whole[:-9]), ("empty", b""),
                           ("without a ref", nameless)):
            with self.subTest(name):
                self.serve_bundle("plezy", data=data)
                self.assertTrue(self.failure("plezy").startswith(
                    "plezy-linux-x64.flatpak is not a Flatpak bundle this check can read: "))

    def test_flathub_has_to_be_where_the_catalog_adds_it_from(self):
        for answer, why in (({"status": 404}, f"Could not get {FLATHUB_REPO}: HTTP Error 404: Error."),
                            ({"body": b"[Flatpak Repo]\nTitle=Flathub\n"}, f"{FLATHUB_REPO} names no Url."),
                            ({"body": b"<html>Not here</html>"}, f"{FLATHUB_REPO} has no [Flatpak Repo] group.")):
            with self.subTest(why):
                self.web.add(FLATHUB_REPO, **answer)
                self.assertEqual(self.failure("plezy"), why)

    def test_the_catalog_has_to_add_a_repository_for_the_runtime(self):
        with self.changed_catalog('        flatpak remote-add --user --if-not-exists flathub "$FLATHUB_REPO" 9>&-\n',
                                  "        :\n"):
            self.assertEqual(self.failure("plezy"), "The catalog no longer adds a repository for "
                             f"plezy-linux-x64.flatpak's runtime, so {CHECK} can't tell where Flatpak would look "
                             "for it.")


class Themes(Checks):
    """Ghostty Themes: the one download the catalog pins."""

    def test_the_helper_downloads_its_archive_and_checks_it(self):
        self.assertEqual(self.passes("ghostty-themes"), "20260216-151611-fc73ce3 (8 themes)")
        self.assertEqual(self.web.requests, ["https://deps.files.ghostty.org/ghostty-themes-release-"
                                             "20260216-151611-fc73ce3.tgz"])

    def test_the_checksum_is_the_one_the_helper_pins(self):
        self.pins = None
        self.assertEqual(self.failure("ghostty-themes"),
                         "Ghostty theme download failed its pinned SHA256 check; existing themes were kept")

    def test_an_archive_that_changed_fails(self):
        self.web.add(THEMES_URL, tarball({"ghostty/Another Theme": b"background = 000000\n"}))
        code, out, rows = self.check("ghostty-themes")
        self.assertEqual((code, rows["Ghostty Themes"]), (1, (
            "FAILED", "Ghostty theme download failed its pinned SHA256 check; existing themes were kept")), out)
        self.assertEqual(self.web.requests, [THEMES_URL] * 3)
        self.assertEqual(self.pauses, [15, 60])

    def test_an_archive_with_other_themes_than_expected_fails(self):
        self.pins = (self.pins[0], 463)
        self.assertEqual(self.failure("ghostty-themes"), "Expected 463 Ghostty themes, found 8")
        other = tarball({"ghostty/../Made-up Theme": b"background = 000000\n"})
        self.web.add(THEMES_URL, other)
        self.pins = (sha256(other), 1)
        self.assertEqual(self.failure("ghostty-themes"),
                         "Unsafe or unexpected Ghostty theme archive entry: 'ghostty/../Made-up Theme'")
        self.web.add(THEMES_URL, b"no archive")
        self.pins = (sha256(b"no archive"), 1)
        self.assertEqual(self.failure("ghostty-themes"), "not a gzip file")

    def test_an_archive_that_is_gone_fails(self):
        self.web.add(THEMES_URL, status=404)
        self.assertEqual(self.failure("ghostty-themes"), f"Could not get {THEMES_URL}: HTTP Error 404: Error.")


class Formats(unittest.TestCase):
    """The binary form Flatpak keeps summaries and bundles in, and its files of keys."""

    # The examples of the GVariant specification:
    # https://developer.gnome.org/documentation/specifications/gvariant-specification-1.0.html#examples
    EXAMPLES = (
        ("s", b"hello world\0", "hello world"),
        ("ab", "01 00 00 01 01", [True, False, False, True, True]),
        ("(si)", b"foo\0" + bytes.fromhex("ff ff ff ff 04"), ("foo", -1)),
        ("as", b"i\0can\0has\0strings?\0" + bytes.fromhex("02 06 0a 13"), ["i", "can", "has", "strings?"]),
        ("(yy)", "70 80", (0x70, 0x80)),
        ("(iy)", "60 00 00 00 70 00 00 00", (96, 0x70)),
        ("(yi)", "70 00 00 00 60 00 00 00", (0x70, 96)),
        ("a(iy)", "60 00 00 00 70 00 00 00 88 02 00 00 f7 00 00 00", [(96, 0x70), (648, 0xf7)]),
        ("ai", "04 00 00 00 02 01 00 00", [4, 258]),
        ("{si}", b"a key\0\0\0" + bytes.fromhex("02 02 00 00 06"), ("a key", 514)),
        # That page prints the next two without their last 15 and their 0d. GLib writes them like this.
        ("a(si)", b"hi\0\0" + bytes.fromhex("fe ff ff ff 03 00 00 00") + b"bye\0"
         + bytes.fromhex("ff ff ff ff 04 09 15"), [("hi", -2), ("bye", -1)]),
        ("((ys)as)", b"ican\0has\0strings?\0" + bytes.fromhex("04 0d 05"), ((0x69, "can"), ["has", "strings?"])),
    )

    def test_the_specifications_examples_are_read_as_it_says(self):
        for kind, data, value in self.EXAMPLES:
            data = bytes.fromhex(data) if isinstance(data, str) else data
            with self.subTest(kind):
                self.assertEqual(script.unpack(kind, data), value)
                self.assertEqual(pack(kind, value), data, "and the tests write it the same way")
        self.assertEqual(bytes(script.unpack("ay", bytes.fromhex("04 05 06 07"))), b"\x04\x05\x06\x07")

    def test_a_value_of_any_type_says_its_type_at_its_end(self):
        self.assertEqual(script.unpack("v", b"foo\0\xff\xff\xff\xff\x04\0(si)"), ("foo", -1))
        self.assertEqual(script.unpack("a{sv}", pack("a{sv}", [("ref", ("s", "app/x")), ("size", ("t", 2 ** 40)),
                                                             ("both", ("v", ("as", ["a", "b"])))])),
                         [("ref", "app/x"), ("size", 2 ** 40), ("both", ["a", "b"])])

    def test_tables_of_ends_grow_with_what_they_are_in(self):
        # GLib 2.90 writes these sizes.
        self.assertEqual(len(pack("as", ["a" * 253])), 255, "253 letters, their end, and where that is")
        self.assertEqual(len(pack("as", ["a" * 254])), 257, "what is longer than 255 bytes needs two for an end")
        self.assertEqual(len(pack("as", ["a" * 65532])), 65535)
        self.assertEqual(len(pack("as", ["a" * 65533])), 65538, "and what is longer than 65535 bytes needs four")
        for length in (0, 1, 200, 253, 254, 255, 256, 300, 65000, 65532, 65533, 65536, 70000):
            with self.subTest(length):
                value = [("a" * (length // 2), 1), ("b" * (length - length // 2), 2)]
                self.assertEqual(script.unpack("a(su)", pack("a(su)", value)), value)
                self.assertEqual(script.unpack("as", pack("as", ["a" * length])), ["a" * length])

    def test_data_that_is_cut_off_or_of_another_type_is_refused(self):
        whole = summary_of(offered(("org.example.App", FREEDESKTOP), runtimes=(FREEDESKTOP,)))
        self.assertEqual(len(script.unpack(script.SUMMARY, whole)[0]), 2)
        self.assertEqual(script.unpack(script.SUMMARY, b"\0"), ([], []), "a summary of nothing")
        for data in (whole[:-1], whole[:40], whole[7:], b"", b"<html>Not here</html>", bytes(64), b"\xff" * 64):
            with self.subTest(data[:12]), self.assertRaises((ValueError, struct.error)):
                script.unpack(script.SUMMARY, data)
        for kind, data in (("s", b"no end"), ("s", b""), ("v", b"no type"), ("ai", b"\1\2\3"), ("mi", b"\1\0\0\0"),
                           ("as", b"a\0\x09"), ("as", b"a\0\0"), ("(su)", b"a\0\0\0\1\0\0\0\x09"),
                           ("aay", b"abcde\x05\x03")):
            with self.subTest(kind=kind, data=data), self.assertRaises((ValueError, struct.error)):
                script.unpack(kind, data)

    def test_a_file_of_keys_gives_those_of_one_group(self):
        text = "# a comment\n[Flatpak Ref]\nName=org.example.App\nUrl = https://example.org/repo/ \n\n" \
               "GPGKey=abc=\n[Other Group]\nName=another\n[Flatpak Ref]\nBranch=stable\n"
        self.assertEqual(script.keyfile(text, "Flatpak Ref"), {"Name": "org.example.App", "Branch": "stable",
                                                               "Url": "https://example.org/repo/", "GPGKey": "abc="})
        self.assertEqual(script.keyfile(text, "Other Group"), {"Name": "another"})
        self.assertIsNone(script.keyfile(text, "Flatpak Repo"))
        self.assertIsNone(script.keyfile("<html>Not here</html>", "Flatpak Ref"))
        self.assertEqual(script.keyfile("[Application]\n", "Application"), {})

    def test_a_downloads_address_says_which_release_it_is(self):
        self.assertEqual(script.release_of(TEA), "0.16.0 (tea-0.16.0-linux-amd64)")
        self.assertEqual(script.release_of("https://github.com/o/r/releases/download/v1%2B2/a%20b.flatpak?x=1"),
                         "v1+2 (a b.flatpak)")


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
