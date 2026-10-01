#!/usr/bin/env python3
"""Offline tests for Monolith's WowUp-CF helper against a file-backed Gear Lever 4.6.2 model.

Besides running the suite, this file provides test doubles for other harnesses:

  python3 tests/wowup-cf.py fake-flatpak ARGS...
      Act as `flatpak` against the JSON state file named by $WOWUP_TEST_FLATPAK_STATE:
      `info --user|--system it.mijorus.gearlever`,
      `list --app --user|--system --columns=application:f,version:f`,
      `install --user --noninteractive --assumeyes <official flatpakref>` and
      `run --user|--system it.mijorus.gearlever ACTION ...` (Gear Lever 4.6.2's CLI, maintaining real
      AppImage, desktop, icon and gearlever.conf files under $HOME). Other calls exit 97 and are logged.
  python3 tests/wowup-cf.py install-fake-flatpak BIN_DIR STATE_FILE [SCOPE=VERSION ...]
      Write a `flatpak` shim into BIN_DIR and a default state (Gear Lever system=4.6.2 unless given).
  python3 tests/wowup-cf.py run-helper HELPER_ARGS...
      Run the real helper with HTTP answered from the routes file $WOWUP_TEST_HTTP (see FakeWeb.save)
      and processes read from $WOWUP_TEST_PROC_ROOT; unknown URLs fail and are written to
      "$WOWUP_TEST_HTTP.unexpected".
"""

import ast
import configparser
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import struct
import sys
import time

sys.dont_write_bytecode = True


THIS = Path(__file__).resolve()
ROOT = THIS.parents[1]
HELPER = ROOT / "files/system/usr/libexec/monolith/wowup-cf"
APP_ID = "it.mijorus.gearlever"
FLATPAKREF = "https://dl.flathub.org/repo/appstream/it.mijorus.gearlever.flatpakref"
STATE_ENV = "WOWUP_TEST_FLATPAK_STATE"
HTTP_ENV = "WOWUP_TEST_HTTP"
PROC_ENV = "WOWUP_TEST_PROC_ROOT"
UNMODELLED = 97
RELEASES = "https://api.github.com/repos/WowUp/WowUp.CF/releases?per_page=100&page={page}"
ICON = b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR monolith fixture icon"


# --- fixture AppImages ----------------------------------------------------------------------------

def elf(sections):
    """A minimal ELF64 x86-64 executable with the type-2 AppImage magic and the given sections."""
    names = bytearray(b"\0")
    offsets = []
    for name, _ in [*sections, (".shstrtab", b"")]:
        offsets.append(len(names))
        names += name.encode() + b"\0"
    body = bytearray()
    placed = []
    for _, data in sections:
        placed.append((64 + len(body), len(data)))
        body += data + bytes(-len(data) % 8)
    strtab = 64 + len(body)
    body += names + bytes(-len(names) % 8)
    count = len(sections) + 2
    header = b"\x7fELF\x02\x01\x01\x00AI\x02" + bytes(5) + struct.pack(
        "<HHIQQQIHHHHHH", 2, 62, 1, 0x401000, 0, 64 + len(body), 0, 64, 56, 0, 64, count, count - 1)
    table = bytes(64)
    for offset, (start, size) in zip(offsets, placed):
        table += struct.pack("<IIQQQQIIQQ", offset, 1, 0, 0, start, size, 0, 0, 1, 0)
    table += struct.pack("<IIQQQQIIQQ", offsets[-1], 3, 0, 0, strtab, len(names), 0, 0, 1, 0)
    return header + bytes(body) + table


_APPIMAGES = {}


def make_appimage(version, *, name="WowUp-CF", update_info=b""):
    """A small AppImage: real ELF for readelf, plus the embedded desktop entry and .DirIcon Gear Lever reads."""
    key = (version, name, update_info)
    if key not in _APPIMAGES:
        desktop = (f"[Desktop Entry]\nName={name}\nExec=AppRun --no-sandbox %U\nTerminal=false\n"
                   f"Type=Application\nIcon={name.lower()}\nStartupWMClass={name}\n"
                   f"X-AppImage-Version={version}\nComment=World of Warcraft addon updater\n"
                   "Categories=Game;\n").encode()
        _APPIMAGES[key] = elf([
            (".upd_info", update_info.ljust(1024, b"\0")),
            (".monolith.desktop", desktop),
            (".monolith.diricon", ICON),
            (".monolith.payload", f"{name} {version} payload\n".encode() * 32),
        ])
    return _APPIMAGES[key]


def appimage_sections(path):
    try:
        data = Path(path).read_bytes()
    except OSError:
        return {}
    if len(data) < 64 or data[:4] != b"\x7fELF" or data[4] != 2:
        return {}
    offset, = struct.unpack_from("<Q", data, 40)
    size, count, names_index = struct.unpack_from("<HHH", data, 58)
    if not offset or size != 64 or offset + count * 64 > len(data) or names_index >= count:
        return {}
    headers = [struct.unpack_from("<IIQQQQIIQQ", data, offset + index * 64) for index in range(count)]
    names = data[headers[names_index][4]:headers[names_index][4] + headers[names_index][5]]
    return {names[header[0]:names.index(b"\0", header[0])].decode(): data[header[4]:header[4] + header[5]]
            for header in headers[1:]}


def desktop_values(text):
    values, group, seen = {}, None, False
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            group = line[1:-1]
            seen = seen or group == "Desktop Entry"
        elif group == "Desktop Entry" and "=" in line:
            key, value = line.split("=", 1)
            values.setdefault(key.strip(), value.strip())
    return values if seen else None


def desktop_entry(path):
    try:
        return desktop_values(Path(path).read_text())
    except (OSError, UnicodeDecodeError):
        return None


def same_bytes(first, second):
    try:
        return Path(first).read_bytes() == Path(second).read_bytes()
    except OSError:
        return False


# --- Gear Lever 4.6.2 and Flatpak model ---------------------------------------------------------------
# Modelled on Gear Lever 4.6.2's Cli.py, AppImageProvider.py, ini_config.py and UpdateManagerChecker.py.

TEMPLATES = {
    "StaticFileUpdater": {"url": str},
    "GithubUpdater": {"allow_prereleases": bool, "repo": str, "repo_filename": str},
    "GitlabUpdater": {"repo_url": str, "repo_filename": str},
    "CodebergUpdater": {"allow_prereleases": bool, "repo_url": str, "repo_filename": str},
    "FTPUpdater": {"url": str, "filename": str},
    "ForgejoUpdater": {"allow_prereleases": bool, "repo_url": str, "repo_filename": str},
}


def default_state(installed=None):
    """Flatpak/Gear Lever model state; every key may be changed by a test or harness.

    installed: {"user"|"system": "4.6.2"} Gear Lever installations by scope.
    install_version / install_fails: result of the official flatpakref user installation.
    home: GLib home directory Gear Lever sees (default: the caller's $HOME).
    running: {AppImage path or "*": true|false|null} reported in the JSON `running` field.
    faults: {"list": "fail"|"bad-json"|"schema"|"hang", "integrate": "fail"|"partial"|"crash"|"corrupt"|"block",
             "remove": "fail"|"partial", "set-update-source": "fail"}; a {"mode": ..., "once": true}
             value is consumed by its first use. "crash" stops after copying the AppImage and icon.
    The AppImage folder is the appimages-default-folder GSettings value in Gear Lever's keyfile
    (see set_gear_lever_folder), as in the real Flatpak.
    """
    return {"installed": dict({"system": "4.6.2"} if installed is None else installed),
            "install_version": "4.6.2", "install_fails": False, "home": None,
            "running": {}, "faults": {}, "hang_seconds": 30}


def settings_keyfile(home):
    return os.path.join(home, ".var", "app", APP_ID, "config", "glib-2.0", "settings", "keyfile")


def set_gear_lever_folder(home, folder):
    """What `gsettings set it.mijorus.gearlever appimages-default-folder FOLDER` writes in the Flatpak."""
    path = settings_keyfile(home)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Path(path).write_text(f"[it/mijorus/gearlever]\nappimages-default-folder={json.dumps(folder)}\n")


def gear_lever_folder(home):
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(settings_keyfile(home))
    return ast.literal_eval(parser.get("it/mijorus/gearlever", "appimages-default-folder",
                                       fallback="'~/AppImages'"))


def remove_special_chars(filename):
    return re.sub(r"[^\w\._]+", "", filename)


def terminal_arguments(command):
    environment, executable, arguments = [], "", []
    for token in shlex.split(command):
        if token == "env":
            continue
        if "=" in token and not token.startswith("/") and not token.startswith("-"):
            environment.append(token)
        elif not executable and not token.startswith("-"):
            executable = token
        else:
            arguments.append(token)
    return environment, executable, arguments


def update_info_dump(path):
    """What `readelf --string-dump=.upd_info --wide` prints for a fixture AppImage."""
    strings = [item.decode("utf-8", "replace") for item in
               appimage_sections(path).get(".upd_info", b"").split(b"\0") if item]
    if not strings:
        return "\nString dump of section '.upd_info':\n  No strings found in this section.\n"
    return "\nString dump of section '.upd_info':\n" + "".join(f"  [     0]  {item}\n" for item in strings) + "\n"


