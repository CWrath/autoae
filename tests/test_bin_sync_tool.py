import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock, patch

from tkinterdnd2 import COPY

from tools.bin_sync_tool import (
    SELECT_PRESET,
    SELECT_RECENT_BIN,
    SELECT_RECENT_DESTINATION,
    Tool,
    initialize_drag_and_drop,
    sync_bins,
)


class BinSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.sources = self.root / "sources"
        self.destination = self.root / "destination"
        self.sources.mkdir()
        self.destination.mkdir()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_drag_and_drop_initialization_uses_tkinterdnd2_loader(self):
        window = Mock()

        with patch("tools.bin_sync_tool.TkinterDnD._require", return_value="2.9.4") as require:
            result = initialize_drag_and_drop(window)

        require.assert_called_once_with(window)
        self.assertEqual(result, "2.9.4")

    def test_sync_bins_copies_each_bin_to_each_destination(self):
        first = self.sources / "first.avb"
        second = self.sources / "second.avb"
        first.write_bytes(b"first")
        second.write_bytes(b"second")

        results = sync_bins([first, second], [self.destination])

        self.assertEqual(
            [(source.name, destination.name, error) for source, destination, error in results],
            [
                ("first.avb", "destination", None),
                ("second.avb", "destination", None),
            ],
        )
        self.assertEqual((self.destination / first.name).read_bytes(), b"first")
        self.assertEqual((self.destination / second.name).read_bytes(), b"second")

    def test_sync_bins_reports_invalid_source_without_stopping_later_bins(self):
        missing = self.sources / "missing.avb"
        valid = self.sources / "valid.avb"
        valid.write_bytes(b"valid")

        results = sync_bins([missing, valid], [self.destination])

        self.assertEqual(len(results), 2)
        self.assertIsInstance(results[0][2], FileNotFoundError)
        self.assertIsNone(results[1][2])
        self.assertEqual((self.destination / valid.name).read_bytes(), b"valid")

    def test_drop_adds_avb_bins_and_reports_ignored_items(self):
        source = self.sources / "bin with spaces.avb"
        source.write_bytes(b"bin")
        other_file = self.sources / "notes.txt"
        other_file.write_text("not a bin", encoding="utf-8")
        tool = object.__new__(Tool)
        tool.source_list = SimpleNamespace(tk=SimpleNamespace(
            splitlist=Mock(return_value=(str(source), str(other_file), str(self.destination)))
        ))
        tool.add_sources = Mock()

        with patch("tools.bin_sync_tool.messagebox.showwarning") as showwarning:
            result = Tool.handle_source_drop(tool, SimpleNamespace(data="ignored"))

        self.assertEqual(result, COPY)
        tool.add_sources.assert_called_once_with([source], update_recent=True)
        showwarning.assert_called_once()
        self.assertIn("notes.txt", showwarning.call_args.args[1])
        self.assertIn(str(self.destination), showwarning.call_args.args[1])

    def test_recent_and_preset_menus_start_with_blank_prompts(self):
        tool = object.__new__(Tool)
        tool.presets = {"Editorial": []}
        tool.recent_paths = {
            "sources": [str(self.sources / "old.avb")],
            "destinations": [str(self.destination)],
        }

        self.assertEqual(tool.preset_values(), [SELECT_PRESET, "Editorial"])
        self.assertEqual(tool.recent_source_values(), [
            SELECT_RECENT_BIN,
            str(self.sources / "old.avb"),
        ])
        self.assertEqual(tool.recent_destination_values(), [
            SELECT_RECENT_DESTINATION,
            str(self.destination),
        ])

    def test_successful_sync_clears_inputs_and_menu_selections(self):
        source = self.sources / "bin.avb"
        source.write_bytes(b"bin")
        tool = object.__new__(Tool)
        tool.source_paths = [source]
        tool.destination_paths = [self.destination]
        tool.overwrite_var = SimpleNamespace(get=lambda: True)
        tool.source_list = Mock()
        tool.destination_list = Mock()
        tool.preset_menu = Mock()
        tool.recent_source_menu = Mock()
        tool.recent_destination_menu = Mock()

        with patch("tools.bin_sync_tool.messagebox.showinfo") as showinfo:
            Tool.perform_sync(tool)

        self.assertEqual(tool.source_paths, [])
        self.assertEqual(tool.destination_paths, [])
        self.assertEqual((self.destination / source.name).read_bytes(), b"bin")
        tool.preset_menu.set.assert_called_once_with(SELECT_PRESET)
        tool.recent_source_menu.set.assert_called_once_with(SELECT_RECENT_BIN)
        tool.recent_destination_menu.set.assert_called_once_with(SELECT_RECENT_DESTINATION)
        showinfo.assert_called_once()

    def test_failed_sync_keeps_inputs_for_retry(self):
        source = self.sources / "bin.avb"
        source.write_bytes(b"bin")
        (self.destination / "bin.lck").write_text("", encoding="utf-8")
        tool = object.__new__(Tool)
        tool.source_paths = [source]
        tool.destination_paths = [self.destination]
        tool.overwrite_var = SimpleNamespace(get=lambda: True)
        tool.source_list = Mock()
        tool.destination_list = Mock()
        tool.preset_menu = Mock()
        tool.recent_source_menu = Mock()
        tool.recent_destination_menu = Mock()

        with patch("tools.bin_sync_tool.messagebox.askyesno", return_value=False), \
                patch("tools.bin_sync_tool.messagebox.showwarning"):
            Tool.perform_sync(tool)

        self.assertEqual(tool.source_paths, [source])
        self.assertEqual(tool.destination_paths, [self.destination])
        tool.preset_menu.set.assert_not_called()
        tool.recent_source_menu.set.assert_not_called()
        tool.recent_destination_menu.set.assert_not_called()


if __name__ == "__main__":
    unittest.main()
