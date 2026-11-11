#!/usr/bin/env python3
"""monolith update, with stand-ins for every program it runs.

    python3 tests/monolith-update.py        run the tests
    python3 tests/monolith-update.py demo   play a slow run in this terminal
    python3 tests/monolith-update.py demo catalog-fails='Error: No room left'
                                            the same, with scenario files (NAME=TEXT)

The stand-ins log their arguments to one file and answer from files in a
scenario folder, so each test sets up only what it changes.
"""
import fcntl
import json
import os
import pty
import re
import select
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import time
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SOURCE = REPO / "files/system/usr/libexec/monolith/update"
LOGOS = REPO / "files/system/usr/share/fastfetch/logos"
ESCAPE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[@-Z\\-_])")

BOOTED = {"booted": True, "staged": False, "version": "44.20261031.0", "checksum": "aaaa",
          "container-image-reference": "ostree-image-signed:docker://ghcr.io/monolithlinux/kde:latest"}
STAGED = {"booted": False, "staged": True, "version": "44.20261101.0", "checksum": "bbbb",
          "container-image-reference": "ostree-image-signed:docker://ghcr.io/monolithlinux/kde:latest"}

# Each stand-in logs "NAME ARGS" to $CALLS and reads its answers from $SCENARIO.
STAND_INS = {
    "rpm-ostree": r'''
case "$1" in
  status)
    n="$(cat "$SCENARIO/busy" 2>/dev/null || echo 0)"
    if [ "$n" -gt 0 ]; then echo $((n - 1)) >"$SCENARIO/busy"; cat "$SCENARIO/status-busy.json"; exit 0; fi
    if [ -e "$SCENARIO/upgraded" ]; then cat "$SCENARIO/status-after.json"; else cat "$SCENARIO/status.json"; fi ;;
  upgrade)
    code="$(cat "$SCENARIO/upgrade-exit" 2>/dev/null || echo 0)"
    case "$code" in
      0)
        echo "Pulling manifest: ostree-image-signed:docker://ghcr.io/monolithlinux/kde:latest"
        echo "Importing: ostree-image-signed:docker://ghcr.io/monolithlinux/kde:latest (digest: sha256:abc)"
        echo "ostree chunk layers needed: 3 (846.2 MB)"
        for k in 1 2 3; do printf '[%s/3] Fetching ostree chunk sha256:%s (282.1 MB)...' "$k" "$k"; sleep "${DELAY:-0}"; echo done; done
        echo "Staging deployment...done"
        echo "  Upgraded: kernel-cachyos 6.17.1-1.fc44 -> 6.17.2-1.fc44"
        echo "            mesa-dri-drivers 25.2.4-1.fc44 -> 25.2.5-1.fc44"
        echo "            plasma-workspace 6.5.0-1.fc44 -> 6.5.1-1.fc44"
        echo "            zlib-ng 2.2.4-1.fc44 -> 2.2.5-1.fc44"
        echo "     Added: python3-foo-1.0-1.fc44.noarch"
        echo 'Run "systemctl reboot" to start a reboot'
        touch "$SCENARIO/upgraded" ;;
      77) echo "No upgrade available." ;;
      *) echo "error: Failed to fetch manifest: connection refused" ;;
    esac
    exit "$code" ;;
  db)
    echo "ostree diff commit from: booted deployment (aaaa)"
    echo "ostree diff commit to:   pending deployment (bbbb)"
    echo "Upgraded:"
    echo "  kernel-cachyos 6.17.1-1.fc44 -> 6.17.3-1.fc44"
    echo "  systemd 258.1-1.fc44 -> 258.2-1.fc44" ;;
esac''',
    # flatpak update -y to a pipe lists its changes in tab-separated rows, then
    # prints a line as each starts and as it moves (REAL_FLATPAK_* below are
    # real runs). Sizes and speeds have a no-break space before the unit.
    "flatpak": r'''
[ "$1" != update ] || printf '%s LC_ALL=%s LANGUAGE=%s\n' "$2" "${LC_ALL:-}" "${LANGUAGE:-}" >>"$CALLS-env"
[ ! -e "$SCENARIO/flatpak-$1-$2-fails" ] || { echo "error: Unable to load summary from remote flathub"; exit 1; }
if [ "$1 $2" = "update --user" ] && [ -e "$SCENARIO/flatpak-replay" ]; then
  cat "$SCENARIO/flatpak-replay"; exit "$(cat "$SCENARIO/flatpak-replay-exit" 2>/dev/null || echo 0)"
fi
case "$1 $2" in
  "update --user")
    printf 'Looking for updates…\n\n\n'
    printf ' 1.\t   \torg.kde.okular\tstable\tu\tflathub\t< 41.6\xc2\xa0MB\n'
    printf ' 2.\t   \torg.kde.Platform\t6.10\ti\tflathub\t< 312.0\xc2\xa0MB\n\n\n'
    printf 'Updating 1/2…\n'
    for p in 0 35 70 100; do printf 'Updating 1/2… ████████ %3d%%  6.1\xc2\xa0MB/s\n' "$p"; sleep "${TICK:-0}"; done
    printf 'Installing 2/2…\n'
    for p in 0 20 45 70 90 100; do printf 'Installing 2/2… ████████ %3d%%  8.4\xc2\xa0MB/s\n' "$p"; sleep "${TICK:-0}"; done
    echo "Changes complete." ;;
  "update --system")
    printf 'Looking for updates…\n\n\n'
    printf ' 1.\t   \torg.mozilla.Thunderbird\tstable\tu\tflathub\t< 88.0\xc2\xa0MB\n\n\n'
    printf 'Updating…\n'
    for p in 0 50 100; do printf 'Updating… ████████ %3d%%  7.0\xc2\xa0MB/s\n' "$p"; sleep "${TICK:-0}"; done
    echo "Updates complete." ;;
  "uninstall --user") echo "Uninstalling runtime/org.kde.Platform/x86_64/6.9" ;;
  "list --user") printf 'org.kde.okular\tstable\tOkular\norg.kde.Platform\t6.9\tKDE Application Platform\n'
                 printf 'org.test.Hello\tstable\tHello World\norg.test.Platform\t1\tTest Platform\n' ;;
  "list --system") printf 'org.mozilla.Thunderbird\tstable\tThunderbird\n' ;;
  "run com.vysp3r.ProtonPlus")
    echo "Updating all runners..."
    printf 'Updating GE-Proton Latest...\r'
    for p in 10 55 100; do printf '\r\033[2KUpdating GE-Proton10-25... %s%% (9.1 MB/s) [ETA: 2s]\r' "$p"; sleep "${TICK:-0}"; done
    sleep "${DELAY:-0}"; printf '\r\033[2K\r'
    echo "Successfully updated GE-Proton Latest" ;;
  "run it.mijorus.gearlever") echo "No updates available" ;;
esac''',
    "systemctl": r'''
if [ "$1 $2" = "is-active --quiet" ] || [ "$2 $3" = "is-active --quiet" ]; then
  unit="${*: -1}"
  n="$(cat "$SCENARIO/active-$unit" 2>/dev/null || echo 0)"
  [ "$n" -gt 0 ] && { echo $((n - 1)) >"$SCENARIO/active-$unit"; exit 0; }
  exit 3
fi''',
    "fwupdmgr": r'''
case "$1" in
  refresh) echo "Updating lvfs"; exit 0 ;;
  get-updates)
    if [ -e "$SCENARIO/firmware-update" ]; then
      echo '{"Devices": [{"Name": "System Firmware", "Releases": [{"Version": "1.2"}]}, {"Name": "Mouse"}]}'
    else
      echo '{"Devices": []}'
    fi ;;
esac''',
    "podman": r'''echo fedora; echo arch''',
    "distrobox": r'''
for box in fedora arch; do
  printf >&2 "\033[1;31m Upgrading %s...\n\033[0m" "$box"; sleep "${DELAY:-0}"
  echo "Upgraded: 12 packages"; sleep "${TICK:-0}"
  echo "Complete!"
done
[ ! -e "$SCENARIO/distrobox-fails" ] || { echo "error: arch: pacman failed"; exit 1; }''',
    "rustup": r'''echo "  stable-x86_64-unknown-linux-gnu updated - rustc 1.91.1 (ed61e7d7e 2025-11-07) (from rustc 1.91.0 (f8297e351 2025-10-28))"''',
    "npm": r'''
case "$1" in
  prefix) echo "$HOME/.local" ;;
  update) echo "changed 2 packages in 3s" ;;
esac''',
    "gh": r'''
case "$1 $2" in
  "extension upgrade") echo "[gh-dash]: upgraded from v4.1.0 to v4.2.0" ;;
  "skill --help") exit 0 ;;
  "skill update") echo "All skills are up to date." ;;
esac''',
}

