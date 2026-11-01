#!/usr/bin/python3
"""Stop the build when Plasma's parts come from different Plasma releases.

Fedora builds its Kinoite base once a day, and a Plasma update can reach
Fedora's repositories after that. Packages installed on top of the base then
pull in only the parts of the update they depend on. The 2026-11-01 build got
plasma-workspace 6.8.2 with KWin 6.7.5 that way, and it never reached the login
screen. common.yml updates the base first now; this catches a mix that still
gets through. The recipes run it after their last package change.
"""

import subprocess
import sys

# Plasma's core, by the names of Fedora's source packages. Every package built
# from one of these has to come from the same Plasma release.
CORE = (
    "kwin", "plasma-workspace", "libplasma", "plasma-desktop", "plasma-login-manager",
    "kscreenlocker", "kdecoration", "libkscreen", "plasma-breeze", "kglobalacceld",
    "layer-shell-qt", "powerdevil", "plasma-integration", "xdg-desktop-portal-kde",
)
# Without these, there's no telling which Plasma the image has.
REQUIRED = ("kwin", "plasma-workspace")


def plasma_release(version):
    """6.8.2 for 6.8.2, and for a fix release such as 6.8.2.1 too."""
    return ".".join(version.split(".")[:3])


def source_name(sourcerpm):
    """kwin for kwin-6.8.2-2.fc44.src.rpm."""
    return sourcerpm.removesuffix(".src.rpm").rsplit("-", 2)[0]


def version_key(version):
    return [(0, int(part), "") if part.isdigit() else (1, 0, part) for part in version.split(".")]


def main():
    query = subprocess.run(["rpm", "-qa", "--queryformat", r"%{SOURCERPM}\t%{NAME}\t%{VERSION}\n"],
                           capture_output=True, text=True, check=True)
    releases, sources = {}, set()
    for line in query.stdout.splitlines():
        sourcerpm, name, version = line.split("\t")
        source = source_name(sourcerpm)
        if source in CORE:
            sources.add(source)
            releases.setdefault(plasma_release(version), []).append(f"{name} {version}")

    missing = [source for source in REQUIRED if source not in sources]
    if missing:
        print(f"Can't check Plasma's versions: {' and '.join(missing)} isn't installed.", file=sys.stderr)
        return 1
    if len(releases) > 1:
        print("Plasma's core comes from more than one Plasma release:", file=sys.stderr)
        for release in sorted(releases, key=version_key):
            print(f"  {release}: {', '.join(sorted(releases[release]))}", file=sys.stderr)
        print("All of it has to come from the same release, or Plasma may not start.", file=sys.stderr)
        return 1
    [(release, packages)] = releases.items()
    print(f"Plasma {release}: all {len(packages)} packages from Plasma's core match.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
