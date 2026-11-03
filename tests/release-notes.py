#!/usr/bin/env python3
"""changelog.py, with made-up package lists for two editions.

GitHub turns away release notes longer than 125,000 characters, and a move
to a new Fedora release changes nearly every package.

    python3 tests/release-notes.py
"""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CHANGELOG = ROOT / ".github/workflows/changelog.py"
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


if __name__ == "__main__":
    unittest.main()
