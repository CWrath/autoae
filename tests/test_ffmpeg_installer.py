import hashlib
import io
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import ffmpeg_installer


def make_archive(names=("ffmpeg", "ffprobe", "COPYING.LGPLv2.1", "THIRD-PARTY-NOTICES.txt")):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name in names:
            archive.writestr(name, b"test binary or license")
    return stream.getvalue()


class FfmpegInstallerTests(unittest.TestCase):
    def _opener(self, archive, digest=None):
        release = {
            "assets": [{
                "name": ffmpeg_installer.ASSET_NAME,
                "size": len(archive),
                "digest": "sha256:" + (digest or hashlib.sha256(archive).hexdigest()),
                "browser_download_url": (
                    "https://github.com/CWrath/autoae/releases/download/v1.0.9/"
                    + ffmpeg_installer.ASSET_NAME
                ),
            }],
        }

        def opener(request, timeout):
            if request.full_url == ffmpeg_installer.app_updater.API_URL:
                return io.BytesIO(json.dumps(release).encode("utf-8"))
            return io.BytesIO(archive)

        return opener

    def test_installs_binaries_and_license_files_into_support_directory(self):
        archive = make_archive()
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch("ffmpeg_installer.sys.platform", "darwin"),
            patch("ffmpeg_installer.subprocess.run"),
        ):
            path = ffmpeg_installer.install_ffmpeg(
                opener=self._opener(archive), destination=temporary
            )

            self.assertEqual(path, Path(temporary) / "ffmpeg" / "ffmpeg")
            self.assertTrue(path.is_file())
            self.assertTrue((path.parent / "ffprobe").is_file())
            self.assertTrue((path.parent / "COPYING.LGPLv2.1").is_file())
            self.assertTrue((path.parent / "THIRD-PARTY-NOTICES.txt").is_file())
            if os.name == "posix":
                self.assertTrue(path.stat().st_mode & 0o111)

    def test_rejects_download_that_does_not_match_github_digest(self):
        archive = make_archive()
        wrong_digest = "0" * 64
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch("ffmpeg_installer.sys.platform", "darwin"),
            patch("ffmpeg_installer.subprocess.run"),
        ):
            with self.assertRaisesRegex(RuntimeError, "SHA-256"):
                ffmpeg_installer.install_ffmpeg(
                    opener=self._opener(archive, wrong_digest),
                    destination=temporary,
                )
            self.assertFalse((Path(temporary) / "ffmpeg").exists())

    def test_rejects_archive_paths_outside_expected_file_names(self):
        archive = make_archive(("../ffmpeg", "ffprobe", "COPYING.LGPLv2.1",
                                "THIRD-PARTY-NOTICES.txt"))
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch("ffmpeg_installer.sys.platform", "darwin"),
            patch("ffmpeg_installer.subprocess.run"),
        ):
            with self.assertRaisesRegex(RuntimeError, "unexpected file path"):
                ffmpeg_installer.install_ffmpeg(
                    opener=self._opener(archive), destination=temporary
                )
            self.assertFalse((Path(temporary) / "ffmpeg").exists())

    def test_fails_if_latest_release_has_no_ffmpeg_asset(self):
        response = io.BytesIO(b'{"assets": []}')
        with self.assertRaisesRegex(RuntimeError, "does not include"):
            ffmpeg_installer._latest_asset(opener=lambda request, timeout: response)


if __name__ == "__main__":
    unittest.main()
