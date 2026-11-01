#!/usr/bin/env python3
"""monolith update, with stand-ins for every program it runs.

    python3 tests/monolith-update.py        run the tests
    python3 tests/monolith-update.py demo   play a slow run in this terminal

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
    "flatpak": r'''
[ ! -e "$SCENARIO/flatpak-$1-$2-fails" ] || { echo "error: Unable to load summary from remote flathub"; exit 1; }
case "$1 $2" in
  "update --user") echo "Updating app/org.kde.okular/x86_64/stable"; sleep "${DELAY:-0}"
                   echo "Installing runtime/org.kde.Platform/x86_64/6.10" ;;
  "update --system") echo "Updating app/org.mozilla.Thunderbird/x86_64/stable" ;;
  "uninstall --user") echo "Uninstalling runtime/org.kde.Platform/x86_64/6.9" ;;
  "run com.vysp3r.ProtonPlus")
    echo "Updating all runners..."
    printf 'Updating GE-Proton Latest...\r'; sleep "${DELAY:-0}"; printf '\r\033[2K\r'
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
[ "$1" = update ] || exit 0
printf '\n(1/2) Sidra — Update\n'
sleep "${DELAY:-0}"
echo "Updated Sidra from 1.4.2 to 1.5.0 at $HOME/AppImages/Sidra.AppImage."
printf '\n(2/2) Epic Games Launcher — Update\n'
echo "Epic Games Launcher is already up to date."
printf '\nResults:\n  %-19s %s\n  %-19s %s\n' Sidra Updated "Epic Games Launcher" "Up to date"
'''


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


def expect(condition, message, detail=""):
    if not condition:
        raise AssertionError(message + (f"\n--- got ---\n{detail}" if detail else ""))


def has(text, *needles):
    for needle in needles:
        expect(needle in text, f"missing {needle!r}", text)


def lines_with(text, needle):
    return [line for line in text.splitlines() if needle in line]


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
    for call in ("rpm-ostree upgrade --unchanged-exit-77", "flatpak update --user -y --noninteractive",
                 "flatpak update --system -y --noninteractive", "flatpak uninstall --user --unused -y --noninteractive",
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
    expect("flatpak update --system -y --noninteractive" in w.calls(), "the system Flatpaks were skipped")
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
    expect("flatpak update --user -y --noninteractive" not in calls, "kept going after Ctrl+C", "\n".join(calls))
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
    states = [("done", module.Result(module.OK, "Something long " * 8, ["More notes " * 9])),
              ("done", module.Result(module.FAIL, "Broke")), ("running", None), ("waiting", None)]
    for i, step in enumerate(steps):
        step.state, step.result = states[min(i, 3)] if i < 3 else states[3]
        step.started, step.ended = 0.0, 75.0
        step.detail, step.sub = "Detail " * 20, "Output line " * 20
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


def main():
    if sys.argv[1:] == ["demo"]:
        w = World()
        w.everything()
        w.flag("firmware-update")
        env = w.env(DELAY="1.2", TERM=os.environ.get("TERM", "xterm-256color"))
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
