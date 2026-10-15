import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from macos import package_universal_ffmpeg


class PackageUniversalFfmpegTests(unittest.TestCase):
    def test_packages_two_signed_universal_binaries_and_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            intel = root / "intel"
            arm = root / "arm"
            for directory in (intel, arm):
                (directory / "bin").mkdir(parents=True)
                (directory / "bin" / "ffmpeg").write_bytes(b"native ffmpeg")
                (directory / "bin" / "ffprobe").write_bytes(b"native ffprobe")
                (directory / "ffmpeg-9.0.2.tar.xz").write_bytes(b"same source")
                (directory / "COPYING.LGPLv2.1").write_text("license", encoding="utf-8")
            notices = root / "notices.txt"
            notices.write_text("notices", encoding="utf-8")

            def run(command, **kwargs):
                if command[1:2] == ["-archs"]:
                    binary = command[2]
                    if "ffmpeg-universal" in binary:
                        architectures = "x86_64 arm64"
                    elif "\\intel\\" in binary or "/intel/" in binary:
                        architectures = "x86_64"
                    else:
                        architectures = "arm64"
                    return SimpleNamespace(stdout=architectures)
                if command[1:2] == ["-create"]:
                    output = Path(command[command.index("-output") + 1])
                    output.write_bytes(b"universal binary")
                return SimpleNamespace(stdout="")

            output_zip = root / "AutoAE-ffmpeg-macos.zip"
            source_output = root / "AutoAE-ffmpeg-source-9.0.2.tar.xz"
            with (
                patch("macos.package_universal_ffmpeg.shutil.which", return_value="lipo"),
                patch("macos.package_universal_ffmpeg.subprocess.run", side_effect=run),
                patch.dict("os.environ", {"KEYCHAIN_PATH": "test-keychain"}),
            ):
                package_universal_ffmpeg.package(
                    intel, arm, output_zip, source_output, notices,
                    arm / "COPYING.LGPLv2.1", "Developer ID",
                )

            self.assertEqual(source_output.read_bytes(), b"same source")
            with zipfile.ZipFile(output_zip) as archive:
                self.assertEqual(set(archive.namelist()), {
                    "ffmpeg", "ffprobe", "THIRD-PARTY-NOTICES.txt", "COPYING.LGPLv2.1",
                })
                self.assertEqual(archive.read("ffmpeg"), b"universal binary")
                self.assertEqual(archive.read("ffprobe"), b"universal binary")

    def test_rejects_different_ffmpeg_sources_between_architectures(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            intel = root / "intel"
            arm = root / "arm"
            intel.mkdir()
            arm.mkdir()
            (intel / "ffmpeg-9.0.2.tar.xz").write_bytes(b"source A")
            (arm / "ffmpeg-9.0.2.tar.xz").write_bytes(b"source B")

            with patch("macos.package_universal_ffmpeg.shutil.which", return_value="lipo"):
                with self.assertRaisesRegex(ValueError, "different source archives"):
                    package_universal_ffmpeg.package(
                        intel, arm, root / "out.zip", root / "source.tar.xz",
                        root / "notices.txt", root / "license.txt", "Developer ID",
                    )


if __name__ == "__main__":
    unittest.main()
