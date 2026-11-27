#!/usr/bin/env python3
"""The COSMIC edition's terminal: its build check and the solid logo.

check-cosmic-terminal.py runs against made-up images here: the repository's
own defaults for COSMIC Terminal, a stand-in fish and font files that hold
only the three tables the check reads.

The solid logo is fastfetch's mark built from whole cells, for a terminal
that leaves gaps between block characters. It must stay the large mark with
every cell filled or emptied; after changing the mark, write it again:

    python3 tests/cosmic-terminal.py
    python3 tests/cosmic-terminal.py --write
"""

import json
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "files/scripts/check-cosmic-terminal.py"
DEFAULTS = "usr/share/cosmic/com.system76.CosmicTerm/v1"
MARK = ROOT / "files/system/usr/share/fastfetch/logos/monolith.txt"
SOLID = ROOT / "files/cosmic/usr/share/fastfetch/logos/monolith-solid.txt"
RECIPE = ROOT / "recipes/cosmic.yml"

# A cell is filled when half of it or more is.
FILLED = set("█▀▄▌▐▚▞▛▜▙▟")
EMPTY = set(" ▘▝▖▗")
# Reverse video fills a cell with the terminal's own text color, as the outer
# slabs have; the middle slab is the accent.
FILL = {"1": "\x1b[7m", "2": "\x1b[104m"}
RESET = "\x1b[0m"


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def solid_logo(mark):
    """The mark, in fastfetch's logo format, as whole cells."""
    lines = []
    for row in mark.splitlines():
        out, ink, on, at = "", "1", "", 0
        while at < len(row):
            if row[at] == "$" and row[at + 1:at + 2] in FILL:
                ink = row[at + 1]
                at += 2
                continue
            if row[at] not in FILLED | EMPTY:
                raise ValueError(f"the mark has {row[at]!r}, which isn't a quadrant block")
            want = ink if row[at] in FILLED else ""
            if want != on:
                out += (RESET if on else "") + FILL.get(want, "")
                on = want
            out += " "
            at += 1
        lines.append(out + (RESET if on else ""))
    return "\n".join(lines) + "\n"


def font(family, weight, fixed=True, width=5):
    """A font file with only the name, post and OS/2 tables."""
    text = family.encode("utf-16-be")
    name = struct.pack(">HHH", 0, 1, 18) + struct.pack(">6H", 3, 1, 0x409, 1, len(text), 0) + text
    post = struct.pack(">IIhhI", 0x00030000, 0, 0, 0, 1 if fixed else 0) + bytes(16)
    os2 = struct.pack(">HhHH", 4, 0, weight, width) + bytes(70)
    parts = sorted({b"OS/2": os2, b"name": name, b"post": post}.items())
    directory, body, start = b"", b"", 12 + 16 * len(parts)
    for tag, data in parts:
        directory += struct.pack(">4sIII", tag, 0, start + len(body), len(data))
        body += data + bytes(-len(data) % 4)
    return struct.pack(">IHHHH", 0x00010000, len(parts), 0, 0, 0) + directory + body


class Image:
    """A made-up image root with the repository's defaults in it."""

    def __init__(self, folder):
        self.root = Path(folder)
        shutil.copytree(ROOT / "files/cosmic" / DEFAULTS, self.root / DEFAULTS)
        fonts = self.root / "usr/share/fonts/nerd-fonts/JetBrainsMono"
        fonts.mkdir(parents=True)
        for weight in (400, 700):
            # Nerd Fonts' plain family isn't marked fixed-pitch; its Mono one is.
            (fonts / f"plain-{weight}.ttf").write_bytes(font("JetBrainsMono Nerd Font", weight, fixed=False))
            (fonts / f"mono-{weight}.ttf").write_bytes(font("JetBrainsMono Nerd Font Mono", weight))
        (fonts / "thin.ttf").write_bytes(font("Only Regular Mono", 400))
        fish = self.root / "usr/bin/fish"
        fish.parent.mkdir(parents=True)
        fish.write_text("#!/bin/sh\n", encoding="utf-8")
        fish.chmod(0o755)

    def edit(self, key, pattern, replacement, flags=0):
        path = self.root / DEFAULTS / key
        text, count = re.subn(pattern, replacement, read(path), flags=flags)
        if count != 1:
            raise AssertionError(f"{key}: {pattern!r} matched {count} times")
        path.write_text(text, encoding="utf-8")

    def check(self):
        result = subprocess.run([sys.executable, "-B", str(CHECK), str(self.root)],
                                capture_output=True, text=True, check=False)
        return result.returncode, result.stdout, result.stderr


class CheckTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.image = Image(folder.name)

    def fails(self, words):
        code, out, err = self.image.check()
        self.assertEqual(code, 1, out + err)
        self.assertIn(words, err)
        self.assertEqual(out, "")

    def test_the_repositorys_defaults_pass(self):
        code, out, err = self.image.check()
        self.assertEqual((code, err), (0, ""), out + err)
        self.assertEqual(out, "COSMIC Terminal: JetBrainsMono Nerd Font Mono 16px, Gruvbox Dark Medium, /usr/bin/fish.\n")

    def test_a_comment_in_a_file_is_fine(self):
        self.image.edit("font_name", r"\A", "// Monolith's font\n")
        self.assertEqual(self.image.check()[0], 0)

    def test_the_font_ghostty_uses_is_refused(self):
        self.image.edit("font_name", r' Mono"', '"')
        self.fails("would not offer the font 'JetBrainsMono Nerd Font': it isn't marked fixed-pitch")
        self.assertIn("JetBrains families it would offer: JetBrainsMono Nerd Font Mono", self.image.check()[2])

    def test_a_font_without_a_bold_is_refused(self):
        self.image.edit("font_name", r'".*"', '"Only Regular Mono"')
        self.fails("would not offer the font 'Only Regular Mono'")

    def test_a_font_that_is_not_installed_is_refused(self):
        self.image.edit("font_name", r'".*"', '"Nope Sans Mono"')
        self.fails("'Nope Sans Mono': no font file has that family name")

    def test_a_missing_font_file_is_refused(self):
        (self.image.root / DEFAULTS / "font_name").unlink()
        self.fails("font_name is missing")

    def test_a_size_the_terminal_cannot_show_is_refused(self):
        self.image.edit("font_size", r"\d+", "64")
        self.fails("font_size 64 is outside COSMIC Terminal's 4 to 32")

    def test_a_profile_naming_a_missing_scheme_is_refused(self):
        self.image.edit("profiles", r'(syntax_theme_dark: )"Gruvbox Dark Medium"', r'\1"Gruvbox Dark"')
        self.fails("profile 'Monolith' names 'Gruvbox Dark', which is not a dark colour scheme")

    def test_a_theme_naming_a_missing_scheme_is_refused(self):
        self.image.edit("syntax_theme_dark", "Medium", "Hard")
        self.fails("syntax_theme_dark names 'Gruvbox Dark Hard', which is not a dark colour scheme")

    def test_a_scheme_missing_a_colour_is_refused(self):
        self.image.edit("color_schemes_dark", r"(dim: \(\s*black: [^\n]*\n)\s*red: [^\n]*\n", r"\1")
        self.fails("colour scheme 'Gruvbox Dark Medium' has no dim red")

    def test_a_scheme_without_a_cursor_is_refused(self):
        self.image.edit("color_schemes_dark", r"\s*cursor: [^\n]*", "")
        self.fails("colour scheme 'Gruvbox Dark Medium' has no cursor")

    def test_a_default_profile_that_does_not_exist_is_refused(self):
        self.image.edit("default_profile", "1", "2")
        self.fails("default_profile 2 is not in profiles")

    def test_no_default_profile_is_refused(self):
        self.image.edit("default_profile", r"Some\(1\)", "None")
        self.fails("default_profile is not set, so the terminal starts the login shell")

    def test_a_shell_that_is_not_in_the_image_is_refused(self):
        self.image.edit("profiles", "/usr/bin/fish", "/usr/bin/zsh")
        self.fails("profile 'Monolith' starts '/usr/bin/zsh', which is not a program in the image")

    def test_a_shell_without_its_folder_is_refused(self):
        self.image.edit("profiles", "/usr/bin/fish", "fish")
        self.fails("profile 'Monolith' starts 'fish', which is not a program in the image")

    def test_a_file_that_does_not_parse_is_refused(self):
        self.image.edit("profiles", r"\}\s*\Z", "")
        self.fails(f"/{DEFAULTS}/profiles: line ")


