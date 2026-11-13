#!/usr/bin/env python3
"""Tests for Monolith's startup screen (the Plymouth theme in
files/system/usr/share/plymouth/themes/monolith).

Plymouth can't run on the test machine, so the theme's script runs here in a
small stand-in for Plymouth's script plugin: the same language rules (read from
Plymouth's source, src/plugins/splash/script) and the calls the script uses.
The stand-in also complains about what Plymouth would swallow in silence, such
as calling a function that doesn't exist. The image build asks Plymouth's own
parser as well (files/scripts/check-boot-splash.py).

Run: python3 tests/boot-splash.py
"""
import bz2
import contextlib
import gc
import gzip
import importlib.machinery
import importlib.util
import io
import lzma
import math
import os
import re
import shutil
import struct
import sys
import tempfile
import unittest
import weakref
import zlib

sys.dont_write_bytecode = True
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
THEME = os.path.join(REPO, "files/system/usr/share/plymouth/themes/monolith")
MENU = os.path.join(REPO, "files/system/usr/share/monolith/grub/themes/monolith")
SCRIPT = os.path.join(THEME, "monolith.script")
CHECK = os.path.join(REPO, "files/scripts/check-boot-splash.py")


def read(path, mode="r"):
    """What a file holds."""
    with open(path, mode) as f:
        return f.read()


# ---------------------------------------------------------------- pictures

