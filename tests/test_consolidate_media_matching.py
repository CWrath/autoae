import tempfile
import unittest
from pathlib import Path

from tools.consolidate_media_tool import Tool, filename_match_key, sanitize_folder_name


class ConsolidateMediaMatchingTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.source_folder = Path(self.temp_dir.name) / "source"
        self.source_folder.mkdir()
        self.source_file = self.source_folder / "Scene_V1-0003.mov"
        self.source_file.write_bytes(b"media")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_main_tool_requires_suffix_matching_to_be_enabled(self):
        tool = object.__new__(Tool)

        self.assertIsNone(
            tool.find_matching_file(str(self.source_folder), "Scene_V1-0000.mov")
        )
        self.assertEqual(
            tool.find_matching_file(
                str(self.source_folder), "Scene_V1-0000.mov",
                ignore_resolve_suffix=True,
            ),
            str(self.source_file),
        )

        exact_index = tool.index_source_files(str(self.source_folder))
        self.assertNotIn("scene_v1-0000.mov", exact_index)

        suffix_index = tool.index_source_files(
            str(self.source_folder), ignore_resolve_suffix=True
        )
        query_key = filename_match_key("Scene_V1-0000.mov", True)
        self.assertEqual(suffix_index[query_key], str(self.source_file))

    def test_suffix_normalization_preserves_extensionless_and_unrelated_names(self):
        self.assertEqual(filename_match_key("Scene_V1-0003", True), "scene")
        self.assertEqual(
            filename_match_key("Scene_V2-0003.mov", True),
            "scene_v2-0003.mov",
        )

    def test_sanitize_folder_name_handles_invalid_and_reserved_names(self):
        self.assertEqual(sanitize_folder_name("Episode: 01/FINAL*"), "Episode_ 01_FINAL_")
        self.assertEqual(sanitize_folder_name("CON"), "_CON")
        self.assertEqual(sanitize_folder_name("..."), "Consolidated Media")
        self.assertEqual(sanitize_folder_name("Show. "), "Show")


if __name__ == "__main__":
    unittest.main()