class GearLeverModel:
    def __init__(self, home, state, version="4.6.2", sync_dir=None):
        self.home = str(home)
        self.state = state
        self.version = tuple(int(part) for part in version.split("."))
        self.sync_dir = sync_dir
        self.apps = os.path.join(self.home, ".local", "share", "applications")
        self.folder = re.sub(r"^~", lambda _: self.home, gear_lever_folder(self.home))
        self.config_path = os.path.join(self.home, ".var", "app", APP_ID, "config", "gearlever.conf")
        self.parser = configparser.ConfigParser(interpolation=None)
        if not os.path.exists(self.config_path):
            # main.py: migrate_to_v2_config() on the first start, then Config.refresh().
            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
            self.parser[self.parser.default_section]["fetch-updates-in-background"] = "no"
            for app in self.installed():
                self.set_app_config(app["path"], app["name"], {"website": ""})
            self.write_config()
        self.parser.read(self.config_path)

    def fault(self, name):
        value = self.state.get("faults", {}).get(name)
        if isinstance(value, dict):
            if value.get("once"):
                del self.state["faults"][name]
                self.state["_dirty"] = True
            return value.get("mode")
        return value

    # ini_config.Config
    def write_config(self):
        with open(self.config_path, "w") as file:
            self.parser.write(file)

    @staticmethod
    def digest(path):
        return hashlib.md5(path.encode()).hexdigest()

    def set_app_config(self, path, name, data):
        data = dict(data, name=name, file_path=path)
        self.parser[f"app.{self.digest(path)}"] = data
        self.write_config()

    def update_section(self, path):
        section = f"app.{self.digest(path)}.update_manager"
        if not self.parser.has_section(section):
            self.parser.add_section(section)
        return self.parser[section]

    def delete_update_section(self, path):
        section = f"app.{self.digest(path)}.update_manager"
        if self.parser.has_section(section):
            self.parser.remove_section(section)
        self.write_config()

    def delete_app_config(self, path):
        for section in (f"app.{self.digest(path)}", f"app.{self.digest(path)}.update_manager"):
            if self.parser.has_section(section):
                self.parser.remove_section(section)
        self.write_config()

    # AppImageProvider
    @staticmethod
    def is_appimage(path):
        if not os.path.isfile(path):
            return False
        if path.lower().endswith(".appimage"):
            return True
        with open(path, "rb") as file:
            head = file.read(11)
        return head[:4] == b"\x7fELF" and head[8:11] in (b"AI\x01", b"AI\x02")

    def installed(self):
        found = []
        if not os.path.exists(self.apps):
            return found
        for name in os.listdir(self.apps):
            desktop = os.path.join(self.apps, name)
            if not (name.endswith(".desktop") and os.path.isfile(desktop)):
                continue
            entry = desktop_entry(desktop)
            location = (entry or {}).get("TryExec", "")
            if entry is not None and os.path.isfile(location) and self.is_appimage(location):
                found.append({"name": entry.get("Name", ""), "path": location, "desktop": desktop,
                              "version": entry.get("X-AppImage-Version") or None, "icon": entry.get("Icon", "")})
        return found

    def default_copy(self, path):
        """is_installed(): the first AppImage in the default folder with identical bytes."""
        if not os.path.exists(self.folder):
            return None
        for name in os.listdir(self.folder):
            candidate = os.path.normpath(self.folder + "/" + name)
            if self.is_appimage(candidate) and same_bytes(candidate, path):
                return candidate
        return None

    def element_path(self, path):
        """create_list_element_from_file(path).file_path"""
        copy = self.default_copy(path)
        if copy is None:
            return path
        for app in self.installed():
            if same_bytes(app["path"], copy):
                return app["path"]
        return copy

    def manager(self, path):
        """UpdateManagerChecker.check_url_for_app(): custom source, else embedded update information."""
        name = self.update_section(path).get("manager")
        if name:
            return name, False
        dump = update_info_dump(path).replace("\n", " ") + " "
        if re.search(r"gh-releases-zsync\|.*(.zsync)", dump):
            return "GithubUpdater", True
        if re.search(r"\szsync\|http(.*)\s", dump):
            return "StaticFileUpdater", True
        return None, False

    # Cli
    def file_from_args(self, arguments):
        for argument in arguments:
            if not argument.startswith("-") and os.path.isfile(argument) and self.is_appimage(argument):
                return os.path.normpath(argument)
        return None

    def cli(self, arguments):
        if "--help" in arguments:
            return None
        action = {"--list-installed": self.list_installed, "--integrate": self.integrate,
                  "--remove": self.remove, "--set-update-source": self.set_update_source}.get(arguments[0])
        return action(arguments) if action else None

    def list_installed(self, arguments):
        fault = self.fault("list")
        if fault == "fail":
            return 1, "", "Traceback (most recent call last):\nOSError: [Errno 5] Input/output error\n"
        if fault == "hang":
            time.sleep(self.state.get("hang_seconds", 30))
        if fault == "bad-json":
            return 0, '{"schema_version": 1, "installed": [\n', ""
        if fault == "schema":
            return 0, json.dumps({"schema_version": 2, "installed": []}) + "\n", ""
        apps = self.installed()
        if "--json" not in arguments or self.version < (4, 6, 2):
            rows = [f"{app['name']}   [{app['version'] or 'Not specified'}]   [UpdatesNotAvailable]   {app['path']}"
                    for app in apps]
            return 0, "".join(row + "\n" for row in rows), ""
        rows = []
        running = self.state.get("running", {})
        for app in apps:
            manager, embedded = self.manager(app["path"])
            rows.append({"name": app["name"], "path": app["path"], "desktop_id": os.path.basename(app["desktop"]),
                         "current_version": app["version"], "available_version": None, "download_size": None,
                         "manager": manager, "embedded_source": embedded,
                         "running": running.get(app["path"], running.get("*", False))})
        return 0, json.dumps({"schema_version": 1, "installed": rows}, ensure_ascii=False) + "\n", ""

    def integrate(self, arguments):
        if "--yes" not in arguments and "-y" not in arguments:
            return None
        source = self.file_from_args(arguments[1:])
        if source is None:
            return 1, "Error: please specify a valid AppImage file\n", ""
        if self.default_copy(source):
            return 0, "This AppImage is already integrated\n", ""
        fault = self.fault("integrate")
        if fault == "fail":
            return 1, "", "Traceback (most recent call last):\nInternalError: Missing mounted extraction folder\n"
        sections = appimage_sections(source)
        embedded = desktop_values(sections.get(".monolith.desktop", b"").decode()) or {}
        if "Name" not in embedded:
            return 1, "", "AttributeError: 'NoneType' object has no attribute 'get'\n"
        keep = "--replace" not in arguments
        self.update_section(source)  # check_url_for_app() on the source element; persisted below
        name = embedded["Name"]
        version = embedded.get("X-AppImage-Version", "")
        try:
            if not os.path.exists(self.folder):
                os.mkdir(self.folder)
            filename = name.lower().replace(" ", "_") + ".appimage"
            without_extension = filename
            filename = remove_special_chars(filename).lower()
            existing = os.listdir(self.folder)
            index = 0
            while filename in existing:
                filename = (without_extension + "_" + version.replace(".", "_") if index == 0 and version
                            else without_extension + f"_{index}") + ".appimage"
                index += 1
            prefix = os.path.splitext(filename)[0]
            destination = os.path.join(self.folder, filename)
            shutil.copyfile(source, destination)
            os.chmod(destination, 0o755)
            icon = "applications-other"
            if ".monolith.diricon" in sections:
                if not os.path.exists(os.path.join(self.folder, ".icons")):
                    os.mkdir(os.path.join(self.folder, ".icons"))
                icon = os.path.join(self.folder, ".icons", prefix)  # .DirIcon has no extension
                with open(icon, "wb") as file:
                    file.write(sections[".monolith.diricon"])
            if fault == "crash":  # e.g. Ctrl-C, after the copies but before the launcher exists
                return 1, "", "Traceback (most recent call last):\nKeyboardInterrupt\n"
            if not os.path.exists(self.apps):
                os.makedirs(self.apps)
            desktop = f"{os.path.join(self.apps, prefix)}.desktop".replace(" ", "_")
            _, _, exec_arguments = terminal_arguments(embedded.get("Exec", ""))
            title = f"{name} ({version or hashlib.md5(Path(source).read_bytes()).hexdigest()[:6]})" if keep else name
            exec_line = " ".join(["env DESKTOPINTEGRATION=1", shlex.quote(destination), *exec_arguments])
            lines = [("Type", embedded.get("Type", "Application")), ("Name", title),
                     ("Comment", embedded.get("Comment")), ("Icon", icon), ("TryExec", destination),
                     ("Exec", exec_line), ("Terminal", embedded.get("Terminal", "false")),
                     ("Categories", embedded.get("Categories")), ("StartupWMClass", embedded.get("StartupWMClass"))]
            custom = {key: value for key, value in embedded.items() if key.startswith("X-")}
            if version:
                custom["X-AppImage-Version"] = version
            custom["X-AppImage-Name"] = name
            text = "[Desktop Entry]\n" + "".join(f"{key}={value}\n" for key, value in [*lines, *custom.items()]
                                                 if value is not None)
            with open(desktop, "w+") as file:
                file.write(text)
            self.set_app_config(destination, name, {"default_exec_arguments": " ".join(exec_arguments)})
        except OSError as error:
            return 1, "", f"Traceback (most recent call last):\n{type(error).__name__}: {error}\n"
        if fault == "partial":
            return 1, "", "Traceback (most recent call last):\nInternalError: Cannot delete original file\n"
        if fault == "corrupt":
            with open(destination, "ab") as file:
                file.write(b"corrupted copy")
        if os.path.dirname(source) != self.folder:  # move-appimage-on-integration=true
            os.remove(source)
        if fault == "block":
            write_state(os.environ[STATE_ENV], self.state)  # consume a one-shot fault before blocking
            Path(self.sync_dir, "integrate.blocked").write_text("integrated\n")
            time.sleep(120)
        return 0, f"{destination} was integrated successfully\n", ""

    def remove(self, arguments):
        source = self.file_from_args(arguments[1:])
        if source is None:
            return 1, "Error: please specify a valid AppImage file\n", ""
        app = next((item for item in self.installed() if item["path"] == source), None)
        if app is None:
            return 1, "Error: AppImage not integrated\n", ""
        path = self.default_copy(app["path"])
        if path is None:
            return 1, "This AppImage is not integrated\n", ""
        if ("--yes" not in arguments and "-y" not in arguments) or "--delete" not in arguments:
            return None
        fault = self.fault("remove")
        if fault == "fail":
            return 1, "", "Traceback (most recent call last):\nPermissionError: [Errno 13] Permission denied\n"
        os.remove(path)
        if fault == "partial":
            return 1, "", "Traceback (most recent call last):\nOSError: [Errno 5] Input/output error\n"
        os.remove(app["desktop"])
        if "/" in app["icon"] and os.path.isfile(app["icon"]):
            os.remove(app["icon"])
        self.delete_update_section(path)
        self.delete_app_config(path)
        self.delete_update_section(path)
        return 0, f"{path} was removed sucessfully\n", ""

    def set_update_source(self, arguments):
        manager = None
        if "--manager" in arguments:
            index = arguments.index("--manager")
            if len(arguments) < index + 2:
                return 1, "Error: --manager requires a value\n", ""
            manager = arguments[index + 1]
        if manager not in TEMPLATES:
            return 1, "", f"Exception: Invalid model name: {manager or ''}\n"
        source = self.file_from_args(arguments[1:])
        if source is None:
            return 1, "Error: please specify a valid AppImage file\n", ""
        path = self.element_path(source)
        options = dict(argument.split("=", 1) for argument in arguments if "=" in argument)
        if self.fault("set-update-source") == "fail":
            return 1, "", "Traceback (most recent call last):\nOSError: [Errno 28] No space left on device\n"
        if "--unset" in arguments:
            self.delete_update_section(path)
            return 0, "", ""
        template = TEMPLATES[manager]
        if set(template) != set(options):
            return 1, "Missing or invalid update configuration, required keys: " + ", ".join(template) + "\n", ""
        for key, kind in template.items():
            if kind is bool:
                if options[key] not in ("0", "1", "false", "true"):
                    return 1, f"{key} is not a boolean value, allowed values: 0/1/false/true\n", ""
                options[key] = options[key] in ("1", "true")
        if manager == "GithubUpdater" and len(options["repo"].split("/")) != 2:
            return 1, "", "Exception: Invalid data, please enter <username>/<repo>\n"
        self.delete_update_section(path)
        section = f"app.{self.digest(path)}.update_manager"
        self.parser[section] = {key: str(value) for key, value in options.items()}
        self.parser[section]["manager"] = manager
        self.write_config()
        return 0, "", ""


def read_state(path):
    return json.loads(Path(path).read_text())


def write_state(path, state):
    state = {key: value for key, value in state.items() if not key.startswith("_")}
    temporary = Path(f"{path}.tmp")
    temporary.write_text(json.dumps(state, indent=2))
    os.replace(temporary, path)


