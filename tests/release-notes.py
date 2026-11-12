#!/usr/bin/env python3
"""changelog.py, with made-up package lists for two editions, and the step
of generate_release.yml that publishes the notes, with a stand-in for gh.

GitHub turns away release notes longer than 125,000 characters, and a move
to a new Fedora release changes nearly every package.

    python3 tests/release-notes.py
"""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CHANGELOG = ROOT / ".github/workflows/changelog.py"
WORKFLOW = ROOT / ".github/workflows/generate_release.yml"
EDITIONS = ["kde", "kde-nvidia"]
GITHUB_LIMIT = 125_000


def packages(count, release, start=0):
    """count packages named like real ones, all at one Fedora release. Each
    release rebuilds them, so 44 has 1.0-1.fc44 and 45 has 1.0-2.fc45."""
    return {f"python3-package-number-{n:05d}": f"0:1.{n}.0-{release - 43}.fc{release}"
            for n in range(start, start + count)}


class ReleaseNotes(unittest.TestCase):
    def notes(self, prev, curr):
        """Release notes for the same package change in both editions."""
        with tempfile.TemporaryDirectory() as tmp:
            for edition in EDITIONS:
                for kind, lists in (("prev", prev), ("curr", curr)):
                    lines = "".join(f"{n}\t{v}\n" for n, v in sorted(lists.items()))
                    Path(tmp, f"{edition}.{kind}.txt").write_text(lines)
            images = ",".join(f"ghcr.io/monolithlinux/{e}" for e in EDITIONS)
            subprocess.run(
                [sys.executable, CHANGELOG, "--packages-dir", tmp, "--images", images,
                 "--prev-tag", "20261103", "--curr-tag", "20261104",
                 "--repo", "monolithlinux/monolith",
                 "--output", Path(tmp, "release.env"), "--changelog", Path(tmp, "notes.md")],
                check=True, stdout=subprocess.DEVNULL)
            return Path(tmp, "notes.md").read_text()

    def test_lists_every_change_in_a_normal_build(self):
        prev = packages(2600, 45)
        curr = dict(prev)
        curr["python3-package-number-00007"] = "0:1.7.1-1.fc45"
        del curr["python3-package-number-00008"]
        curr["new-package"] = "0:2.0-1.fc45"
        notes = self.notes(prev, curr)
        self.assertIn("| 🔄 | python3-package-number-00007 | 1.7.0-2 | 1.7.1-1 |", notes)
        self.assertIn("| ❌ | python3-package-number-00008 | 1.8.0-2 | |", notes)
        self.assertIn("| ✨ | new-package | | 2.0-1 |", notes)
        self.assertNotIn("too many to list", notes)

    def test_new_fedora_release_lists_only_added_and_removed(self):
        # Every package changed, as in the move from Fedora 44 to 45.
        prev = packages(2600, 44)
        curr = packages(2600, 45, start=40)
        notes = self.notes(prev, curr)
        self.assertLessEqual(len(notes), GITHUB_LIMIT)
        self.assertNotIn("| 🔄 |", notes)
        self.assertIn("| ✨ | python3-package-number-02639 | | 1.2639.0-2 |", notes)
        self.assertIn("| ❌ | python3-package-number-00000 | 1.0.0-1 | |", notes)
        self.assertIn("_2,560 packages changed, 40 were added and 40 removed. "
                      "That's too many to list here, so the table shows only "
                      "the added and removed ones._", notes)
        # The rest of the notes are still there.
        self.assertIn("### How to rebase", notes)
        self.assertIn("## `kde-nvidia`", notes)

    def test_falls_back_to_counts_when_even_that_is_too_long(self):
        prev = packages(3000, 44)
        curr = packages(3000, 45, start=3000)
        notes = self.notes(prev, curr)
        self.assertLessEqual(len(notes), GITHUB_LIMIT)
        self.assertNotIn("| ✨ |", notes)
        self.assertIn("_0 packages changed, 3,000 were added and 3,000 removed. "
                      "That's too many to list here._", notes)