def png(path):
    """(width, height, rows of (r, g, b, a)) of an 8-bit PNG without interlacing."""
    data = read(path, "rb")
    assert data[:8] == b"\x89PNG\r\n\x1a\n", path
    at, chunks, head = 8, b"", None
    while at < len(data):
        size, kind = struct.unpack(">I4s", data[at:at + 8])
        body = data[at + 8:at + 8 + size]
        if kind == b"IHDR":
            head = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            chunks += body
        at += 12 + size
    width, height, depth, colour, _, _, interlace = head
    assert depth == 8 and interlace == 0 and colour in (0, 2, 4, 6), (path, head)
    n = {0: 1, 2: 3, 4: 2, 6: 4}[colour]
    raw = zlib.decompress(chunks)
    stride, rows, before = width * n, [], bytearray(width * n)
    for y in range(height):
        kind = raw[y * (stride + 1)]
        line = bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for i in range(stride):
            a = line[i - n] if i >= n else 0
            b = before[i]
            c = before[i - n] if i >= n else 0
            if kind == 1:
                line[i] = (line[i] + a) & 255
            elif kind == 2:
                line[i] = (line[i] + b) & 255
            elif kind == 3:
                line[i] = (line[i] + (a + b) // 2) & 255
            elif kind == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        before = line
        row = []
        for x in range(width):
            v = line[x * n:(x + 1) * n]
            row.append({1: lambda: (v[0], v[0], v[0], 255), 3: lambda: (v[0], v[1], v[2], 255),
                        2: lambda: (v[0], v[0], v[0], v[1]), 4: lambda: tuple(v)}[n]())
        rows.append(row)
    return width, height, rows


def png_size(path):
    with open(path, "rb") as f:
        head = f.read(24)
    return struct.unpack(">II", head[16:24])


# ------------------------------------------- Plymouth's script language

class ScriptError(Exception):
    pass


def scan(text):
    """Tokens as (kind, value, line): kind is one of num, str, name, sym, end."""
    out, i, line = [], 0, 1
    while i < len(text):
        c = text[i]
        if c == "\n":
            line += 1
            i += 1
        elif c in " \t\r":
            i += 1
        elif c == "#" or text.startswith("//", i):
            while i < len(text) and text[i] != "\n":
                i += 1
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            if end < 0:
                raise ScriptError(f"line {line}: comment without an end")
            line += text.count("\n", i, end)
            i = end + 2
        elif c.isdigit():
            j = i
            while j < len(text) and text[j].isdigit():
                j += 1
            if j < len(text) and text[j] == ".":
                j += 1
                while j < len(text) and text[j].isdigit():
                    j += 1
            out.append(("num", float(text[i:j]), line))
            i = j
        elif c.isalpha() or c == "_":
            j = i
            while j < len(text) and (text[j].isalnum() or text[j] == "_"):
                j += 1
            out.append(("name", text[i:j], line))
            i = j
        elif c == '"':
            j, s = i + 1, ""
            while True:
                if j >= len(text) or text[j] == "\n":
                    raise ScriptError(f"line {line}: string without an end")
                if text[j] == '"':
                    break
                if text[j] == "\\":
                    j += 1
                    s += {"n": "\n", "e": "\x1b", "0": "\0"}.get(text[j], text[j])
                else:
                    s += text[j]
                j += 1
            out.append(("str", s, line))
            i = j + 1
        else:
            out.append(("sym", c, line))
            i += 1
    out.append(("end", None, line))
    return out


BINARY = [["|"], ["||"], ["&&"], ["==", "!="], [">=", "<=", ">", "<"], ["+", "-"], ["*", "/", "%"]]
ASSIGN = ["+=", "-=", "*=", "/=", "%=", "|=", "="]
WORDS = ("NULL", "INFINITY", "NAN", "global", "local", "this")


class Parser:
    """Plymouth's grammar (script-parse.c), as a tree of tuples."""

    def __init__(self, text):
        self.t, self.i = scan(text), 0

    def fail(self, what):
        raise ScriptError(f"line {self.t[self.i][2]}: {what}")

    def sym(self, s):
        """Is the operator s next? Operators are one or two symbols long."""
        for k, ch in enumerate(s):
            if self.t[self.i + k][:2] != ("sym", ch):
                return False
        return True

    def take(self, s):
        if self.sym(s):
            self.i += len(s)
            return True
        return False

    def need(self, s):
        if not self.take(s):
            self.fail(f"expected '{s}'")

    def name(self, value=None):
        kind, v, _ = self.t[self.i]
        return kind == "name" and (value is None or v == value)

    def operator(self, table):
        # Longest first, and never the first half of an assignment such as "+=".
        for s in sorted(table, key=len, reverse=True):
            if self.sym(s) and not (len(s) == 1 and any(self.sym(a) for a in ASSIGN[:-1] + ["||", "&&", "==", "++", "--"]
                                                           if a.startswith(s) and len(a) == 2)):
                return s
        return None

    def term(self):
        kind, v, _ = self.t[self.i]
        if kind in ("num", "str"):
            self.i += 1
            return (kind, v)
        if kind == "name":
            self.i += 1
            if v == "fun":
                return ("fun", *self.function())
            if v == "NULL":
                return ("null",)
            if v in ("INFINITY", "NAN"):
                return ("num", math.inf if v == "INFINITY" else math.nan)
            return (v,) if v in WORDS else ("var", v)
        if self.take("["):
            items = []
            while not self.take("]"):
                items.append(self.exp())
                if not self.sym("]"):
                    self.need(",")
            return ("set", items)
        if self.take("("):
            e = self.exp()
            self.need(")")
            return e
        self.fail("expected a value")

    def postfix(self):
        e = self.term()
        while True:
            if self.take("("):
                args = []
                while not self.take(")"):
                    args.append(self.exp())
                    if not self.sym(")"):
                        self.need(",")
                e = ("call", e, args)
            elif self.sym(".") and self.t[self.i + 1][0] == "name":
                e = ("at", e, ("str", self.t[self.i + 1][1]))
                self.i += 2
            elif self.take("["):
                e = ("at", e, self.exp())
                self.need("]")
            else:
                return e

    def prefix(self):
        for s in ("++", "--", "+", "-", "!"):
            if self.sym(s) and not (len(s) == 1 and self.sym(s + "=")):
                self.i += len(s)
                return ("pre" + s, self.prefix())
        return self.postfix()

    def after(self):
        e = self.prefix()
        while self.sym("++") or self.sym("--"):
            e = ("post" + self.t[self.i][1] * 2, e)
            self.i += 2
        return e

    def binary(self, level=0):
        if level >= len(BINARY):
            return self.after()
        a = self.binary(level + 1)
        while True:
            s = self.operator(BINARY[level])
            if s is None:
                return a
            self.i += len(s)
            a = (s, a, self.binary(level + 1))

    def exp(self):
        a = self.binary()
        for s in ASSIGN:
            if self.sym(s) and not (s == "=" and self.sym("==")):
                self.i += len(s)
                return ("set" + s, a, self.exp())
        return a

    def function(self):
        self.need("(")
        params = []
        while not self.take(")"):
            if not self.name():
                self.fail("expected a parameter's name")
            params.append(self.t[self.i][1])
            self.i += 1
            if not self.sym(")"):
                self.need(",")
        if not self.sym("{"):
            self.fail("expected '{' after a function's parameters")
        return params, self.op()

    def op(self):
        if self.take("{"):
            ops = []
            while not self.take("}"):
                if self.t[self.i][0] == "end":
                    self.fail("'{' without a '}'")
                ops.append(self.op())
            return ("block", ops)
        if self.name("if") or self.name("while"):
            kind = self.t[self.i][1]
            self.i += 1
            self.need("(")
            cond = self.exp()
            self.need(")")
            then = self.op()
            other = None
            if kind == "if" and self.name("else"):
                self.i += 1
                other = self.op()
            return (kind, cond, then, other)
        if self.name("do"):
            self.i += 1
            body = self.op()
            if not self.name("while"):
                self.fail("expected 'while' after 'do'")
            self.i += 1
            self.need("(")
            cond = self.exp()
            self.need(")")
            self.need(";")
            return ("do", cond, body, None)
        if self.name("for"):
            self.i += 1
            self.need("(")
            first = self.exp()
            self.need(";")
            cond = self.exp()
            self.need(";")
            last = self.exp()
            self.need(")")
            return ("block", [("exp", first), ("while", cond, self.op(), ("exp", last))])
        if self.name("fun") and self.t[self.i + 1][0] == "name":
            name = self.t[self.i + 1][1]
            self.i += 2
            return ("exp", ("set=", ("var", name), ("fun", *self.function())))
        for word in ("return", "break", "continue", "fail"):
            if self.name(word):
                self.i += 1
                value = None
                if word == "return" and not self.sym(";"):
                    value = self.exp()
                self.need(";")
                return (word, value)
        e = self.exp()
        self.need(";")
        return ("exp", e)

    def program(self):
        ops = []
        while self.t[self.i][0] != "end":
            ops.append(self.op())
        return ("block", ops)


class Slot:
    """A variable, an element of a hash, or a value on its way. Numbers, strings and NULL are
    copied on assignment; hashes, functions and Plymouth's own objects are shared."""
    __slots__ = ("v",)

    def __init__(self, v=None):
        self.v = v


class Hash:
    def __init__(self, parent=None):
        self.d, self.parent = {}, parent

    def peek(self, name):
        h = self
        while h is not None:
            if name in h.d:
                return h.d[name]
            h = h.parent
        return None

    def get(self, name):
        s = self.peek(name)
        if s is None:
            s = self.d[name] = Slot()
        return s


class Function:
    def __init__(self, params, body):
        self.params, self.body = params, body


class Native:
    def __init__(self, call):
        self.call = call


class Callable(Hash):
    """A hash that can also be called, like Image and Sprite."""

    def __init__(self, call):
        super().__init__()
        self.call = call


def text_of(v):
    if isinstance(v, str):
        return v
    if isinstance(v, float):
        return "%g" % v
    return "#NULL" if v is None else "#(object)"


def truth(v):
    if v is None:
        return False
    if isinstance(v, float):
        return v != 0 and not math.isnan(v)
    if isinstance(v, str):
        return v != ""
    return True


def compare(a, b):
    """'<', '>', '=' or '!' (different and not ordered), as script_obj_cmp."""
    if a is None and b is None:
        return "="
    if isinstance(a, float) and isinstance(b, float):
        return "<" if a < b else ">" if a > b else "=" if a == b else "!"
    if isinstance(a, str) and isinstance(b, str):
        return "<" if a < b else ">" if a > b else "="
    if not isinstance(a, (float, str)) and a is not None and a is b:
        return "="
    return "!"


class Interpreter:
    def __init__(self):
        self.globals = Hash()
        self.complaints = []          # what Plymouth would let pass in silence

    def complain(self, what):
        self.complaints.append(what)

    def run(self, text):
        self.execute(Parser(text).program(), self.globals, None)

    # op -> (how it ended, value)
    def execute(self, op, local, this):
        kind = op[0]
        if kind == "exp":
            self.value(op[1], local, this)
        elif kind == "block":
            for sub in op[1]:
                r = self.execute(sub, local, this)
                if r[0] != "normal":
                    return r
        elif kind == "if":
            branch = op[2] if truth(self.value(op[1], local, this).v) else op[3]
            if branch is not None:
                return self.execute(branch, local, this)
        elif kind in ("while", "do"):
            first = kind == "do"
            while first or truth(self.value(op[1], local, this).v):
                first = False
                r = self.execute(op[2], local, this)
                if r[0] in ("return", "fail"):
                    return r
                if r[0] == "break":
                    break
                if op[3] is not None:
                    self.execute(op[3], local, this)
        elif kind == "return":
            return ("return", Slot(self.value(op[1], local, this).v) if op[1] is not None else Slot())
        else:
            return (kind, None)
        return ("normal", None)

    def call(self, f, this, args):
        """Call f (a value) with slots as arguments. Returns a value."""
        if isinstance(f, Callable):
            f = f.call
        if isinstance(f, Native):
            return f.call(this, [a.v for a in args])
        if isinstance(f, Function):
            local = Hash()
            for name, a in zip(f.params, args):
                local.get(name).v = a.v
            if this is not None:
                local.get("this").v = this
            r = self.execute(f.body, local, this)
            return r[1].v if r[0] == "return" else None
        return "not a function"

    def value(self, e, local, this):
        kind = e[0]
        if kind in ("num", "str"):
            return Slot(e[1])
        if kind == "null":
            return Slot()
        if kind == "var":
            for scope in (local, this if isinstance(this, Hash) else None, self.globals):
                if scope is not None:
                    s = scope.peek(e[1])
                    if s is not None:
                        return s
            return local.get(e[1])
        if kind == "local":
            return Slot(local)
        if kind == "global":
            return Slot(self.globals)
        if kind == "this":
            return Slot(this)
        if kind == "at":
            h = self.value(e[1], local, this)
            key = text_of(self.value(e[2], local, this).v)
            if not isinstance(h.v, Hash):
                if h.v is not None and e[1][0] not in ("local", "global"):
                    self.complain(f"'.{key}' asked of {text_of(h.v)!r}, which then becomes an empty hash")
                h.v = Hash()
            return h.v.get(key)
        if kind == "set":
            h = Hash()
            for i, item in enumerate(e[1]):
                h.get(str(i)).v = self.value(item, local, this).v
            return Slot(h)
        if kind == "fun":
            return Slot(Function(e[1], e[2]))
        if kind == "call":
            target, owner = e[1], None
            if target[0] == "at":
                owner = self.value(target[1], local, this).v
                key = text_of(self.value(target[2], local, this).v)
                f = owner.peek(key) if isinstance(owner, Hash) else None
                name = key
            else:
                f = self.value(target, local, this) if target[0] != "var" else None
                if target[0] == "var":
                    f = local.peek(target[1]) or (this.peek(target[1]) if isinstance(this, Hash) else None) \
                        or self.globals.peek(target[1])
                name = target[1] if target[0] == "var" else "?"
            args = [self.value(a, local, this) for a in e[2]]
            out = self.call(f.v if f is not None else None, owner, args)
            if isinstance(out, str) and out == "not a function":
                self.complain(f"call of '{name}', which is not a function")
                out = None
            return Slot(out)
        if kind in ("||", "&&"):
            a = self.value(e[1], local, this)
            if truth(a.v) == (kind == "||"):
                return a
            return self.value(e[2], local, this)
        if kind in ("==", "!=", "<", ">", "<=", ">="):
            c = compare(self.value(e[1], local, this).v, self.value(e[2], local, this).v)
            return Slot(1.0 if c in {"==": "=", "!=": "<>!", "<": "<", ">": ">", "<=": "<=", ">=": ">="}[kind] else 0.0)
        if kind in ("+", "-", "*", "/", "%", "|"):
            return Slot(self.arithmetic(kind, self.value(e[1], local, this).v, self.value(e[2], local, this).v))
        if kind.startswith("set"):
            a = self.value(e[1], local, this)
            b = self.value(e[2], local, this)
            a.v = b.v if kind == "set=" else self.arithmetic(kind[3], a.v, b.v)
            return a
        if kind == "pre!":
            return Slot(0.0 if truth(self.value(e[1], local, this).v) else 1.0)
        if kind == "pre+":
            return self.value(e[1], local, this)
        a = self.value(e[1], local, this)
        if kind == "pre-":
            if not isinstance(a.v, float):
                self.complain(f"minus in front of {text_of(a.v)!r}")
                return Slot()
            return Slot(-a.v)
        if not isinstance(a.v, float):                       # ++ and --
            self.complain(f"'{kind[-2:]}' on {text_of(a.v)!r}")
            a.v = None
            return Slot()
        before = a.v
        a.v += 1 if kind.endswith("++") else -1
        return Slot(a.v if kind.startswith("pre") else before)

    def arithmetic(self, kind, a, b):
        if isinstance(a, float) and isinstance(b, float):
            if kind == "+":
                return a + b
            if kind == "-":
                return a - b
            if kind == "*":
                return a * b
            if kind in ("/", "%") and b == 0:
                return math.nan if a == 0 or kind == "%" else math.copysign(math.inf, a)
            if kind == "/":
                return a / b
            if kind == "%":
                return math.fmod(a, b)
        if kind == "+" and (isinstance(a, str) or isinstance(b, str)):
            if a is None or b is None:
                self.complain("NULL joined to a string (it reads '#NULL')")
            return text_of(a) + text_of(b)
        self.complain(f"'{kind}' between {text_of(a)!r} and {text_of(b)!r}")
        return None


# ------------------------------------------------------ Plymouth's side

class Picture(Hash):
    def __init__(self, parent, name, width, height, text=None, font=None):
        super().__init__(parent)
        self.name, self.width, self.height, self.text, self.font = name, width, height, text, font


class Sprite(Hash):
    def __init__(self, parent, order):
        super().__init__(parent)
        self.image, self.x, self.y, self.z, self.opacity, self.order = None, 0, 0, 0, 1.0, order


class Plymouth:
    """What the script plugin gives a script, and what it does around the script's callbacks
    (plugin.c and script-lib-*.c of Plymouth 26.134.222)."""

    def __init__(self, screens=((1920, 1080),), mode="boot", script=SCRIPT, images=THEME):
        self.i = Interpreter()
        self.images, self.mode, self.capslock_asked = images, mode, 0
        self.screens = []                 # [width, height, x, y]: x and y are its place on the canvas
        self.hooks, self.texts, self.missing = {}, [], []
        self.sprites, self.made, self.background = weakref.WeakSet(), 0, None
        g = self.i.globals
        self.image_class, self.sprite_class = Hash(), Hash()
        for name, f in (("GetWidth", lambda this, a: float(this.width)), ("GetHeight", lambda this, a: float(this.height))):
            self.image_class.get(name).v = Native(f)
        image = Callable(Native(lambda this, a: self.picture(a[0])))
        image.get("Text").v = Native(lambda this, a: self.words(*a))
        g.get("Image").v = image
        for name in ("X", "Y", "Z", "Opacity"):
            attr = name.lower()
            self.sprite_class.get("Set" + name).v = Native(lambda this, a, attr=attr: self.put(this, attr, a[0]))
            self.sprite_class.get("Get" + name).v = Native(lambda this, a, attr=attr: float(getattr(this, attr)))
        self.sprite_class.get("SetImage").v = Native(lambda this, a: self.put(this, "image", a[0]))
        self.sprite_class.get("GetImage").v = Native(lambda this, a: this.image)
        self.sprite_class.get("SetPosition").v = Native(lambda this, a: [self.put(this, k, v) for k, v in zip("xyz", a)] and None)
        g.get("Sprite").v = Callable(Native(lambda this, a: self.sprite(a[0] if a else None)))
        window = Hash()
        for i, name in enumerate(("Width", "Height", "X", "Y")):
            window.get("Get" + name).v = Native(lambda this, a, i=i: self.window(i, a))
        window.get("SetX").v = Native(lambda this, a: self.move(a, 2))
        window.get("SetY").v = Native(lambda this, a: self.move(a, 3))
        window.get("SetBackgroundTopColor").v = Native(lambda this, a: setattr(self, "background", tuple(a)))
        window.get("SetBackgroundBottomColor").v = Native(lambda this, a: None)
        g.get("Window").v = window
        ply = Hash()
        for name in ("Refresh", "BootProgress", "RootMounted", "KeyboardInput", "UpdateStatus", "DisplayNormal",
                     "DisplayPassword", "DisplayQuestion", "DisplayPrompt", "DisplayHotplug", "ValidateInput",
                     "DisplayMessage", "HideMessage", "Quit", "SystemUpdate"):
            ply.get(f"Set{name}Function").v = Native(lambda this, a, name=name: self.hooks.__setitem__(name, a[0]))
        ply.get("SetMessageFunction").v = ply.peek("SetDisplayMessageFunction").v
        ply.get("GetMode").v = Native(lambda this, a: self.mode)
        ply.get("GetCapslockState").v = Native(self.asked_for_capslock)
        ply.get("SetRefreshRate").v = Native(lambda this, a: None)
        g.get("Plymouth").v = ply
        m = Hash()
        for name, f in (("Sin", math.sin), ("Cos", math.cos), ("Tan", math.tan), ("Sqrt", math.sqrt), ("Int", math.floor)):
            m.get(name).v = Native(lambda this, a, f=f: float(f(a[0])) if isinstance(a[0], float) else self.i.complain(f"Math on {a[0]!r}"))
        m.get("Abs").v = Native(lambda this, a: abs(a[0]))
        m.get("Min").v = Native(lambda this, a: min(a[0], a[1]))
        m.get("Max").v = Native(lambda this, a: max(a[0], a[1]))
        m.get("Clamp").v = Native(lambda this, a: min(max(a[0], a[1]), a[2]))
        m.get("Pi").v = math.pi
        g.get("Math").v = m
        for w, h in screens:
            self.screens.append([w, h, 0, 0])
        self.centre()
        self.i.run(read(script))

    # --- what the script calls

    def picture(self, name):
        path = os.path.join(self.images, name)
        if not isinstance(name, str) or not os.path.isfile(path):
            self.missing.append(name)
            return None
        return Picture(self.image_class, name, *png_size(path))

    def words(self, text=None, r=1.0, g=1.0, b=1.0, alpha=1.0, font=None, align=None):
        """Text has no real size here: the width follows the number of letters, as close to
        Plymouth's Noto Sans as the tests need (a VM measured 686 x 22 for 86 letters at 15px)."""
        if not isinstance(text, str):
            return None
        m = re.search(r" (\d+)(px)?$", font or "Sans 12")
        px = int(m.group(1)) * (1 if m.group(2) else 96 / 72)
        self.texts.append((text, font, align, (r, g, b)))
        return Picture(self.image_class, None, round(0.532 * px * len(text)), round(1.47 * px), text, font)

    def sprite(self, image):
        self.made += 1
        s = Sprite(self.sprite_class, self.made)
        s.image = image if isinstance(image, Picture) else None
        self.sprites.add(s)
        return s

    def put(self, sprite, attr, value):
        if not isinstance(sprite, Sprite):
            return self.i.complain(f"a sprite's {attr} set on something else")
        if attr == "image":
            sprite.image = value if isinstance(value, Picture) else None
        elif not isinstance(value, float) or math.isnan(value):
            self.i.complain(f"a sprite's {attr} set to {value!r}")
        else:
            setattr(sprite, attr, value if attr == "opacity" else int(value))

    def window(self, what, a):
        if a and a[0] is not None:
            i = int(a[0])
            return float(self.screens[i][what]) if 0 <= i < len(self.screens) else None
        if what < 2:
            return float(max([s[what] for s in self.screens], default=0))
        return float(min([s[what] for s in self.screens] + [0]))

    def move(self, a, what):
        if isinstance(a[0], float) and 0 <= int(a[0]) < len(self.screens):
            self.screens[int(a[0])][what] = int(a[1])

    # --- what Plymouth does

    def centre(self):
        """update_displays(): every screen back onto the middle of the largest one."""
        mw = max([s[0] for s in self.screens], default=0)
        mh = max([s[1] for s in self.screens], default=0)
        for s in self.screens:
            s[2], s[3] = (mw - s[0]) // 2, (mh - s[1]) // 2

    def hook(self, name, *args):
        f = self.hooks.get(name)
        if f is not None:
            return self.i.call(f, None, [Slot(a) for a in args])

    def tick(self, times=1):
        for _ in range(times):
            self.hook("Refresh")

    def password(self, prompt, bullets):
        self.hook("DisplayPassword", prompt, float(bullets))
        self.centre()
        self.hook("DisplayPrompt", prompt, "*" * bullets, 1.0)
        self.centre()

    def question(self, prompt, entry):
        self.hook("DisplayQuestion", prompt, entry)
        self.centre()
        self.hook("DisplayPrompt", prompt, entry, 0.0)
        self.centre()

    def normal(self):
        self.hook("DisplayNormal")
        self.centre()

    def message(self, text):
        self.hook("DisplayMessage", text)
        self.centre()

    def hide_message(self, text):
        self.hook("HideMessage", text)
        self.centre()

    def update(self, percent):
        self.hook("SystemUpdate", float(percent))

    def plug(self, width, height):
        self.screens.append([width, height, 0, 0])
        self.centre()
        self.hook("DisplayHotplug")

    def unplug(self, i):
        del self.screens[i]
        self.centre()
        self.hook("DisplayHotplug")

    # --- what is on a screen

    def shown(self, i=0):
        """The sprites Plymouth would draw on screen i, bottom first, placed from the screen's
        own top left corner: (name or text, x, y, opacity, picture)."""
        gc.collect()
        w, h, ox, oy = self.screens[i]
        out = []
        for s in sorted(self.sprites, key=lambda s: (s.z, s.order)):
            p = s.image
            if p is None or s.opacity < 0.011:
                continue
            x, y = s.x - ox, s.y - oy
            if x >= w or y >= h or x + p.width <= 0 or y + p.height <= 0:
                continue
            out.append((p.name or p.text, x, y, s.opacity, p))
        return out

    def asked_for_capslock(self, this, a):
        """The script plugin answers from the keyboard it was given when the script started
        (script-lib-plymouth.c: data->keyboard). Plymouth frees that keyboard and makes a new one
        when the screen's driver changes during the start, and the plugin's set_keyboard() doesn't
        pass the new one on. So this call can read freed memory, and a script must not make it."""
        self.capslock_asked += 1
        self.i.complaints.append("Plymouth.GetCapslockState() was called: it can read a keyboard Plymouth has freed")
        return 0.0

    def names(self, i=0):
        return [s[0] for s in self.shown(i)]

    def find(self, name, i=0):
        found = [s for s in self.shown(i) if s[0] == name]
        return found[0] if len(found) == 1 else None if not found else found


# ------------------------------------------------------------ the tests

def numbers():
    """The numbers at the top of the script."""
    return {m.group(1): float(m.group(2)) for m in re.finditer(r"^(\w+) = ([\d.]+);", read(SCRIPT), re.M)}


N = numbers()
IN = int(N["dots_in"])                 # refreshes the dots take to come in
LUKS = "Please enter passphrase for disk Samsung SSD 990 PRO 2TB (luks-0f3d7a52-6b1e-4c8a-9d2f-7e5a1b3c4d5e)"


def menu_place(width, height, scale):
    """Where GRUB puts the boot menu's logo, from the theme file's own words."""
    name = "theme.txt" if scale == 1 else "theme-2x.txt"
    text = read(os.path.join(MENU, name))
    block = re.search(r"\+ image \{[^}]*?file = \"logo[^\"]*\"[^}]*\}", text, re.S).group(0)
    left = re.search(r"left = (\d+)%-(\d+)", block)
    top = re.search(r"top = (\d+)%-(\d+)", block)
    # GRUB counts a percentage as a 16.16 fixed point number (gui_util: grub_fixed_*).
    def at(size, m):
        return (size * (int(m.group(1)) * 65536 // 100) >> 16) - int(m.group(2))
    return at(width, left), at(height, top)


def tag(scale):
    return "" if scale == 1 else "-2x"


class Pictures(unittest.TestCase):
    def test_the_logo_is_the_boot_menus_own_file(self):
        for scale in (1, 2):
            name = f"logo{tag(scale)}.png"
            self.assertEqual(read(os.path.join(THEME, name), "rb"), read(os.path.join(MENU, name), "rb"), name)
            self.assertEqual(png_size(os.path.join(THEME, name)), (N["logo_w"] * scale, N["logo_h"] * scale), name)

    def test_the_logo_is_centred_in_its_picture(self):
        # The dots sit in the middle of the screen. They are under the middle of the logo only
        # if the logo is drawn in the middle of its picture.
        for scale in (1, 2):
            w, _, logo = png(os.path.join(THEME, f"logo{tag(scale)}.png"))
            inked = [x for x in range(w) if any(row[x][3] for row in logo)]
            self.assertLessEqual(abs(inked[0] - (w - 1 - inked[-1])), scale, f"scale {scale}")

    def test_every_picture_is_there_at_both_sizes(self):
        names = set(re.findall(r'Image\("(\w+)" \+ tag \+ "\.png"\)', read(SCRIPT)))
        self.assertEqual(names, {"logo", "dot", "entry"})
        for name in names:
            w, h = png_size(os.path.join(THEME, name + ".png"))
            self.assertEqual(png_size(os.path.join(THEME, name + "-2x.png")), (2 * w, 2 * h), name)
        self.assertEqual(sorted(os.listdir(THEME)),
                         sorted([n + t + ".png" for n in names for t in ("", "-2x")] + ["monolith.plymouth", "monolith.script"]))
        self.assertEqual(png_size(os.path.join(THEME, "entry.png")), (N["field_w"], N["field_h"]))


class Wiring(unittest.TestCase):
    def test_the_theme_file_points_at_its_own_folder(self):
        text = read(os.path.join(THEME, "monolith.plymouth"))
        self.assertIn("\nModuleName=script\n", text)
        self.assertIn("\nImageDir=/usr/share/plymouth/themes/monolith\n", text)
        self.assertIn("\nScriptFile=/usr/share/plymouth/themes/monolith/monolith.script\n", text)

    def test_monolith_is_the_default_theme(self):
        conf = read(os.path.join(REPO, "files/system/etc/plymouth/plymouthd.conf"))
        self.assertRegex(conf, r"(?m)^\[Daemon\]\nTheme=monolith$")

    def test_plymouth_leaves_the_size_to_the_theme(self):
        # On a screen Plymouth takes for a dense one (a guess while the firmware's screen is in
        # use: 2880 x 1620 and up, or 3:2 from 1800 x 1200) it draws everything twice the size by
        # stretching, and tells the script a screen half as large. The logo would be blurred,
        # and on a 2256 x 1504 laptop twice the boot menu's. DeviceScale=1 turns that off; every
        # test here counts in real pixels.
        conf = read(os.path.join(REPO, "files/system/etc/plymouth/plymouthd.conf"))
        self.assertRegex(conf, r"(?m)^\[Daemon\]\n(?:[A-Za-z]+=.*\n)*DeviceScale=1$")
        for screen in ((2256, 1504), (2160, 1440), (1920, 1280), (2880, 1620), (2560, 1600)):
            p = Plymouth([screen])
            self.assertEqual(p.names(), ["logo.png"], screen)
            self.assertEqual(p.find("logo.png")[1:3], menu_place(*screen, 1), screen)

    def test_the_image_gets_the_plugin_and_is_checked_after_the_initramfs(self):
        common = read(os.path.join(REPO, "recipes/common.yml"))
        self.assertRegex(common, r"(?m)^\s+- plymouth-plugin-script$")
        for recipe in ("recipe-kde.yml", "recipe-kde-nvidia.yml"):
            text = read(os.path.join(REPO, "recipes", recipe))
            self.assertLess(text.index("- type: initramfs"), text.index("check-boot-splash.py"), recipe)

    def test_large_starts_where_the_boot_menus_does(self):
        menu = read(os.path.join(REPO, "files/system/usr/libexec/monolith/boot-menu"))
        self.assertIn(f"{int(N['large_from'])} pixels tall", menu)


class Story(unittest.TestCase):
    def check(self, p):
        self.assertEqual(p.i.complaints, [])
        self.assertEqual(p.missing, [])

    def test_it_starts_as_the_boot_menu_left_the_screen(self):
        for (w, h), scale in (((1920, 1080), 1), ((2560, 1440), 1), ((1366, 768), 1), ((3440, 1440), 1), ((1280, 800), 1),
                              ((3840, 2160), 2), ((2880, 1800), 2), ((3000, 2000), 2), ((1080, 1920), 2)):
            p = Plymouth([(w, h)])
            x, y = menu_place(w, h, scale)
            for when in ("before the first refresh", "after it"):
                self.assertEqual(p.names(), [f"logo{tag(scale)}.png"], (w, h, when))
                self.assertEqual(p.find(f"logo{tag(scale)}.png")[1:4], (x, y, 1.0), (w, h, when))
                p.tick()
            self.assertEqual(p.background, (0.0, 0.0, 0.0))
            self.check(p)

    def test_a_screen_too_short_for_the_logo_keeps_it_whole(self):
        p = Plymouth([(800, 600)])
        self.assertEqual(p.find("logo.png")[1:3], (211, 0))

    def test_the_whole_logo_stays(self):
        """Nothing folds or fades: through a minute of refreshes the logo is the boot menu's
        picture in the boot menu's place, and the only other things on the screen are the dots."""
        for (w, h), scale in (((1920, 1080), 1), ((3840, 2160), 2)):
            p = Plymouth([(w, h)])
            logo = (f"logo{tag(scale)}.png",) + menu_place(w, h, scale) + (1.0,)
            for c in range(3000):
                p.tick()
                if c < 200 or c % 50 == 0:
                    shown = p.shown()
                    self.assertEqual(shown[0][:4], logo, c)
                    self.assertEqual({s[0] for s in shown[1:]} - {f"dot{tag(scale)}.png"}, set(), c)
            self.check(p)

    def test_the_dots_come_in_under_the_logo(self):
        for (w, h), scale in (((1920, 1080), 1), ((3840, 2160), 2), ((1366, 768), 1)):
            p = Plymouth([(w, h)])
            y = menu_place(w, h, scale)[1]
            name = f"dot{tag(scale)}.png"
            p.tick(3)
            self.assertLess(max((s[3] for s in p.shown() if s[0] == name), default=0), 0.1, "they come in softly")
            p.tick(IN - 3)
            dots = [s for s in p.shown() if s[0] == name]
            self.assertEqual(len(dots), 3, (w, h))
            self.assertGreaterEqual(min(d[3] for d in dots), 0.349, "all the way in")
            self.assertEqual([d[1] + d[4].width / 2 - w / 2 for d in dots], [-N["dot_gap"] * scale, 0, N["dot_gap"] * scale])
            self.assertEqual({d[2] + d[4].height / 2 for d in dots}, {y + (N["logo_h"] + N["dots_down"]) * scale})
            self.assertGreaterEqual(min(d[2] for d in dots), y + N["logo_h"] * scale, "clear of the logo")
            self.check(p)

    def test_the_dots_keep_pulsing(self):
        p = Plymouth()
        p.tick(IN)
        low, high, lead = 1.0, 0.0, set()
        for _ in range(500):
            p.tick()
            dots = [s[3] for s in p.shown() if s[0] == "dot.png"]
            self.assertEqual(len(dots), 3, "no dot ever goes out")
            low, high = min(low, *dots), max(high, *dots)
            lead.add(dots.index(max(dots)))
        self.assertAlmostEqual(low, 0.35, 2)
        self.assertGreater(high, 0.99)
        self.assertEqual(lead, {0, 1, 2}, "the bright one goes round")
        self.assertEqual(p.names(), ["logo.png"] + ["dot.png"] * 3)
        self.check(p)

    def test_shutdown_and_restart_show_the_same_screen(self):
        for mode in ("shutdown", "reboot"):
            p = Plymouth(mode=mode)
            self.assertEqual(p.names(), ["logo.png"], mode)
            self.assertEqual(p.find("logo.png")[1:4], menu_place(1920, 1080, 1) + (1.0,), mode)
            p.tick(IN)
            self.assertEqual(p.names(), ["logo.png"] + ["dot.png"] * 3, mode)
            self.check(p)

    def test_an_update_says_what_it_is_doing(self):
        for mode, title in (("updates", "Installing updates"), ("system-upgrade", "Upgrading the system"),
                            ("firmware-upgrade", "Updating firmware"), ("system-reset", "Resetting the system")):
            p = Plymouth(mode=mode)
            for when in ("before the first refresh", "later"):
                self.assertEqual([n for n in p.names() if not n.endswith(".png")],
                                 [title, "Don't turn off your computer."], (mode, when))
                p.tick(IN)
            self.assertEqual(p.names()[:4], ["logo.png"] + ["dot.png"] * 3, "the dots show it is alive")
            heading, warning = p.find(title), p.find("Don't turn off your computer.")
            self.assertGreater(heading[2], max(s[2] + s[4].height for s in p.shown() if s[0] == "dot.png"), "under the dots")
            self.assertGreaterEqual(warning[2], heading[2] + heading[4].height)
            p.update(42.7)
            p.tick()
            self.assertIn(title + ", 42%", p.names())
            self.assertNotIn(title, p.names())
            self.check(p)


class Asking(unittest.TestCase):
    def check(self, p):
        self.assertEqual(p.i.complaints, [])
        self.assertEqual(p.missing, [])

    def dialog(self, p, i=0, scale=1):
        w, h = p.screens[i][:2]
        x, y = menu_place(w, h, scale)
        prompt = p.find(LUKS, i)
        field = p.find(f"entry{tag(scale)}.png", i)
        self.assertIsNotNone(prompt)
        self.assertIsNotNone(field)
        self.assertLessEqual(abs(prompt[1] + prompt[4].width / 2 - w / 2), 1, "the prompt is centred")
        self.assertEqual(field[1] + field[4].width / 2, w / 2, "the field is centred")
        self.assertGreaterEqual(prompt[2], y + N["logo_h"] * scale, "below the logo")
        self.assertGreaterEqual(field[2], prompt[2] + prompt[4].height, "the field is below the prompt")
        return prompt, field

    def test_a_passphrase_is_asked_for_under_the_whole_logo(self):
        p = Plymouth()
        p.tick(10)
        p.password(LUKS, 0)
        for bullets in (0, 1, 2, 3):
            p.password(LUKS, bullets)
            p.tick(30)
            self.assertEqual(p.find("logo.png")[1:4], menu_place(1920, 1080, 1) + (1.0,), "the logo stays as it is")
            prompt, field = self.dialog(p)
            dots = [s for s in p.shown() if s[0] == "dot.png"]
            self.assertEqual(len(dots), bullets, "only the bullets: the waiting dots are gone")
            for k, d in enumerate(dots):
                self.assertEqual(d[1], field[1] + 18 + k * N["bullet_gap"])
                self.assertEqual(d[2] + d[4].height / 2, field[2] + field[4].height / 2)
        p.normal()
        self.assertEqual(p.names(), ["logo.png"], "the question is gone")
        p.tick(IN)
        self.assertEqual(p.names(), ["logo.png"] + ["dot.png"] * 3, "and the dots are back")
        self.check(p)

    def test_the_dots_make_room_for_a_question_at_once(self):
        """The question goes where the dots are. They have to be gone in the same call, not
        one refresh later."""
        for kind in ("password", "question"):
            p = Plymouth()
            p.tick(100)
            self.assertEqual(p.names(), ["logo.png"] + ["dot.png"] * 3)
            if kind == "password":
                p.password(LUKS, 0)
                self.assertEqual(p.names(), ["logo.png", LUKS, "entry.png"], "no dots, no bullets yet")
            else:
                p.question("Which disk?", "")
                self.assertEqual(p.names(), ["logo.png", "Which disk?", "entry.png"])
            p.tick(50)
            self.assertNotIn("dot.png", p.names(), "and they stay away while it is up")
            self.check(p)

    def test_after_the_answer_the_dots_start_over(self):
        fresh = Plymouth()
        p = Plymouth()
        p.tick(137)
        p.password(LUKS, 0)
        p.tick(61)
        p.normal()
        for c in range(IN + 60):
            p.tick()
            fresh.tick()
            self.assertEqual([s[:4] for s in p.shown()], [s[:4] for s in fresh.shown()], c)
        self.assertEqual(p.names(), ["logo.png"] + ["dot.png"] * 3)
        self.check(p)

    def test_a_long_password_fills_the_field_and_no_more(self):
        p = Plymouth()
        p.password(LUKS, 64)
        p.tick()
        prompt, field = self.dialog(p)
        dots = [s for s in p.shown() if s[0] == "dot.png"]
        self.assertEqual(len(dots), N["bullets_shown"])
        self.assertLessEqual(dots[-1][1] + dots[-1][4].width, field[1] + field[4].width - 10)
        self.check(p)

    def test_a_question_shows_what_is_typed(self):
        p = Plymouth()
        p.tick(100)
        p.question("Which disk?", "")
        p.tick()
        self.assertEqual(p.names(), ["logo.png", "Which disk?", "entry.png"])
        p.question("Which disk?", "sda")
        p.tick()
        self.assertEqual(p.names(), ["logo.png", "Which disk?", "entry.png", "sda"])
        field, typed = p.find("entry.png"), p.find("sda")
        self.assertLessEqual(abs(typed[1] + typed[4].width / 2 - 960), 1)
        self.assertLessEqual(abs(typed[2] + typed[4].height / 2 - (field[2] + field[4].height / 2)), 1)
        p.normal()
        p.tick()
        self.assertEqual(p.names()[0], "logo.png")
        self.assertNotIn("entry.png", p.names())
        self.check(p)

    def test_caps_lock_is_never_asked_for(self):
        """In a VM plymouthd crashed in Plymouth.GetCapslockState() in about one start out of five
        when a passphrase was asked for after the screen's driver had changed, and the start then
        hung on a black screen. The stand-in's function explains why."""
        with open(SCRIPT, encoding="utf-8") as f:
            code = "".join(line.split("#", 1)[0] + "\n" for line in f)
        self.assertNotIn("Capslock", code)
        p = Plymouth()
        p.password(LUKS, 1)
        p.tick(3)
        p.password(LUKS, 2)
        p.tick(3)
        p.question("Which disk?", "s")
        p.tick(3)
        self.assertEqual(p.capslock_asked, 0)
        self.assertNotIn("Caps Lock is on", p.names())
        self.check(p)

    def test_a_prompt_too_wide_for_the_screen_is_set_smaller(self):
        for screen, font in (((1920, 1080), "Sans 15px"), ((800, 600), "Sans 13px"), ((640, 480), "Sans 11px"),
                             ((480, 800), "Sans 11px"), ((3840, 2160), "Sans 30px")):
            p = Plymouth([screen])
            p.password(LUKS, 0)
            p.tick()
            prompt = p.find(LUKS)
            self.assertEqual(prompt[4].font, font, screen)
            if screen == (480, 800):               # as small as it gets, and still too wide
                self.assertGreater(prompt[4].width, screen[0])
            else:
                self.assertLess(prompt[4].width, screen[0])
            self.assertEqual({t[2] for t in p.texts}, {"center"})
            self.check(p)

    def test_a_message_shows_until_it_is_taken_back(self):
        p = Plymouth()
        p.tick(100)
        p.message("Checking the disk")
        p.tick()
        note = p.find("Checking the disk")
        self.assertLessEqual(abs(note[1] + note[4].width / 2 - 960), 1)
        self.assertGreater(note[2], max(s[2] + s[4].height for s in p.shown() if s[0] == "dot.png"))
        p.hide_message("Something else")
        p.tick()
        self.assertIn("Checking the disk", p.names())
        p.password(LUKS, 0)
        p.tick()
        self.assertGreater(p.find("Checking the disk")[2], p.find("entry.png")[2] + N["field_h"], "it moves below a question")
        p.normal()
        p.hide_message("Checking the disk")
        p.tick()
        self.assertNotIn("Checking the disk", p.names())
        self.check(p)

    def test_nothing_piles_up(self):
        p = Plymouth()
        p.tick(100)
        before = len(list(p.sprites))
        for n in range(40):
            p.password(LUKS, n % 7)
            p.tick(3)
            p.message(f"message {n}")
            p.normal()
            p.tick(3)
            p.hide_message(f"message {n}")
        p.tick()
        gc.collect()
        self.assertEqual(len(list(p.sprites)), before)
        self.check(p)


class Screens(unittest.TestCase):
    def check(self, p):
        self.assertEqual(p.i.complaints, [])
        self.assertEqual(p.missing, [])

    def whole(self, p, i, scale, names):
        w, h = p.screens[i][:2]
        x, y = menu_place(w, h, scale)
        self.assertEqual(p.names(i), [n.replace(".png", tag(scale) + ".png") for n in names], f"screen {i}")
        self.assertEqual(p.find(f"logo{tag(scale)}.png", i)[1:3], (x, y), f"screen {i}")

    def test_two_screens_of_different_sizes_each_get_their_own(self):
        p = Plymouth([(3840, 2160), (1920, 1080)])
        p.tick()
        self.whole(p, 0, 2, ["logo.png"])
        self.whole(p, 1, 1, ["logo.png"])
        p.tick(60)
        self.whole(p, 0, 2, ["logo.png", "dot.png", "dot.png", "dot.png"])
        self.whole(p, 1, 1, ["logo.png", "dot.png", "dot.png", "dot.png"])
        # Plymouth puts the screens back on top of each other at every question; the next
        # refresh has to part them again before anything is drawn.
        p.password(LUKS, 3)
        p.tick()
        self.whole(p, 0, 2, ["logo.png", LUKS, "entry.png", "dot.png", "dot.png", "dot.png"])
        self.whole(p, 1, 1, ["logo.png", LUKS, "entry.png", "dot.png", "dot.png", "dot.png"])
        p.normal()
        p.message("Checking the disk")
        p.tick(40)
        self.whole(p, 0, 2, ["logo.png", "dot.png", "dot.png", "dot.png", "Checking the disk"])
        self.whole(p, 1, 1, ["logo.png", "dot.png", "dot.png", "dot.png", "Checking the disk"])
        self.check(p)

    def test_three_screens(self):
        p = Plymouth([(2560, 1440), (3840, 2160), (1920, 1200)])
        p.tick(60)
        for i, scale in enumerate((1, 2, 1)):
            self.whole(p, i, scale, ["logo.png", "dot.png", "dot.png", "dot.png"])
        self.check(p)

    def test_the_graphics_driver_takes_over_at_another_size(self):
        p = Plymouth([(1920, 1080)])
        p.tick(60)
        p.unplug(0)
        p.tick(2)                              # no screen at all for a moment
        p.plug(3840, 2160)
        self.whole(p, 0, 2, ["logo.png", "dot.png", "dot.png", "dot.png"])
        self.assertGreater(min(s[3] for s in p.shown() if s[0] == "dot-2x.png"), 0.34, "the dots don't come in again")
        p.tick(100)
        self.whole(p, 0, 2, ["logo.png", "dot.png", "dot.png", "dot.png"])
        self.check(p)

    def test_a_screen_that_changes_size_without_a_word_is_noticed(self):
        for size, scale in (((2560, 1440), 1), ((2560, 1080), 1), ((1920, 1200), 1), ((1920, 2160), 2)):
            p = Plymouth([(1920, 1080)])
            p.tick(IN)
            p.screens[0][:2] = size
            p.tick()
            self.whole(p, 0, scale, ["logo.png", "dot.png", "dot.png", "dot.png"])
            self.check(p)

    def test_a_second_screen_arrives_and_leaves(self):
        p = Plymouth([(1920, 1080)])
        p.tick(60)
        p.password(LUKS, 1)
        p.plug(3840, 2160)
        p.tick()
        self.whole(p, 0, 1, ["logo.png", LUKS, "entry.png", "dot.png"])
        self.whole(p, 1, 2, ["logo.png", LUKS, "entry.png", "dot.png"])
        p.unplug(0)
        p.tick()
        self.whole(p, 0, 2, ["logo.png", LUKS, "entry.png", "dot.png"])
        gc.collect()
        self.assertEqual(len(list(p.sprites)), 1 + 3 + 3, "the first screen's pieces are gone")
        self.check(p)


class StandIn(unittest.TestCase):
    """The stand-in itself, where the script leans on Plymouth's rules."""

    def run_script(self, text):
        i = Interpreter()
        i.run(text)
        return i

    def value(self, text, name="x"):
        return self.run_script(text).globals.peek(name).v

    def test_numbers_and_strings(self):
        self.assertEqual(self.value('x = "Sans " + 15 * 2 + "px";'), "Sans 30px")
        self.assertEqual(self.value("x = 7 / 2;"), 3.5)
        self.assertEqual(self.value("x = 1 + 2 * 3 - 4 % 3;"), 6)
        self.assertEqual(self.value("x = -2 * 3;"), -6)
        self.assertEqual(self.value('x = "a" + 0.5;'), "a0.5")
        self.assertEqual(self.value("x = 1 < 2 && 2 <= 2 && 3 > 2 && !(1 == 2) && 1 != 2;"), 1)
        self.assertEqual(self.value('x = NULL == NULL; y = 3 != NULL; z = "a" == "a";', "y"), 1)

    def test_names_are_found_in_the_function_first_then_among_the_globals(self):
        self.assertEqual(self.value("x = 1; fun f() { x = 2; } f();"), 2, "a bare name reaches the global")
        self.assertEqual(self.value("x = 1; fun f() { local.x = 2; x = 3; } f();"), 1, "unless it is declared local")
        self.assertEqual(self.value("x = 1; fun f(x) { x = 2; } f(x);"), 1, "arguments are copies")
        self.assertEqual(self.value("h.a = 1; fun f(h) { h.a = 2; } f(h); x = h.a;"), 2, "but hashes are shared")

    def test_asking_for_a_part_makes_a_hash(self):
        i = self.run_script("x = NULL; if (x.part) y = 1; z = 0; if (x) z = 1;")
        self.assertEqual(i.globals.peek("z").v, 1, "x became a hash by being asked")

    def test_loops_and_returns(self):
        self.assertEqual(self.value("x = 0; for (i = 0; i < 5; i++) { if (i == 3) continue; x += i; }"), 7)
        self.assertEqual(self.value("x = 0; while (1) { x++; if (x > 4) break; }"), 5)
        self.assertEqual(self.value("fun f(a) { if (a) return 1; return 2; } x = f(0) * 10 + f(5);"), 21)
        self.assertEqual(self.value("fun f() { } x = f(); x = (x == NULL);"), 1)

    def test_what_plymouth_swallows_is_reported_here(self):
        self.assertEqual(len(self.run_script("x = Nothing(1);").complaints), 1)
        self.assertEqual(len(self.run_script("h.a = 1; h.Missing();").complaints), 1)
        self.assertEqual(len(self.run_script("x = 1 - NULL;").complaints), 1)
        self.assertEqual(len(self.run_script('x = "a" * 2;').complaints), 1)

    def test_what_plymouths_parser_refuses_is_refused_here(self):
        for text in ("x = ;", "fun f( { }", "if (1) { x = 1;", "x = 1", 'x = "abc;', "x = (1 + 2;", "fun f() x = 1;"):
            with self.assertRaises(ScriptError, msg=text):
                Interpreter().run(text)


# ------------------------------------------------ the image build's check

def cpio(entries):
    """A cpio archive in the kernel's "newc" format. An entry is (name, bytes) for a file,
    (name, None) for a folder, (name, ("link", target)) for a link, or (name, bytes, inode, names)
    for one name of a file that has several."""
    out = bytearray()

    def add(name, mode, body, inode, links):
        raw = name.encode() + b"\0"
        fields = (inode, mode, 0, 0, links, 0, len(body), 0, 3, 0, 0, len(raw), 0)
        out.extend(b"070701" + b"".join(b"%08X" % f for f in fields) + raw)
        out.extend(b"\0" * (-len(out) % 4))
        out.extend(body)
        out.extend(b"\0" * (-len(out) % 4))

    for number, entry in enumerate(entries, 1):
        name, body = entry[0], entry[1]
        if body is None:
            add(name, 0o040755, b"", number, 2)
        elif isinstance(body, tuple):
            add(name, 0o120777, body[1].encode(), number, 1)
        elif len(entry) == 4:
            add(name, 0o100644, body, 1000 + entry[2], entry[3])
        else:
            add(name, 0o100644, body, number, 1)
    add("TRAILER!!!", 0, b"", 0, 1)
    out.extend(b"\0" * (-len(out) % 512))
    return bytes(out)


def zstd_or_none():
    try:
        from compression import zstd
    except ImportError:
        return None
    return zstd.compress


PACK = {"gzip": gzip.compress, "xz": lzma.compress, "bzip2": bz2.compress, "none": bytes, "zstd": zstd_or_none()}


class Plugin:
    """Stands in for Plymouth's script plugin, loaded the way the check loads it:
    script_parse_file says whether Plymouth could read the file."""

    def __init__(self, accepts=True):
        self.accepts, self.asked = accepts, []

    def __call__(self, path):
        if not os.path.isfile(path):
            raise OSError(f"{path}: cannot open shared object file: No such file or directory")

        def script_parse_file(script):
            self.asked.append(script.decode())
            return 0x7F00DEAD0000 if self.accepts else None

        return type("Library", (), {"script_parse_file": staticmethod(script_parse_file)})()


class Check(unittest.TestCase):
    """files/scripts/check-boot-splash.py, against images put together here."""

    STOCK = "# Administrator customizations go in this file\n#[Daemon]\n#Theme=fade-in\n"
    DEFAULTS = ("# Distribution defaults. Changes to this file will get overwritten during\n# upgrades.\n[Daemon]\n"
                "Theme=bgrt\nShowDelay=0\nDeviceTimeout=8\nUseSimpledrmNoLuks=1\n")
    FONT = "usr/share/fonts/google-noto/NotoSans-Regular.ttf"

    @classmethod
    def setUpClass(cls):
        loader = importlib.machinery.SourceFileLoader("check_boot_splash", CHECK)
        spec = importlib.util.spec_from_loader(loader.name, loader)
        cls.check = importlib.util.module_from_spec(spec)
        loader.exec_module(cls.check)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        self.theme = os.path.join(self.root, "usr/share/plymouth/themes/monolith")
        shutil.copytree(THEME, self.theme)
        self.conf = read(os.path.join(REPO, "files/system/etc/plymouth/plymouthd.conf"))
        self.put("etc/plymouth/plymouthd.conf", self.conf)
        self.put("usr/share/plymouth/plymouthd.defaults", self.DEFAULTS)
        self.put("usr/lib64/plymouth/script.so", "the plugin")

    def put(self, name, text):
        path = os.path.join(self.root, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)

    def inside(self, without=(), **changed):
        """What a good initramfs carries of Plymouth, as the real one of 2026-11-12 did, plus the theme."""
        files = {"usr/bin/plymouthd": b"\x7fELF", "usr/lib64/plymouth/script.so": b"\x7fELF script",
                 "usr/lib64/plymouth/label-freetype.so": b"\x7fELF label", "usr/lib64/plymouth/details.so": b"\x7fELF",
                 self.FONT: b"\0\1\0\0 a font", "usr/share/fonts/Plymouth.ttf": ("link", "/" + self.FONT),
                 "etc/plymouth/plymouthd.conf": self.conf.encode(),
                 "usr/share/plymouth/plymouthd.defaults": self.DEFAULTS.encode()}
        for name in os.listdir(THEME):
            files[f"usr/share/plymouth/themes/monolith/{name}"] = read(os.path.join(THEME, name), "rb")
        files.update({name: (body.encode() if isinstance(body, str) else body) for name, body in changed.items()})
        entries = [(".", None), ("usr", None), ("usr/share", None)]
        return entries + [(name, body) for name, body in sorted(files.items()) if name not in without]

    def image(self, entries, pack="gzip", kernel="7.3.4-1.cachyos1.fc45.x86_64", early=True):
        path = os.path.join(self.root, "usr/lib/modules", kernel, "initramfs.img")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        microcode = cpio([(".", None), ("early_cpio", b"1\n"), ("kernel/x86/microcode/AuthenticAMD.bin", b"\0" * 700)])
        with open(path, "wb") as f:
            f.write((microcode if early else b"") + PACK[pack](cpio(entries)))
        return path

    def run_check(self, *args, parser=None):
        self.parser = parser or Plugin()
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = self.check.main(["check-boot-splash.py", "--root", self.root, *args], load=self.parser)
        return code, out.getvalue(), err.getvalue()

    def test_a_complete_image_passes_however_it_is_packed(self):
        for pack in PACK:
            for early in (True, False):
                with self.subTest(pack=pack, early=early):
                    if PACK[pack] is None:
                        self.skipTest("this Python has no zstd")
                    self.image(self.inside(), pack, early=early)
                    code, out, err = self.run_check()
                    self.assertEqual((code, err), (0, ""))
                    self.assertIn("monolith is Plymouth's default theme at its own size", out)
                    self.assertIn("1 initramfs carries its 8 files", out)
                    self.assertEqual(self.parser.asked, [os.path.join(self.theme, "monolith.script")])

    def test_every_missing_piece_is_named(self):
        pieces = [f"usr/share/plymouth/themes/monolith/{name}" for name in os.listdir(THEME)]
        pieces += ["usr/lib64/plymouth/script.so", "usr/lib64/plymouth/label-freetype.so", "usr/share/fonts/Plymouth.ttf"]
        self.assertEqual(len(pieces), 11)
        for piece in pieces:
            with self.subTest(piece=piece):
                path = self.image(self.inside(without=(piece,)))
                code, out, err = self.run_check()
                self.assertEqual((code, out), (1, ""))
                self.assertEqual(err.count("\n"), 2, err)
                self.assertIn(f"  {path}: {piece} is missing", err)

    def test_a_font_that_points_nowhere_counts_as_missing(self):
        self.image(self.inside(without=(self.FONT,)))
        code, _, err = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("usr/share/fonts/Plymouth.ttf is missing (the font for text)", err)

    def test_an_empty_plugin_counts_as_missing(self):
        self.image(self.inside(**{"usr/lib64/plymouth/script.so": b""}))
        code, _, err = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("usr/lib64/plymouth/script.so is missing (the plugin that runs the theme's script)", err)

    def test_a_theme_file_that_differs_is_named(self):
        script = read(SCRIPT)
        self.image(self.inside(**{"usr/share/plymouth/themes/monolith/monolith.script": script.replace("dots_in", "dots_soon", 1)}))
        code, _, err = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("usr/share/plymouth/themes/monolith/monolith.script isn't the image's file", err)
        self.assertEqual(err.count("\n"), 2, err)

    def test_fedoras_own_settings_in_the_initramfs_fail(self):
        # What every build before this one carried: no Theme of its own, so Fedora's bgrt.
        path = self.image(self.inside(**{"etc/plymouth/plymouthd.conf": self.STOCK}))
        code, _, err = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn(f"  {path}: its default theme is bgrt; it has to be monolith\n", err)
        self.assertIn(f"  {path}: its DeviceScale is not set; it has to be 1, or Plymouth blurs the logo on dense screens\n", err)
        self.assertEqual(err.count("\n"), 3, err)

    def test_an_initramfs_where_plymouth_would_enlarge_the_theme_fails(self):
        for conf in ("[Daemon]\nTheme=monolith\n", "[Daemon]\nTheme=monolith\nDeviceScale=2\n", "[Daemon]\nTheme=monolith\n[Other]\nDeviceScale=1\n"):
            with self.subTest(conf=conf):
                self.image(self.inside(**{"etc/plymouth/plymouthd.conf": conf}))
                code, _, err = self.run_check()
                self.assertEqual(code, 1)
                self.assertIn("; it has to be 1, or Plymouth blurs the logo on dense screens", err)
                self.assertEqual(err.count("\n"), 2, err)

    def test_another_default_theme_in_the_image_fails(self):
        self.image(self.inside())
        self.put("etc/plymouth/plymouthd.conf", self.STOCK)
        code, _, err = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("Plymouth's default theme is bgrt; it has to be monolith (/etc/plymouth/plymouthd.conf).", err)
        self.assertIn("Plymouth's DeviceScale is not set; it has to be 1, or Plymouth blurs the logo on dense screens "
                      "(/etc/plymouth/plymouthd.conf).", err)
        os.remove(os.path.join(self.root, "usr/share/plymouth/plymouthd.defaults"))
        self.assertIn("Plymouth's default theme is not set; it has to be monolith", self.run_check()[2])
        self.put("etc/plymouth/plymouthd.conf", "[Daemon]\nTheme=monolith\n")
        code, _, err = self.run_check()
        self.assertEqual((code, err.count("\n")), (1, 2), err)
        self.assertIn("Plymouth's DeviceScale is not set", err)

    def test_a_script_plymouth_cant_read_fails(self):
        self.image(self.inside())
        code, out, err = self.run_check(parser=Plugin(accepts=False))
        self.assertEqual((code, out), (1, ""))
        self.assertIn("Plymouth's parser doesn't accept /usr/share/plymouth/themes/monolith/monolith.script", err)
        self.assertEqual(err.count("\n"), 2, err)

    def test_an_image_without_the_plugin_fails(self):
        self.image(self.inside())
        os.remove(os.path.join(self.root, "usr/lib64/plymouth/script.so"))
        code, _, err = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("Plymouth's script plugin can't be loaded, is plymouth-plugin-script installed?", err)

    def test_a_plugin_without_the_parser_fails(self):
        self.image(self.inside())
        code, _, err = self.run_check(parser=lambda path: object())
        self.assertEqual(code, 1)
        self.assertIn("Plymouth's script plugin can't be loaded", err)

    def test_an_image_without_the_theme_fails(self):
        self.image(self.inside())
        shutil.rmtree(self.theme)
        code, _, err = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("/usr/share/plymouth/themes/monolith/monolith.script isn't in the image.", err)

    def test_no_initramfs_fails(self):
        code, out, err = self.run_check()
        self.assertEqual((code, out), (1, ""))
        self.assertIn("There is no initramfs in /usr/lib/modules to check.", err)

    def test_every_initramfs_is_checked(self):
        good = self.image(self.inside())
        bad = self.image(self.inside(without=("usr/lib64/plymouth/script.so",)), kernel="7.4.0-1.cachyos1.fc45.x86_64")
        code, _, err = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn(f"  {bad}: usr/lib64/plymouth/script.so is missing", err)
        self.assertNotIn(good, err)
        os.remove(bad)
        self.image(self.inside(), kernel="7.4.0-1.cachyos1.fc45.x86_64")
        code, out, _ = self.run_check()
        self.assertEqual(code, 0)
        self.assertIn("2 initramfs carry its 8 files", out)

    def test_an_initramfs_named_on_the_command_line_is_checked_instead(self):
        self.image(self.inside())
        other = os.path.join(self.root, "initramfs-7.2.11-300.fc45.x86_64.img")
        with open(other, "wb") as f:
            f.write(gzip.compress(cpio(self.inside(without=("usr/share/fonts/Plymouth.ttf",)))))
        code, _, err = self.run_check(other)
        self.assertEqual(code, 1)
        self.assertIn(f"  {other}: usr/share/fonts/Plymouth.ttf is missing", err)

    def test_a_file_with_several_names_is_read(self):
        # cpio stores such a file's bytes once, after the last of its names.
        script = read(SCRIPT, "rb")
        name = "usr/share/plymouth/themes/monolith/monolith.script"
        entries = [e for e in self.inside() if e[0] != name]
        entries += [(name, b"", 7, 2), ("usr/share/twin", script, 7, 2)]
        self.image(entries)
        self.assertEqual(self.run_check()[0], 0)
        entries = [e for e in self.inside() if e[0] != name]
        entries += [("usr/share/twin", b"", 7, 2), (name, script, 7, 2)]
        self.image(entries)
        self.assertEqual(self.run_check()[0], 0)
        # Some tools put the bytes with the first name instead.
        entries = [e for e in self.inside() if e[0] != name]
        entries += [("usr/share/twin", script, 7, 2), (name, b"", 7, 2)]
        self.image(entries)
        self.assertEqual(self.run_check()[0], 0)

    def test_an_initramfs_it_cant_read_fails(self):
        whole = gzip.compress(cpio(self.inside()))
        cases = {"it is compressed in a way this check can't read": b"\x02\x21\x4c\x18" + b"\0" * 64,
                 "its compressed part can't be unpacked": whole[:len(whole) // 2],
                 "it isn't a cpio archive all the way through": cpio(self.inside())[:5000],
                 "it is empty": b""}
        for message, data in cases.items():
            with self.subTest(message=message):
                path = self.image([])
                with open(path, "wb") as f:
                    f.write(data)
                code, out, err = self.run_check()
                self.assertEqual((code, out), (1, ""))
                self.assertIn(f"  {path}: {message}", err)

    def test_reading_plymouths_settings(self):
        def theme(*texts):
            return self.check.setting("Theme", *texts)
        self.assertEqual(theme(self.conf, self.DEFAULTS), "monolith")
        self.assertEqual(theme(self.STOCK, self.DEFAULTS), "bgrt")
        self.assertEqual(theme(None, self.DEFAULTS), "bgrt")
        self.assertEqual(theme(None, None), None)
        self.assertEqual(theme("[Daemon]\nShowDelay=0\n", "[Daemon]\nTheme = spinner \n"), "spinner")
        self.assertEqual(theme("[Other]\nTheme=monolith\n[Daemon]\n# Theme=text\nTheme=\n", None), None)
        self.assertEqual(self.check.setting("DeviceScale", self.conf, self.DEFAULTS), "1")
        self.assertEqual(self.check.setting("DeviceScale", self.STOCK, self.DEFAULTS), None)
        self.assertEqual(self.check.setting("ShowDelay", self.conf, self.DEFAULTS), "0")

    def test_the_script_runs_as_a_program(self):
        self.assertTrue(os.access(CHECK, os.X_OK))
        self.assertTrue(read(CHECK).startswith("#!/usr/bin/python3"))


if __name__ == "__main__":
    unittest.main()