class SolidLogoTests(unittest.TestCase):
    def test_it_is_the_mark_in_whole_cells(self):
        self.assertEqual(read(SOLID), solid_logo(read(MARK)),
                         "run: python3 tests/cosmic-terminal.py --write")

    def test_it_takes_the_marks_room_and_leaves_nothing_switched_on(self):
        mark = [re.sub(r"\$[12]", "", row) for row in read(MARK).splitlines()]
        rows = read(SOLID).splitlines()
        self.assertEqual(len(rows), len(mark))
        for row, original in zip(rows, mark):
            plain = re.sub(r"\x1b\[[0-9;]*m", "", row)
            self.assertEqual(plain, " " * len(original))
            # fastfetch starts the next row, and its padding, with the last
            # color code of this one.
            self.assertTrue(row.endswith(RESET), repr(row))
            self.assertEqual(re.findall(r"\x1b\[[0-9;]*m", row)[-1], RESET)

    def test_the_slabs_keep_their_colours(self):
        solid = read(SOLID)
        self.assertNotIn("$", solid)
        # Two outer slabs in each of the thirteen rows, and twice in the row
        # where their sloped tops end in mid-air. The middle slab starts in
        # the fifth row.
        self.assertEqual(solid.count(FILL["1"]), 28)
        self.assertEqual(solid.count(FILL["2"]), 9)

    def test_the_recipe_shows_it_in_fastfetch(self):
        config = read(ROOT / "files/system/etc/xdg/fastfetch/config.jsonc")
        usual = '"/usr/share/fastfetch/logos/monolith.txt"'
        solid = '"/usr/share/fastfetch/logos/monolith-solid.txt"'
        # The recipe swaps the one path in fastfetch's settings.
        self.assertEqual(config.count(usual), 1)
        self.assertIn(f"sed -i 's|{usual}|{solid}|'", read(RECIPE))

    def test_the_recipe_drops_fastfetchs_terminal_font_row(self):
        # fastfetch can't read the font Monolith sets, so the row would name another one. The
        # recipe's own sed runs here on the shared settings, which have to stay valid JSON.
        command = re.search(r"sed -i -z '(s\|[^']+)' \\\n", read(RECIPE))
        assert command is not None, "cosmic.yml has no sed for fastfetch's Terminal Font row"
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp, "config.jsonc")
            shutil.copy(ROOT / "files/system/etc/xdg/fastfetch/config.jsonc", config)
            before = json.loads(read(config))["modules"]
            subprocess.run(["sed", "-i", "-z", command.group(1), str(config)], check=True)
            after = json.loads(read(config))["modules"]
        self.assertIn({"type": "terminal", "key": " \ue795"}, after)
        self.assertEqual([row for row in before if row not in after], [{"type": "terminalfont", "key": " \uf031"}])
        self.assertEqual(len(after), len(before) - 1)


if __name__ == "__main__":
    if sys.argv[1:] == ["--write"]:
        SOLID.parent.mkdir(parents=True, exist_ok=True)
        SOLID.write_text(solid_logo(read(MARK)), encoding="utf-8")
        print(f"wrote {SOLID.relative_to(ROOT)}")
        sys.exit(0)
    unittest.main()