def flatpak_command(state, arguments, sync_dir):
    scopes = ("--user", "--system")
    if arguments[:1] == ["run"]:
        if len(arguments) < 4 or arguments[1] not in scopes or arguments[2] != APP_ID:
            return None
        version = state["installed"].get(arguments[1][2:])
        if version is None:
            return 1, "", f"error: app/{APP_ID}/x86_64/master not installed\n"
        model = GearLeverModel(state.get("home") or os.environ["HOME"], state, version, sync_dir)
        return model.cli(arguments[3:])
    if len(arguments) == 3 and arguments[0] == "info" and arguments[1] in scopes and arguments[2] == APP_ID:
        version = state["installed"].get(arguments[1][2:])
        if version is None:
            return 1, "", f"error: {APP_ID}/*unspecified*/*unspecified* not installed\n"
        return 0, f"\nGear Lever - Manage AppImages\n\n          ID: {APP_ID}\n     Version: {version}\n", ""
    if (len(arguments) == 4 and arguments[0] == "list" and "--app" in arguments
            and "--columns=application:f,version:f" in arguments
            and len(set(arguments) & set(scopes)) == 1):
        scope = next(item for item in arguments if item in scopes)[2:]
        version = state["installed"].get(scope)
        return 0, f"{APP_ID}\t{version}\n" if version else "", ""
    if arguments == ["install", "--user", "--noninteractive", "--assumeyes", FLATPAKREF]:
        if state.get("install_fails"):
            return 1, "", "error: Failed to install it.mijorus.gearlever: Could not connect\n"
        state["installed"]["user"] = state.get("install_version", "4.6.2")
        state["_dirty"] = True
        return 0, "Installing app/it.mijorus.gearlever/x86_64/stable\nInstallation complete.\n", ""
    return None


def fake_flatpak(arguments):
    state_path = os.environ.get(STATE_ENV)
    if not state_path:
        print(f"fake flatpak: {STATE_ENV} is not set", file=sys.stderr)
        return UNMODELLED
    log = Path(f"{state_path}.calls")
    with log.open("a") as file:
        file.write(json.dumps({"argv": arguments}) + "\n")
    state = read_state(state_path)
    original = json.dumps(state, sort_keys=True)
    try:
        result = flatpak_command(state, arguments, os.path.dirname(state_path))
    finally:
        if state.pop("_save_now", False) or state.pop("_dirty", False) or json.dumps(
                state, sort_keys=True) != original:
            write_state(state_path, state)
    if result is None:
        with log.open("a") as file:
            file.write(json.dumps({"unmodelled": arguments}) + "\n")
        print(f"fake flatpak: unmodelled call {arguments}", file=sys.stderr)
        return UNMODELLED
    code, out, err = result
    sys.stdout.write(out)
    sys.stderr.write(err)
    return code


class FakeFlatpak:
    """A `flatpak` shim and its state, for Python tests; shell harnesses use install-fake-flatpak."""

    def __init__(self, directory, installed=None):
        self.directory = Path(directory)
        self.bin = self.directory / "bin"
        self.state_path = self.directory / "flatpak-state.json"
        install_fake_flatpak(self.bin, self.state_path, installed)

    def env(self):
        return {"PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', '/usr/bin:/bin')}",
                STATE_ENV: str(self.state_path)}

    def state(self):
        return read_state(self.state_path)

    def update(self, **changes):
        state = self.state()
        state.update(changes)
        write_state(self.state_path, state)

    def fault(self, name, mode, once=False):
        state = self.state()
        state["faults"][name] = {"mode": mode, "once": True} if once else mode
        write_state(self.state_path, state)

    def records(self):
        log = Path(f"{self.state_path}.calls")
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def calls(self):
        return [record["argv"] for record in self.records() if "argv" in record]

    def unmodelled(self):
        return [record["unmodelled"] for record in self.records() if "unmodelled" in record]

    def gear_lever(self, *arguments, home=None):
        """Run the Gear Lever model in-process, e.g. to create integrations a test starts from."""
        state = self.state()
        model = GearLeverModel(home or state.get("home") or os.environ["HOME"], state,
                               next(iter(state["installed"].values())), self.directory)
        result = model.cli(list(arguments))
        write_state(self.state_path, state)
        if result is None:
            raise AssertionError(f"unmodelled Gear Lever call {arguments}")
        return result

    def listing(self, home=None):
        code, out, err = self.gear_lever("--list-installed", "--json", home=home)
        if code != 0:
            raise AssertionError(err)
        return json.loads(out)["installed"]


def install_fake_flatpak(bin_dir, state_path, installed=None):
    bin_dir = Path(bin_dir)
    bin_dir.mkdir(parents=True, exist_ok=True)
    shim = bin_dir / "flatpak"
    shim.write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} -I -S {shlex.quote(str(THIS))} "
                    'fake-flatpak "$@"\n')
    shim.chmod(0o755)
    write_state(state_path, default_state(installed))


if __name__ == "__main__" and sys.argv[1:2] == ["fake-flatpak"]:
    sys.exit(fake_flatpak(sys.argv[2:]))


# --- test harness (imported only when not acting as flatpak) -----------------------------------------------

import contextlib  # noqa: E402
import email.message  # noqa: E402
import errno  # noqa: E402
import importlib.machinery  # noqa: E402
import importlib.util  # noqa: E402
import io  # noqa: E402
import signal  # noqa: E402
import stat  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402
import unittest  # noqa: E402
from unittest import mock  # noqa: E402
import urllib.error  # noqa: E402
import urllib.request  # noqa: E402


_LOADS = 0


def load_helper():
    global _LOADS
    _LOADS += 1
    loader = importlib.machinery.SourceFileLoader(f"wowup_cf_test_{_LOADS}", str(HELPER))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module
    loader.exec_module(module)
    return module


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def download_url(version):
    return f"https://github.com/WowUp/WowUp.CF/releases/download/v{version}/WowUp-CF-{version}.AppImage"


def release(version, *, data=None, prerelease=None, draft=False, digest="auto", state="uploaded",
            name=None, url=None, size=None, duplicate=False):
    """A GitHub release object as the REST API returns it, with Windows/macOS/updater assets beside it."""
    data = make_appimage(version) if data is None else data
    name = name or f"WowUp-CF-{version}.AppImage"
    asset = {"name": name, "state": state, "size": len(data) if size is None else size,
             "content_type": "application/octet-stream",
             "digest": f"sha256:{sha256(data)}" if digest == "auto" else digest,
             "browser_download_url": url or f"https://github.com/WowUp/WowUp.CF/releases/download/v{version}/{name}"}
    others = [{"name": f"WowUp-CF-Setup-{version}.exe", "state": "uploaded", "size": 7,
               "digest": f"sha256:{sha256(b'windows')}",
               "browser_download_url": f"https://github.com/WowUp/WowUp.CF/releases/download/v{version}/WowUp-CF-Setup-{version}.exe"},
              {"name": "latest-linux.yml", "state": "uploaded", "size": 5, "digest": f"sha256:{sha256(b'yaml')}",
               "browser_download_url": f"https://github.com/WowUp/WowUp.CF/releases/download/v{version}/latest-linux.yml"}]
    return {"tag_name": f"v{version}", "name": f"v{version}", "draft": draft,
            "prerelease": ("-beta." in version) if prerelease is None else prerelease,
            "assets": [others[0], asset, *([dict(asset)] if duplicate else []), others[1]]}


class FakeResponse(io.BytesIO):
    def __init__(self, data, headers, hold=None):
        super().__init__(data)
        self.status = 200
        self.headers = email.message.Message()
        for key, value in headers.items():
            self.headers[key] = value
        self.hold = hold
        self.reads = 0

    def read(self, size=-1):
        self.reads += 1
        if self.hold and self.reads > 1:
            Path(self.hold).write_text("downloading\n")
            while True:
                time.sleep(0.05)
        return super().read(size)


class FakeWeb:
    """Patched urlopen: only explicitly served URLs answer; anything else is recorded and fails."""

    def __init__(self):
        self.routes = {}
        self.requests = []
        self.unexpected = []

    def add(self, url, body=b"", *, status=200, headers=None, error=None, hold=None, on_open=None):
        self.routes[url] = {"body": body, "status": status, "headers": headers or {}, "error": error,
                            "hold": hold, "on_open": on_open}

    def publish(self, releases):
        pages = [releases[index:index + 100] for index in range(0, len(releases), 100)] or [[]]
        if len(pages[-1]) == 100:
            pages.append([])
        for number, page in enumerate(pages, 1):
            self.add(RELEASES.format(page=number), json.dumps(page).encode(),
                     headers={"Content-Type": "application/json; charset=utf-8"})

    def asset(self, version, data, *, length=True):
        self.add(download_url(version), data, headers={"Content-Length": str(len(data))} if length else {})

    def urlopen(self, request, timeout=None, **kwargs):
        url = getattr(request, "full_url", request)
        self.requests.append(url)
        route = self.routes.get(url)
        if route is None or not timeout:
            self.unexpected.append(url)
            raise urllib.error.URLError(f"unexpected request in test: {url}")
        if url.startswith("https://api.github.com/") and not request.get_header("User-agent"):
            raise urllib.error.HTTPError(url, 403, "Forbidden", email.message.Message(), io.BytesIO(b""))
        if route["on_open"]:
            route["on_open"]()
        if route["error"]:
            raise urllib.error.URLError(route["error"])
        if route["status"] != 200:
            raise urllib.error.HTTPError(url, route["status"], "Error", email.message.Message(), io.BytesIO(b""))
        return FakeResponse(route["body"], route["headers"], route["hold"])

    def save(self, path):
        directory = Path(f"{path}.bodies")
        directory.mkdir(exist_ok=True)
        routes = {}
        for index, (url, route) in enumerate(self.routes.items()):
            body = directory / str(index)
            body.write_bytes(route["body"])
            routes[url] = {"body_file": str(body), "status": route["status"], "headers": route["headers"],
                           "error": route["error"], "hold": route["hold"]}
        Path(path).write_text(json.dumps(routes))

    @classmethod
    def load(cls, path):
        web = cls()
        for url, route in json.loads(Path(path).read_text()).items():
            web.add(url, Path(route["body_file"]).read_bytes(), status=route["status"], headers=route["headers"],
                    error=route["error"], hold=route["hold"])
        return web


class FakeProc:
    """A /proc look-alike: status (Uid), comm, cmdline and environ per pid."""

    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def add(self, pid, *, uid, environ=None, comm="bash", cmdline=("bash",), readable=True, exe=None):
        base = self.root / str(pid)
        base.mkdir()
        (base / "status").write_text(f"Name:\t{comm}\nState:\tS (sleeping)\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\n")
        (base / "comm").write_text(comm + "\n")
        (base / "cmdline").write_bytes(b"".join(part.encode() + b"\0" for part in cmdline))
        if exe is not None:
            (base / "exe").symlink_to(exe)
        if readable:
            (base / "environ").write_bytes(b"".join(f"{key}={value}".encode() + b"\0"
                                                    for key, value in (environ or {}).items()))
        else:
            (base / "environ").mkdir()  # reading fails, like another process's protected environ

    def add_vanished(self, pid):
        (self.root / str(pid)).mkdir()

    def clear(self):
        shutil.rmtree(self.root)
        self.root.mkdir()


def run_helper(arguments):
    helper = load_helper()
    routes = os.environ.get(HTTP_ENV)
    web = FakeWeb.load(routes) if routes else FakeWeb()
    if os.environ.get(PROC_ENV):
        helper.PROC_ROOT = Path(os.environ[PROC_ENV])
    try:
        with mock.patch("urllib.request.urlopen", web.urlopen):
            return helper.main(arguments)
    finally:
        if routes and web.unexpected:
            Path(f"{routes}.unexpected").write_text("\n".join(web.unexpected) + "\n")


class Result:
    def __init__(self, code, stdout, stderr):
        self.code, self.stdout, self.stderr = code, stdout, stderr

    def __repr__(self):
        return f"exit {self.code}\nstdout:\n{self.stdout}\nstderr:\n{self.stderr}"


