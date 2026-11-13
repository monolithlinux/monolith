#!/usr/bin/python3
"""Stop the build when the startup screen wouldn't show.

Plymouth draws the startup screen from the initramfs, the small system the
kernel starts before the disk is unlocked and mounted. The initramfs is built
near the end of the recipe and takes a copy of Plymouth's default theme, the
plugin that theme is written for, and a font. If one of them doesn't get in,
the computer still starts, but with Plymouth's plain text screen, and nothing
at build time would say so. The recipes run this right after the initramfs is
built.

It checks that Monolith's theme is the default and that Plymouth leaves its
size alone, that Plymouth's own parser accepts the theme's script, and that
every initramfs in the image carries the same settings, the theme's files as
the image has them, the script plugin, and the plugin and font that draw text
(the question for an encrypted disk's passphrase).

    check-boot-splash.py [--root DIR] [INITRAMFS ...]

Without arguments it checks every /usr/lib/modules/*/initramfs.img.
"""

import bz2
import ctypes
import glob
import gzip
import lzma
import os
import subprocess
import sys

THEME = "monolith"
THEME_DIR = f"usr/share/plymouth/themes/{THEME}"
PLUGIN = "usr/lib64/plymouth/script.so"
# What Plymouth needs in the initramfs besides the theme's own files.
NEEDED = (
    (PLUGIN, "the plugin that runs the theme's script"),
    ("usr/lib64/plymouth/label-freetype.so", "the plugin that draws text"),
    ("usr/share/fonts/Plymouth.ttf", "the font for text"),
)


class Unreadable(Exception):
    pass


def setting(name, *texts):
    """What [Daemon] says for `name` in the first of Plymouth's settings files
    that says anything: plymouthd.conf first, then plymouthd.defaults."""
    for text in texts:
        group = None
        for line in (text or "").splitlines():
            line = line.strip()
            if line.startswith("[") and line.endswith("]"):
                group = line[1:-1]
            elif group == "Daemon":
                key, sep, value = line.partition("=")
                if sep and key.strip() == name and value.strip():
                    return value.strip()
    return None


def settings_problems(conf, defaults, whose):
    """Monolith's theme has to be the default, and Plymouth must not enlarge it:
    on a screen it takes for a dense one it would stretch the theme's small
    pictures instead of leaving the choice of the large ones to the theme."""
    problems = []
    theme = setting("Theme", conf, defaults)
    if theme != THEME:
        problems.append(f"{whose} default theme is {theme or 'not set'}; it has to be {THEME}")
    scale = setting("DeviceScale", conf, defaults)
    if scale != "1":
        problems.append(f"{whose} DeviceScale is {scale or 'not set'}; it has to be 1, or Plymouth blurs the logo on dense screens")
    return problems


