import hashlib
import json
import plistlib
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path

from app_updater import stage_macos_update
from macos.merge_universal_app import zip_app


class MacOSBundledUpdateTests(unittest.TestCase):
    def make_archive(self, path, extra_entries=()):
        source = b"print('updated')\n"
        manifest = {
            "version": "9.8.7",
            "files": {"main.py": hashlib.sha256(source).hexdigest()},
        }
        info = {
            "CFBundleIdentifier": "com.autoae.app",
            "CFBundleExecutable": "Auto AE",
            "CFBundlePackageType": "APPL",
            "CFBundleShortVersionString": "9.8.7",
        }
        app = path.parent / "Auto AE.app"
        contents = app / "Contents"
        resources = contents / "Resources"
        (contents / "MacOS").mkdir(parents=True)
        (resources / "app").mkdir(parents=True)
        (contents / "Info.plist").write_bytes(plistlib.dumps(info))
        executable = contents / "MacOS" / "Auto AE"
        executable.write_bytes(b"binary")
        executable.chmod(0o755)
        (resources / "app" / "main.py").write_bytes(source)
        (resources / "app-manifest.json").write_text(json.dumps(manifest))
        readme = path.parent / "Install Auto AE README.txt"
        readme.write_text("Install instructions")
        zip_app(app, path, readme)
        if path.name:
            temporary = path.with_suffix(".permissions.zip")
            with zipfile.ZipFile(path) as source, zipfile.ZipFile(temporary, "w") as target:
                for entry in source.infolist():
                    copied = zipfile.ZipInfo(entry.filename, entry.date_time)
                    copied.create_system = 3
                    copied.external_attr = entry.external_attr
                    if entry.filename == "Auto AE.app/Contents/MacOS/Auto AE":
                        copied.external_attr = (stat.S_IFREG | 0o755) << 16
                    target.writestr(copied, source.read(entry))
            temporary.replace(path)
        if extra_entries:
            with zipfile.ZipFile(path, "a") as archive:
                for entry in extra_entries:
                    archive.writestr(*entry)

    def test_stages_complete_bundle_and_verifies_version_and_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "release.zip"
            self.make_archive(archive)

            bundle = Path(stage_macos_update(archive, directory, expected_version="9.8.7"))

            self.assertEqual(bundle.name, "Auto AE.app")
            self.assertEqual(
                (bundle / "Contents" / "Resources" / "app" / "main.py").read_text(),
                "print('updated')\n",
            )
            self.assertTrue((bundle / "Contents" / "MacOS" / "Auto AE").is_file())

    def test_rejects_version_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "release.zip"
            self.make_archive(archive)

            with self.assertRaisesRegex(RuntimeError, "version"):
                stage_macos_update(archive, directory, expected_version="9.8.8")

    def test_rejects_archive_path_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "release.zip"
            self.make_archive(archive, [("../escape", "bad")])

            with self.assertRaisesRegex(RuntimeError, "unsafe file path"):
                stage_macos_update(archive, directory, expected_version="9.8.7")
            self.assertFalse((Path(directory).parent / "escape").exists())

    def test_rejects_symlink_outside_app_bundle(self):
        link = zipfile.ZipInfo("Auto AE.app/Contents/Frameworks/Python")
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "release.zip"
            self.make_archive(archive, [(link, "../../../outside")])

            with self.assertRaisesRegex(RuntimeError, "symbolic link"):
                stage_macos_update(archive, directory, expected_version="9.8.7")

    def test_rejects_symlink_parent_traversal_within_app_root(self):
        link = zipfile.ZipInfo("Auto AE.app/Contents/Frameworks/Python")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "release.zip"
            self.make_archive(archive, [(link, "../../../Info.plist")])

            with self.assertRaisesRegex(RuntimeError, "symbolic link"):
                stage_macos_update(archive, directory, expected_version="9.8.7")


if __name__ == "__main__":
    unittest.main()
