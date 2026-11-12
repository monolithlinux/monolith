#!/usr/bin/python3
"""Make the boot menu's large text: GRUB's own font with every pixel doubled.

On a screen that is 1800 pixels tall or more the boot menu is drawn at twice
the size (theme-2x.txt). Its text has to double as well, and GRUB can't scale
a font, so this writes one: the printable ASCII letters of the font GRUB
ships, Unifont Regular 16 in /usr/share/grub/unicode.pf2, each pixel drawn
two by two. The menu then looks the same as on a 1920x1080 screen. The lines,
corners and arrows GRUB draws its edit screen with come along, so its frame
stays in one piece.

The image build runs it without arguments. To try it elsewhere:

    make-boot-menu-font.py [SOURCE.pf2 [TARGET.pf2]]

GRUB only loads the result when Secure Boot is off; see user.cfg.

The PF2 format (GRUB's manual, "Font file format", and grub-core/font/font.c)
is a row of sections, each a four letter name and a big-endian length. CHIX
lists code point, flags and file offset for each letter. A letter is its
width, height, x and y offset and advance, 16 bits each, then its pixels as
one stream of bits, most significant first, rows not padded.
"""

import struct
import sys

SOURCE = "/usr/share/grub/unicode.pf2"
TARGET = "/usr/share/monolith/grub/themes/monolith/unifont-32.pf2"
# The name theme-2x.txt asks GRUB for.
WANTED = "Unifont Regular 32"
SCALE = 2
# What the boot menu counts on in GRUB's font: boot-menu centres the entries
# by giving every letter 8 pixels.
FAMILY, SIZE, WIDTH = "Unifont", 16, 8
# What GRUB's own screens are drawn with: the arrows, lines, corners and
# triangles named in its include/grub/unicode.h.
FRAME = (0x2190, 0x2191, 0x2192, 0x2193, 0x2195, 0x2500, 0x2501, 0x2502, 0x2503, 0x250C, 0x250F, 0x2510,
         0x2513, 0x2514, 0x2517, 0x2518, 0x251B, 0x25B2, 0x25BA, 0x25BC, 0x25C4)
LETTERS = tuple(range(0x20, 0x7F)) + FRAME


class Failed(Exception):
    pass


def read_font(data):
    """The sections before the letters, and where each letter starts."""
    if data[:12] != b"FILE\x00\x00\x00\x04PFF2":
        raise Failed("it isn't a PF2 font")
    position, sections = 12, {}
    while True:
        if position + 8 > len(data):
            raise Failed("it ends before its letters start")
        name, (length,) = data[position:position + 4], struct.unpack(">I", data[position + 4:position + 8])
        position += 8
        if name == b"DATA":
            break
        sections[name.decode("ascii", "replace")] = data[position:position + length]
        position += length
    for name in ("FAMI", "PTSZ", "ASCE", "DESC", "CHIX"):
        if name not in sections:
            raise Failed(f"it has no {name} section")
    index = {}
    for start in range(0, len(sections["CHIX"]) - 8, 9):
        code, _flags, offset = struct.unpack(">IBI", sections["CHIX"][start:start + 9])
        index[code] = offset
    return sections, index


