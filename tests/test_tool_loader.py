import os
import sys
import unittest
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


if __name__ == "__main__":
    unittest.main()