def read_text(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def unzstd(data):
    try:
        from compression import zstd    # Python 3.14 and later
    except ImportError:
        try:
            done = subprocess.run(["zstd", "-dc"], input=data, capture_output=True, check=True)
        except (OSError, subprocess.CalledProcessError) as error:
            raise Unreadable(f"zstd can't unpack it ({error})") from error
        return done.stdout
    return zstd.decompress(data)


UNPACK = (
    (b"\x28\xb5\x2f\xfd", unzstd),
    (b"\x1f\x8b", gzip.decompress),
    (b"\xfd7zXZ\x00", lzma.decompress),
    (b"BZh", bz2.decompress),
)


class Initramfs:
    """The files of an initramfs: cpio archives in the kernel's "newc" format,
    one after the other, the last one usually compressed."""

    def __init__(self, data):
        self.entries = {}       # name -> (mode, the file's bytes)
        self.scan(data)
        if not self.entries:
            raise Unreadable("it is empty")

    def scan(self, data):
        at = 0
        while at < len(data):
            if data[at] == 0:               # padding between two archives
                at += 1
            elif data[at:at + 5] == b"07070":
                at = self.archive(data, at)
            else:
                for magic, unpack in UNPACK:
                    if data.startswith(magic, at):
                        try:
                            rest = unpack(data[at:])
                        except Unreadable:
                            raise
                        except Exception as error:
                            raise Unreadable(f"its compressed part can't be unpacked ({error})") from error
                        self.scan(rest)
                        return
                raise Unreadable("it is compressed in a way this check can't read")

    def archive(self, data, at):
        """Read one archive that starts at `at`; returns where it ends."""
        view = memoryview(data)
        linked = {}             # a file with several names has its bytes after the last one
        waiting = {}
        while True:
            head = data[at:at + 110]
            if len(head) < 110 or head[:6] not in (b"070701", b"070702"):
                raise Unreadable("it isn't a cpio archive all the way through")
            try:
                field = [int(head[6 + 8 * i:14 + 8 * i], 16) for i in range(13)]
            except ValueError as error:
                raise Unreadable("it isn't a cpio archive all the way through") from error
            inode, mode, links, size, name_size = field[0], field[1], field[4], field[6], field[11]
            name = data[at + 110:at + 110 + name_size - 1].decode("utf-8", "replace")
            start = (at + 110 + name_size + 3) & ~3
            at = (start + size + 3) & ~3
            if name == "TRAILER!!!":
                return at
            body = view[start:start + size]
            if name.startswith("./"):
                name = name[2:]
            name = name.lstrip("/")
            if mode & 0o170000 == 0o100000 and links > 1:
                key = (field[7], field[8], inode)
                if size:
                    linked[key] = body
                    for other in waiting.pop(key, []):
                        self.entries[other] = (mode, body)
                elif key in linked:
                    body = linked[key]
                else:
                    waiting.setdefault(key, []).append(name)
            self.entries[name] = (mode, body)

    def read(self, name, hops=8):
        """The bytes of a file, following links; None when it isn't there."""
        entry = self.entries.get(name)
        if entry is None:
            return None
        mode, body = entry
        if mode & 0o170000 == 0o120000:
            if not hops:
                return None
            target = bytes(body).decode("utf-8", "replace")
            if target.startswith("/"):
                target = target.lstrip("/")
            else:
                target = os.path.normpath(os.path.join(os.path.dirname(name), target))
            return self.read(target, hops - 1)
        return bytes(body) if mode & 0o170000 == 0o100000 else None


def theme_problems(root):
    problems = [f"{problem} (/etc/plymouth/plymouthd.conf)." for problem in settings_problems(
        read_text(os.path.join(root, "etc/plymouth/plymouthd.conf")),
        read_text(os.path.join(root, "usr/share/plymouth/plymouthd.defaults")), "Plymouth's")]
    if not os.path.isfile(os.path.join(root, THEME_DIR, f"{THEME}.script")):
        problems.append(f"/{THEME_DIR}/{THEME}.script isn't in the image.")
    return problems


def parser_problems(root, load=ctypes.CDLL):
    """Ask Plymouth itself whether it can read the theme's script. A script it
    can't read is no error for Plymouth: it shows its text screen instead."""
    script = os.path.join(root, THEME_DIR, f"{THEME}.script")
    if not os.path.isfile(script):
        return []               # theme_problems has said so
    try:
        plugin = load(os.path.join(root, PLUGIN))
        plugin.script_parse_file.restype = ctypes.c_void_p
        plugin.script_parse_file.argtypes = [ctypes.c_char_p]
    except (OSError, AttributeError) as error:
        return [f"Plymouth's script plugin can't be loaded, is plymouth-plugin-script installed? ({error})"]
    if not plugin.script_parse_file(script.encode()):
        return [f"Plymouth's parser doesn't accept /{THEME_DIR}/{THEME}.script; its own message is above."]
    return []


def initramfs_problems(path, root):
    try:
        with open(path, "rb") as f:
            inside = Initramfs(f.read())
    except OSError as error:
        return [f"it can't be read ({error})"], 0
    except Unreadable as error:
        return [str(error)], 0
    problems = []
    try:
        names = sorted(os.listdir(os.path.join(root, THEME_DIR)))
    except OSError:
        names = []
    for name in names:
        with open(os.path.join(root, THEME_DIR, name), "rb") as f:
            want = f.read()
        have = inside.read(f"{THEME_DIR}/{name}")
        if have is None:
            problems.append(f"{THEME_DIR}/{name} is missing")
        elif have != want:
            problems.append(f"{THEME_DIR}/{name} isn't the image's file")
    for name, what in NEEDED:
        if not inside.read(name):
            problems.append(f"{name} is missing ({what})")
    problems += settings_problems((inside.read("etc/plymouth/plymouthd.conf") or b"").decode("utf-8", "replace"),
                                  (inside.read("usr/share/plymouth/plymouthd.defaults") or b"").decode("utf-8", "replace"), "its")
    return problems, len(names)


def main(argv, load=ctypes.CDLL):
    args = argv[1:]
    root = "/"
    if args[:1] == ["--root"]:
        if len(args) < 2:
            print("check-boot-splash.py: --root needs a folder", file=sys.stderr)
            return 2
        root, args = args[1], args[2:]
    images = args or sorted(glob.glob(os.path.join(root, "usr/lib/modules/*/initramfs.img")))

    problems = theme_problems(root) + parser_problems(root, load)
    files = 0
    if not images:
        problems.append("There is no initramfs in /usr/lib/modules to check.")
    for image in images:
        found, files = initramfs_problems(image, root)
        problems += [f"{image}: {problem}" for problem in found]

    if problems:
        print("The startup screen wouldn't show as it should:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    print(f"Startup screen: {THEME} is Plymouth's default theme at its own size, Plymouth's parser accepts its "
          f"script, and {len(images)} initramfs carr{'ies' if len(images) == 1 else 'y'} its {files} files, "
          "the script plugin and a font.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
