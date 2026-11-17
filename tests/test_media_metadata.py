import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import build_macos_autoae
import media_metadata
from tools.batch_renamer_tool import build_names
from tools.watch_folder_sorter import build_output_path, new_rule


class MediaMetadataTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root / "clip.mov"
        self.source.write_bytes(b"clip")
        media_metadata._probe_cached.cache_clear()
        self.addCleanup(media_metadata._probe_cached.cache_clear)

    def test_metadata_tokens_match_in_both_tools(self):
        info = {
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 1920,
                 "height": 1080, "avg_frame_rate": "25/1",
                 "tags": {"timecode": "01:02:03;04"}},
                {"codec_type": "audio", "channels": 2},
            ]
        }
        kinds = ["filetype", "resolution", "framerate", "codec", "timecode", "audiochannels"]
        layout = [{"kind": "original"}] + [{"kind": kind} for kind in kinds]
        rule = new_rule()
        rule.update(watch_folder=str(self.root), destination_folder=str(self.root / "out"),
                    layout=layout)
        with mock.patch.object(media_metadata, "_find_ffprobe", return_value="ffprobe"), \
                mock.patch.object(media_metadata.subprocess, "run",
                                  return_value=SimpleNamespace(stdout=json.dumps(info))) as probe:
            ingest = build_output_path(self.source, rule, datetime(2026, 11, 17))
            names = build_names([str(self.source)], [self.source.name], [None],
                                [{"kind": "name"}] + layout[1:], {})
        expected = "clip_Video_1920x1080_25fps_H264_01020304_Stereo.mov"
        self.assertEqual(ingest.name, expected)
        self.assertEqual(names, [expected])
        probe.assert_called_once()

    def test_timecode_strips_dash_and_drop_frame_separators(self):
        info = {"format": {"tags": {"timecode": "01-02-03;04"}}}
        self.assertEqual(media_metadata._timecode(info), "01020304")

    def test_macos_bundle_includes_shared_metadata_module(self):
        self.assertIn("media_metadata.py", build_macos_autoae.collect_app_files())


if __name__ == "__main__":
    unittest.main()