def read_letter(data, offset):
    if offset + 10 > len(data):
        raise Failed("a letter starts past the end of the file")
    width, height, x, y, advance = struct.unpack(">HHhhh", data[offset:offset + 10])
    bits = data[offset + 10:offset + 10 + (width * height + 7) // 8]
    if len(bits) < (width * height + 7) // 8:
        raise Failed("a letter's pixels run past the end of the file")
    rows = [[bits[(row * width + column) >> 3] >> (7 - ((row * width + column) & 7)) & 1 for column in range(width)]
            for row in range(height)]
    return width, height, x, y, advance, rows


def pack_letter(width, height, x, y, advance, rows):
    bits = bytearray((width * height + 7) // 8)
    for row in range(height):
        for column in range(width):
            if rows[row][column]:
                bits[(row * width + column) >> 3] |= 0x80 >> ((row * width + column) & 7)
    return struct.pack(">HHhhh", width, height, x, y, advance) + bytes(bits)


def section(name, body):
    return name.encode("ascii") + struct.pack(">I", len(body)) + body


def enlarge(data, scale=SCALE, letters=LETTERS):
    """The font in data with each pixel drawn scale by scale: its name and its bytes."""
    sections, index = read_font(data)
    (size,), (ascent,), (descent,) = (struct.unpack(">H", sections[name]) for name in ("PTSZ", "ASCE", "DESC"))
    family = sections["FAMI"].rstrip(b"\0").decode("ascii", "replace")
    if FAMILY not in family or size != SIZE:
        raise Failed(f"it is {family} {size}, and the boot menu is laid out for {FAMILY} {SIZE}")
    packed, widest = [], 0
    for code in letters:
        if code not in index:
            raise Failed(f"it has no letter U+{code:04X}")
        width, height, x, y, advance, rows = read_letter(data, index[code])
        if code < 0x7F and advance != WIDTH:
            raise Failed(f"its letter U+{code:04X} is {advance} pixels wide, and the boot menu counts on {WIDTH}")
        big = [[bit for bit in row for _ in range(scale)] for row in rows for _ in range(scale)]
        packed.append((code, pack_letter(width * scale, height * scale, x * scale, y * scale, advance * scale, big)))
        widest = max(widest, width * scale, advance * scale)
    name = WANTED
    head = (section("FILE", b"PFF2") + section("NAME", name.encode("ascii") + b"\0")
            + section("FAMI", FAMILY.encode("ascii") + b"\0") + section("WEIG", b"normal\0") + section("SLAN", b"normal\0")
            + section("PTSZ", struct.pack(">H", size * scale)) + section("MAXW", struct.pack(">H", widest))
            + section("MAXH", struct.pack(">H", (ascent + descent) * scale))
            + section("ASCE", struct.pack(">H", ascent * scale)) + section("DESC", struct.pack(">H", descent * scale)))
    # The letters follow the CHIX section and the eight bytes of the DATA header.
    first = len(head) + 8 + 9 * len(packed) + 8
    chix, pixels = b"", b""
    for code, letter in packed:
        chix += struct.pack(">IBI", code, 0, first + len(pixels))
        pixels += letter
    return name, head + section("CHIX", chix) + b"DATA" + struct.pack(">I", 0xFFFFFFFF) + pixels


def check(source, made, scale=SCALE, letters=LETTERS):
    """Read the new font back: every letter has to be the old one, pixel for pixel, scale times as large."""
    _, old_index = read_font(source)
    _, new_index = read_font(made)
    if sorted(new_index) != list(letters):
        raise Failed("the new font doesn't hold exactly the letters asked for")
    for code in letters:
        width, height, x, y, advance, rows = read_letter(source, old_index[code])
        new = read_letter(made, new_index[code])
        if new[:5] != (width * scale, height * scale, x * scale, y * scale, advance * scale) or any(
                new[5][row][column] != rows[row // scale][column // scale]
                for row in range(height * scale) for column in range(width * scale)):
            raise Failed(f"letter U+{code:04X} came out wrong")


def main(arguments):
    source_path = arguments[0] if arguments else SOURCE
    target_path = arguments[1] if len(arguments) > 1 else TARGET
    try:
        with open(source_path, "rb") as file:
            source = file.read()
        name, made = enlarge(source)
        check(source, made)
        with open(target_path, "wb") as file:
            file.write(made)
    except (Failed, OSError) as error:
        print(f"The boot menu's large font wasn't made from {source_path}: {error}", file=sys.stderr)
        return 1
    print(f"{target_path}: {name}, {len(LETTERS)} letters, {len(made)} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
