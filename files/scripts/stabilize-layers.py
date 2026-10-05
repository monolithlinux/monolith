#!/usr/bin/python3
"""Keep files that did not change in layers that did not change.

chunkah splits the finished image into layers by package. A file or folder
that no package owns keeps its build-time timestamp, so its layer gets a new
digest, and every system downloads it again, on every build even when nothing
in it changed. Such an entry also lends its timestamp to the folders of
whatever layer it shares.

Installed systems never see these timestamps: ostree stores none, shows every
file and folder in /usr as 1970-01-01 (the value used here), and creates /etc
at deployment. Package files and folders are left alone; chunkah already gives
them the package's build time. The recipes run this last, after everything
that writes files, including the font cache, so nothing later reads the new
timestamps.
"""

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

# Build leftovers in /run, which installed systems hide under a tmpfs, tagged on
# the folder so every entry below it joins one small layer. It includes the
# container runtime's stub for /etc/resolv.conf, which every build step mounts
# over, so nothing in the build can re-date or remove it.
RUN_LEFTOVERS = ("/run", b"build-leftovers", b"daily")

# Written when the kernel package is installed, named after a random machine ID,
# so it differed in every build. Installed systems get their boot entries from
# bootc/ostree, never from the image's /boot.
BUILD_BOOT_ENTRIES = "/boot/loader/entries/*.conf"

# Mounts and scratch space of the build container, and ostree's /sysroot, which
# the image build leaves out: not image content.
SKIP = {"/proc", "/sys", "/dev", "/tmp", "/var", "/sysroot"}

# Listed by a package but not part of its payload, such as /etc/ld.so.cache, and
# rewritten at build time, so these are pinned like unowned files.
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
    """Map every non-ghost package path, resolved through merged-/usr links, to its file types."""
    listing = subprocess.run(
        ["rpm", "-qa", "--qf", "[%{FILEFLAGS}\t%{FILEMODES}\t%{FILENAMES}\n]"],
        check=True, capture_output=True, text=True,
    ).stdout
    resolved_dirs = {}
    entries = {}
    for line in listing.splitlines():
        flags, mode, path = line.split("\t", 2)
        if not path or int(flags) & RPMFILE_GHOST:
            continue
        parent, name = os.path.split(path)
        if parent not in resolved_dirs:
            resolved_dirs[parent] = os.path.realpath(parent)
        entries.setdefault(os.path.join(resolved_dirs[parent], name), set()).add(stat.S_IFMT(int(mode)))
    return entries


def tag(directory, component, interval):
    if not os.path.isdir(directory):
        print(f"warning: {directory} is missing; nothing to tag as {component.decode()}", file=sys.stderr)
        return 0
    tagged = 0
    for entry, info in walk(directory, os.lstat(directory).st_dev):
        if not stat.S_ISREG(info.st_mode):
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
    for entry, info in walk("/", os.lstat("/").st_dev):
        # Only the type a package lists counts: /opt is a package folder but a
        # link on disk, and chunkah layers it as unowned.
        if stat.S_IFMT(info.st_mode) in packaged.get(entry.path, ()) or info.st_mtime_ns == 0:
            continue
        # A file with more than one name may also be a package file.
        if stat.S_ISREG(info.st_mode) and info.st_nlink > 1:
            continue
        if stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            os.utime(entry.path, ns=(0, 0), follow_symlinks=False)
            pinned += 1
    return pinned


def main():
    for directory, component, interval in COMPONENTS:
        print(f"Tagged {tag(directory, component, interval)} files in {directory} as {component.decode()}.")
    directory, component, interval = RUN_LEFTOVERS
    try:
        os.setxattr(directory, "user.component", component, follow_symlinks=False)
        os.setxattr(directory, "user.update-interval", interval, follow_symlinks=False)
        print(f"Tagged {directory} as {component.decode()}.")
    except OSError as error:
        print(f"warning: cannot tag {directory} for chunkah: {error.strerror}", file=sys.stderr)
    boot_entries = glob.glob(BUILD_BOOT_ENTRIES)
    for path in boot_entries:
        os.remove(path)
    print(f"Removed {len(boot_entries)} build-time boot entries.")
    print(f"Pinned the timestamps of {pin_unowned(package_entries())} files and folders no package owns.")


if __name__ == "__main__":
    main()
