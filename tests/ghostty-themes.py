#!/usr/bin/env python3
"""Offline checks for the optional official Ghostty theme collection."""

import contextlib
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "files/system/usr/libexec/monolith/ghostty-themes"
ARCHIVE = Path("/tmp/ghostty-official-themes-20260216.tgz")


def archive_bytes(entries):
    result = io.BytesIO()
    with tarfile.open(fileobj=result, mode="w:gz") as archive:
        for name, content in entries.items():
            member = tarfile.TarInfo(name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
    return result.getvalue()


def digest(data):
    return hashlib.sha256(data).hexdigest()


class ThemeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="monolith themes ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = {
            "XDG_CONFIG_HOME": str(self.root / "config with spaces"),
            "XDG_DATA_HOME": str(self.root / "data with spaces"),
            "XDG_STATE_HOME": str(self.root / "state with spaces"),
        }
        patch = mock.patch.dict(os.environ, self.env)
        patch.start()
        self.addCleanup(patch.stop)
        loader = importlib.machinery.SourceFileLoader("ghostty_themes_test", str(HELPER))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        self.helper = importlib.util.module_from_spec(spec)
        loader.exec_module(self.helper)
        self.themes = Path(self.env["XDG_CONFIG_HOME"]) / "ghostty/themes"
        self.manifest = Path(self.env["XDG_DATA_HOME"]) / "monolith/software/ghostty-themes/manifest.json"
        self.marker = Path(self.env["XDG_STATE_HOME"]) / "monolith/software/ghostty-themes.managed"
        if ARCHIVE.is_file():
            self.download = ARCHIVE.read_bytes()
            self.opener = lambda *args, **kwargs: ARCHIVE.open("rb")
        else:
            # Keep CI offline without redistributing the upstream theme data.
            self.download = archive_bytes({
                f"ghostty/Test Theme {index}": f"background = {index:06x}\n".encode()
                for index in range(8)
            })
            self.helper.SHA256 = digest(self.download)
            self.helper.EXPECTED_COUNT = 8
            self.opener = lambda *args, **kwargs: io.BytesIO(self.download)
        with tarfile.open(fileobj=io.BytesIO(self.download)) as archive:
            self.entries = {
                Path(member.name).name: archive.extractfile(member).read()
                for member in archive if member.isfile()
            }
        self.names = sorted(self.entries)

    def call(self, action):
        with mock.patch("urllib.request.urlopen", side_effect=self.opener), \
                contextlib.redirect_stdout(io.StringIO()):
            return getattr(self.helper, action)()

    def manifest_data(self):
        return json.loads(self.manifest.read_text())

    def snapshot(self):
        return {
            str(path.relative_to(self.root)): (
                ("symlink", os.readlink(path)) if path.is_symlink() else
                ("file", path.read_bytes()) if path.is_file() else ("dir",)
            )
            for path in self.root.rglob("*")
        }

    def reject_download(self, data, *, checksum=None, count=None):
        before = self.snapshot()
        self.opener = lambda *args, **kwargs: io.BytesIO(data)
        with mock.patch.object(self.helper, "SHA256", checksum or digest(data)), \
                mock.patch.object(self.helper, "EXPECTED_COUNT", count or self.helper.EXPECTED_COUNT):
            with self.assertRaises((Exception, SystemExit)):
                self.call("install")
        self.assertEqual(before, self.snapshot())

    def test_download_identifies_itself_to_public_cdn(self):
        with mock.patch("urllib.request.urlopen", side_effect=self.opener) as opener:
            self.assertEqual(self.helper.download_themes(), self.entries)
        request = opener.call_args.args[0]
        self.assertEqual(request.full_url, self.helper.URL)
        self.assertEqual(request.get_header("User-agent"), "monolith-ghostty-themes/1")
        self.assertEqual(opener.call_args.kwargs["timeout"], 30)

    def test_install_manifest_and_config_are_preserved(self):
        self.assertEqual(self.call("status"), "not installed")
        self.themes.parent.mkdir(parents=True)
        configs = {
            self.themes.parent / "config": b"theme = GruvboxDark\nfont-size = 14\n",
            self.themes.parent / "config.ghostty": b"theme = GruvboxDark\n",
        }
        for path, data in configs.items():
            path.write_bytes(data)
        self.call("install")
        manifest = self.manifest_data()
        self.assertEqual(manifest["version"], 1)
        self.assertEqual(manifest["source"], self.helper.URL)
        self.assertEqual(manifest["sha256"], self.helper.SHA256)
        self.assertEqual(manifest["themes_dir"], str(self.themes.resolve()))
        self.assertEqual(manifest["files"], {name: digest(data) for name, data in self.entries.items()})
        self.assertTrue(self.marker.is_file())
        self.assertEqual(self.call("status"), "installed")
        for name, data in self.entries.items():
            self.assertEqual((self.themes / name).read_bytes(), data)
        before = self.snapshot()
        self.call("install")
        self.assertEqual(before, self.snapshot())
        self.call("remove")
        for path, data in configs.items():
            self.assertEqual(path.read_bytes(), data)
        self.assertFalse(self.manifest.exists())
        self.assertFalse(self.marker.exists())
        self.assertEqual(self.call("status"), "not installed")

    def test_conflicts_edits_symlinks_and_missing_files(self):
        regular, link, directory, broken, edited, replaced, missing = self.names[:7]
        self.themes.mkdir(parents=True)
        (self.themes / regular).write_bytes(b"user theme\n")
        target = self.root / "personal theme"
        target.write_bytes(b"personal symlink target\n")
        (self.themes / link).symlink_to(target)
        (self.themes / directory).mkdir()
        (self.themes / broken).symlink_to(self.root / "missing target")
        self.call("install")
        for name in (regular, link, directory, broken):
            self.assertNotIn(name, self.manifest_data()["files"])
        (self.themes / edited).write_bytes(b"user-edited theme\n")
        (self.themes / replaced).unlink()
        (self.themes / replaced).symlink_to(target)
        (self.themes / missing).unlink()
        self.call("install")
        self.assertEqual((self.themes / missing).read_bytes(), self.entries[missing])
        self.assertNotIn(edited, self.manifest_data()["files"])
        self.assertNotIn(replaced, self.manifest_data()["files"])
        self.call("remove")
        self.assertEqual((self.themes / regular).read_bytes(), b"user theme\n")
        self.assertEqual((self.themes / edited).read_bytes(), b"user-edited theme\n")
        self.assertEqual(target.read_bytes(), b"personal symlink target\n")
        self.assertTrue((self.themes / directory).is_dir())
        self.assertTrue((self.themes / link).is_symlink())
        self.assertTrue((self.themes / broken).is_symlink())
        self.assertTrue((self.themes / replaced).is_symlink())
        self.assertFalse((self.themes / missing).exists())

    def test_remove_preserves_managed_file_edited_without_update(self):
        self.call("install")
        modified = self.themes / self.names[0]
        modified.write_bytes(b"local edits\n")
        self.call("remove")
        self.assertEqual(modified.read_bytes(), b"local edits\n")
        self.assertEqual(list(self.themes.iterdir()), [modified])

    def test_new_bundle_updates_and_retires_only_unedited_themes(self):
        self.call("install")
        updated, edited, retired, retired_edited = self.names[:4]
        for name in (edited, retired_edited):
            (self.themes / name).write_bytes(b"user edits\n")
        data = archive_bytes({
            f"ghostty/{name}": b"background = 123456\n" for name in (updated, edited)
        })
        self.opener = lambda *args, **kwargs: io.BytesIO(data)
        with mock.patch.object(self.helper, "SHA256", digest(data)), \
                mock.patch.object(self.helper, "EXPECTED_COUNT", 2):
            self.call("install")
        self.assertEqual((self.themes / updated).read_bytes(), b"background = 123456\n")
        self.assertFalse((self.themes / retired).exists())
        for name in (edited, retired_edited):
            self.assertEqual((self.themes / name).read_bytes(), b"user edits\n")
        self.assertEqual(set(self.manifest_data()["files"]), {updated})

    def test_bad_checksum_preserves_previous_install(self):
        self.call("install")
        self.reject_download(self.download, checksum="0" * 64)

    def test_status_reports_missing_files_and_corrupt_manifest(self):
        self.call("install")
        (self.themes / self.names[0]).unlink()
        self.assertEqual(self.call("status"), "needs repair")
        self.call("install")
        self.assertEqual(self.call("status"), "installed")
        self.manifest.write_text("{invalid json")
        self.assertEqual(self.call("status"), "needs repair")

    def test_bad_count_and_archive_paths_write_nothing(self):
        cases = [
            ({"ghostty/Only One": b"background = 000000\n"}, 2),
            ({"ghostty/../../escaped": b"bad\n"}, 1),
            ({"/absolute-theme": b"bad\n"}, 1),
            ({"ghostty/nested/theme": b"bad\n"}, 1),
        ]
        for entries, count in cases:
            with self.subTest(paths=list(entries), count=count):
                self.reject_download(archive_bytes(entries), count=count)

    def test_symlink_archive_member_writes_nothing(self):
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode="w:gz") as archive:
            member = tarfile.TarInfo("ghostty/Symlink")
            member.type = tarfile.SYMTYPE
            member.linkname = "../../outside"
            archive.addfile(member)
        self.reject_download(data.getvalue(), count=1)

    def test_config_alias_resolves_to_same_install(self):
        self.call("install")
        alias = self.root / "config alias"
        alias.symlink_to(self.env["XDG_CONFIG_HOME"], target_is_directory=True)
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(alias)}):
            self.assertEqual(self.call("status"), "installed")
            self.call("remove")
        self.assertFalse(self.manifest.exists())
        self.assertFalse(self.marker.exists())
        self.assertFalse(any(self.themes.glob("*")))

    def test_monolith_lists_and_dispatches_theme_actions(self):
        stub = self.root / "usr/libexec/monolith/ghostty-themes"
        stub.parent.mkdir(parents=True)
        calls = self.root / "helper calls"
        stub.write_text(
            "#!/usr/bin/env python3\n"
            "import os, pathlib, sys\n"
            "action = sys.argv[1]\n"
            "with open(os.environ['THEME_TEST_CALLS'], 'a') as log: log.write(action + '\\n')\n"
            "if action == 'status': print(os.environ['THEME_TEST_STATUS'])\n"
        )
        stub.chmod(0o755)
        launcher = self.root / "usr/bin/monolith"
        launcher.parent.mkdir(parents=True)
        launcher.write_text((ROOT / "files/system/usr/bin/monolith").read_text())
        env = dict(os.environ, THEME_TEST_CALLS=str(calls), THEME_TEST_STATUS="not installed")

        def cli(*args):
            result = subprocess.run(["bash", str(launcher), *args], env=env,
                                    capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return result.stdout

        for status in ("not installed", "installed", "needs repair"):
            env["THEME_TEST_STATUS"] = status
            if status != "not installed":
                self.marker.parent.mkdir(parents=True, exist_ok=True)
                self.marker.write_text("version=test\n")
            row = next(line for line in cli("list").splitlines() if "Ghostty" in line)
            self.assertIn("Appearance", row)
            self.assertIn(status, row)
        cli("install", "ghostty-themes")
        self.marker.parent.mkdir(parents=True, exist_ok=True)
        self.marker.write_text("version=test\n")
        cli("update", "ghostty-themes")
        cli("remove", "ghostty-themes")
        self.assertEqual([line for line in calls.read_text().splitlines() if line != "status"],
                         ["install", "install", "remove"])


if __name__ == "__main__":
    unittest.main()
