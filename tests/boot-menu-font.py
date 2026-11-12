#!/usr/bin/env python3
"""make-boot-menu-font.py, run on small made-up fonts.

The image build makes the boot menu's large text from the font GRUB ships.
Each test here writes a font in GRUB's PF2 format, lets the script enlarge it
and reads the result back with a reader of its own, so a mistake the script
makes when writing can't hide behind the same mistake when reading.

    python3 tests/boot-menu-font.py
"""

from pathlib import Path
import struct
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "files/scripts/make-boot-menu-font.py"
GRUBS_OWN = Path("/usr/share/grub/unicode.pf2")

ASCII = list(range(0x20, 0x7F))
# The arrows, lines, corners and triangles in GRUB's include/grub/unicode.h.
FRAME = [0x2190, 0x2191, 0x2192, 0x2193, 0x2195, 0x2500, 0x2501, 0x2502, 0x2503, 0x250C, 0x250F, 0x2510,
         0x2513, 0x2514, 0x2517, 0x2518, 0x251B, 0x25B2, 0x25BA, 0x25BC, 0x25C4]
WANTED = sorted(ASCII + FRAME)
# Letters the script has to leave out.
OTHERS = [0x00E9, 0x2504, 0x4E2D, 0x1F600]


def made_up_letter(code, wide=()):
    """Size, place and pixels that differ from letter to letter, odd sizes among them.

    The ASCII letters move the pen on by 8 pixels, as GRUB's font does and the
    boot menu counts on, except those in wide.
    """
    seed = code * 2654435761 % 2**32
    width, height = 3 + seed % 14, 2 + (seed >> 4) % 15
    x, y, advance = (seed >> 8) % 3 - 1, (seed >> 10) % 5 - 3, 8 + 8 * (seed >> 13 & 1)
    if code < 0x7F:
        advance = 9 if code in wide else 8
    rows = []
    for row in range(height):
        seed = (seed * 1103515245 + 12345) % 2**31
        rows.append([seed >> (column + 3) & 1 for column in range(width)])
    return width, height, x, y, advance, rows


def section(name, body):
    return name.encode() + struct.pack(">I", len(body)) + body


