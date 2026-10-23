import json
import os
import plistlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from macos.merge_universal_app import merge_apps


class MacOSUniversalMergeTests(unittest.TestCase):
    def make_app(self, path, architecture, linked_framework=False):
        contents = path / "Contents"
        frameworks = contents / "Frameworks"
        (contents / "MacOS").mkdir(parents=True)
        frameworks.mkdir(parents=True)
        resources = contents / "Resources" / "bundle"
        resources.mkdir(parents=True)
        (resources / "app-manifest.json").write_text(json.dumps({
            "version": "1.0.5",
            "files": {"main.py": "same"},
        }))
        with (contents / "Info.plist").open("wb") as manifest:
            plistlib.dump({"CFBundleShortVersionString": "1.0.5"}, manifest)
        executable = contents / "MacOS" / "Auto AE"
        executable.write_bytes(b"\xfe\xed\xfa\xcf executable")
        framework = frameworks / "libcrypto.3.dylib"
        if linked_framework:
            target = frameworks / "Versions" / "A" / "libcrypto.3.dylib"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"\xfe\xed\xfa\xcf " + architecture.encode())
            framework.symlink_to(os.path.relpath(target, frameworks))
        else:
            framework.write_bytes(b"\xfe\xed\xfa\xcf " + architecture.encode())
        for platform, binary_architecture in (
            ("osx-arm64", "arm64"),
            ("osx-x86_64", "x86_64"),
            ("osx-arm64-tcl9", "arm64"),
            ("osx-x86_64-tcl9", "x86_64"),
            ("osx-x64", "x86_64"),
        ):
            helper = frameworks / "tkinterdnd2" / "tkdnd" / platform / "libtkdnd.dylib"
            helper.parent.mkdir(parents=True, exist_ok=True)
            helper.write_bytes(b"\xfe\xed\xfa\xcf " + binary_architecture.encode())

    def test_merges_framework_when_file_and_symlink_layouts_differ(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            x86_app = root / "x86" / "Auto AE.app"
            arm_app = root / "arm" / "Auto AE.app"
            output_app = root / "universal" / "Auto AE.app"
            self.make_app(x86_app, "x86_64")
            self.make_app(arm_app, "arm64", linked_framework=True)

            def run_lipo(arguments, **kwargs):
                if "-create" in arguments:
                    self.assertNotIn("tkinterdnd2", arguments[1])
                    self.assertNotIn("tkinterdnd2", arguments[2])
                    Path(arguments[-1]).write_bytes(b"\xfe\xed\xfa\xcf universal")
                    return subprocess.CompletedProcess(arguments, 0)
                binary = Path(arguments[-1])
                if "osx-arm64" in str(binary):
                    architectures = "arm64"
                elif "osx-x86_64" in str(binary) or "osx-x64" in str(binary):
                    architectures = "x86_64"
                elif str(binary).startswith(str(output_app)):
                    architectures = "x86_64 arm64"
                elif str(binary).startswith(str(x86_app)):
                    architectures = "x86_64"
                else:
                    architectures = "arm64"
                return subprocess.CompletedProcess(arguments, 0, stdout=architectures)

            with patch("macos.merge_universal_app.sys.platform", "darwin"), \
                    patch("macos.merge_universal_app.shutil.which", return_value="lipo"), \
                    patch("macos.merge_universal_app.subprocess.run", side_effect=run_lipo):
                merge_apps(x86_app, arm_app, output_app, expected_version="1.0.5")

            merged_framework = output_app / "Contents" / "Frameworks" / "libcrypto.3.dylib"
            self.assertFalse(merged_framework.is_symlink())
            self.assertIn(b"universal", merged_framework.read_bytes())
            for platform, binary_architecture in (
                ("osx-arm64", "arm64"),
                ("osx-x86_64", "x86_64"),
                ("osx-arm64-tcl9", "arm64"),
                ("osx-x86_64-tcl9", "x86_64"),
                ("osx-x64", "x86_64"),
            ):
                helper = (
                    output_app / "Contents" / "Frameworks" / "tkinterdnd2"
                    / "tkdnd" / platform / "libtkdnd.dylib"
                )
                expected = b"\xfe\xed\xfa\xcf " + binary_architecture.encode()
                self.assertEqual(helper.read_bytes(), expected)


if __name__ == "__main__":
    unittest.main()
