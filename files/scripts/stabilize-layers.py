#!/usr/bin/python3
"""Keep files that did not change in layers that did not change.

chunkah splits the finished image into layers by package. A file or folder
that no package owns keeps its build-time timestamp, so its layer gets a new
digest, and every system downloads it again, on every build even when nothing
in it changed. chunkah also writes each layer's folders with timestamps capped
at that layer's own time; for its catch-all "unclaimed" layer that time is the
build's, so that layer still changes with every build.

So this gives everything no package owns the timestamp 0, and moves the small
unowned files out of "unclaimed" into a component of their own, whose time is
that of its newest member, here 0.

Installed systems never see these timestamps: ostree stores none, shows every
file and folder in /usr as 1970-01-01 (the value used here), and creates /etc
at deployment. fontconfig treats a font folder dated 0 as matching its cache.
Package entries are left alone; chunkah gives them the package's build time.
The recipes run this last, after everything that writes files.
"""

import errno
import glob
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

# The small unowned files. Files from 1 MiB up already get a layer each from
# chunkah. What stays in "unclaimed" changes anyway: links, which cannot carry
# the tag, package folders the build touched, and /run, which holds files the
# container runtime creates or mounts over in every build step.
UNOWNED = (b"unowned", b"daily")
OWN_LAYER_SIZE = 1 << 20

# Written when the kernel package is installed, named after a random machine ID,
# so it differed in every build. Installed systems get their boot entries from
# bootc/ostree, never from the image's /boot.
BUILD_BOOT_ENTRIES = "/boot/loader/entries/*.conf"

# Mounts and scratch space of the build container, and ostree's /sysroot, which
# the image build leaves out: not image content.
SKIP = {"/proc", "/sys", "/dev", "/tmp", "/var", "/sysroot"}

# Listed by a package but not part of its payload, such as /etc/ld.so.cache, and
# rewritten at build time: pinned like unowned files, but left to chunkah's own
# placement, which may be the package's layer.
RPMFILE_GHOST = 64


def walk(top, device):
    """Yield every entry below top, skipping SKIP and anything mounted from elsewhere."""
    pending = [top]
    while pending:
        with os.scandir(pending.pop()) as found:
            for entry in found:
                if entry.path in SKIP:
                    continue
                info = entry.stat(follow_symlinks=False)
                if info.st_dev != device:
                    continue
                yield entry, info
                if stat.S_ISDIR(info.st_mode):
                    pending.append(entry.path)


def package_entries():
    """Return {path: file types} of package entries and the set of ghost paths.

    Paths are resolved through merged-/usr links.
    """
    listing = subprocess.run(
        ["rpm", "-qa", "--qf", "[%{FILEFLAGS}\t%{FILEMODES}\t%{FILENAMES}\n]"],
        check=True, capture_output=True, text=True,
    ).stdout
    resolved_dirs = {}
    entries, ghosts = {}, set()
    for line in listing.splitlines():
        flags, mode, path = line.split("\t", 2)
        parent, name = os.path.split(path)
        if parent not in resolved_dirs:
            resolved_dirs[parent] = os.path.realpath(parent)
        path = os.path.join(resolved_dirs[parent], name)
        if int(flags) & RPMFILE_GHOST:
            ghosts.add(path)
        else:
            entries.setdefault(path, set()).add(stat.S_IFMT(int(mode)))
    return entries, ghosts


def set_component(path, component, interval):
    os.setxattr(path, "user.component", component, follow_symlinks=False)
    os.setxattr(path, "user.update-interval", interval, follow_symlinks=False)


def has_component(path):
    try:
        os.getxattr(path, "user.component", follow_symlinks=False)
    except OSError as error:
        if error.errno == errno.ENODATA:
            return False
        raise
    return True


def tag(directory, component, interval):
    if not os.path.isdir(directory):
        print(f"warning: {directory} is missing; nothing to tag as {component.decode()}", file=sys.stderr)
        return 0
    tagged = 0
    for entry, info in walk(directory, os.lstat(directory).st_dev):
        if stat.S_ISREG(info.st_mode):
            set_component(entry.path, component, interval)
            tagged += 1
    return tagged


def stabilize_unowned(packaged, ghosts):
    pinned = tagged = 0
    tagging = True
    for entry, info in walk("/", os.lstat("/").st_dev):
        kind = stat.S_IFMT(info.st_mode)
        # Only the type a package lists counts: /opt is a package folder but a
        # link on disk, and chunkah layers it as unowned.
        if kind in packaged.get(entry.path, ()) or kind not in (stat.S_IFREG, stat.S_IFDIR, stat.S_IFLNK):
            continue
        # A file with more than one name may also be a package file.
        if kind == stat.S_IFREG and info.st_nlink > 1:
            continue
        if info.st_mtime_ns != 0:
            os.utime(entry.path, ns=(0, 0), follow_symlinks=False)
            pinned += 1
        if (tagging and kind == stat.S_IFREG and info.st_size < OWN_LAYER_SIZE
                and entry.path not in ghosts and not entry.path.startswith("/run/")):
            try:
                if not has_component(entry.path):
                    set_component(entry.path, *UNOWNED)
                    tagged += 1
            except OSError as error:
                # Only the layer split depends on the tags, so keep pinning.
                print(f"warning: cannot tag {entry.path} for chunkah: {error.strerror}", file=sys.stderr)
                tagging = False
    return pinned, tagged


def main():
    boot_entries = glob.glob(BUILD_BOOT_ENTRIES)
    for path in boot_entries:
        os.remove(path)
    print(f"Removed {len(boot_entries)} build-time boot entries.")
    try:
        for directory, component, interval in COMPONENTS:
            print(f"Tagged {tag(directory, component, interval)} files in {directory} as {component.decode()}.")
    except OSError as error:
        # Only the layer split depends on the tags, so keep building.
        print(f"warning: cannot tag files for chunkah: {error}", file=sys.stderr)
    pinned, tagged = stabilize_unowned(*package_entries())
    print(f"Pinned the timestamps of {pinned} files and folders no package owns; tagged {tagged} of them as unowned.")


if __name__ == "__main__":
    main()