# What the stand-ins and the terminal tests run besides the stand-ins.
UTILS = ("cat", "sleep", "touch", "stty")

CATALOG = r'''
printf 'software %s progress=%s locale=%s language=%s\n' "$*" "${MONOLITH_PROGRESS:-}" "${LC_ALL:-}" "${LANGUAGE:-}" >>"$CALLS"
[ "$1" = update ] || exit 0
[ "${MONOLITH_PROGRESS:-}" != 1 ] || echo "Plan: Sidra | Epic Games Launcher"
printf '\n(1/2) Sidra — Update\n'
if [ -e "$SCENARIO/catalog-fails" ]; then
  # Sidra can't update, and says what the file says.
  sleep "${DELAY:-0}"
  cat "$SCENARIO/catalog-fails"
  printf '\n(2/2) Epic Games Launcher — Update\n'
  sleep "${DELAY:-0}"
  echo "Epic Games Launcher 17.2.1 is already up to date."
  printf '\nResults:\n  %-19s %s\n  %-19s %s\n' Sidra "Failed (exit 1)" "Epic Games Launcher" "Up to date"
  printf '\nSome items did not complete; see the messages above.\n'
  exit 1
fi
printf '\n==> Downloading Sidra 1.5.0\n'
for p in 12.5 47.3 81.0 100.0; do printf '\r#######################                           %s%%' "$p"; sleep "${TICK:-0}"; done
printf '\n'
sleep "${DELAY:-0}"
echo "Updated Sidra from 1.4.2 to 1.5.0 at $HOME/AppImages/Sidra.AppImage."
printf '\n(2/2) Epic Games Launcher — Update\n'
sleep "${DELAY:-0}"
echo "Epic Games Launcher 17.2.1 is already up to date."
printf '\nResults:\n  %-19s %s\n  %-19s %s\n' Sidra Updated "Epic Games Launcher" "Up to date"
'''


# Lines from real runs of `flatpak update --user -y` into a pipe, with
# flatpak 1.18.4 (what Fedora 45 ships) and a local test repository.
REAL_FLATPAK_UPDATE = "\n".join([
    "Looking for updates…",
    "Required runtime for org.test.Paint/x86_64/stable (runtime/org.test.Platform/x86_64/2) found in remote testrepo",
    "", "",
    " 1.\t   \torg.test.Platform\t1\tu\ttestrepo\t< 4.0\xa0MB",
    " 2.\t   \torg.test.Hello\tstable\tu\ttestrepo\t< 4.0\xa0MB",
    " 3.\t   \torg.test.Platform\t2\ti\ttestrepo\t< 4.0\xa0MB",
    " 4.\t   \torg.test.Paint\tstable\tu\ttestrepo\t< 5.0\xa0MB",
    "", "",
    "Updating 1/4…",
    "Updating 1/4…                        0%  0 bytes/s",
    "Updating 1/4… ▌                      3%",
    "Updating 1/4… ███████████████▍      77%  7.1\xa0MB/s",
    "Updating 1/4… ████████████████████ 100%",
    "Updating 2/4…",
    "Updating 2/4…                        0%  0 bytes/s",
    "Updating 2/4… ██████████████▌       73%",
    "Updating 2/4… ████████████████████ 100%",
    "Installing 3/4…",
    "Installing 3/4…                        0%  0 bytes/s",
    "Installing 3/4… ████████████████████ 100%",
    "Updating 4/4…",
    "Updating 4/4…                        0%  0 bytes/s",
    "Updating 4/4… ███████████████▍      77%",
    "Updating 4/4… ████████████████████ 100%",
    "Changes complete.",
    ""])
# One change fails: flatpak goes on with the rest, then exits 1.
REAL_FLATPAK_FAILURE = "\n".join([
    "Looking for updates…", "", "",
    " 1.\t   \torg.test.Paint\tstable\tu\ttestrepo\t< 2.0\xa0MB",
    " 2.\t   \torg.test.Hello\tstable\tu\ttestrepo\t< 3.0\xa0MB",
    "", "",
    "Updating 1/2…",
    "Updating 1/2…                        0%  0 bytes/s",
    "Updating 1/2… ▌                      3%",
    "Updating 1/2… ████████████████████ 100%",
    "Updating 2/2…",
    "Updating 2/2…                        0%  0 bytes/s",
    "Error: While pulling app/org.test.Hello/x86_64/stable from remote testrepo: While fetching "
    "http://127.0.0.1:18734/repo/objects/c2/55fc0c51a6b1363bb648ca28d07cd4a51f6c37b4e7addc7e73662a1af18f2a.filez: "
    "Server returned HTTP 404",
    "Updates complete.",
    "error: There were one or more errors",
    ""])
FLATPAK_NAMES = "org.test.Hello\tstable\tHello World\norg.test.Paint\tstable\tPaint Studio\norg.test.Platform\t1\tTest Platform\n"


