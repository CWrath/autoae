import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import tool_loader


class ToolLoaderTests(unittest.TestCase):
    def test_uses_module_directory_when_frozen_bundle_path_differs(self):
        expected_tools = os.path.join(
            os.path.dirname(os.path.abspath(tool_loader.__file__)), "tools"
        )

        with (
            patch.object(sys, "_MEIPASS", "/read-only/translocated/app", create=True),
            patch("tool_loader.os.path.exists", return_value=False),
            patch("tool_loader.os.makedirs") as makedirs,
        ):
            self.assertEqual(tool_loader.load_tools(), [])

        makedirs.assert_called_once_with(str(expected_tools))

    def test_static_metadata_defers_import_and_reuses_instance(self):
        with tempfile.TemporaryDirectory() as directory:
            tools = Path(directory) / "tools"
            tools.mkdir()
            (tools / "sample.py").write_text(
                'class Tool:\n'
                '    def __init__(self):\n'
                '        self.name = "Example"\n'
                '        self.description = "A tool"\n'
                '        self.category = "Media"\n'
                '        self.icon = "film"\n',
                encoding="utf-8",
            )
            calls = []

            class Tool:
                def create_window(self, parent):
                    calls.append(("open", parent))
                    return parent

                def close_window(self):
                    calls.append(("close_window",))

                def close_app(self):
                    calls.append(("close_app",))

            with patch.object(tool_loader, "__file__", str(Path(directory) / "tool_loader.py")), \
                    patch.object(tool_loader.importlib, "import_module", return_value=types.SimpleNamespace(Tool=Tool)) as load:
                result = tool_loader.load_tools()
                self.assertEqual(len(result), 1)
                tool = result[0]
                self.assertEqual((tool.name, tool.description, tool.category, tool.icon),
                                 ("Example", "A tool", "Media", "film"))
                tool.close_window()
                tool.close_app()
                load.assert_not_called()
                self.assertEqual(tool.create_window("first"), "first")
                self.assertEqual(tool.create_window("second"), "second")
                tool.close_window()
                tool.close_app()
                load.assert_called_once_with("tools.sample")
                self.assertEqual(calls, [("open", "first"), ("open", "second"),
                                         ("close_window",), ("close_app",)])

    def test_dynamic_metadata_falls_back_to_original_loading(self):
        with tempfile.TemporaryDirectory() as directory:
            tools = Path(directory) / "tools"
            tools.mkdir()
            (tools / "custom.py").write_text(
                'class Tool:\n'
                '    def __init__(self):\n'
                '        self.name = get_name()\n'
                '        self.description = "A tool"\n',
                encoding="utf-8",
            )
            instance = object()
            with patch.object(tool_loader, "__file__", str(Path(directory) / "tool_loader.py")), \
                    patch.object(tool_loader.importlib, "import_module",
                                 return_value=types.SimpleNamespace(Tool=lambda: instance)) as load:
                self.assertEqual(tool_loader.load_tools(), [instance])
                load.assert_called_once_with("tools.custom")

    def test_failed_import_is_reported_to_caller_and_can_be_retried(self):
        tool = tool_loader.LazyTool("tools.sample", {"name": "Example", "description": "A tool"})
        with patch.object(tool_loader.importlib, "import_module", side_effect=ImportError("missing")) as load:
            for _ in range(2):
                with self.assertRaisesRegex(ImportError, "missing"):
                    tool.create_window(None)
            self.assertEqual(load.call_count, 2)


if __name__ == "__main__":
    unittest.main()