RUNNING = "Close WowUp-CF and retry this action."
EMBEDDED = {
    "GitHub zsync": b"gh-releases-zsync|WowUp|WowUp.CF|latest|WowUp-CF-*.AppImage.zsync",
    "static zsync": b"zsync|https://example.invalid/WowUp-CF.AppImage.zsync",
}


class WowUpCfTests(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        # Gear Lever 4.6.2 replaces spaces anywhere in its launcher path, so HOME itself has none here;
        # XDG directories, staging and the AppImage folder in the alias test do contain spaces.
        temporary = tempfile.TemporaryDirectory(prefix="monolith-wowup-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.harness = self.root / "harness"
        self.home = self.root / "var/home/player"
        self.home.mkdir(parents=True)
        self.flatpak = FakeFlatpak(self.harness / "flatpak")
        self.web = FakeWeb()
        self.proc = FakeProc(self.harness / "proc")
        self.data = self.root / "xdg data"
        self.config = self.root / "xdg config"
        self.state_home = self.root / "xdg state"
        patcher = mock.patch.dict(os.environ, {
            "HOME": str(self.home), "XDG_DATA_HOME": str(self.data), "XDG_CONFIG_HOME": str(self.config),
            "XDG_STATE_HOME": str(self.state_home), **self.flatpak.env()})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(os.umask, os.umask(0o022))  # Gear Lever creates files with the caller's umask
        self.helper = load_helper()
        self.receipt = self.state_home / "monolith/software/wowup-cf.managed"
        self.managed = self.data / "monolith/software"
        self.snapshots = self.managed / "wowup-cf"
        self.prefs = self.config / "WowUpCf/preferences.json"
        self.apps = self.home / ".local/share/applications"
        self.appimages = self.home / "AppImages"
        self.cf = self.appimages / "wowupcf.appimage"
        self.cf_desktop = self.apps / "wowupcf.desktop"
        self.cf_icon = self.appimages / ".icons/wowupcf"
        self.allow_recovery = False
        # Unrelated data and a regular (non-CurseForge) WowUp integration that must never change.
        self.apps.mkdir(parents=True)
        (self.apps / "org.example.Editor.desktop").write_text("[Desktop Entry]\nType=Application\nName=Editor\n"
                                                              "Exec=editor %F\n")
        self.regular = self.integrate(make_appimage("2.22.0", name="WowUp"))
        self.kept = {
            self.config / "WowUp/preferences.json": b'{\n\t"wowup_release_channel_2_6": "0"\n}',
            self.home / "Games/World of Warcraft/_retail_/Interface/AddOns/Details/Details.toc": b"## Title: Details\n",
            self.home / "Games/World of Warcraft/_retail_/WTF/Config.wtf": b'SET gxWindow "1"\n',
        }
        for path, data in self.kept.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        self.regular_files = self.integration_files(self.regular)
        self.addCleanup(self.check_clean)

    def check_clean(self):
        self.assertEqual(self.web.unexpected, [], "unexpected HTTP requests")
        self.assertEqual(self.flatpak.unmodelled(), [], "unmodelled flatpak calls")
        if not self.allow_recovery:
            self.assertEqual(self.leaks(), [], "temporary files leaked")
        for path, data in self.kept.items():
            self.assertEqual(path.read_bytes(), data, path)
        self.assertEqual(self.integration_files(self.regular), self.regular_files)

    # --- harness helpers ---------------------------------------------------------------------------------

    def call(self, *arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch("urllib.request.urlopen", self.web.urlopen), \
                mock.patch.object(self.helper, "PROC_ROOT", self.proc.root), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = self.helper.main(list(arguments))
        return Result(code, stdout.getvalue(), stderr.getvalue())

    def install(self, scope="system"):
        return self.call("--scope", scope, "install")

    def remove(self, scope="system"):
        return self.call("--scope", scope, "remove")

    def ok(self, result):
        self.assertEqual(result.code, 0, result)
        return result

    def failed(self, result):
        self.assertEqual(result.code, 1, result)
        self.assertTrue(result.stderr.strip(), result)
        return result

    def status(self):
        result = self.ok(self.call("status"))
        return result.stdout.rstrip("\n")

    def info(self):
        return json.loads(self.ok(self.call("info")).stdout)

    def serve(self, *versions, assets=(), extra=()):
        self.web.routes.clear()
        self.web.publish([release(version) for version in versions] + list(extra))
        for version in assets:
            self.web.asset(version, make_appimage(version))

    def integrate(self, data, *, keep_both=False):
        staged = self.root / "Downloads" / f"WowUp-CF-{sha256(data)[:12]}.AppImage"
        staged.parent.mkdir(exist_ok=True)
        staged.write_bytes(data)
        code, out, err = self.flatpak.gear_lever("--integrate", str(staged), "--yes",
                                                 *([] if keep_both else ["--replace"]))
        self.assertEqual(code, 0, err)
        return Path(out.split(" was integrated successfully")[0])

    def integration_files(self, appimage):
        listing = {entry["path"]: entry for entry in self.flatpak.listing()}
        entry = listing.get(str(appimage))
        if entry is None:
            return None
        desktop = self.apps / entry["desktop_id"]
        icon = desktop_entry(desktop)["Icon"]
        section = f"app.{GearLeverModel.digest(str(appimage))}"
        return (Path(appimage).read_bytes(), desktop.read_bytes(), Path(icon).read_bytes(),
                {key: value for key, value in self.gear_lever_config().items() if key.startswith(section)})

    def gear_lever_config(self):
        path = self.home / ".var/app" / APP_ID / "config/gearlever.conf"
        parser = configparser.ConfigParser(interpolation=None, default_section="\0none")
        if path.exists():
            parser.read(path)
        sections = {name: dict(parser.items(name, raw=True)) for name in parser.sections() if name != "DEFAULT"}
        return {name: values for name, values in sections.items() if values}

    def snapshot(self):
        """Every byte, mode and link outside the harness; Gear Lever's config is compared by content."""
        found = {}
        for directory, dirnames, filenames in os.walk(self.root):
            base = Path(directory)
            if base == self.root:
                dirnames.remove("harness")
            for name in dirnames + filenames:
                path = base / name
                relative = path.relative_to(self.root).as_posix()
                if relative.endswith("/gearlever.conf"):
                    continue
                status = os.lstat(path)
                if stat.S_ISLNK(status.st_mode):
                    found[relative] = ("link", os.readlink(path))
                elif stat.S_ISDIR(status.st_mode):
                    found[relative] = ("dir", stat.S_IMODE(status.st_mode))
                else:
                    found[relative] = ("file", stat.S_IMODE(status.st_mode), path.read_bytes())
        return found, self.gear_lever_config()

    def leaks(self):
        found = []
        for directory, dirnames, filenames in os.walk(self.root):
            for name in dirnames + filenames:
                if name.startswith((".stage.", ".rollback.")) or name.endswith((".monolith-tmp", ".monolith-new")):
                    found.append(os.path.join(directory, name))
        return found

    def cf_launchers(self):
        return sorted(path for path in self.apps.glob("*.desktop")
                      if (desktop_entry(path) or {}).get("X-AppImage-Name") == "WowUp-CF")

    def cf_entries(self):
        return [entry for entry in self.flatpak.listing() if entry["name"].startswith("WowUp-CF")]

    def integrations_attempted(self, since):
        return [call for call in self.flatpak.calls()[since:] if "--integrate" in call]

    def read_receipt(self):
        return dict(line.split("=", 1) for line in self.receipt.read_text().splitlines())

    def write_prefs(self, value, mode=0o644):
        self.prefs.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(value, indent="\t").encode()
        self.prefs.write_bytes(data)
        self.prefs.chmod(mode)
        return data

    def prefs_value(self):
        return json.loads(self.prefs.read_text())

    def managed_install(self, version="2.24.0-beta.6"):
        self.serve(version, assets=[version])
        self.ok(self.install())

    def spawn(self, *arguments):
        routes = self.harness / "http.json"
        self.web.save(routes)
        environment = dict(os.environ, **{HTTP_ENV: str(routes), PROC_ENV: str(self.proc.root)})
        return subprocess.Popen([sys.executable, str(THIS), "run-helper", *arguments], env=environment,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                start_new_session=True)

    def wait_for(self, path, process):
        deadline = time.monotonic() + 30
        while not path.exists():
            if process.poll() is not None:
                self.fail(f"helper exited before {path.name}: {process.communicate()}")
            if time.monotonic() > deadline:
                os.killpg(process.pid, signal.SIGKILL)
                self.fail(f"timed out waiting for {path}")
            time.sleep(0.02)

    # --- release policy -----------------------------------------------------------------------------------

    def test_release_selection_policy(self):
        helper = self.helper

        def pick(*items, latched=False):
            return helper.select_target(helper.parse_releases(list(items)), latched).version

        line = [release("2.23.1"), release("2.24.0-beta.5"), release("2.24.0-beta.6"),
                release("2.24.0-beta.10"), release("2.25.0-beta.1")]
        self.assertEqual(pick(*line), "2.24.0-beta.10")
        self.assertEqual(pick(*reversed(line), release("2.24.0-beta.9")), "2.24.0-beta.10")
        # An old-line hotfix published last, and therefore listed first, is never chosen.
        self.assertEqual(pick(release("2.23.2"), *line), "2.24.0-beta.10")
        stable = [release("2.24.0"), *line, release("2.24.1-beta.1"), release("2.25.0-beta.2")]
        self.assertEqual(pick(*stable), "2.24.0")
        self.assertEqual(pick(*stable, latched=True), "2.24.0")
        self.assertEqual(pick(*stable, release("2.24.3"), release("2.25.0")), "2.25.0")
        with self.assertRaises(helper.HelperError):
            pick(*line, release("2.24.1-beta.1"), latched=True)
        with self.assertRaises(helper.HelperError):
            pick(release("2.23.1"), release("2.24.0-beta.5"), release("2.24.1-beta.1"), release("2.25.0-beta.1"))
        rejected = [
            release("2.24.0-beta.20", draft=True), release("2.24.0", draft=True),
            release("2.24.0-beta.21", prerelease=False), release("2.24.1", prerelease=True),
            release("2.24.0-beta.22", name="WowUp-2.24.0-beta.22.AppImage"),
            release("2.24.0-beta.23", url="https://github.com/WowUp/WowUp/releases/download/v2.24.0-beta.23/"
                                          "WowUp-CF-2.24.0-beta.23.AppImage"),
            release("2.24.0-beta.24", digest="sha256:" + "Z" * 64), release("2.24.0-beta.25", digest=None),
            release("2.24.2", digest="md5:" + "0" * 32), release("2.24.0-beta.26", state="starter"),
            release("2.24.0-beta.27", duplicate=True), release("2.24.3", size=0),
            dict(release("2.24.0-beta.28"), tag_name="v2.24.0-alpha.28"),
            dict(release("2.24.4"), tag_name="v2.24.4-rc.1"), dict(release("2.24.5"), tag_name="2.24.5"),
        ]
        self.assertEqual(pick(*line, *rejected), "2.24.0-beta.10")
        # The same tag listed twice with different bytes is ambiguous, never guessed.
        twin = release("2.24.0-beta.10", data=make_appimage("2.24.0-beta.10", update_info=b"rebuilt"))
        self.assertEqual(pick(*line, twin), "2.24.0-beta.6")

    def test_install_follows_the_beta_bridge_then_stays_on_stable(self):
        self.serve("2.23.1", "2.24.0-beta.6", "2.24.0-beta.10", "2.25.0-beta.1", assets=["2.24.0-beta.10"])
        self.ok(self.install())
        self.assertEqual(self.cf.read_bytes(), make_appimage("2.24.0-beta.10"))
        self.assertEqual(self.read_receipt()["channel"], "beta-bridge")
        self.serve("2.24.0", "2.23.1", "2.24.0-beta.10", "2.25.0-beta.1", assets=["2.24.0"])
        self.ok(self.install())
        self.assertEqual(self.cf.read_bytes(), make_appimage("2.24.0"))
        receipt = self.read_receipt()
        self.assertEqual((receipt["version"], receipt["channel"]), ("2.24.0", "stable"))
        self.assertEqual(self.cf_launchers(), [self.cf_desktop])
        stable = self.snapshot()
        # With stable reached, betas are never considered again, even if the stable release vanishes.
        self.serve("2.24.0-beta.11", "2.24.1-beta.1", "2.23.1", assets=["2.24.0-beta.11"])
        self.failed(self.install())
        self.assertEqual(self.snapshot(), stable)
        # Later betas are ignored while stable stays current; nothing is downloaded or rewritten.
        self.serve("2.24.0", "2.24.1-beta.1", "2.25.0-beta.2")
        self.ok(self.install())
        self.assertEqual(self.snapshot(), stable)

    def test_paginated_release_metadata_and_metadata_failures(self):
        filler = [release(f"1.{number}.0", digest=None) for number in range(99)]
        self.web.publish(filler + [release("2.24.0-beta.6"), release("2.24.0-beta.10")])
        self.web.asset("2.24.0-beta.10", make_appimage("2.24.0-beta.10"))
        self.ok(self.install())
        self.assertEqual(self.read_receipt()["version"], "2.24.0-beta.10")
        installed = self.snapshot()
        failures = {
            "server error": {"status": 502},
            "network error": {"error": "timed out"},
            "invalid JSON": {"body": b'[{"tag_name": "v2.24.0-beta.12"'},
            "not a release list": {"body": b'{"message": "API rate limit exceeded"}'},
            "malformed release": {"body": b'["v2.24.0-beta.12"]'},
        }
        for label, route in failures.items():
            with self.subTest(label):
                self.web.routes.clear()
                self.web.publish(filler + [release("2.24.0-beta.6"), release("2.24.0-beta.11")])
                self.web.asset("2.24.0-beta.11", make_appimage("2.24.0-beta.11"))
                self.web.add(RELEASES.format(page=2), **route)
                self.failed(self.install())
                self.assertEqual(self.snapshot(), installed)
                self.assertNotIn(download_url("2.24.0-beta.11"), self.web.requests)

    def test_never_downgrades_or_replaces_unidentified_bytes(self):
        cases = {
            "locally rebuilt bytes": make_appimage("2.24.0-beta.6", update_info=b"rebuilt"),
            "newer beta outside the bridge": make_appimage("2.25.0-beta.1"),
        }
        for label, data in cases.items():
            with self.subTest(label):
                appimage = self.integrate(data)
                self.serve("2.24.0-beta.6", "2.24.0-beta.10", "2.25.0-beta.1", assets=["2.24.0-beta.10"])
                before = self.snapshot()
                self.failed(self.install())
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(self.flatpak.gear_lever("--remove", str(appimage), "--yes", "--delete")[0], 0)
        # The receipt identifies bytes whose release disappeared; an older target is still refused.
        self.managed_install("2.24.0-beta.10")
        self.serve("2.24.0-beta.6", "2.24.0-beta.5")
        before = self.snapshot()
        self.failed(self.install())
        self.assertEqual(self.snapshot(), before)

    # --- Gear Lever integration --------------------------------------------------------------------------

    def test_fresh_install_repeat_and_remove(self):
        self.serve("2.23.1", "2.24.0-beta.6", assets=["2.24.0-beta.6"])
        self.assertEqual(self.status(), "not installed")
        self.ok(self.install())
        self.assertEqual(self.cf_launchers(), [self.cf_desktop])
        self.assertEqual(len(self.cf_entries()), 1)
        launcher = desktop_entry(self.cf_desktop)
        self.assertEqual(launcher["TryExec"], str(self.cf))
        self.assertEqual(launcher["X-AppImage-Version"], "2.24.0-beta.6")
        self.assertEqual(self.cf.read_bytes(), make_appimage("2.24.0-beta.6"))
        self.assertTrue(self.receipt.read_text().startswith("version=2.24.0-beta.6\n"))
        self.assertEqual(self.read_receipt(), {
            "version": "2.24.0-beta.6", "source": download_url("2.24.0-beta.6"), "channel": "beta-bridge",
            "sha256": sha256(make_appimage("2.24.0-beta.6")), "appimage": str(self.cf),
            "desktop_id": "wowupcf.desktop", "icon": str(self.cf_icon)})
        self.assertEqual((self.snapshots / "integration.desktop").read_bytes(), self.cf_desktop.read_bytes())
        self.assertEqual((self.snapshots / "integration.icon").read_bytes(), ICON)
        # Missing preferences get exactly the application channel, private to the user.
        self.assertEqual(self.prefs_value(), {"wowup_release_channel_2_6": "1"})
        self.assertEqual(stat.S_IMODE(self.prefs.stat().st_mode), 0o600)
        self.assertFalse(self.prefs.with_name("preferences.json.monolith-backup").exists())
        self.assertEqual(self.status(), "installed")
        self.assertEqual(self.info(), {
            "status": "installed", "version": "2.24.0-beta.6", "appimage": str(self.cf),
            "desktop_id": "wowupcf.desktop", "source": download_url("2.24.0-beta.6"),
            "channel": "beta-bridge", "conflicts": []})
        installed = self.snapshot()
        self.serve("2.23.1", "2.24.0-beta.6")
        self.ok(self.install())
        self.assertEqual(self.snapshot(), installed)
        self.assertEqual(len(self.cf_entries()), 1)
        # Removal deletes only the integration; preferences, profile and game data stay.
        (self.prefs.parent / "addons.json").write_text("{}")
        self.ok(self.remove())
        for path in (self.cf, self.cf_desktop, self.cf_icon, self.receipt, self.snapshots):
            self.assertFalse(path.exists(), path)
        self.assertEqual(self.cf_entries(), [])
        self.assertEqual(self.prefs_value(), {"wowup_release_channel_2_6": "1"})
        self.assertEqual((self.prefs.parent / "addons.json").read_text(), "{}")
        self.assertEqual(self.status(), "not installed")
        self.failed(self.remove())

    def test_adopts_self_updated_integration_with_stale_launcher_version(self):
        self.assertEqual(self.integrate(make_appimage("2.24.0-beta.5")), self.cf)
        self.cf.write_bytes(make_appimage("2.24.0-beta.6"))  # WowUp updated itself in place
        launcher = self.cf_desktop.read_bytes()
        self.assertIn(b"X-AppImage-Version=2.24.0-beta.5\n", launcher)
        original = {"wowup_release_channel_2_6": "0", "retail_default_addon_channel": "1",
                    "classic_default_addon_channel": "2", "wow_installations": [{"location": "/games/wow"}]}
        data = self.write_prefs(original)
        self.assertEqual(self.info(), {
            "status": "external", "version": None, "appimage": str(self.cf), "desktop_id": "wowupcf.desktop",
            "source": None, "channel": None, "conflicts": []})
        self.serve("2.24.0-beta.5", "2.24.0-beta.6")  # no asset route: nothing may be downloaded
        self.ok(self.install())
        self.assertEqual(self.cf.read_bytes(), make_appimage("2.24.0-beta.6"))
        self.assertEqual(self.cf_desktop.read_bytes(), launcher.replace(b"X-AppImage-Version=2.24.0-beta.5\n",
                                                                        b"X-AppImage-Version=2.24.0-beta.6\n"))
        receipt = self.read_receipt()
        self.assertEqual((receipt["version"], receipt["sha256"], receipt["channel"]),
                         ("2.24.0-beta.6", sha256(make_appimage("2.24.0-beta.6")), "beta-bridge"))
        self.assertEqual(self.prefs_value(), dict(original, wowup_release_channel_2_6="1"))
        self.assertEqual(list(self.prefs_value()), list(original))
        self.assertEqual(stat.S_IMODE(self.prefs.stat().st_mode), 0o644)
        self.assertEqual(self.prefs.with_name("preferences.json.monolith-backup").read_bytes(), data)
        self.assertEqual(self.status(), "installed")

    def test_update_keeps_launcher_id_custom_exec_and_one_integration(self):
        self.integrate(make_appimage("2.24.0-beta.6"))
        stock = f"Exec=env DESKTOPINTEGRATION=1 {self.cf} --no-sandbox %U\n".encode()
        custom = self.cf_desktop.read_bytes().replace(
            stock, f"Exec=env DESKTOPINTEGRATION=1 MANGOHUD=1 {self.cf} --no-sandbox --disable-gpu %U\n".encode())
        self.assertNotIn(stock, custom)
        self.cf_desktop.write_bytes(custom)
        config = self.gear_lever_config()
        self.serve("2.24.0-beta.6", "2.24.0-beta.10", assets=["2.24.0-beta.10"])
        self.ok(self.install())
        self.assertEqual(self.cf.read_bytes(), make_appimage("2.24.0-beta.10"))
        self.assertEqual(self.cf_desktop.read_bytes(), custom.replace(b"X-AppImage-Version=2.24.0-beta.6\n",
                                                                      b"X-AppImage-Version=2.24.0-beta.10\n"))
        self.assertEqual(self.cf_launchers(), [self.cf_desktop])
        self.assertEqual([entry["path"] for entry in self.cf_entries()], [str(self.cf)])
        self.assertEqual(self.gear_lever_config(), config)

    def test_canonical_home_alias_and_spaces(self):
        (self.root / "home").symlink_to("var/home")
        gear_lever_home = self.root / "home/player"
        (self.home / "Games and Apps").mkdir()
        self.flatpak.update(home=str(gear_lever_home))
        set_gear_lever_folder(str(gear_lever_home), "~/Games and Apps/AppImages")
        appimage = gear_lever_home / "Games and Apps/AppImages/wowupcf.appimage"
        self.serve("2.24.0-beta.6", assets=["2.24.0-beta.6"])
        self.ok(self.install())
        receipt = self.read_receipt()
        self.assertEqual(receipt["appimage"], str(appimage))
        self.assertEqual(receipt["icon"], str(appimage.parent / ".icons/wowupcf"))
        self.assertEqual(self.status(), "installed")
        self.serve("2.24.0-beta.6", "2.24.0-beta.10", assets=["2.24.0-beta.10"])
        self.ok(self.install())
        self.assertEqual((self.home / "Games and Apps/AppImages/wowupcf.appimage").read_bytes(),
                         make_appimage("2.24.0-beta.10"))
        self.assertEqual(self.read_receipt()["appimage"], str(appimage))
        self.ok(self.remove())
        self.assertFalse(appimage.exists())
        self.assertFalse(self.cf_desktop.exists())
        self.assertEqual(self.status(), "not installed")

    def test_multiple_cf_integrations_are_a_conflict(self):
        self.integrate(make_appimage("2.24.0-beta.5"))
        self.integrate(make_appimage("2.24.0-beta.6"), keep_both=True)
        launchers = self.cf_launchers()
        self.assertEqual(len(launchers), 2)
        info = self.info()
        self.assertEqual((info["status"], info["appimage"], info["desktop_id"]), ("external", None, None))
        self.assertEqual(sorted(info["conflicts"]), [str(path) for path in launchers])
        self.serve("2.24.0-beta.6", assets=["2.24.0-beta.6"])
        before = self.snapshot()
        result = self.failed(self.install())
        for path in launchers:
            self.assertIn(str(path), result.stderr)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.web.requests, [])
        # A second CurseForge launcher beside Monolith's own needs consolidation too.
        for path in launchers:
            self.assertEqual(self.flatpak.gear_lever("--remove", desktop_entry(path)["TryExec"], "--yes",
                                                     "--delete")[0], 0)
        self.managed_install()
        other = self.integrate(make_appimage("2.24.0-beta.5"), keep_both=True)
        self.assertEqual(self.status(), "needs repair")
        second = [path for path in self.cf_launchers() if path != self.cf_desktop]
        self.assertEqual(self.info()["conflicts"], [str(path) for path in second])
        before = self.snapshot()
        self.failed(self.install())
        self.failed(self.remove())
        self.assertEqual(self.snapshot(), before)
        self.assertTrue(other.exists())

    # --- WowUp preferences --------------------------------------------------------------------------------

    def test_existing_preferences_are_merged_with_a_single_backup(self):
        original = {"theme": "dark", "wowup_release_channel_2_6": "0", "retail_default_addon_channel": "0",
                    "classic_default_addon_channel": "1", "wow_installations": [{"location": "/games/wow"}]}
        data = self.write_prefs(original, mode=0o640)
        profile = self.prefs.parent / "addons.json"
        profile.write_text('{"Details": "1.0"}')
        backup = self.prefs.with_name("preferences.json.monolith-backup")
        self.managed_install()
        self.assertEqual(self.prefs_value(), dict(original, wowup_release_channel_2_6="1"))
        self.assertEqual(list(self.prefs_value()), list(original))
        self.assertEqual(stat.S_IMODE(self.prefs.stat().st_mode), 0o640)
        self.assertEqual(backup.read_bytes(), data)
        # WowUp switched itself back to Beta later; the channel is reset and the first backup kept.
        self.write_prefs(dict(self.prefs_value(), wowup_release_channel_2_6="0", language="de"), mode=0o640)
        self.serve("2.24.0-beta.6")
        self.ok(self.install())
        self.assertEqual(self.prefs_value(), dict(original, wowup_release_channel_2_6="1", language="de"))
        self.assertEqual(backup.read_bytes(), data)
        stable = self.snapshot()
        self.ok(self.install())
        self.assertEqual(self.snapshot(), stable)
        self.assertEqual(profile.read_text(), '{"Details": "1.0"}')
        # Large preference stores (over 1 MiB of other settings) are updated too.
        padding = "x" * (2 * 1024 * 1024)
        self.write_prefs(dict(self.prefs_value(), wowup_release_channel_2_6="0", cache=padding), mode=0o640)
        self.ok(self.install())
        self.assertEqual(self.prefs_value(), dict(original, wowup_release_channel_2_6="1", language="de",
                                                  cache=padding))

    def test_unusable_preferences_block_every_change(self):
        target = self.root / "elsewhere.json"
        target.write_text('{"wowup_release_channel_2_6": "0"}')
        cases = {
            "corrupt JSON": lambda: self.prefs.write_text('{"wowup_release_channel_2_6": "0",'),
            "JSON array": lambda: self.prefs.write_text('["wowup_release_channel_2_6"]'),
            "symbolic link": lambda: self.prefs.symlink_to(target),
            "directory": lambda: self.prefs.mkdir(),
        }
        self.serve("2.24.0-beta.6", assets=["2.24.0-beta.6"])
        for label, arrange in cases.items():
            with self.subTest(label):
                self.prefs.parent.mkdir(parents=True, exist_ok=True)
                arrange()
                before = self.snapshot()
                result = self.failed(self.install())
                self.assertIn(str(self.prefs), result.stderr)
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(self.web.requests, [])
                if self.prefs.is_dir() and not self.prefs.is_symlink():
                    self.prefs.rmdir()
                else:
                    self.prefs.unlink()

    def test_running_app_blocks_every_change(self):
        self.managed_install()
        self.serve("2.24.0-beta.6", "2.24.0-beta.10", assets=["2.24.0-beta.10"])
        uid = os.getuid()
        alias = self.root / "AppImages alias"
        alias.symlink_to(self.appimages)
        installed = self.snapshot()
        cases = {
            "Gear Lever reports it running": lambda: self.flatpak.update(running={str(self.cf): True}),
            "Gear Lever could not tell": lambda: self.flatpak.update(running={str(self.cf): None}),
            "its AppImage process": lambda: self.proc.add(4100, uid=uid,
                                                          environ={"APPIMAGE": str(alias / "wowupcf.appimage")}),
            # Electron overwrites its processes' environ; the AppImage runtime keeps running as the
            # FUSE server with the AppImage itself as /proc/PID/exe (resolved, e.g. /var/home/...).
            "its AppImage runtime": lambda: self.proc.add(
                4150, uid=uid, comm="wowupcf.appimag", cmdline=(str(self.cf), "--no-sandbox"),
                environ={"PATH": "/usr/bin"}, exe=str(alias / "wowupcf.appimage")),
            "a runtime whose AppImage was replaced": lambda: self.proc.add(
                4160, uid=uid, comm="wowupcf.appimag", exe=f"{self.cf} (deleted)"),
            "an unreadable WowUp-CF process": lambda: self.proc.add(
                4200, uid=uid, comm="wowup-cf", cmdline=("/tmp/.mount_WowUpCabc/wowup-cf", "--type=zygote"),
                readable=False),
        }
        for label, arrange in cases.items():
            with self.subTest(label):
                arrange()
                for action in (self.install, self.remove):
                    result = action()
                    self.assertEqual((result.code, result.stderr.strip()), (1, RUNNING))
                    self.assertEqual(self.snapshot(), installed)
                self.flatpak.update(running={})
                self.proc.clear()
        self.assertNotIn(download_url("2.24.0-beta.10"), self.web.requests)
        # WowUp-CF starting during the download is caught by the recheck before anything changes.
        self.web.routes[download_url("2.24.0-beta.10")]["on_open"] = lambda: self.proc.add(
            4300, uid=uid, environ={"APPIMAGE": str(self.cf)})
        result = self.install()
        self.assertEqual((result.code, result.stderr.strip()), (1, RUNNING))
        self.assertEqual(self.snapshot(), installed)
        self.proc.clear()
        self.web.routes[download_url("2.24.0-beta.10")]["on_open"] = None
        # Other users' processes, unrelated unreadable ones and vanished ones do not block.
        self.proc.add(4400, uid=uid + 1, environ={"APPIMAGE": str(self.cf)})
        self.proc.add(4500, uid=uid, comm="kwin_wayland", cmdline=("/usr/bin/kwin_wayland",), readable=False)
        self.proc.add(4600, uid=uid, environ={"APPIMAGE": str(self.regular)})
        self.proc.add(4650, uid=uid, comm="wowup.appimage", exe=str(self.regular))
        self.proc.add_vanished(4700)
        self.ok(self.install())
        self.assertEqual(self.cf.read_bytes(), make_appimage("2.24.0-beta.10"))

    # --- failures and rollback ----------------------------------------------------------------------------

    def test_fresh_install_failures_leave_no_trace(self):
        good = make_appimage("2.24.0-beta.6")
        tampered = bytearray(good)
        tampered[good.index(b"payload")] ^= 1
        state_root = self.state_home / "monolith/software"

        def receipt_unwritable():
            state_root.parent.mkdir(parents=True)
            state_root.write_text("not a directory\n")

        cases = {
            "checksum mismatch": lambda: self.web.asset("2.24.0-beta.6", bytes(tampered)),
            "truncated download": lambda: self.web.asset("2.24.0-beta.6", good[:-512], length=False),
            "oversized download": lambda: self.web.asset("2.24.0-beta.6", good + b"\0" * 64, length=False),
            "Gear Lever integration fails": lambda: self.flatpak.fault("integrate", "fail"),
            "Gear Lever fails after integrating": lambda: self.flatpak.fault("integrate", "partial"),
            "Gear Lever integrates other bytes": lambda: self.flatpak.fault("integrate", "corrupt"),
            "receipt cannot be published": receipt_unwritable,
        }
        for label, arrange in cases.items():
            with self.subTest(label):
                self.serve("2.24.0-beta.6", assets=["2.24.0-beta.6"])
                arrange()
                before = self.snapshot()
                calls = len(self.flatpak.calls())
                self.failed(self.install())
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(self.cf_entries(), [])
                if label.endswith("download") or label == "checksum mismatch":
                    self.assertEqual(self.integrations_attempted(calls), [])  # never handed to Gear Lever
                self.flatpak.update(faults={})
                if state_root.is_file():
                    state_root.unlink()

    def test_identical_unlaunched_copy_is_named_and_kept(self):
        # A launcher deleted by hand leaves Gear Lever's copy behind; Gear Lever then calls an identical
        # AppImage "already integrated" and creates nothing. That user file must survive the rollback.
        self.integrate(make_appimage("2.24.0-beta.6"))
        self.cf_desktop.unlink()
        self.serve("2.24.0-beta.6", assets=["2.24.0-beta.6"])
        before = self.snapshot()
        result = self.failed(self.install())
        self.assertIn(str(self.cf), result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_gear_lever_crash_after_copying_leaves_no_stray_files(self):
        # Gear Lever 4.6.2 writes its launcher to a path with spaces replaced; with a space in HOME it
        # crashes after copying the AppImage and icon. Monolith removes exactly those new copies.
        home = self.root / "var/home/player two"
        (home / ".local/share/applications").mkdir(parents=True)
        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            self.flatpak.listing()  # Gear Lever has been started in this HOME before
            self.serve("2.24.0-beta.6", assets=["2.24.0-beta.6"])
            before = self.snapshot()
            self.failed(self.install())
            self.assertEqual(self.snapshot(), before)

    def test_crash_in_a_custom_appimage_folder_leaves_no_stray_files(self):
        # Gear Lever copies into its configured folder; a crash there must be cleaned up the same way,
        # or every retry would hit Gear Lever's "already integrated" no-op.
        set_gear_lever_folder(str(self.home), "~/Apps")
        self.flatpak.fault("integrate", "crash", once=True)
        self.serve("2.24.0-beta.6", assets=["2.24.0-beta.6"])
        before = self.snapshot()
        self.failed(self.install())
        self.assertEqual(self.snapshot(), before)
        self.ok(self.install())
        self.assertEqual(self.read_receipt()["appimage"], str(self.home / "Apps/wowupcf.appimage"))
        self.assertEqual(len(self.cf_entries()), 1)

    def test_update_failures_restore_the_previous_install(self):
        self.managed_install()
        self.write_prefs({"wowup_release_channel_2_6": "0", "theme": "dark"})
        self.serve("2.24.0-beta.6", "2.24.0-beta.10", assets=["2.24.0-beta.10"])
        installed = self.snapshot()
        helper = self.helper
        launcher_key = self.cf_desktop.relative_to(self.root).as_posix()
        edited = self.cf_desktop.read_bytes() + b"X-KDE-Edited=true\n"

        def disk_full(method, path):
            original = getattr(helper.Transaction, method)

            def failing(transaction, target, *arguments):
                if Path(target) == path:
                    raise OSError(errno.ENOSPC, "No space left on device", str(target))
                return original(transaction, target, *arguments)
            return mock.patch.object(helper.Transaction, method, failing)

        def concurrent_edit(data, key, value, original=helper.set_desktop_value):
            self.cf_desktop.write_bytes(edited)  # the user edits the launcher in Gear Lever meanwhile
            return original(data, key, value)

        @contextlib.contextmanager
        def tampered_download():
            good = make_appimage("2.24.0-beta.10")
            tampered = bytearray(good)
            tampered[good.index(b"payload")] ^= 1  # same size, valid header: only the digest differs
            self.web.asset("2.24.0-beta.10", bytes(tampered))
            try:
                yield
            finally:
                self.web.asset("2.24.0-beta.10", good)

        cases = {
            "download digest": (tampered_download(), None),
            "receipt": (disk_full("write_private", self.receipt), None),
            "launcher snapshot": (disk_full("write_private", self.snapshots / "integration.desktop"), None),
            "preferences": (disk_full("replace_file", self.prefs), None),
            "launcher changed meanwhile": (mock.patch.object(helper, "set_desktop_value", concurrent_edit), edited),
        }
        for label, (patch, launcher) in cases.items():
            with self.subTest(label):
                with patch:
                    self.failed(self.install())
                files, config = installed
                if launcher is not None:
                    files = {**files, launcher_key: (*files[launcher_key][:2], launcher)}
                self.assertEqual(self.snapshot(), (files, config))
        self.cf_desktop.write_bytes(installed[0][launcher_key][2])
        self.ok(self.install())
        self.assertEqual(self.cf.read_bytes(), make_appimage("2.24.0-beta.10"))
        self.assertEqual(self.prefs_value(), {"wowup_release_channel_2_6": "1", "theme": "dark"})

    def test_unremovable_new_integration_is_reported_not_hidden(self):
        self.serve("2.24.0-beta.6", assets=["2.24.0-beta.6"])
        state_root = self.state_home / "monolith/software"
        state_root.parent.mkdir(parents=True)
        state_root.write_text("not a directory\n")
        self.flatpak.fault("remove", "fail")
        result = self.failed(self.install())
        self.assertEqual([entry["path"] for entry in self.cf_entries()], [str(self.cf)])
        self.assertIn(str(self.cf), result.stderr)
        self.assertIn(str(self.cf_desktop), result.stderr)
        self.assertFalse(self.prefs.exists())
        self.assertTrue(state_root.is_file())
        self.assertEqual(self.status(), "external")

    def test_repairs_missing_owned_artifacts_from_snapshots(self):
        self.managed_install()
        custom = self.cf_desktop.read_bytes().replace(b" --no-sandbox %U", b" --no-sandbox --disable-gpu %U")
        self.cf_desktop.write_bytes(custom)
        self.serve("2.24.0-beta.6")
        self.ok(self.install())
        self.assertEqual((self.snapshots / "integration.desktop").read_bytes(), custom)
        installed = self.snapshot()
        for label, path in {"icon": self.cf_icon, "launcher": self.cf_desktop, "AppImage": self.cf}.items():
            with self.subTest(label):
                path.unlink()
                info = self.info()
                self.assertEqual((info["status"], info["conflicts"]), ("needs repair", [str(path)]))
                self.serve("2.24.0-beta.6", assets=["2.24.0-beta.6"] if path == self.cf else ())
                self.ok(self.install())
                self.assertEqual(self.snapshot(), installed)
                self.assertEqual(self.status(), "installed")
        # A needed snapshot that is missing, or a conflicting replacement, is never overwritten.
        self.cf_desktop.unlink()
        (self.snapshots / "integration.desktop").unlink()
        before = self.snapshot()
        self.failed(self.install())
        self.assertEqual(self.snapshot(), before)
        self.cf_desktop.write_bytes(custom.replace(str(self.cf).encode(), str(self.regular).encode()))
        before = self.snapshot()
        self.assertEqual(self.status(), "needs repair")
        result = self.failed(self.install())
        self.assertIn(str(self.cf_desktop), result.stderr)
        self.failed(self.remove())
        self.assertEqual(self.snapshot(), before)
        self.cf_desktop.unlink()

    def test_removal_keeps_custom_icons_and_stays_retryable(self):
        self.managed_install()
        picture = self.home / "Pictures/wowup-cf.png"
        picture.parent.mkdir()
        picture.write_bytes(b"\x89PNG custom icon")
        launcher = self.cf_desktop.read_bytes().replace(f"Icon={self.cf_icon}".encode(), f"Icon={picture}".encode())
        self.cf_desktop.write_bytes(launcher)
        self.flatpak.fault("remove", "fail")
        before = self.snapshot()
        self.failed(self.remove())
        self.assertEqual(self.snapshot(), before)
        self.flatpak.fault("remove", "partial", once=True)
        self.failed(self.remove())
        self.assertFalse(self.cf.exists())
        self.assertEqual(self.cf_desktop.read_bytes(), launcher)
        self.assertTrue(self.receipt.exists())
        self.assertEqual(self.status(), "needs repair")
        self.serve("2.24.0-beta.6", assets=["2.24.0-beta.6"])
        self.ok(self.remove())
        for path in (self.cf, self.cf_desktop, self.cf_icon, self.receipt, self.snapshots):
            self.assertFalse(path.exists(), path)
        self.assertEqual(picture.read_bytes(), b"\x89PNG custom icon")
        self.assertEqual(self.status(), "not installed")

    def test_removal_after_gear_lever_already_removed_it(self):
        self.managed_install()
        self.assertEqual(self.flatpak.gear_lever("--remove", str(self.cf), "--yes", "--delete")[0], 0)
        (self.snapshots / "integration.desktop").unlink()
        self.assertEqual(self.status(), "needs repair")
        self.web.routes.clear()  # nothing may be downloaded merely to forget a removed integration
        self.ok(self.remove())
        for path in (self.cf, self.cf_desktop, self.cf_icon, self.receipt, self.snapshots):
            self.assertFalse(path.exists(), path)
        self.assertEqual(self.prefs_value(), {"wowup_release_channel_2_6": "1"})
        self.assertEqual(self.status(), "not installed")

    def test_custom_update_source_is_cleared_per_app_and_restored_on_failure(self):
        self.integrate(make_appimage("2.24.0-beta.6"))
        self.assertEqual(self.flatpak.gear_lever(
            "--set-update-source", str(self.cf), "--manager", "GithubUpdater", "repo=WowUp/WowUp.CF",
            "repo_filename=WowUp-CF-*.AppImage", "allow_prereleases=true")[0], 0)
        self.assertEqual(self.flatpak.gear_lever(
            "--set-update-source", str(self.regular), "--manager", "StaticFileUpdater",
            "url=https://example.invalid/WowUp.AppImage")[0], 0)
        self.regular_files = self.integration_files(self.regular)
        section = f"app.{GearLeverModel.digest(str(self.cf))}.update_manager"
        configured = self.gear_lever_config()
        self.assertEqual(configured[section], {"repo": "WowUp/WowUp.CF", "repo_filename": "WowUp-CF-*.AppImage",
                                               "allow_prereleases": "True", "manager": "GithubUpdater"})
        self.serve("2.24.0-beta.6")
        before = self.snapshot()
        write_private = self.helper.Transaction.write_private

        def full_disk(transaction, path, data, label):
            if Path(path) == self.receipt:
                raise OSError(errno.ENOSPC, "No space left on device", str(path))
            return write_private(transaction, path, data, label)

        with mock.patch.object(self.helper.Transaction, "write_private", full_disk):
            self.failed(self.install())
        self.assertEqual(self.snapshot(), before)
        self.assertEqual([entry["manager"] for entry in self.cf_entries()], ["GithubUpdater"])
        self.ok(self.install())
        expected = dict(configured)
        del expected[section]
        self.assertEqual(self.gear_lever_config(), expected)
        self.assertEqual([(entry["manager"], entry["embedded_source"]) for entry in self.cf_entries()],
                         [(None, False)])

    def test_embedded_update_source_blocks_publication(self):
        for label, update_info in EMBEDDED.items():
            with self.subTest(label):
                data = make_appimage("2.24.0-beta.6", update_info=update_info)
                self.web.routes.clear()
                self.web.publish([release("2.24.0-beta.6", data=data)])
                self.web.asset("2.24.0-beta.6", data)
                before = self.snapshot()
                calls = len(self.flatpak.calls())
                self.failed(self.install())
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(self.integrations_attempted(calls), [])  # refused before publication
        # An existing embedded build that is already the target is not adopted ...
        data = make_appimage("2.24.0-beta.6", update_info=EMBEDDED["GitHub zsync"])
        self.integrate(data)
        self.web.routes.clear()
        self.web.publish([release("2.24.0-beta.6", data=data)])
        before = self.snapshot()
        self.failed(self.install())
        self.assertEqual(self.snapshot(), before)
        # ... but a newer target without one replaces it in place, leaving no update source.
        self.web.publish([release("2.24.0-beta.6", data=data), release("2.24.0-beta.10")])
        self.web.asset("2.24.0-beta.10", make_appimage("2.24.0-beta.10"))
        self.ok(self.install())
        self.assertEqual([(entry["manager"], entry["embedded_source"]) for entry in self.cf_entries()],
                         [(None, False)])

    def test_gear_lever_failures_change_nothing(self):
        self.managed_install()
        self.serve("2.24.0-beta.6", "2.24.0-beta.10", assets=["2.24.0-beta.10"])
        installed = self.snapshot()
        cases = {
            "listing fails": lambda: self.flatpak.fault("list", "fail"),
            "listing is not JSON": lambda: self.flatpak.fault("list", "bad-json"),
            "unknown listing schema": lambda: self.flatpak.fault("list", "schema"),
            "listing hangs": lambda: self.flatpak.fault("list", "hang"),
            "Gear Lever older than 4.6.2": lambda: self.flatpak.update(installed={"system": "4.5.0"}),
            "Gear Lever missing in that scope": lambda: self.flatpak.update(installed={"user": "4.6.2"}),
        }
        for label, arrange in cases.items():
            with self.subTest(label):
                arrange()
                self.web.requests.clear()
                try:
                    with mock.patch.object(self.helper, "GEAR_LEVER_TIMEOUT", 2):
                        results = [self.install(), self.remove()]
                finally:
                    self.flatpak.update(faults={}, installed={"system": "4.6.2"})
                for result in results:
                    self.failed(result)
                self.assertEqual(self.snapshot(), installed)
                self.assertEqual(self.web.requests, [])
        self.flatpak.update(installed={"user": "4.6.2"})
        self.ok(self.install("user"))
        self.assertEqual(self.cf.read_bytes(), make_appimage("2.24.0-beta.10"))

    def test_signals_roll_back_and_exit_with_signal_status(self):
        self.serve("2.24.0-beta.6", assets=["2.24.0-beta.6"])
        self.flatpak.fault("integrate", "block", once=True)
        before = self.snapshot()
        process = self.spawn("--scope", "system", "install")
        self.wait_for(self.flatpak.directory / "integrate.blocked", process)
        os.killpg(process.pid, signal.SIGTERM)
        out, err = process.communicate(timeout=60)
        self.assertEqual(process.returncode, 143, out + err)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.cf_entries(), [])
        hold = self.harness / "download.held"
        self.web.routes[download_url("2.24.0-beta.6")]["hold"] = str(hold)
        process = self.spawn("--scope", "system", "install")
        self.wait_for(hold, process)
        os.killpg(process.pid, signal.SIGINT)
        out, err = process.communicate(timeout=60)
        self.assertEqual(process.returncode, 130, out + err)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(Path(f"{self.harness / 'http.json'}.unexpected").exists())

    # --- read-only inspection -----------------------------------------------------------------------------

    def test_status_and_info_only_read_the_filesystem(self):
        def observe(expected):
            before = self.snapshot()
            calls = len(self.flatpak.calls())
            requests = len(self.web.requests)
            with mock.patch.object(self.helper.subprocess, "run", side_effect=AssertionError("process")), \
                    mock.patch.object(self.helper.subprocess, "Popen", side_effect=AssertionError("process")), \
                    mock.patch.object(self.helper, "hashlib", mock.NonCallableMock(spec=[])):
                status = self.ok(self.call("status"))
                info = json.loads(self.ok(self.call("info")).stdout)
            self.assertEqual(status.stdout, expected + "\n")
            self.assertEqual(info["status"], expected)
            self.assertEqual(list(info), ["status", "version", "appimage", "desktop_id", "source", "channel",
                                          "conflicts"])
            self.assertEqual(self.snapshot(), before)
            self.assertEqual((len(self.flatpak.calls()), len(self.web.requests)), (calls, requests))
            return info

        self.assertEqual(observe("not installed")["conflicts"], [])
        self.assertFalse(self.managed.exists())
        self.assertFalse(self.receipt.parent.exists())
        self.integrate(make_appimage("2.24.0-beta.6"))
        self.assertEqual(observe("external")["appimage"], str(self.cf))
        launcher = self.cf_desktop.read_bytes()
        self.cf_desktop.write_bytes(launcher.replace(b"X-AppImage-Name=WowUp-CF\n", b""))
        self.assertEqual(observe("external")["conflicts"], [str(self.cf_desktop)])
        self.cf_desktop.write_bytes(launcher)
        self.cf.unlink()
        self.assertEqual(observe("external")["conflicts"], [str(self.cf)])
        self.cf_desktop.unlink()
        self.cf_icon.unlink()
        self.managed_install()
        info = observe("installed")
        self.assertEqual((info["version"], info["channel"], info["conflicts"]), ("2.24.0-beta.6", "beta-bridge", []))
        self.cf_icon.unlink()
        self.assertEqual(observe("needs repair")["conflicts"], [str(self.cf_icon)])
        receipt = self.receipt.read_text()
        for broken in (receipt + "extra=1\n", receipt.replace("appimage=/", "appimage="),
                       receipt.replace("channel=beta-bridge", "channel=stable"), receipt.rstrip("\n")):
            self.receipt.write_text(broken)
            info = observe("needs repair")
            self.assertEqual((info["version"], info["conflicts"]), (None, [str(self.receipt)]))
        self.receipt.write_text(receipt)
        # --help exits before any filesystem work; install/remove require a scope.
        missing = self.root / "missing home"
        result = subprocess.run([sys.executable, str(HELPER), "--help"], capture_output=True, text=True,
                                env={"PATH": os.environ["PATH"], "HOME": str(missing)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout)
        self.assertFalse(missing.exists())
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            self.helper.main(["install"])
        self.assertEqual(raised.exception.code, 2)


MANAGER = ROOT / "files/system/usr/bin/monolith"
PYTHON_SHIM = """#!/bin/sh
# Run the real WowUp-CF helper with the suite's HTTP and /proc doubles; other Python runs are real.
if [ "$#" -gt 0 ] && [ "$(readlink -f -- "$1")" = {helper} ]; then
    shift
    exec {python} {suite} run-helper "$@"
fi
exec {python} "$@"
"""


class ManagerPrerequisiteTests(unittest.TestCase):
    """The real `monolith` manager finding, offering, and checking Gear Lever before WowUp-CF changes."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="monolith-wowup-manager-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / "home/player"
        self.home.mkdir(parents=True)
        self.flatpak = FakeFlatpak(self.root / "harness/flatpak", installed={})
        self.web = FakeWeb()
        self.web.publish([release("2.23.1"), release("2.24.0-beta.6")])
        self.web.asset("2.24.0-beta.6", make_appimage("2.24.0-beta.6"))
        self.routes = self.root / "harness/http.json"
        self.proc = FakeProc(self.root / "harness/proc")
        shims = self.root / "harness/python"
        shims.mkdir()
        (shims / "python3").write_text(PYTHON_SHIM.format(
            helper=shlex.quote(str(HELPER.resolve())), python=shlex.quote(sys.executable),
            suite=shlex.quote(str(THIS))))
        (shims / "python3").chmod(0o755)
        self.env = {
            "HOME": str(self.home), "USER": "player", "LOGNAME": "player", "LC_ALL": "C.UTF-8",
            # TERM=dumb selects the plain terminal prompt instead of fzf.
            "TERM": "dumb", "PATH": f"{shims}:{self.flatpak.bin}:/usr/bin:/bin",
            "XDG_DATA_HOME": str(self.root / "data"), "XDG_STATE_HOME": str(self.root / "state"),
            "XDG_CONFIG_HOME": str(self.root / "config"), "XDG_CACHE_HOME": str(self.root / "cache"),
            STATE_ENV: str(self.flatpak.state_path), HTTP_ENV: str(self.routes), PROC_ENV: str(self.proc.root),
        }
        self.receipt = self.root / "state/monolith/software/wowup-cf.managed"
        self.appimages = self.home / "AppImages"

    def run_manager(self, *arguments, answer=None):
        """Run the manager; with `answer`, on a terminal that answers its [y/N] prompt."""
        self.web.save(self.routes)
        command = [str(MANAGER), *arguments]
        if answer is None:
            result = subprocess.run(command, env=self.env, stdin=subprocess.DEVNULL, capture_output=True,
                                    text=True, timeout=120)
            return Result(result.returncode, result.stdout, result.stderr)
        primary, secondary = os.openpty()
        process = subprocess.Popen(command, env=self.env, stdin=secondary, stdout=secondary, stderr=secondary,
                                   start_new_session=True)
        os.close(secondary)
        output, answered = b"", False
        deadline = time.monotonic() + 120
        try:
            while time.monotonic() < deadline:
                try:
                    chunk = os.read(primary, 65536)
                except OSError as error:
                    if error.errno == errno.EIO:  # the terminal closed: the manager exited
                        break
                    raise
                if not chunk:
                    break
                output += chunk
                if not answered and b"[y/N]" in output:
                    os.write(primary, answer)
                    answered = True
            else:
                os.killpg(process.pid, signal.SIGKILL)
                self.fail(f"manager did not finish: {output.decode(errors='replace')}")
        finally:
            os.close(primary)
        code = process.wait(timeout=30)
        self.assertTrue(answered or b"[y/N]" not in output, output)
        return Result(code, output.decode("utf-8", "replace"), "")

    def gear_lever_runs(self):
        return [call for call in self.flatpak.calls() if call[:1] == ["run"]]

    def installs(self):
        return [call for call in self.flatpak.calls() if call[:1] == ["install"]]

    def assert_wowup_untouched(self, result):
        self.assertEqual(self.gear_lever_runs(), [], result)
        self.assertFalse(self.routes.with_name("http.json.unexpected").exists(), result)
        self.assertFalse(self.receipt.exists(), result)
        self.assertFalse(self.appimages.exists(), result)

    def test_declined_or_unanswered_offer_changes_nothing(self):
        for answer in (b"n\n", b"\x04"):
            with self.subTest(answer=answer):
                result = self.run_manager("install", "wowup-cf", answer=answer)
                self.assertEqual(result.code, 1, result)
                self.assertRegex(result.stdout, r"WowUp-CF +Skipped by user")
                self.assertNotIn("Installed WowUp-CF.", result.stdout)
                self.assertEqual(self.installs(), [], result)
                self.assert_wowup_untouched(result)

    def test_missing_gear_lever_without_a_terminal_names_the_install_command(self):
        result = self.run_manager("install", "wowup-cf")
        self.assertEqual(result.code, 1, result)
        self.assertIn(f"flatpak install --user --noninteractive --assumeyes {FLATPAKREF}", result.stderr)
        self.assertEqual(self.installs(), [], result)
        self.assert_wowup_untouched(result)

    def test_accepting_installs_gear_lever_for_the_user_then_wowup(self):
        result = self.run_manager("install", "wowup-cf", answer=b"y\n")
        self.assertEqual(result.code, 0, result)
        self.assertEqual(self.flatpak.state()["installed"], {"user": "4.6.2"})
        # The policy is disclosed before Monolith changes anything.
        self.assertLess(result.stdout.index("WowUp-CF release policy"), result.stdout.index("[y/N]"))
        receipt = dict(line.split("=", 1) for line in self.receipt.read_text().splitlines())
        self.assertEqual((receipt["version"], receipt["channel"]), ("2.24.0-beta.6", "beta-bridge"))
        self.assertEqual(Path(receipt["appimage"]).read_bytes(), make_appimage("2.24.0-beta.6"))
        self.assertFalse(self.routes.with_name("http.json.unexpected").exists())

    def test_failed_gear_lever_installation_stops_before_wowup(self):
        self.flatpak.update(install_fails=True)
        result = self.run_manager("install", "wowup-cf", answer=b"y\n")
        self.assertEqual(result.code, 1, result)
        self.assertRegex(result.stdout, r"WowUp-CF +Failed")
        self.assertEqual(self.flatpak.state()["installed"], {})
        self.assert_wowup_untouched(result)

    def test_existing_installation_is_reused_in_its_scope(self):
        for scope in ("system", "user"):
            with self.subTest(scope=scope):
                self.flatpak.update(installed={scope: "4.6.2"})
                result = self.run_manager("install", "wowup-cf")
                self.assertEqual(result.code, 0, result)
                # The model refuses Gear Lever runs in a scope where it is not installed.
                self.assertTrue(self.receipt.exists(), result)
                self.assertEqual(self.installs(), [])
                self.assertEqual(self.flatpak.state()["installed"], {scope: "4.6.2"})
                self.assertEqual(self.run_manager("remove", "wowup-cf").code, 0)
                self.assertFalse(self.receipt.exists())
                Path(f"{self.flatpak.state_path}.calls").unlink()

    def test_unsupported_gear_lever_version_is_refused(self):
        self.flatpak.update(installed={"system": "4.6.1"})
        result = self.run_manager("install", "wowup-cf")
        self.assertEqual(result.code, 1, result)
        self.assertIn("flatpak update --system it.mijorus.gearlever", result.stderr)
        self.assertEqual(self.installs(), [])
        self.assert_wowup_untouched(result)


def main():
    if sys.argv[1:2] == ["run-helper"]:
        sys.exit(run_helper(sys.argv[2:]))
    if sys.argv[1:2] == ["install-fake-flatpak"]:
        if len(sys.argv) < 4:
            sys.exit("usage: wowup-cf.py install-fake-flatpak BIN_DIR STATE_FILE [SCOPE=VERSION ...]")
        installed = dict(item.split("=", 1) for item in sys.argv[4:]) or None
        install_fake_flatpak(sys.argv[2], sys.argv[3], installed)
        return
    unittest.main()


if __name__ == "__main__":
    main()