def step_script(name):
    """The shell script of one step in generate_release.yml. The file is read
    by its indentation, because the runner's Python has no YAML reader."""
    lines = WORKFLOW.read_text().splitlines()
    step = lines.index(f"      - name: {name}")
    run = lines.index("        run: |", step)
    if any(line.startswith("      - ") for line in lines[step + 1:run]):
        raise ValueError(f"the step {name!r} has no script of its own")
    script = []
    for line in lines[run + 1:]:
        if line.strip() and not line.startswith(" " * 10):
            break
        script.append(line[10:])
    return "\n".join(script) + "\n"


# gh, as the step uses it: every call is written down, `release view` says
# whether the day's release exists, and `api` can be made to fail.
GH = """#!/bin/bash
printf '%s\\0' "$@" >> "$GH_CALLS"
printf '\\n' >> "$GH_CALLS"
case "$1" in
    release) [ "$2" != view ] || [ "$RELEASE_EXISTS" = yes ] ;;
    api) [ "$API_WORKS" = yes ] ;;
    *) exit 64 ;;
esac
"""
TAG = "2026-11-11"
SHA = "4754a30000000000000000000000000000000000"
TITLE = "2026-11-11 (Fedora 45)"
NOTES = ["--target", SHA, "--title", TITLE, "--notes-file", "./changelog.md"]


class PublishTheRelease(unittest.TestCase):
    def publish(self, release_exists, api_works=True):
        """Run the step. Returns its exit code and the gh calls it made."""
        with tempfile.TemporaryDirectory() as tmp:
            # Only the stand-in is on PATH: the runner has a real gh.
            programs = Path(tmp, "bin")
            programs.mkdir()
            Path(programs, "gh").write_text(GH)
            Path(programs, "gh").chmod(0o755)
            Path(tmp, "step.sh").write_text(step_script("Create release"))
            calls = Path(tmp, "calls")
            calls.touch()
            env = {"PATH": str(programs), "HOME": tmp, "GH_CALLS": str(calls), "GH_TOKEN": "made-up",
                   "GITHUB_REPOSITORY": "monolithlinux/monolith", "TAG": TAG, "SHA": SHA, "TITLE": TITLE,
                   "RELEASE_EXISTS": "yes" if release_exists else "no", "API_WORKS": "yes" if api_works else "no"}
            # The shell GitHub runs a step's script with.
            done = subprocess.run([shutil.which("bash"), "--noprofile", "--norc", "-e", "-o", "pipefail", "step.sh"],
                                  cwd=tmp, env=env, capture_output=True, text=True)
            made = [call.split("\0")[:-1] for call in calls.read_text().splitlines()]
            return done.returncode, made, done.stderr

    def test_the_first_build_of_a_day_creates_the_release(self):
        code, calls, errors = self.publish(release_exists=False)
        self.assertEqual(code, 0, errors)
        self.assertEqual(calls, [["release", "view", TAG], ["release", "create", TAG, *NOTES]])

    def test_a_second_build_on_the_same_day_moves_the_tag(self):
        """GitHub keeps an existing tag where it is. The next day's commit
        list starts at the tag, so it would repeat this build's commits."""
        code, calls, errors = self.publish(release_exists=True)
        self.assertEqual(code, 0, errors)
        self.assertEqual(calls[:2], [["release", "view", TAG], ["release", "edit", TAG, *NOTES]])
        self.assertEqual(len(calls), 3, calls)
        move = calls[2]
        self.assertEqual(move[0], "api")
        self.assertEqual(move[move.index("-X") + 1], "PATCH")
        self.assertIn(f"repos/monolithlinux/monolith/git/refs/tags/{TAG}", move)
        self.assertEqual(move[move.index(f"sha={SHA}") - 1], "-f")
        # -F sends true as a boolean; -f would send the word, which GitHub turns away.
        self.assertEqual(move[move.index("force=true") - 1], "-F")

    def test_a_tag_that_cannot_be_moved_fails_the_job(self):
        code, calls, _ = self.publish(release_exists=True, api_works=False)
        self.assertNotEqual(code, 0)
        self.assertEqual([call[:2] for call in calls], [["release", "view"], ["release", "edit"], ["api", "--silent"]])


if __name__ == "__main__":
    unittest.main()
