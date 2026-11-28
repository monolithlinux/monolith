#!/usr/bin/python3
"""Fail the image build if COSMIC Terminal would not use Monolith's defaults.

COSMIC Terminal reads one file per setting from
/usr/share/cosmic/com.system76.CosmicTerm/v1 and falls back without a word
when a value doesn't fit:

  * It only offers fonts whose files are marked fixed-pitch and that have a
    regular and a bold weight at normal width. Given any other name it picks
    the first font of its list and saves that as the user's choice.
    ("JetBrainsMono Nerd Font", which Ghostty uses, is not marked fixed-pitch.
    "JetBrainsMono Nerd Font Mono" is.)
  * A profile that names a missing colour scheme gets COSMIC's own colours.
  * A colour left out of a scheme is drawn black.

usage: check-cosmic-terminal.py [ROOT]      (ROOT defaults to /)
"""
import os
import shlex
import struct
import sys

DEFAULTS = "usr/share/cosmic/com.system76.CosmicTerm/v1"
FONT_DIRS = ("usr/share/fonts", "usr/local/share/fonts")
BUILT_IN = {"dark": "COSMIC Dark", "light": "COSMIC Light"}
ANSI = ("black", "red", "green", "yellow", "blue", "magenta", "cyan", "white")
SINGLE = ("foreground", "background", "cursor", "bright_foreground", "dim_foreground")


class Problem(Exception):
    pass


# --- the small part of RON these files use ---------------------------------

class Ron:
    def __init__(self, text, where):
        self.text, self.at, self.where = text, 0, where

    def fail(self, what):
        line = self.text.count("\n", 0, self.at) + 1
        raise Problem(f"{self.where}: line {line}: {what}")

    def space(self):
        while self.at < len(self.text):
            if self.text[self.at].isspace():
                self.at += 1
            elif self.text.startswith("//", self.at):
                end = self.text.find("\n", self.at)
                self.at = len(self.text) if end < 0 else end
            elif self.text.startswith("/*", self.at):
                end = self.text.find("*/", self.at)
                if end < 0:
                    self.fail("comment never ends")
                self.at = end + 2
            else:
                break

    def take(self, token):
        self.space()
        if self.text.startswith(token, self.at):
            self.at += len(token)
            return True
        return False

    def word(self):
        self.space()
        start = self.at
        while self.at < len(self.text) and (self.text[self.at].isalnum() or self.text[self.at] == "_"):
            self.at += 1
        return self.text[start:self.at]

    def value(self):
        self.space()
        if self.at >= len(self.text):
            self.fail("value missing")
        char = self.text[self.at]
        if char == '"':
            end = self.at + 1
            out = []
            while end < len(self.text) and self.text[end] != '"':
                if self.text[end] == "\\":
                    end += 1
                out.append(self.text[end:end + 1])
                end += 1
            if end >= len(self.text):
                self.fail("string never ends")
            self.at = end + 1
            return "".join(out)
        if char == "{":
            self.at += 1
            out = {}
            while not self.take("}"):
                key = self.value()
                if not self.take(":"):
                    self.fail("':' expected")
                out[key] = self.value()
                if not self.take(","):
                    if not self.take("}"):
                        self.fail("',' or '}' expected")
                    break
            return out
        if char == "(":
            return self.struct()
        if char.isdigit() or char == "-":
            start = self.at
            self.at += 1
            while self.at < len(self.text) and self.text[self.at].isdigit():
                self.at += 1
            return int(self.text[start:self.at])
        name = self.word()
        if name == "true":
            return True
        if name == "false":
            return False
        if name == "None":
            return None
        if name == "Some":
            if not self.take("("):
                self.fail("'(' expected after Some")
            inner = self.value()
            if not self.take(")"):
                self.fail("')' expected")
            return inner
        self.space()
        if name and self.text.startswith("(", self.at):
            return self.struct()
        self.fail(f"unexpected {self.text[self.at:self.at + 12]!r}")

    def struct(self):
        self.at += 1
        out = {}
        while not self.take(")"):
            key = self.word()
            if not key or not self.take(":"):
                self.fail("'name:' expected")
            out[key] = self.value()
            if not self.take(","):
                if not self.take(")"):
                    self.fail("',' or ')' expected")
                break
        return out


def setting(root, key, default=None):
    path = os.path.join(root, DEFAULTS, key)
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except FileNotFoundError:
        return default
    parser = Ron(text, f"/{DEFAULTS}/{key}")
    value = parser.value()
    parser.space()
    if parser.at != len(text):
        parser.fail("text after the value")
    return value


# --- fonts, read the way fontdb (COSMIC Terminal's font list) reads them ----

def faces(data):
    if data[:4] == b"ttcf":
        count = struct.unpack(">I", data[8:12])[0]
        return list(struct.unpack(f">{count}I", data[12:12 + 4 * count]))
    return [0]


def tables(data, offset):
    count = struct.unpack(">H", data[offset + 4:offset + 6])[0]
    out = {}
    for index in range(count):
        tag, _, start, length = struct.unpack(">4sIII", data[offset + 12 + 16 * index:offset + 28 + 16 * index])
        out[tag] = data[start:start + length]
    return out


