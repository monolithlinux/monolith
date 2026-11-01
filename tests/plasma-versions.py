#!/usr/bin/env python3
"""check-plasma-versions.py, with a stand-in for rpm.

The stand-in prints a made-up `rpm -qa` list, so each test describes the
packages of one image.

    python3 tests/plasma-versions.py
"""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "files/scripts/check-plasma-versions.py"

# Plasma's core as Fedora builds it: each source package with its
# version-release and some of the packages built from it. KWin's release is
# later than the rest, as in Fedora's Plasma 6.8.2 update.
CORE = {
    "kwin": ("6.8.2-2", ["kwin", "kwin-common", "kwin-libs"]),
    "plasma-workspace": ("6.8.2-1", ["plasma-workspace", "plasma-workspace-common",
                                     "plasma-workspace-libs", "libkworkspace6"]),
    "libplasma": ("6.8.2-1", ["libplasma"]),
    "plasma-desktop": ("6.8.2-1", ["plasma-desktop"]),
    "plasma-login-manager": ("6.8.2-1", ["plasma-login-manager", "kcm-plasmalogin"]),
    "kscreenlocker": ("6.8.2-1", ["kscreenlocker"]),
    "kdecoration": ("6.8.2-1", ["kdecoration"]),
    "libkscreen": ("6.8.2-1", ["libkscreen"]),
    "plasma-breeze": ("6.8.2-1", ["plasma-breeze", "plasma-breeze-common"]),
    "kglobalacceld": ("6.8.2-1", ["kglobalacceld"]),
    "layer-shell-qt": ("6.8.2-1", ["layer-shell-qt"]),
    "powerdevil": ("6.8.2-1", ["powerdevil"]),
    "plasma-integration": ("6.8.2-1", ["plasma-integration", "plasma-integration-qt5"]),
    "xdg-desktop-portal-kde": ("6.8.2-1", ["xdg-desktop-portal-kde"]),
}
CORE_PACKAGES = sum(len(names) for _, names in CORE.values())

# Not part of Plasma's core, so their versions don't matter here. kwin-zones
# shows that a source package starting with "kwin-" isn't KWin.
OTHERS = [
    ("kwin-zones", "1.0.13-3", "kwin-zones"),
    ("kf6-kirigami", "6.31.0-1", "kf6-kirigami"),
    ("plasma-nm", "6.8.3-1", "plasma-nm"),
    ("kate", "26.08.2-2", "kate"),
]

FAKE_RPM = """#!{python}
import os, sys
with open(os.environ["RPM_CALLS"], "a") as calls:
    calls.write(" ".join(sys.argv[1:]) + "\\n")
if os.environ.get("RPM_FAILS"):
    print("error: cannot open Packages database", file=sys.stderr)
    sys.exit(1)
sys.stdout.write(open(os.environ["RPM_LIST"]).read())
"""


def line(source, evr, name):
    version, release = evr.split("-")
    return f"{source}-{version}-{release}.fc44.src.rpm\t{name}\t{version}"


def image(core=None, changed=None, dropped=(), extra=()):
    """`rpm -qa` lines for an image: CORE with CHANGED as {package: version-release},
    without the source packages in DROPPED, plus EXTRA lines."""
    changed = changed or {}
    lines = []
    for source, (evr, names) in (core or CORE).items():
        if source in dropped:
            continue
        for name in names:
            lines.append(line(source, changed.get(name, evr), name))
    lines += [line(*other) for other in OTHERS]
    lines.append("(none)\tgpg-pubkey\t105ef944")
    lines += extra
    return "\n".join(lines) + "\n"


# The 2026-11-01 build: what plasma-oxygen pulled in went to 6.8.2, and the
# rest of Plasma's core stayed on 6.7.5.
NOVEMBER_1 = {name: "6.7.5-1" for source in ("kwin", "kdecoration", "libkscreen", "plasma-breeze",
                                               "kglobalacceld", "layer-shell-qt", "xdg-desktop-portal-kde")
              for name in CORE[source][1]}
