#!/usr/bin/python3
"""Keep files that did not change in layers that did not change.

chunkah splits the finished image into layers by package. A file that no
package owns keeps its build-time timestamp, so its layer gets a new digest,
and every system downloads it again, on every build even when the file is
identical. Such a file also lends its timestamp to the folders of whatever
layer it shares. Installed systems never see these timestamps: ostree stores
none, shows every file in /usr as 1970-01-01 (the value used here), and copies
/etc defaults in at deployment.

Package files are left alone (chunkah already gives them the package build
time), as are folders, whose timestamps fontconfig uses to validate its caches.
The recipes run this last, after everything that writes files.
"""

import os
import stat
import subprocess
import sys

# Files that get a chunkah component, and so a layer, of their own instead of
# sharing one with unrelated files: (directory, component, update interval).
COMPONENTS = (
    # Installed by the fonts module in common.yml; Nerd Fonts releases a few
    # times a year.
    ("/usr/share/fonts/nerd-fonts", b"nerd-fonts", b"quarterly"),
    # Rebuilt by every build because they record folder timestamps; on their
    # own, their changes no longer pull other files' layers along.
    ("/usr/lib/fontconfig/cache", b"fontconfig-cache", b"daily"),
)

# Listed by a package but not part of its payload; chunkah does not count these
# as package files either.
RPMFILE_GHOST = 64


def entries(top):
    """Yield every non-directory entry below top without following links."""
    pending = [top]
    while pending:
        with os.scandir(pending.pop()) as found:
            for entry in found:
                if entry.is_dir(follow_symlinks=False):
                    pending.append(entry.path)
                else:
                    yield entry


def package_paths():
    """Return the paths of every non-ghost package file, resolved through merged-/usr links."""
    listing = subprocess.run(
        ["rpm", "-qa", "--qf", "[%{FILEFLAGS}\t%{FILENAMES}\n]"],
        check=True, capture_output=True, text=True,
    ).stdout
    resolved_dirs = {}
    paths = set()
    for line in listing.splitlines():
        flags, _, path = line.partition("\t")
        if not path or int(flags) & RPMFILE_GHOST:
            continue
        parent, name = os.path.split(path)
        if parent not in resolved_dirs:
            resolved_dirs[parent] = os.path.realpath(parent)
        paths.add(os.path.join(resolved_dirs[parent], name))
    return paths


def tag(directory, component, interval):
    if not os.path.isdir(directory):
        print(f"warning: {directory} is missing; nothing to tag as {component.decode()}", file=sys.stderr)
        return 0
    tagged = 0
    for entry in entries(directory):
        if not entry.is_file(follow_symlinks=False):
            continue
        try:
            os.setxattr(entry.path, "user.component", component, follow_symlinks=False)
            os.setxattr(entry.path, "user.update-interval", interval, follow_symlinks=False)
        except OSError as error:
            # Only the layer split depends on this, so keep building.
            print(f"warning: cannot tag {entry.path} for chunkah: {error.strerror}", file=sys.stderr)
            return tagged
        tagged += 1
    return tagged


def pin_unowned(packaged):
    pinned = 0
    for top in ("/usr", "/etc"):
        for entry in entries(top):
            if entry.path in packaged:
                continue
            info = entry.stat(follow_symlinks=False)
            # A file with more than one name may also be a package file.
            if not stat.S_ISLNK(info.st_mode) and not (stat.S_ISREG(info.st_mode) and info.st_nlink == 1):
                continue
            if info.st_mtime_ns != 0:
                os.utime(entry.path, ns=(0, 0), follow_symlinks=False)
                pinned += 1
    return pinned


def main():
    for directory, component, interval in COMPONENTS:
        print(f"Tagged {tag(directory, component, interval)} files in {directory} as {component.decode()}.")
    print(f"Pinned the timestamps of {pin_unowned(package_paths())} files no package owns.")


if __name__ == "__main__":
    main()