def family(name_table):
    """Typographic family (16), else family (1); the English name when there is one."""
    count, strings = struct.unpack(">HH", name_table[2:6])
    found = {16: [], 1: []}
    for index in range(count):
        platform, encoding, language, name_id, length, start = struct.unpack(
            ">6H", name_table[6 + 12 * index:18 + 12 * index])
        if name_id not in found:
            continue
        raw = name_table[strings + start:strings + start + length]
        if platform == 3 or platform == 0:
            text = raw.decode("utf-16-be", "replace")
        elif platform == 1 and encoding == 0:
            text = raw.decode("mac-roman", "replace")
        else:
            continue
        english = (platform == 3 and language == 0x409) or (platform == 1 and language == 0)
        found[name_id].append((not english, text))
    names = found[16] or found[1]
    return sorted(names)[0][1] if names else None


def listed_families(root):
    """Family -> True when COSMIC Terminal would offer it."""
    weights = {}
    for font_dir in FONT_DIRS:
        for folder, _, files in os.walk(os.path.join(root, font_dir), followlinks=True):
            for name in files:
                if not name.lower().endswith((".ttf", ".otf", ".ttc", ".otc")):
                    continue
                try:
                    with open(os.path.join(folder, name), "rb") as handle:
                        data = handle.read()
                    for offset in faces(data):
                        parts = tables(data, offset)
                        fixed = parts[b"post"][12:16] != b"\0\0\0\0"
                        weight, width = struct.unpack(">HH", parts[b"OS/2"][4:8])
                        who = family(parts[b"name"])
                        weights.setdefault(who, set())
                        if fixed and width == 5:
                            weights[who].add(weight)
                except (KeyError, struct.error, OSError):
                    continue
    return {who: {400, 700} <= found for who, found in weights.items() if who}


# --- the checks -------------------------------------------------------------

def table(root, key):
    value = setting(root, key, {})
    if not isinstance(value, dict) or not all(isinstance(entry, dict) for entry in value.values()):
        raise Problem(f"/{DEFAULTS}/{key}: a map of entries expected")
    return value


def check(root):
    problems = []
    font = setting(root, "font_name")
    size = setting(root, "font_size")
    profiles = table(root, "profiles")
    default_profile = setting(root, "default_profile")
    schemes = {"dark": table(root, "color_schemes_dark"), "light": table(root, "color_schemes_light")}
    themes = {"dark": setting(root, "syntax_theme_dark", BUILT_IN["dark"]),
              "light": setting(root, "syntax_theme_light", BUILT_IN["light"])}

    if not isinstance(font, str):
        problems.append("font_name is missing")
    else:
        fonts = listed_families(root)
        if not fonts.get(font):
            why = ("it isn't marked fixed-pitch or lacks a regular or bold weight"
                   if font in fonts else "no font file has that family name")
            offered = ", ".join(sorted(name for name, ok in fonts.items() if ok and "JetBrains" in name)) or "none"
            problems.append(f"COSMIC Terminal would not offer the font {font!r}: {why}. "
                            f"JetBrains families it would offer: {offered}")
    if not isinstance(size, int) or not 4 <= size <= 32:
        problems.append(f"font_size {size!r} is outside COSMIC Terminal's 4 to 32")

    names = {}
    for kind, entries in schemes.items():
        names[kind] = {BUILT_IN[kind]}
        for scheme in entries.values():
            names[kind].add(scheme.get("name"))
            for key in SINGLE:
                if key not in scheme:
                    problems.append(f"colour scheme {scheme.get('name')!r} has no {key}")
            for group in ("normal", "bright", "dim"):
                colours = scheme.get(group)
                for colour in ANSI:
                    if not isinstance(colours, dict) or colour not in colours:
                        problems.append(f"colour scheme {scheme.get('name')!r} has no {group} {colour}")
    for kind, theme in themes.items():
        if theme not in names[kind]:
            problems.append(f"syntax_theme_{kind} names {theme!r}, which is not a {kind} colour scheme")

    command = None
    if default_profile is None:
        problems.append("default_profile is not set, so the terminal starts the login shell")
    elif default_profile not in profiles:
        problems.append(f"default_profile {default_profile} is not in profiles")
    else:
        profile = profiles[default_profile]
        for kind in ("dark", "light"):
            theme = profile.get(f"syntax_theme_{kind}")
            if theme not in names[kind]:
                problems.append(f"profile {profile.get('name')!r} names {theme!r}, which is not a {kind} colour scheme")
        text = profile.get("command")
        words = shlex.split(text) if isinstance(text, str) else []
        if not words:
            problems.append(f"profile {profile.get('name')!r} has no command")
        else:
            command = words[0]
            program = os.path.join(root, command.lstrip("/"))
            if not command.startswith("/") or not os.access(program, os.X_OK):
                problems.append(f"profile {profile.get('name')!r} starts {command!r}, which is not a program in the image")
    return problems, f"COSMIC Terminal: {font} {size}px, {themes['dark']}, {command}."


def main(argv):
    root = argv[1] if len(argv) > 1 else "/"
    try:
        problems, summary = check(root)
    except Problem as problem:
        problems, summary = [str(problem)], ""
    if problems:
        for problem in problems:
            print(f"ERROR: {problem}", file=sys.stderr)
        return 1
    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