NOVEMBER_1["plasma-integration"] = "6.7.5-1"


class PlasmaVersionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="plasma-versions-test.")
        self.root = Path(self.tmp.name)
        (self.root / "bin").mkdir()
        rpm = self.root / "bin/rpm"
        rpm.write_text(FAKE_RPM.format(python=sys.executable))
        rpm.chmod(0o755)

    def tearDown(self):
        self.tmp.cleanup()

    def run_check(self, listing, **env):
        (self.root / "list").write_text(listing)
        # Only the stand-in on PATH, so a real rpm on this machine can't answer.
        result = subprocess.run(
            [sys.executable, str(CHECK)], capture_output=True, text=True, timeout=30,
            env={"PATH": str(self.root / "bin"), "RPM_LIST": str(self.root / "list"),
                 "RPM_CALLS": str(self.root / "calls"), **env})
        return result.returncode, result.stdout, result.stderr

    def test_matching_core_passes(self):
        code, out, err = self.run_check(image())
        self.assertEqual(code, 0, err)
        self.assertIn(f"Plasma 6.8.2: all {CORE_PACKAGES} packages from Plasma's core match.", out)
        self.assertEqual((self.root / "calls").read_text(),
                         "-qa --queryformat %{SOURCERPM}\\t%{NAME}\\t%{VERSION}\\n\n")

    def test_the_november_1_build_fails(self):
        code, out, err = self.run_check(image(changed=NOVEMBER_1))
        self.assertEqual(code, 1, out)
        self.assertIn("Plasma's core comes from more than one Plasma release:", err)
        older, newer = [l for l in err.splitlines() if l.startswith("  ")]
        self.assertTrue(older.startswith("  6.7.5: kdecoration 6.7.5, kglobalacceld 6.7.5, kwin 6.7.5, "), older)
        self.assertIn("plasma-integration 6.7.5", older)
        self.assertTrue(newer.startswith("  6.8.2: kcm-plasmalogin 6.8.2, kscreenlocker 6.8.2, "), newer)
        self.assertIn("plasma-integration-qt5 6.8.2", newer)
        self.assertIn("plasma-workspace 6.8.2", newer)

    def test_a_fix_release_counts_as_its_release(self):
        code, out, err = self.run_check(image(changed={"plasma-workspace": "6.8.2.1-1",
                                                       "libkworkspace6": "6.8.2.1-1"}))
        self.assertEqual(code, 0, err)
        self.assertIn("Plasma 6.8.2:", out)

    def test_one_source_package_split_across_releases_fails(self):
        code, out, err = self.run_check(image(changed={"plasma-integration": "6.7.5-1"}))
        self.assertEqual(code, 1, out)
        self.assertIn("  6.7.5: plasma-integration 6.7.5\n", err)

    def test_releases_sort_by_number(self):
        code, _, err = self.run_check(image(changed={"kwin": "6.10.0-1"}))
        self.assertEqual(code, 1)
        listed = [l.split(":")[0].strip() for l in err.splitlines() if l.startswith("  ")]
        self.assertEqual(listed, ["6.8.2", "6.10.0"])

    def test_no_kwin_fails(self):
        code, out, err = self.run_check(image(dropped=("kwin",)))
        self.assertEqual(code, 1, out)
        self.assertIn("Can't check Plasma's versions: kwin isn't installed.", err)

    def test_a_missing_optional_part_is_fine(self):
        code, out, err = self.run_check(image(dropped=("plasma-breeze", "powerdevil")))
        self.assertEqual(code, 0, err)
        self.assertIn(f"all {CORE_PACKAGES - 3} packages", out)

    def test_rpm_failing_fails(self):
        code, out, _ = self.run_check(image(), RPM_FAILS="1")
        self.assertNotEqual(code, 0)
        self.assertNotIn("match", out)


if __name__ == "__main__":
    unittest.main(verbosity=1)