def write_font(path, codes, family="Unifont", size=16, ascent=14, descent=2, cut=0, wide=()):
    """A PF2 font with a made-up letter for each of codes. cut: bytes to leave off its end."""
    head = (section("FILE", b"PFF2") + section("NAME", f"{family} Regular {size}".encode() + b"\0")
            + section("FAMI", family.encode() + b"\0") + section("WEIG", b"normal\0") + section("SLAN", b"normal\0")
            + section("PTSZ", struct.pack(">H", size)) + section("MAXW", struct.pack(">H", 16))
            + section("MAXH", struct.pack(">H", ascent + descent)) + section("ASCE", struct.pack(">H", ascent))
            + section("DESC", struct.pack(">H", descent)))
    first = len(head) + 8 + 9 * len(codes) + 8
    index, pixels = b"", b""
    for code in sorted(codes):
        width, height, x, y, advance, rows = made_up_letter(code, wide)
        bits = "".join(str(bit) for row in rows for bit in row)
        bits += "0" * (-len(bits) % 8)
        index += struct.pack(">IBI", code, 0, first + len(pixels))
        pixels += struct.pack(">HHhhh", width, height, x, y, advance) + int(bits, 2).to_bytes(len(bits) // 8, "big")
    data = head + section("CHIX", index) + b"DATA" + struct.pack(">I", 0xFFFFFFFF) + pixels
    Path(path).write_bytes(data[:len(data) - cut])


def read_font(path):
    """Everything in a PF2 file: its sections in order, and each letter as (size, place, pixels)."""
    data = Path(path).read_bytes()
    position, sections, order = 0, {}, []
    while True:
        name = data[position:position + 4].decode()
        (length,) = struct.unpack(">I", data[position + 4:position + 8])
        position += 8
        order.append(name)
        if name == "DATA":
            sections[name] = (length, position)
            break
        sections[name] = data[position:position + length]
        position += length
    letters, entries = {}, []
    for start in range(0, len(sections["CHIX"]), 9):
        code, flags, offset = struct.unpack(">IBI", sections["CHIX"][start:start + 9])
        width, height, x, y, advance = struct.unpack(">HHhhh", data[offset:offset + 10])
        size = (width * height + 7) // 8
        packed = data[offset + 10:offset + 10 + size]
        bits = bin(int.from_bytes(packed, "big"))[2:].zfill(8 * size) if size else ""
        rows = [[int(bits[row * width + column]) for column in range(width)] for row in range(height)]
        letters[code] = (width, height, x, y, advance, rows)
        entries.append((code, flags, offset, 10 + size))
    return SimpleNamespace(sections=sections, order=order, letters=letters, entries=entries, size=len(data))


class LargeFont(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.source = Path(self.dir.name) / "source.pf2"
        self.target = Path(self.dir.name) / "large.pf2"

    def tearDown(self):
        self.dir.cleanup()

    def make(self, source=None, target=None):
        done = subprocess.run([sys.executable, "-B", str(SCRIPT), str(source or self.source), str(target or self.target)],
                              capture_output=True, text=True)
        return done.returncode, done.stdout, done.stderr

    def refused(self, why):
        code, out, err = self.make()
        self.assertEqual(code, 1, out + err)
        self.assertIn("The boot menu's large font wasn't made", err)
        self.assertIn(why, err)
        self.assertFalse(self.target.exists(), "a font was written all the same")

    def test_every_letter_is_the_old_one_twice_as_large(self):
        write_font(self.source, WANTED + OTHERS)
        code, out, err = self.make()
        self.assertEqual(code, 0, err)
        self.assertIn(f"Unifont Regular 32, {len(WANTED)} letters", out)
        font = read_font(self.target)
        self.assertEqual(sorted(font.letters), WANTED)
        for code in WANTED:
            width, height, x, y, advance, rows = made_up_letter(code)
            new = font.letters[code]
            self.assertEqual(new[:5], (2 * width, 2 * height, 2 * x, 2 * y, 2 * advance), f"U+{code:04X}")
            for row in range(2 * height):
                self.assertEqual(new[5][row], [rows[row // 2][column // 2] for column in range(2 * width)], f"U+{code:04X} row {row}")

    def test_the_file_is_one_grub_can_read(self):
        write_font(self.source, WANTED)
        self.assertEqual(self.make()[0], 0)
        font = read_font(self.target)
        sections = font.sections
        # GRUB's font/font.c wants the file to start with FILE, and stops reading sections at DATA.
        self.assertEqual(font.order[0], "FILE")
        self.assertEqual(font.order[-1], "DATA")
        self.assertEqual(len(font.order), len(set(font.order)), "a section is there twice")
        self.assertEqual(sections["FILE"], b"PFF2")
        self.assertEqual(sections["NAME"], b"Unifont Regular 32\0")
        self.assertEqual(sections["FAMI"], b"Unifont\0")
        self.assertEqual(struct.unpack(">H", sections["PTSZ"]), (32,))
        self.assertEqual(struct.unpack(">H", sections["ASCE"]), (28,))
        self.assertEqual(struct.unpack(">H", sections["DESC"]), (4,))
        self.assertEqual(struct.unpack(">H", sections["MAXH"]), (32,))
        widest = max(max(letter[0], letter[4]) for letter in font.letters.values())
        self.assertEqual(struct.unpack(">H", sections["MAXW"]), (widest,))
        # GRUB finds a letter by halving the list, so the list has to be in order.
        codes = [entry[0] for entry in font.entries]
        self.assertEqual(codes, sorted(codes))
        self.assertTrue(all(flags == 0 for _, flags, _, _ in font.entries))
        # The letters follow the DATA header one after the other and fill the file to its end.
        length, position = sections["DATA"]
        self.assertEqual(length, 0xFFFFFFFF)
        for _, _, offset, size in font.entries:
            self.assertEqual(offset, position)
            position += size
        self.assertEqual(position, font.size)

    def test_the_same_font_comes_out_every_time(self):
        write_font(self.source, WANTED + OTHERS)
        self.assertEqual(self.make()[0], 0)
        first = self.target.read_bytes()
        again = Path(self.dir.name) / "again.pf2"
        self.assertEqual(self.make(target=again)[0], 0)
        self.assertEqual(first, again.read_bytes())

    def test_something_else_than_a_font_is_refused(self):
        self.source.write_text("not a font\n" * 40)
        self.refused("it isn't a PF2 font")

    def test_an_empty_file_is_refused(self):
        self.source.write_bytes(b"")
        self.refused("it isn't a PF2 font")

    def test_a_missing_file_is_refused(self):
        self.refused("No such file")

    def test_a_font_without_one_of_the_letters_is_refused(self):
        for missing in (0x41, 0x7E, 0x2501, 0x25C4):
            with self.subTest(letter=hex(missing)):
                write_font(self.source, [code for code in WANTED if code != missing])
                self.refused(f"it has no letter U+{missing:04X}")

    def test_a_font_cut_short_is_refused(self):
        # Inside the last letter's pixels, inside its header, and before the letters start.
        for cut, why in ((1, "pixels run past the end"), (12, "past the end of the file"), (2000, "past the end of the file")):
            with self.subTest(cut=cut):
                write_font(self.source, WANTED, cut=cut)
                self.refused(why)
        write_font(self.source, WANTED)
        whole = self.source.read_bytes()
        self.source.write_bytes(whole[:60])
        self.refused("it ends before its letters start")

    def test_another_font_than_the_menu_is_laid_out_for_is_refused(self):
        # The themes and boot-menu count on GRUB's Unifont: 16 pixels tall, 8 a letter.
        write_font(self.source, WANTED, family="Terminus")
        self.refused("it is Terminus 16, and the boot menu is laid out for Unifont 16")
        write_font(self.source, WANTED, size=12, ascent=10, descent=2)
        self.refused("it is Unifont 12, and the boot menu is laid out for Unifont 16")
        write_font(self.source, WANTED, wide=(0x6D,))
        self.refused("its letter U+006D is 9 pixels wide, and the boot menu counts on 8")

    def test_the_name_is_the_one_the_theme_asks_for(self):
        # Fedora's build of the font calls its family "Unifont", Ubuntu's "GNU Unifont".
        theme = (ROOT / "files/system/usr/share/monolith/grub/themes/monolith/theme-2x.txt").read_text()
        self.assertIn('item_font = "Unifont Regular 32"', theme)
        for family in ("Unifont", "GNU Unifont"):
            with self.subTest(family=family):
                write_font(self.source, WANTED, family=family)
                self.assertEqual(self.make()[0], 0)
                self.assertEqual(read_font(self.target).sections["NAME"], b"Unifont Regular 32\0")

    def test_a_target_that_cannot_be_written_fails(self):
        write_font(self.source, WANTED)
        code, _, err = self.make(target=Path(self.dir.name) / "no-such-folder" / "large.pf2")
        self.assertEqual(code, 1)
        self.assertIn("The boot menu's large font wasn't made", err)

    def test_the_build_uses_grubs_own_font(self):
        text = SCRIPT.read_text()
        self.assertIn('SOURCE = "/usr/share/grub/unicode.pf2"\n', text)
        self.assertTrue(SCRIPT.stat().st_mode & 0o111, "the build runs the script directly, so it has to be executable")
        self.assertTrue(text.startswith("#!/usr/bin/python3\n"))

    @unittest.skipUnless(GRUBS_OWN.exists(), f"this machine has no {GRUBS_OWN}")
    def test_grubs_own_font(self):
        code, out, err = self.make(source=GRUBS_OWN)
        self.assertEqual(code, 0, err)
        self.assertIn(f"Unifont Regular 32, {len(WANTED)} letters", out)
        font = read_font(self.target)
        self.assertEqual(sorted(font.letters), WANTED)
        # Unifont's letters are 8 pixels wide and its lines fill their cell, so the frame closes.
        for code in ASCII:
            self.assertEqual(font.letters[code][4], 16, f"U+{code:04X} is not 16 pixels wide")
        line = font.letters[0x2501]
        self.assertEqual((line[0], line[4]), (16, 16))
        self.assertTrue(all(any(row[column] for row in line[5]) for column in range(16)), "the level line has a gap")
        self.assertLess(font.size, 16 * 1024)


if __name__ == "__main__":
    unittest.main(verbosity=1)