class World:
    """A home, a /usr and stand-ins for one test."""

    def __init__(self, tools=("rpm-ostree", "flatpak", "systemctl", "fwupdmgr", "podman", "distrobox")):
        self.root = Path(tempfile.mkdtemp(prefix="monolith-update-test."))
        self.home = self.root / "home"
        self.bin = self.root / "bin"
        self.utils = self.root / "utils"
        self.scenario = self.root / "scenario"
        self.calls_file = self.root / "calls"
        libexec = self.root / "usr/libexec/monolith"
        for path in (self.home, self.bin, self.utils, self.scenario, libexec):
            path.mkdir(parents=True)
        # PATH holds only the stand-ins and the few commands they use, so a
        # real program on this machine, such as rustup in /usr/bin, can't run.
        for name in UTILS:
            found = shutil.which(name)
            if not found:
                raise SystemExit(f"the tests need {name}")
            (self.utils / name).symlink_to(found)
        self.update = libexec / "update"
        shutil.copy(SOURCE, self.update)
        self.write(libexec / "software", "#!/bin/bash\n" + CATALOG, mode=0o755)
        for name in tools:
            self.stand_in(name)
        self.write(self.root / "os-release",
                   'NAME=Monolith\nPRETTY_NAME="Monolith KDE"\nVARIANT=KDE\nOSTREE_VERSION=44.20261031.0\n')
        self.write(self.root / "ostree-booted", "")
        self.status(BOOTED)
        self.write(self.scenario / "status-after.json", json.dumps({"deployments": [STAGED, BOOTED]}))
        self.write(self.scenario / "status-busy.json", json.dumps({
            "deployments": [BOOTED], "transaction": ["AutomaticUpdateTrigger", ":1.42", "/org/projectatomic/rpmostree1/fedora"]}))

    def write(self, path, text, mode=None):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        if mode:
            path.chmod(mode)
        return path

    def stand_in(self, name, body=None):
        body = STAND_INS.get(name, "") if body is None else body
        self.write(self.bin / name, f'#!/bin/bash\nprintf "%s\\n" "{name} $*" >>"$CALLS"\n{body}\n', mode=0o755)

    def status(self, *deployments_):
        self.write(self.scenario / "status.json", json.dumps({"deployments": list(deployments_), "transaction": None}))

    def flag(self, name, text=""):
        self.write(self.scenario / name, text)

    def catalog_app(self):
        self.write(self.home / ".local/state/monolith/software/sidra.managed", "1.4.2\n")

    def everything(self):
        """Every optional step installed."""
        self.catalog_app()
        for app in ("com.vysp3r.ProtonPlus", "it.mijorus.gearlever"):
            (self.home / ".local/share/flatpak/app" / app).mkdir(parents=True)
        self.write(self.home / "AppImages/Tool.AppImage", "")
        for name in ("rustup", "npm", "gh"):
            self.stand_in(name)
        (self.home / ".local/lib/node_modules/prettier").mkdir(parents=True)
        (self.home / ".local/share/gh/extensions/gh-dash").mkdir(parents=True)
        self.write(self.home / ".agents/skills/review/SKILL.md", "")

    def env(self, **extra):
        env = {
            "PATH": f"{self.bin}:{self.utils}", "HOME": str(self.home), "CALLS": str(self.calls_file),
            "SCENARIO": str(self.scenario), "MONOLITH_OS_RELEASE": str(self.root / "os-release"),
            "MONOLITH_LOGOS": str(LOGOS), "MONOLITH_OSTREE_BOOTED": str(self.root / "ostree-booted"),
            "FLATPAK_SYSTEM_DIR": str(self.root / "var/lib/flatpak"), "MONOLITH_UPDATE_POLL": "0.05",
            # GitHub's runners have Homebrew in its usual place.
            "HOMEBREW_PREFIX": str(self.root / "linuxbrew"),
            "LANG": "C.UTF-8", "TERM": "xterm-256color", "SHELL": "/bin/bash",
        }
        env.update(extra)
        return {k: v for k, v in env.items() if v is not None}

    def calls(self):
        try:
            return self.calls_file.read_text().splitlines()
        except FileNotFoundError:
            return []

    def run(self, *args, **env):
        result = subprocess.run([str(self.update), *args], env=self.env(**env), capture_output=True,
                                text=True, timeout=60, stdin=subprocess.DEVNULL)
        return result.returncode, result.stdout, result.stderr

    def run_tty(self, *args, cols=100, rows=30, answer=None, interrupt=None, after=None, **env):
        """Run in a pseudo-terminal of COLS x ROWS, then print the terminal's
        settings with stty. ANSWER goes to the restart question. INTERRUPT is a
        call the stand-ins log; once it shows up, Ctrl+C is pressed AFTER (1 or 2) times."""
        command = shlex_join([str(self.update), *args])
        pid, fd = pty.fork()
        if pid == 0:
            fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
            os.execve("/bin/bash", ["bash", "-c", f'{command}; echo "EXIT=$?"; stty -a'], self.env(**env))
        output, pressed, answered, start = b"", 0, False, time.monotonic()
        while time.monotonic() - start < 60:
            ready, _, _ = select.select([fd], [], [], 0.05)
            if ready:
                try:
                    chunk = os.read(fd, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                output += chunk
            if answer is not None and not answered and b"[y/N]" in output:
                os.write(fd, answer.encode() + b"\r")
                answered = True
            if interrupt and pressed < (after or 1) and any(interrupt in c for c in self.calls()):
                time.sleep(0.2)
                os.write(fd, b"\x03")
                pressed += 1
                time.sleep(0.3)
        else:
            os.kill(pid, signal.SIGKILL)
            output += b"\n[the test killed it after 60 seconds]"
        os.waitpid(pid, 0)
        os.close(fd)
        text = output.decode("utf-8", "replace")
        code = re.search(r"EXIT=(\d+)", text)
        return (int(code.group(1)) if code else None), text

    def remove(self):
        shutil.rmtree(self.root, ignore_errors=True)


def shlex_join(words):
    import shlex
    return shlex.join(words)


def plain(text):
    return ESCAPE.sub("", text)


def load_module(env):
    """The updater as a module, with its paths taken from ENV."""
    saved = dict(os.environ)
    os.environ.update(env)
    sys.dont_write_bytecode = True  # no __pycache__ beside the real script
    try:
        loader = SourceFileLoader("monolith_update", str(SOURCE))
        spec = spec_from_loader("monolith_update", loader)
        assert spec is not None
        module = module_from_spec(spec)
        loader.exec_module(module)
        return module
    finally:
        os.environ.clear()
        os.environ.update(saved)


TESTS = []


def test(function):
    TESTS.append(function)
    return function


def expect(condition, message, detail: object = ""):
    if not condition:
        raise AssertionError(message + (f"\n--- got ---\n{detail}" if detail else ""))


def has(text, *needles):
    for needle in needles:
        expect(needle in text, f"missing {needle!r}", text)


def lines_with(text, needle):
    return [line for line in text.splitlines() if needle in line]


def replay(module, step, outputs, captures=(), peek=None):
    """Run STEP alone in a session whose programs print canned output.
    OUTPUTS and CAPTURES are (start of the command, exit code, text); PEEK(line,
    step) sees the step after each line. Returns the session."""
    class Session(module.Session):
        def run(self, argv, parse=None, env=None):
            words = " ".join(map(str, argv))
            self.ran.append((words, env))
            code, text = next(((c, t) for start, c, t in outputs if words.startswith(start)), (0, ""))
            lines = []
            for line in text.split("\n"):
                self._line(line, lines, parse, True)
                if peek:
                    peek(line, step)
            return code, lines

        def capture(self, argv, timeout=30):
            words = " ".join(map(str, argv))
            return next(((c, t) for start, c, t in captures if words.startswith(start)), (0, ""))

        def unit_active(self, unit, user=False):
            return False

    session = Session(module.Log(None))
    session.ran = []
    session.step, step.state, step.started = step, "running", 0.0
    step.result = step.run(session)
    return session


def items_of(step):
    return [(item.name, item.state, item.text, item.kind) for item in step.items]


def ran(out):
    """The steps a plain run reported, in order."""
    return [m.group(1) for m in re.finditer(r"^[✓!✗+x○-] (.+?)\s{2,}", out, re.M)]


# --- tests --------------------------------------------------------------------------------

@test
def full_run_without_a_terminal(w):
    w.everything()
    w.write(w.home / ".config/monolith/update.toml", '[commands]\n"Dotfiles" = "echo pulled >>$CALLS"\n')
    w.flag("firmware-update")
    code, out, err = w.run()
    expect(code == 0, f"exit {code}", out + err)
    has(out, "Updating Monolith (KDE · 44.20261031.0)")
    order = ran(out)
    expect(order == ["Monolith", "Flatpaks", "Monolith apps", "Proton tools", "AppImages", "Distrobox", "Firmware",
                     "Rust", "npm", "GitHub CLI", "Dotfiles"], f"steps ran as {order}", out)
    has(out, "44.20261101.0 is ready",
        "New: Kernel 6.17.2 · Mesa 25.2.5 · Plasma 6.5.1",
        "3 updated, 1 unused removed", "Sidra 1.4.2 → 1.5.0", "GE-Proton Latest updated",
        "2 containers upgraded", "1 update available", "To install: monolith update-firmware", "1.91.0 → 1.91.1",
        "2 packages updated", "gh-dash v4.1.0 → v4.2.0", "Everything is updated",
        "44.20261101.0 is ready. Restart to use it.", "Release notes: https://github.com/monolithlinux/monolith/releases/latest")
    calls = w.calls()
    for call in ("rpm-ostree upgrade --unchanged-exit-77", "flatpak update --user -y",
                 "flatpak update --system -y", "flatpak uninstall --user --unused -y --noninteractive",
                 "software update progress=1 locale=C.UTF-8 language=",
                 "flatpak run com.vysp3r.ProtonPlus update all", "flatpak run it.mijorus.gearlever --update --all --yes",
                 "distrobox upgrade --all", "fwupdmgr refresh", "fwupdmgr get-updates --json", "rustup update",
                 "npm update --global", "gh extension upgrade --all", "gh skill update --all", "pulled"):
        expect(call in calls, f"{call} wasn't run", "\n".join(calls))
    expect(not any("sudo" in c or "bootc" in c for c in calls), "used sudo or bootc", "\n".join(calls))
    expect(not any(c.startswith("systemctl reboot") for c in calls), "restarted without asking")
    # The run counts for the login reminder.
    record = w.home / ".local/state/monolith/update-reminder/last-update"
    expect(record.exists() and time.time() - record.stat().st_mtime < 60, "the run wasn't recorded")
    logs = w.home / ".local/state/monolith/update"
    latest = (logs / "latest.log").read_text()
    has(latest, "$ rpm-ostree upgrade --unchanged-exit-77", "[3/3] Fetching ostree chunk", "== Distrobox ==",
        "-> ok: 2 containers upgraded")


@test
def steps_that_dont_apply_are_left_out(w):
    w.stand_in("podman", "")  # no Distrobox containers
    code, out, _ = w.run()
    expect(code == 0, f"exit {code}", out)
    expect(ran(out) == ["Monolith", "Flatpaks", "Firmware"], f"ran {ran(out)}", out)
    expect(not any(c.startswith("distrobox") for c in w.calls()), "Distrobox ran without containers")
    # Not booted from an ostree image: no OS step.
    os.remove(w.root / "ostree-booted")
    code, out, _ = w.run()
    expect(ran(out) == ["Flatpaks", "Firmware"], f"ran {ran(out)}", out)


@test
def homebrew_outside_path(w):
    # brew isn't always on PATH, so Homebrew's own place counts too.
    w.write(w.root / "linuxbrew/bin/brew", '#!/bin/bash\nprintf "%s\\n" "brew $*" >>"$CALLS"\n'
            'echo "Already up-to-date."\n', mode=0o755)
    code, out, _ = w.run("--only", "brew")
    expect(code == 0 and ran(out) == ["Homebrew"], f"exit {code}, ran {ran(out)}", out)
    calls = w.calls()
    expect(calls[-2:] == ["brew update", "brew upgrade"], "brew didn't update and upgrade", "\n".join(calls))
    has(out, "Up to date")


@test
def list_shows_the_plan(w):
    w.everything()
    w.write(w.home / ".config/monolith/update.toml", 'skip = ["distrobox"]\n[commands]\n"Sync notes" = "true"\n')
    code, out, _ = w.run("--list")
    expect(code == 0, f"exit {code}", out)
    has(out, "system", "rpm-ostree upgrade", "flatpak run com.vysp3r.ProtonPlus update all", "sync-notes",
        "Sync notes", "Not installed here:", "pipx", "Left out: distrobox")
    expect(not any("upgrade" in c and c.startswith("rpm-ostree") for c in w.calls()), "--list ran an update")
    code, out, _ = w.run("__steps")
    expect(out.splitlines()[0] == "system\tMonolith" and "sync-notes\tSync notes" in out, "__steps", out)


@test
def only_and_skip(w):
    w.everything()
    code, out, _ = w.run("--only", "flatpak,firmware")
    expect(code == 0 and ran(out) == ["Flatpaks", "Firmware"], f"--only ran {ran(out)}", out)
    record = w.home / ".local/state/monolith/update-reminder/last-update"
    expect(not record.exists(), "a partial run counted as a full update")
    code, out, _ = w.run("--skip=system,distrobox", "--skip", "rust")
    steps = ran(out)
    expect(steps and "Monolith" not in steps and "Distrobox" not in steps and "Rust" not in steps, f"--skip ran {steps}", out)
    expect(record.exists(), "a run with --skip didn't count")
    code, _, err = w.run("--only", "flatpaks")
    expect(code == 2 and "doesn't have a step called flatpaks" in err and "Did you mean flatpak?" in err, err)
    code, _, err = w.run("sidra")
    expect(code == 2 and "monolith update sidra" in err, err)
    code, _, err = w.run("--bogus")
    expect(code == 2 and "--bogus" in err, err)


@test
def settings_file(w):
    path = w.home / ".config/monolith/update.toml"
    w.write(path, 'skip = ["flatpak", "nope"]\n')
    code, out, err = w.run()
    expect(code == 0 and "Flatpaks" not in out, "skip in update.toml", out)
    has(err, "skips nope, which isn't a step")
    for text, message in (("skip = [", "isn't valid TOML"), ('skips = ["flatpak"]', "doesn't know: skips"),
                          ('skip = "flatpak"', "must be a list"), ('[commands]\nx = 1\n', "must be text")):
        w.write(path, text)
        code, _, err = w.run()
        expect(code == 2 and message in err, f"{text!r}: {err}")
    # Your own commands run in your shell, and their failures show up.
    w.write(path, '[commands]\n"Break" = "echo oops; exit 4"\n')
    code, out, _ = w.run()
    expect(code == 1, f"exit {code}", out)
    has(out, "✗ Break", "oops", "1 step failed", "What went wrong is in")


@test
def a_failing_step_doesnt_stop_the_rest(w):
    w.everything()
    w.flag("flatpak-update---user-fails")
    w.flag("distrobox-fails")
    code, out, _ = w.run()
    expect(code == 1, f"exit {code}", out)
    has(out, "✗ Flatpaks", "Your Flatpaks: Unable to load summary from remote flathub", "✗ Distrobox",
        "arch: pacman failed", "2 steps failed", "What went wrong is in")
    expect("arch: arch:" not in out, "repeated the container name", out)
    expect("flatpak update --system -y" in w.calls(), "the system Flatpaks were skipped")
    expect("rustup update" in w.calls(), "a failure stopped later steps")
    expect((w.home / ".local/state/monolith/update-reminder/last-update").exists(),
           "a finished run with failures wasn't recorded")


@test
def system_update_cases(w):
    # The daily update already downloaded the newest build.
    w.status(STAGED, BOOTED)
    w.flag("upgrade-exit", "77")
    code, out, _ = w.run("--only", "system")
    has(out, "44.20261101.0 is ready", "New: Kernel 6.17.3 · systemd 258.2")
    expect("rpm-ostree db diff" in w.calls(), "no diff for the staged build")
    # Nothing new at all.
    w.status(BOOTED)
    code, out, _ = w.run("--only", "system")
    expect(re.search(r"^✓ Monolith\s+Up to date", out, re.M), "up to date", out)
    expect("ready" not in out and "Restart" not in out, "offered a restart without an update", out)
    # The daily update is running: wait for it instead of failing.
    w.flag("busy", "2")
    w.flag("upgrade-exit", "0")
    code, out, _ = w.run("--only", "system")
    expect(code == 0 and "44.20261101.0 is ready" in out, "waiting for a running update", out)
    expect(w.calls().count("rpm-ostree status --json") >= 4, "didn't poll", "\n".join(w.calls()))
    # A dated tag after monolith rollback: updates are paused on purpose.
    paused = dict(BOOTED, **{"container-image-reference": "ostree-image-signed:docker://ghcr.io/monolithlinux/kde:20261030"})
    w.status(paused)
    (w.scenario / "upgraded").unlink()
    w.calls_file.unlink()
    code, out, _ = w.run("--only", "system")
    expect(re.search(r"^! Monolith\s+Paused on the build from Fri, Oct 30, 2026", out, re.M), "paused", out)
    has(out, "monolith rollback latest")
    expect(not any(c.startswith("rpm-ostree upgrade") for c in w.calls()), "updated a paused system")
    # rpm-ostree fails.
    w.status(BOOTED)
    w.flag("upgrade-exit", "1")
    code, out, _ = w.run("--only", "system")
    expect(code == 1, f"exit {code}", out)
    expect(re.search(r"^✗ Monolith\s+Failed to fetch manifest: connection refused", out, re.M), "failure", out)


@test
def waits_for_the_daily_flatpak_update(w):
    w.flag("active-flatpak-system-update.service", "3")
    code, out, _ = w.run("--only", "flatpak")
    expect(code == 0, f"exit {code}", out)
    expect(w.calls().count("systemctl is-active --quiet flatpak-system-update.service") >= 4, "didn't wait",
           "\n".join(w.calls()))


@test
def plain_symbols_without_utf8(w):
    code, out, _ = w.run("--only", "flatpak", LANG="C", TERM="linux")
    expect(code == 0 and "+ Flatpaks" in out and "✓" not in out, "ASCII output", out)


@test
def the_restart_question(w):
    code, text = w.run_tty("--only", "system", answer="")
    expect(code == 0, f"exit {code}", text)
    has(text, "Restart now to use 44.20261101.0?")
    # The question lines up with the summary above it: the full-screen view
    # indents everything by one column, plain lines don't.
    margin = " " if "\x1b[?1049h" in text else ""
    expect(f"\n{margin}Restart now" in text.replace("\r", ""), "the question isn't lined up", text)
    expect("systemctl reboot" not in w.calls(), "Enter restarted")
    (w.scenario / "upgraded").unlink()
    code, text = w.run_tty("--only", "system", answer="y")
    expect("systemctl reboot" in w.calls(), "y didn't restart", text)


@test
def live_view_fits_and_restores_the_terminal(w):
    w.everything()
    for cols, rows in ((100, 30), (120, 40), (80, 24), (60, 20), (40, 12), (30, 9)):
        for name in ("upgraded",):
            (w.scenario / name).unlink(missing_ok=True)
        code, text = w.run_tty(cols=cols, rows=rows, answer="n")
        expect(code == 0, f"{cols}x{rows}: exit {code}", text)
        has(text, "\x1b[?1049h", "\x1b[?1049l", "\x1b[?25h")
        live, _, after = text.partition("\x1b[?1049l")
        for chunk in re.split(r"\x1b\[\d+;1H", live)[1:]:
            line = plain(chunk.split("\x1b[K")[0])
            expect(len(line) <= cols - 1, f"{cols}x{rows}: drew {len(line)} columns", line)
        used = [int(n) for n in re.findall(r"\x1b\[(\d+);1H", live)]
        expect(max(used) <= rows, f"{cols}x{rows}: drew on row {max(used)}")
        summary = plain(after.split("EXIT=")[0]).replace("\r", "")
        for line in summary.splitlines():
            expect(len(line) <= cols, f"{cols}x{rows}: summary line of {len(line)}", line)
        has(summary, "Everything is updated", "Took")
        settings = after.split("EXIT=")[1]
        expect(re.search(r"(?<![-\w])echo\b", settings), f"{cols}x{rows}: echo is still off", settings)


@test
def ctrl_c_stops_after_the_step(w):
    w.everything()
    code, text = w.run_tty(interrupt="rpm-ostree upgrade", DELAY="0.5")
    expect(code == 130, f"exit {code}", text)
    calls = w.calls()
    expect("flatpak update --user -y" not in calls, "kept going after Ctrl+C", "\n".join(calls))
    summary = plain(text.split("\x1b[?1049l")[-1])
    has(summary, "Stopped before the end", "44.20261101.0 is ready", "Not run")
    expect(not (w.home / ".local/state/monolith/update-reminder/last-update").exists(), "a stopped run counted")


@test
def ctrl_c_twice_stops_now(w):
    start = time.monotonic()
    code, text = w.run_tty(interrupt="rpm-ostree upgrade", after=2, DELAY="3")
    expect(code == 130, f"exit {code}", text)
    expect(time.monotonic() - start < 6, "waited for the step to finish")
    has(plain(text.split("\x1b[?1049l")[-1]), "Stopped")
    expect(not (w.scenario / "upgraded").exists(), "the step wasn't stopped")


@test
def no_motion_and_no_color(w):
    code, text = w.run_tty("--only", "flatpak", MONOLITH_MOTION="0")
    expect(code == 0 and "\x1b[?1049h" in text, "MONOLITH_MOTION=0 should keep the live view, still", text)
    code, text = w.run_tty("--only", "flatpak", NO_COLOR="1")
    expect(code == 0 and "\x1b[?1049h" not in text and "\x1b[3" not in text, "NO_COLOR got colors", text)
    has(plain(text), "✓ Flatpaks")


@test
def help_touches_nothing(w):
    result = subprocess.run([str(w.update), "--help"], env=w.env(HOME="/nonexistent"), capture_output=True, text=True)
    expect(result.returncode == 0 and "Usage: monolith update" in result.stdout, result.stdout + result.stderr)
    expect(all(len(line) <= 80 for line in result.stdout.splitlines()), "help has lines over 80 columns")


@test
def frames_fit_every_window(w):
    module = load_module(w.env())
    module.SYM.update(module.UNICODE_SYMBOLS)
    session = module.Session(module.Log(None))
    session.phase = "run"
    steps = module.all_steps({"A command with a really long name indeed": "true"})
    for step in steps:
        session.found.append(step)
    # A failed step's notes say why, on as many rows as they need, up to REASON_ROWS each.
    why = ["A reason that goes on " * 9 + "https://example.invalid/" + "long" * 30, "理由 " * 60, "Short"]
    states = [("done", module.Result(module.OK, "Something long " * 8, ["More notes " * 9])),
              ("done", module.Result(module.FAIL, "Broke", why)), ("running", None), ("waiting", None)]
    for i, step in enumerate(steps):
        step.state, step.result = states[min(i, 3)] if i < 3 else states[3]
        step.started, step.ended = 0.0, 75.0
        step.detail, step.sub = "Detail " * 20, "Output line " * 20
    # The running step opens up with long, wide and many items.
    running = steps[2]
    for k in range(9):
        running.items.append(module.Item(f"Ein sehr langer Name für eine App 日本語 {k}", "312.0 MB"))
    for item in running.items[:4]:
        item.finish("Something long " * 5, module.FAIL if item is running.items[1] else None)
    running.items[4].state, running.items[4].fraction, running.items[4].rate = "running", 0.45, "17.1 MB/s"
    running.total = 12
    look = module.Look(sys.stdout)
    look.motion, look.color, look.unicode = True, True, True
    screen = module.Screen(look, module.Paint(True), session)
    now = time.monotonic()
    for cols in (20, 30, 41, 67, 68, 80, 91, 92, 100, 140, 250):
        for rows in (5, 9, 12, 13, 17, 21, 22, 24, 30, 60):
            for t in (0.0, 0.7, 5.0):
                lines = screen.compose(cols, rows, now + t)
                expect(len(lines) <= rows, f"{cols}x{rows}: {len(lines)} lines")
                for line in lines:
                    expect(module.width(line) <= cols - 1, f"{cols}x{rows}: a line of {module.width(line)}", plain(line))
            final = screen.compose(cols, rows, now, final=True)
            expect(all(module.width(line) <= cols - 1 for line in final), f"{cols}x{rows}: summary too wide")
    # A running step without items draws a download's bar under it.
    running.items, running.bar = [], 0.37
    for cols in (20, 30, 41, 67, 80, 100, 250):
        for rows in (9, 13, 24):
            for line in screen.compose(cols, rows, now + 1.0):
                expect(module.width(line) <= cols - 1, f"{cols}x{rows}: a bar line of {module.width(line)}", plain(line))
    # Wide characters from a program's output count double.
    expect(module.width(module.fit("日本語のテキスト" * 5, 11)) <= 11, "wide characters")
    expect(module.clip("\x1b[31mabcdef", 3) == "\x1b[31mabc\x1b[0m", "clip keeps colors and resets them")


@test
def the_art_shows_the_whole_mark(w):
    module = load_module(w.env())
    for size in ("large", "medium"):
        scene = module.Scene(size)
        end = [plain(line) for line in scene.frame(10.0, rise=1.0, progress=1.0)]
        mark = (LOGOS / ("monolith.txt" if size == "large" else "monolith-medium.txt")).read_text()
        bottom = re.sub(r"\$[12]", "", mark.splitlines()[-1])
        expect(any(bottom in line for line in end), f"{size}: the mark's base is missing", "\n".join(end))
        expect(all(module.width(line) == scene.cols for line in end), f"{size}: uneven rows")
        start = [plain(line) for line in scene.frame(0.0, rise=0.0, progress=0.0)]
        expect(not any("█" in line for line in start), f"{size}: the mark showed before rising")


@test
def flatpak_changes_from_real_runs(w):
    m = load_module(w.env())
    names = [("flatpak list --user", 0, FLATPAK_NAMES)]
    nothing = ("flatpak update --system", 0, "Looking for updates…\n\nNothing to update.\n")
    step = m.Flatpaks()
    session = replay(m, step, [("flatpak update --user", 0, REAL_FLATPAK_UPDATE), nothing], names)
    expect(items_of(step) == [("Test Platform 1", "done", "Updated", m.OK), ("Hello World", "done", "Updated", m.OK),
                              ("Test Platform 2", "done", "Installed", m.OK), ("Paint Studio", "done", "Updated", m.OK)],
           "flatpak's changes", items_of(step))
    expect([item.size for item in step.items] == ["4.0 MB", "4.0 MB", "4.0 MB", "5.0 MB"], "sizes",
           [item.size for item in step.items])
    expect(step.result.kind == m.OK and step.result.text == "4 updated", "result", step.result.text)
    expect(all(env == m.flatpak_english() for words, env in session.ran if words.startswith("flatpak")),
           "flatpak didn't run in English", session.ran)
    # Halfway through the first change, it has its own share and speed.
    seen = {}

    def peek(line, step):
        if "77%  7.1" in line:
            seen.update(state=[item.state for item in step.items], fraction=step.items[0].fraction,
                        rate=step.items[0].rate)

    replay(m, m.Flatpaks(), [("flatpak update --user", 0, REAL_FLATPAK_UPDATE), nothing], names, peek)
    expect(seen == {"state": ["running", "waiting", "waiting", "waiting"], "fraction": 0.77, "rate": "7.1 MB/s"},
           "halfway", seen)
    # A change that fails says why in a few words, and the rest still count.
    step = m.Flatpaks()
    replay(m, step, [("flatpak update --user", 1, REAL_FLATPAK_FAILURE), nothing], names)
    expect(items_of(step) == [("Paint Studio", "done", "Updated", m.OK), ("Hello World", "done", "Failed", m.FAIL)],
           "a failed change", items_of(step))
    expect(step.result.kind == m.FAIL and step.result.text == "Hello World: Server returned HTTP 404",
           "the failure", step.result.text)
    # The same through the real command, without a terminal.
    w.flag("flatpak-replay", REAL_FLATPAK_FAILURE)
    w.flag("flatpak-replay-exit", "1")
    code, out, _ = w.run("--only", "flatpak")
    expect(code == 1 and re.search(r"^✗ Flatpaks\s+Hello World: Server returned HTTP 404", out, re.M), "plain run", out)

    names = {("org.gnome.Platform", "48"): "GNOME Application Platform version 48",
             ("org.kde.Platform", "6.9"): "KDE Application Platform", ("org.mozilla.firefox", "stable"): "Firefox",
             ("org.freedesktop.Platform.GL.default", "24.08"): "Mesa"}
    names[("org.example.Old", "6.1")] = "Old Platform 6.1"
    for ref, branch, label in (("org.gnome.Platform", "49", "GNOME Platform 49"),
                               ("org.gnome.Platform", "48", "GNOME Platform 48"),
                               ("org.kde.Platform", "6.10", "KDE Platform 6.10"),
                               ("org.kde.Platform.Locale", "6.10", "KDE Platform 6.10 translations"),
                               ("org.example.Old", "6.10", "Old Platform 6.10"),
                               ("org.mozilla.firefox.Locale", "stable", "Firefox translations"),
                               ("org.freedesktop.Platform.GL.default", "25.08", "Mesa 25.08"),
                               ("org.example.New", "stable", "org.example.New"),
                               ("org.example.New.Locale", "stable", "org.example.New.Locale")):
        expect(m.flatpak_label(ref, branch, names) == label, f"{ref} {branch}", m.flatpak_label(ref, branch, names))
    # Rows as flatpak-cli-transaction.c (1.18.4) formats them: a translation's
    # size ends in "(partial)", and nothing to download is "0 bytes".
    step = m.Flatpaks()
    changes = m.FlatpakChanges(step, names)
    for line in (" 1.\t   \torg.kde.Platform.Locale\t6.10\tu\tflathub\t< 18.5\xa0kB (partial)",
                 " 2.\t   \torg.mozilla.firefox\tstable\tu\tflathub\t0 bytes"):
        changes.note(m.plain(line), True)  # as Session hands lines over
    expect([(item.name, item.size) for item in step.items] == [("KDE Platform 6.10 translations", "18.5 kB"),
                                                               ("Firefox", "")],
           "sizes from the table", [(item.name, item.size) for item in step.items])


@test
def flatpak_keeps_your_languages(w):
    # Flatpak picks which translations to download from your languages, and
    # with none at all it downloads every language. Its words are read in
    # English, so LANGUAGE carries your languages: GLib reads it first, and
    # glibc ignores it under C.UTF-8.
    m = load_module(w.env())
    for environ, language in (({"LANG": "de_DE.UTF-8"}, "de_DE.UTF-8"),
                              ({"LANG": "en_US.UTF-8", "LANGUAGE": "de:en_US"}, "de:en_US"),
                              ({"LANG": "en_US.UTF-8", "LC_TIME": "de_DE.UTF-8"}, "en_US.UTF-8:de_DE.UTF-8"),
                              ({"LC_ALL": "fr_FR.UTF-8", "LANG": "en_US.UTF-8", "LC_TIME": "de_DE.UTF-8"}, "fr_FR.UTF-8"),
                              ({"LANG": "de_DE.UTF-8", "LC_CTYPE": "C.UTF-8"}, "de_DE.UTF-8"),
                              ({"LANG": "C.UTF-8"}, None), ({"LANG": "", "LANGUAGE": ""}, None), ({}, None)):
        got = m.flatpak_english(environ)
        expect(got == {"LC_ALL": "C.UTF-8", "LANGUAGE": language}, str(environ), got)
    # Through the real command: flatpak and the catalog get them, ProtonPlus doesn't need them.
    w.everything()
    code, out, err = w.run("--only", "flatpak,apps", LANG="de_DE.UTF-8")
    expect(code == 0, f"exit {code}", out + err)
    env = Path(f"{w.calls_file}-env").read_text().splitlines()
    expect(env == ["--user LC_ALL=C.UTF-8 LANGUAGE=de_DE.UTF-8", "--system LC_ALL=C.UTF-8 LANGUAGE=de_DE.UTF-8"],
           "flatpak's environment", env)
    expect("software update progress=1 locale=C.UTF-8 language=de_DE.UTF-8" in w.calls(), "the catalog's environment",
           "\n".join(w.calls()))


@test
def apps_containers_and_proton_tools_list_what_they_update(w):
    m = load_module(w.env())
    catalog = "\n".join([
        "Plan: Sidra | Epic Games Launcher | Tea", "", "(1/3) Sidra — Update", "", "==> Downloading Sidra 1.5.0",
        "\r#######                      12.5%\r###################          47.3%",
        "Updated Sidra from 1.4.2 to 1.5.0 at /home/u/AppImages/Sidra.AppImage.",
        "", "(2/3) Epic Games Launcher — Update", "Epic Games Launcher 17.2.1 is already up to date.",
        "", "(3/3) Tea — Update", "Error: Tea could not be downloaded",
        "", "Results:", "  Sidra               Updated", "  Epic Games Launcher Up to date",
        "  Tea                 Failed (exit 1)", "", "Some items did not complete; see the messages above."])
    seen = {}

    def peek(line, step):
        if line.startswith("==>"):
            seen["names"] = [item.name for item in step.items]
            seen["doing"] = step.current().doing
        if "47.3%" in line:
            seen["fraction"] = step.current().fraction

    apps = m.Apps()
    session = replay(m, apps, [(str(m.SOFTWARE), 1, catalog)], peek=peek)
    expect(seen == {"names": ["Sidra", "Epic Games Launcher", "Tea"], "doing": "Downloading Sidra 1.5.0",
                    "fraction": 0.473}, "the catalog's apps while they update", seen)
    expect(items_of(apps) == [("Sidra", "done", "1.4.2 → 1.5.0", m.OK),
                              ("Epic Games Launcher", "done", "Up to date", m.OK),
                              ("Tea", "done", "Tea could not be downloaded", m.FAIL)], "apps", items_of(apps))
    expect(apps.result.kind == m.FAIL and apps.result.text == "Tea didn't update", "result", apps.result.text)
    expect(apps.result.more == ["Tea could not be downloaded"], "why it didn't", apps.result.more)
    expect(session.ran[0][1] == dict(m.flatpak_english(), MONOLITH_PROGRESS="1"), "the catalog's environment",
           session.ran)

    box = m.Distrobox()
    box.boxes = ["fedora", "arch"]
    seen.clear()

    def peek(line, step):
        if line == "Upgrading arch...":
            seen["arch"] = items_of(step)

    replay(m, box, [("distrobox upgrade", 1, "Upgrading fedora...\nUpgraded: 12 packages\nComplete!\n"
                     "Upgrading arch...\n:: Synchronizing package databases...\n"
                     "error: failed to synchronize all databases")], peek=peek)
    expect(seen["arch"] == [("fedora", "done", "Upgraded", m.OK), ("arch", "running", "", m.OK)], "containers", seen)
    expect(items_of(box)[1] == ("arch", "done", "Failed", m.FAIL), "the failed container", items_of(box))
    expect(box.result.kind == m.FAIL and box.result.text.startswith("arch: "), "result", box.result.text)

    proton = m.Proton()
    proton.argv = ["protonplus", "update", "all"]
    seen.clear()

    def peek(line, step):
        if "55%" in line:
            current = step.current()
            seen.update(name=current.name, fraction=current.fraction, rate=current.rate)

    replay(m, proton, [("protonplus", 1, "Updating all runners...\n"
                        "Updating GE-Proton Latest...\r\r\x1b[2KUpdating GE-Proton10-25... 55% (9.1 MB/s) [ETA: 2s]\n"
                        "\r\x1b[2K\rSuccessfully updated GE-Proton Latest\n"
                        "Updating Proton-CachyOS Latest...\r\r\x1b[2K\r"
                        "Error: Failed to update Proton-CachyOS Latest: API rate limit exceeded\n")], peek=peek)
    expect(seen == {"name": "GE-Proton Latest", "fraction": 0.55, "rate": "9.1 MB/s"}, "a Proton tool downloading", seen)
    expect(items_of(proton) == [("GE-Proton Latest", "done", "Updated", m.OK),
                                ("Proton-CachyOS Latest", "done", "API rate limit exceeded", m.WARN)],
           "Proton tools", items_of(proton))
    expect(proton.result.kind == m.WARN, "Proton result", proton.result.text)


@test
def a_failed_app_says_why(w):
    m = load_module(w.env())
    m.SYM.update(m.UNICODE_SYMBOLS)
    refusal = ("WowUp-CF 2.26.0 embeds an AppImage update source (gh-releases-zsync|WowUp|WowUp.CF|latest-pre|"
               "WowUp-CF-*.AppImage.zsync) that Gear Lever would follow outside Monolith's release policy and "
               "that cannot be removed. Nothing was changed.")
    curl = "curl: (22) The requested URL returned error: 503"
    failed = ["", "Results:", "  WowUp-CF            Failed (exit 1)", "  Sidra               Failed (exit 1)",
              "  Tea                 Failed (exit 22)", "  Superfile           Up to date",
              "", "Some items did not complete; see the messages above."]
    apps = m.Apps()
    session = replay(m, apps, [(str(m.SOFTWARE), 1, "\n".join([
        "Plan: WowUp-CF | Sidra | Tea | Superfile", "", "(1/4) WowUp-CF — Update",
        "Selected WowUp-CF 2.26.0 (stable channel) from WowUp/WowUp.CF.",
        "Downloading WowUp-CF-2.26.0.AppImage (128.0 MiB)...", f"Error: {refusal}",
        # An open app is refused without an Error line. curl doesn't say whose download it was, and
        # what a program prints after its error isn't the reason.
        "", "(2/4) Sidra — Update", "Close Sidra and retry this action.",
        "", "(3/4) Tea — Update", "", "==> Downloading Tea 0.12.0", curl, "Removed the unfinished download.",
        "", "(4/4) Superfile — Update", "Superfile 1.4.0 is already up to date.", *failed]))])
    reasons = [refusal, "Close Sidra and retry this action.", f"Tea: {curl}"]
    expect(apps.result.text == "WowUp-CF, Sidra, Tea didn't update", "result", apps.result.text)
    expect(apps.result.more == reasons, "why they didn't", apps.result.more)
    expect(items_of(apps) == [("WowUp-CF", "done", refusal, m.FAIL), ("Sidra", "done", reasons[1], m.FAIL),
                              ("Tea", "done", curl, m.FAIL), ("Superfile", "done", "Up to date", m.OK)],
           "the apps", items_of(apps))
    # One failed app needs no name before its reason; one that said nothing has no reason to show.
    alone = m.Apps()
    replay(m, alone, [(str(m.SOFTWARE), 1, "\n".join([
        "Plan: Tea | Superfile", "", "(1/2) Tea — Update", curl, "", "(2/2) Superfile — Update",
        "Superfile 1.4.0 is already up to date.", "", "Results:", "  Tea                 Failed (exit 22)",
        "  Superfile           Up to date", *failed[-2:]]))])
    expect(alone.result.more == [curl], "one failed app", alone.result.more)
    quiet = m.Apps()
    replay(m, quiet, [(str(m.SOFTWARE), 1, "\n".join([
        "Plan: Tea", "", "(1/1) Tea — Update", "", "Results:", "  Tea                 Failed (exit 1)",
        *failed[-2:]]))])
    expect(quiet.result.text == "Tea didn't update" and quiet.result.more == [], "a silent failure",
           quiet.result.more)
    expect(items_of(quiet) == [("Tea", "done", "Failed (exit 1)", m.FAIL)], "a silent failure", items_of(quiet))

    # The reasons stay under the step, wrapped to the window instead of cut.
    session.found, session.phase = [apps], "run"
    apps.state, apps.ended = "done", 7.0
    look = m.Look(sys.stdout)
    look.motion, look.color, look.unicode = True, True, True
    screen = m.Screen(look, m.Paint(True), session)

    def notes(cols, rows, final):
        text = [plain(line) for line in screen.compose(cols, rows, time.monotonic(), final=final)]
        head = next(i for i, line in enumerate(text) if "Monolith apps" in line)
        left, found = min(text[head].index(mark) for mark in "✗!" if mark in text[head]), []
        for line in text[head + 1:]:
            if not line[left + 6:].strip() or line[left:left + 4].strip() or line[left + 4] not in "╰ ":
                break
            found.append((line[left + 4], line[left + 6:].rstrip()))
        expect(all(m.width(line) <= cols - 1 for line in text), f"{cols}x{rows}: too wide", "\n".join(text))
        return found, "\n".join(text)

    squeezed = "".join("".join(reasons).split())
    for cols, rows in ((140, 40), (100, 26), (80, 24), (60, 20)):
        for final in (True, False):
            found, shown = notes(cols, rows, final)
            expect([mark for mark, _ in found].count("╰") == 3, f"{cols}x{rows}: three reasons", shown)
            expect(len(found) > 3 and "".join("".join(row.split()) for _, row in found) == squeezed,
                   f"{cols}x{rows}: cut, not wrapped", shown)
    # A short window drops them before it drops steps; the summary afterwards has them all.
    found, shown = notes(100, 12, False)
    expect(found == [] and "didn't update" in shown, "a short window", shown)
    expect(len(notes(100, 12, True)[0]) > 3, "the summary in a short window", notes(100, 12, True)[1])
    # No reason takes more than REASON_ROWS rows; the log has the rest.
    apps.result = m.Result(m.FAIL, "Tea didn't update", ["It goes on and on " * 200])
    found, shown = notes(100, 40, True)
    expect(len(found) == m.REASON_ROWS and found[-1][1].endswith("…"), "a very long reason", shown)
    # Notes under a step that didn't fail keep to one line each, as before.
    apps.result = m.Result(m.WARN, "Sidra skipped", ["It goes on and on " * 200, "Short"])
    found, shown = notes(100, 40, True)
    expect([row[-1] for _, row in found] == ["…", "t"] and [mark for mark, _ in found] == ["╰", "╰"],
           "other notes", shown)

    for text, limit, most, rows in (("a bb ccc dddd", 4, 0, ["a bb", "ccc", "dddd"]), ("", 9, 0, []),
                                    ("see " + "x" * 10 + " now", 4, 0, ["see", "xxxx", "xxxx", "xx", "now"]),
                                    ("see /var/home/max/AppImages/wowup-cf.appimage now", 20, 0,
                                     ["see", "/var/home/max/", "AppImages/wowup-cf.", "appimage now"]),
                                    ("日本語のテキスト abc", 5, 0, ["日本", "語の", "テキ", "スト", "abc"]),
                                    ("one two three four five", 5, 2, ["one", "two…"])):
        expect(m.wrap(text, limit, most) == rows, f"wrap {text!r} to {limit}", m.wrap(text, limit, most))

    # Without a terminal the reason follows the step's line, whole.
    w.catalog_app()
    w.flag("catalog-fails", f"Error: {refusal}\n")
    code, out, _ = w.run("--only", "apps")
    expect(code == 1, f"exit {code}", out)
    has(out, "✗ Monolith apps", "Sidra didn't update", refusal, "What went wrong is in")
    w.flag("catalog-fails", "Close Sidra and retry this action.\n")
    code, out, _ = w.run("--only", "apps")
    has(out, "Sidra didn't update", "Close Sidra and retry this action.")


@test
def the_running_step_opens_and_folds(w):
    m = load_module(w.env())
    m.SYM.update(m.UNICODE_SYMBOLS)
    session = m.Session(m.Log(None))
    session.phase = "run"
    system, flatpaks, apps = m.System(), m.Flatpaks(), m.Apps()
    session.found = [system, flatpaks, apps]
    now = time.monotonic()
    system.state, system.result, system.started, system.ended = "done", m.Result(m.OK, "Up to date"), now - 30, now - 29
    flatpaks.state, flatpaks.started = "running", now - 20
    for name, size in (("Firefox", "89.4 MB"), ("Discord", "101 MB"), ("OBS Studio", "54.0 MB"), ("GNOME 49", "12.0 MB")):
        flatpaks.items.append(m.Item(name, size))
    for item in flatpaks.items[:2]:
        item.finish("Updated")
    obs = flatpaks.items[2]
    obs.state, obs.fraction, obs.rate = "running", 0.45, "7.1 MB/s"
    look = m.Look(sys.stdout)
    look.motion, look.color, look.unicode = True, True, True
    screen = m.Screen(look, m.Paint(True), session)
    text = []

    def row(*needles):
        return next((line for line in text if all(needle in line for needle in needles)), None)

    def draw(at):
        text[:] = [plain(line) for line in screen.compose(100, 30, at)]
        return "\n".join(text)

    shown = draw(now)
    expect(row("Flatpaks", "3 of 4", "0:20"), "the open step's line", shown)
    expect(row("✓ Firefox", "Updated") and row("✓ Discord", "Updated"), "finished items", shown)
    expect(row("● OBS Studio", "45%", "7.1 MB/s"), "the running item's bar", shown)
    expect(row("○ GNOME 49", "12.0 MB"), "a waiting item and its size", shown)
    expect(row("○   Monolith apps") and not row("Monolith apps", "of"), "a waiting step stays shut", shown)
    # Done, it stays open a moment so the last tick shows, then folds to one line.
    for item in flatpaks.items[2:]:
        item.finish("Updated")
    flatpaks.state, flatpaks.result, flatpaks.ended = "done", m.Result(m.OK, "4 updated"), now
    shown = draw(now + 0.1)
    expect(row("✓ GNOME 49", "Updated") and row("✓   Flatpaks", "4 updated"), "the last tick", shown)
    shown = draw(now + m.FOLD + 0.1)
    expect(row("✓   Flatpaks", "4 updated") and not row("Firefox"), "it didn't fold", shown)
    # A long list keeps the current item in view and counts the rest.
    apps.state, apps.started = "running", now
    for k in range(12):
        apps.items.append(m.Item(f"App {k + 1}"))
    for item in apps.items[:6]:
        item.finish("Up to date")
    apps.items[6].state = "running"
    shown = draw(now + 1)
    expect(row("✓ 5 done") and row("✓ App 6") and row("● App 7") and row("○ App 8") and row("○ 4 more"),
           "a long list", shown)
    expect(not row("App 5") and not row("App 9"), "showed items that don't fit", shown)
    # A step with a download but no items draws its bar under it.
    apps.items, apps.state = [], "waiting"
    flatpaks.state = "waiting"
    system.state, system.started, system.detail = "running", now - 3, "Downloading the new build"
    system.bar, system.sub = 0.5, "Layer 2 of 4 · 846.2 MB in all"
    shown = draw(now + 2)
    expect(row("╰", "50%", "Layer 2 of 4"), "the system's download", shown)


@test
def item_rows_keep_the_current_one(w):
    m = load_module(w.env())
    for n in range(13):
        for focus in range(n + 1):
            for unlisted in (0, 2):
                for room in range(8):
                    items = [m.Item(f"item {k}") for k in range(n)]
                    for item in items[:focus]:
                        item.finish("Done")
                    if focus < n:
                        items[focus].state = "running"
                    rows = m.item_rows(items, unlisted, room)
                    case = f"{n} items, {focus} done, {unlisted} unlisted, {room} rows: {rows}"
                    expect(len(rows) <= room, "too many rows", case)
                    shown = [value for kind, value in rows if kind == "item"]
                    if n and room:
                        expect(items[min(focus, n - 1)] in shown, "lost the current item", case)
                    if room >= 3:
                        counted = sum(value for kind, value in rows if kind != "item")
                        expect(len(shown) + counted == n + unlisted, "lost count", case)
                    if n + bool(unlisted) <= room:
                        expect(len(shown) == n, "left out items that fit", case)


def main():
    if sys.argv[1:2] == ["demo"]:
        w = World()
        w.everything()
        w.flag("firmware-update")
        for scenario in sys.argv[2:]:
            name, _, text = scenario.partition("=")
            w.flag(name, text + "\n")
        env = w.env(DELAY="1.2", TICK="0.3", TERM=os.environ.get("TERM", "xterm-256color"))
        env.pop("MONOLITH_UPDATE_POLL")
        try:
            subprocess.run([str(w.update)], env=env)
        finally:
            w.remove()
        return 0
    failed = 0
    chosen = [f for f in TESTS if not sys.argv[1:] or f.__name__ in sys.argv[1:]]
    for function in chosen:
        w = World()
        started = time.monotonic()
        try:
            function(w)
            print(f"ok   {function.__name__} ({time.monotonic() - started:.1f}s)", flush=True)
        except Exception as error:  # report every failure, then fail
            failed += 1
            print(f"FAIL {function.__name__}: {error}", flush=True)
        finally:
            w.remove()
    if failed:
        print(f"{failed} of {len(chosen)} monolith update tests failed")
        return 1
    print(f"monolith update tests passed ({len(chosen)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
