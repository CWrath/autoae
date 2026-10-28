import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from tools.watch_folder_sorter import (
    Tool,
    build_output_path,
    clean_original_name,
    load_rules,
    load_saved_tokens,
    move_incoming_file,
    new_rule,
    save_rules,
    validate_rules,
)


class WatchFolderSorterTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.watch = self.root / "Footage Graphics"
        self.destination = self.root / "Server" / "Editorial"
        (self.watch / "Camera A").mkdir(parents=True)
        self.destination.mkdir(parents=True)
        self.rule = new_rule()
        self.rule.update({
            "name": "Footage Graphics",
            "watch_folder": str(self.watch),
            "destination_folder": str(self.destination),
            "layout": [
                {"kind": "text", "text": "FTG"},
                {"kind": "folder"},
                {"kind": "date"},
                {"kind": "original"},
            ],
            "join": "_",
            "date_format": "YYMMDD",
        })

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_build_output_path_adds_date_and_preserves_subfolders(self):
        source = self.watch / "Camera A" / "clip 01.mov"

        target = build_output_path(source, self.rule, datetime(2026, 10, 22))

        self.assertEqual(
            target,
            self.destination / "261022" / "Camera A" / "FTG_Footage Graphics_261022_clip 01.mov",
        )

    def test_move_adds_number_on_collision_and_preserves_extension(self):
        source = self.watch / "Camera A" / "clip.mov"
        source.write_bytes(b"source")
        expected = build_output_path(source, self.rule, datetime(2026, 10, 22))
        expected.parent.mkdir(parents=True)
        expected.write_bytes(b"existing")

        moved = move_incoming_file(source, self.rule, datetime(2026, 10, 22))

        self.assertEqual(moved, expected.with_name(expected.stem + "_1" + expected.suffix))
        self.assertEqual(moved.read_bytes(), b"source")
        self.assertEqual(expected.read_bytes(), b"existing")
        self.assertFalse(source.exists())

    def test_invalid_token_characters_are_reported_before_move(self):
        source = self.watch / "Camera A" / "clip.mov"
        source.write_bytes(b"source")
        self.rule["layout"] = [{"kind": "text", "text": "bad/name"}]

        with self.assertRaisesRegex(ValueError, "Invalid generated file name"):
            move_incoming_file(source, self.rule, datetime(2026, 10, 22))

        self.assertTrue(source.exists())

    def test_original_name_cleanup_removes_path_breaking_characters(self):
        self.assertEqual(
            clean_original_name('Clip: "A"/B?*\\C. ', remove_path_breaking_chars=True),
            "Clip ABC",
        )

    def test_path_breaking_characters_are_removed_from_full_generated_name(self):
        source = self.watch / "Camera A" / "clip.mov"
        self.rule["layout"] = [
            {"kind": "text", "text": 'G:FX"'},
            {"kind": "original"},
        ]
        self.rule["remove_path_breaking_chars"] = True

        target = build_output_path(source, self.rule, datetime(2026, 10, 22))

        self.assertEqual(target.name, "GFX_clip.mov")

    def test_original_name_uppercase_and_cleanup_are_independent_options(self):
        source = self.watch / "Camera A" / "clip final.mov"
        rule = dict(self.rule, layout=[
            {"kind": "text", "text": "GFX"},
            {"kind": "original"},
        ])
        rule["uppercase_original"] = True
        expected_without_cleanup = (
            self.destination / "261022" / "Camera A" / "GFX_CLIP FINAL.mov"
        )
        self.assertEqual(
            build_output_path(source, rule, datetime(2026, 10, 22)),
            expected_without_cleanup,
        )

        rule["remove_path_breaking_chars"] = True
        source = self.watch / "Camera A" / "clip: final.mov"
        self.assertEqual(
            build_output_path(source, rule, datetime(2026, 10, 22)).name,
            "GFX_CLIP FINAL.mov",
        )

    def test_existing_tokens_are_skipped_case_insensitively(self):
        source = self.watch / "Camera A" / "ftg_261022_clip.mov"
        self.rule["layout"] = [
            {"kind": "text", "text": "FTG"},
            {"kind": "date"},
            {"kind": "original"},
        ]

        target = build_output_path(source, self.rule, datetime(2026, 10, 22))

        self.assertEqual(target.name, "ftg_261022_clip.mov")

    def test_counter_ignores_files_already_in_the_date_folder(self):
        source = self.watch / "Camera A" / "clip.mov"
        self.rule.update({
            "layout": [{"kind": "counter"}, {"kind": "original"}],
            "counter_start": "5",
            "counter_step": "2",
            "counter_digits": "3",
        })
        date_folder = self.destination / "261022"
        (date_folder / "Camera B").mkdir(parents=True)
        (date_folder / "previous-a.mov").write_bytes(b"existing")
        (date_folder / "Camera B" / "previous-b.mov").write_bytes(b"existing")

        target = build_output_path(source, self.rule, datetime(2026, 10, 22))

        self.assertEqual(target.name, "005_clip.mov")

    def test_counter_is_not_skipped_when_already_in_original_name(self):
        source = self.watch / "Camera A" / "clip_0001.mov"
        self.rule["layout"] = [{"kind": "original"}, {"kind": "counter"}]

        target = build_output_path(source, self.rule, datetime(2026, 10, 22))

        self.assertEqual(target.name, "clip_0001_0001.mov")

    def test_engine_counter_restarts_from_the_start_value_each_run(self):
        self.rule.update({
            "layout": [{"kind": "counter"}, {"kind": "original"}],
            "counter_start": "4",
            "counter_step": "2",
            "counter_digits": "3",
        })
        first_tool = Tool()
        first_tool.wait_until_stable = lambda *_args, **_kwargs: True

        for filename, expected in (("first.mov", "004_first.mov"), ("second.mov", "006_second.mov")):
            source = self.watch / "Camera A" / filename
            source.write_bytes(b"media")
            self.assertTrue(first_tool.process_stable_file(source, self.rule))
            self.assertTrue((self.destination / datetime.now().strftime("%y%m%d") / "Camera A" / expected).is_file())

        restarted_tool = Tool()
        restarted_tool.wait_until_stable = lambda *_args, **_kwargs: True
        source = self.watch / "Camera A" / "third.mov"
        source.write_bytes(b"media")

        self.assertTrue(restarted_tool.process_stable_file(source, self.rule))
        self.assertTrue(
            (self.destination / datetime.now().strftime("%y%m%d") / "Camera A" / "004_third.mov").is_file()
        )

    def test_image_is_converted_and_original_kept(self):
        from PIL import Image
        source = self.watch / "Camera A" / "still.png"
        Image.new("RGBA", (4, 4), (255, 0, 0, 128)).save(source)
        self.rule.update({"layout": [{"kind": "original"}], "convert_image": "jpg"})

        destination = move_incoming_file(source, self.rule, datetime(2026, 10, 22))

        self.assertEqual(destination.name, "still.jpg")
        with Image.open(destination) as converted:
            self.assertEqual(converted.format, "JPEG")
        self.assertFalse(source.exists())
        self.assertTrue((destination.parent / "Originals" / "still.png").is_file())

    def test_audio_conversion_can_discard_original_and_failure_keeps_it(self):
        source = self.watch / "Camera A" / "voice.m4a"
        source.write_bytes(b"audio")
        self.rule.update({"layout": [{"kind": "original"}], "convert_audio": "wav", "convert_keep_originals": False})

        def fake_convert(src, dst):
            Path(dst).write_bytes(b"wav")

        with mock.patch("tools.watch_folder_sorter.convert_file", fake_convert):
            destination = move_incoming_file(source, self.rule, datetime(2026, 10, 22))
        self.assertEqual(destination.name, "voice.wav")
        self.assertFalse(source.exists())
        self.assertFalse((destination.parent / "Originals").exists())

        failing = self.watch / "Camera A" / "bad.m4a"
        failing.write_bytes(b"audio")
        with mock.patch("tools.watch_folder_sorter.convert_file", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                move_incoming_file(failing, self.rule, datetime(2026, 10, 22))
        self.assertTrue(failing.exists())

    def test_files_already_in_target_format_or_other_types_are_just_moved(self):
        self.rule.update({"layout": [{"kind": "original"}], "convert_audio": "wav"})
        for name in ("a.wav", "b.pdf"):
            source = self.watch / "Camera A" / name
            source.write_bytes(b"x")
            self.assertEqual(move_incoming_file(source, self.rule, datetime(2026, 10, 22)).name, name)

    def test_run_now_opens_the_folder_of_the_last_transferred_file(self):
        source = self.watch / "Camera A" / "clip.mov"
        source.write_bytes(b"media")
        tool = Tool()
        tool.wait_until_stable = lambda *_args, **_kwargs: True

        with mock.patch("tools.watch_folder_sorter.open_destination_folder") as open_folder:
            tool._run_now_worker([self.rule])

        open_folder.assert_called_once_with(
            self.destination / datetime.now().strftime("%y%m%d") / "Camera A"
        )
        self.assertFalse(tool._batch_running)

    def test_custom_token_requires_exact_substring_match(self):
        source = self.watch / "Camera A" / "clip_GFX_final.mov"
        self.rule["layout"] = [
            {"kind": "text", "text": "GFX"},
            {"kind": "text", "text": "GFX-final"},
            {"kind": "original"},
        ]

        target = build_output_path(source, self.rule, datetime(2026, 10, 22))

        self.assertEqual(target.name, "GFX-final_clip_GFX_final.mov")

    def test_existing_token_check_can_be_disabled_per_rule(self):
        source = self.watch / "Camera A" / "FTG_clip.mov"
        self.rule["layout"] = [
            {"kind": "text", "text": "FTG"},
            {"kind": "original"},
        ]
        self.rule["skip_existing_tokens"] = False

        target = build_output_path(source, self.rule, datetime(2026, 10, 22))

        self.assertEqual(target.name, "FTG_FTG_clip.mov")

    def test_validate_rules_rejects_overlapping_watches_and_destinations(self):
        nested_watch = self.watch / "nested"
        nested_watch.mkdir()
        other = new_rule()
        other.update({
            "name": "Nested",
            "watch_folder": str(nested_watch),
            "destination_folder": str(self.destination),
        })

        errors = validate_rules([self.rule, other])

        self.assertTrue(any("overlap" in error for error in errors))

    def test_rules_round_trip_to_json(self):
        settings = self.root / "settings.json"
        self.rule["remove_path_breaking_chars"] = True
        self.rule["uppercase_original"] = True

        save_rules([self.rule], settings, saved_tokens=["_GFX_", "_FINAL"])

        self.assertEqual(load_rules(settings), [self.rule])
        self.assertEqual(load_saved_tokens(settings), ["_GFX_", "_FINAL"])

    def test_older_saved_rules_default_new_name_options_to_false(self):
        settings = self.root / "settings.json"
        legacy_rule = {
            key: value
            for key, value in self.rule.items()
            if key not in ("remove_path_breaking_chars", "uppercase_original")
        }
        save_rules([legacy_rule], settings)

        loaded = load_rules(settings)[0]

        self.assertFalse(loaded["remove_path_breaking_chars"])
        self.assertFalse(loaded["uppercase_original"])
        self.assertEqual(loaded["counter_start"], "1")
        self.assertEqual(loaded["counter_step"], "1")
        self.assertEqual(loaded["counter_digits"], "4")

    def test_validate_rules_rejects_invalid_counter_settings(self):
        self.rule["layout"] = [{"kind": "counter"}]
        self.rule["counter_start"] = "first"

        errors = validate_rules([self.rule])

        self.assertTrue(any("counter start value must be an integer" in error for error in errors))

    def test_saved_custom_tokens_are_kept_when_rules_are_saved_again(self):
        settings = self.root / "settings.json"
        save_rules([], settings, saved_tokens=["_GFX_"])

        save_rules([self.rule], settings)

        self.assertEqual(load_saved_tokens(settings), ["_GFX_"])

    def test_builder_drag_reorders_tokens(self):
        first = {"kind": "text", "text": "FTG"}
        second = {"kind": "original"}
        third = {"kind": "date"}
        tool = object.__new__(Tool)
        tool._layout = [first, second, third]
        tool.drag = {"token": first, "palette": False, "index": 0, "moving": True, "ghost": None}
        tool.marker = type("Marker", (), {"place_forget": lambda _self: None})()
        tool.strip = type("Strip", (), {
            "contains": lambda _self, _x, _y: True,
            "index_at": lambda _self, _x, _y: 2,
        })()
        tool.render_strip = lambda: None
        tool.mark_dirty = lambda: None

        tool.drag_end(type("Event", (), {"x_root": 10, "y_root": 10})())

        self.assertEqual(tool._layout, [second, first, third])

    def test_invalid_saved_token_layout_is_rejected(self):
        settings = self.root / "settings.json"
        save_rules([dict(self.rule, layout=["invalid"])], settings)

        with self.assertRaisesRegex(ValueError, "invalid token"):
            load_rules(settings)

    def test_queued_watch_file_moves_without_duplicate_claim(self):
        source = self.watch / "Camera A" / "queued.mov"
        source.write_bytes(b"media")
        tool = Tool()
        tool._rules_file = self.root / "unused-settings.json"
        tool.wait_until_stable = lambda *_args, **_kwargs: True

        tool.queue_file(source, self.rule)
        target = build_output_path(source, self.rule)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not target.exists():
            time.sleep(0.02)

        self.assertFalse(source.exists())
        self.assertTrue(target.is_file())


if __name__ == "__main__":
    unittest.main()
