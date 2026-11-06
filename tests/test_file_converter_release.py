import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

from tools.file_converter_tool import Tool


class FileConverterOutputTests(unittest.TestCase):
    def test_batch_does_not_overwrite_existing_files_or_selected_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "picture.png"
            other_source = root / "picture.jpg"
            Image.new("RGB", (20, 10), "red").save(source)
            Image.new("RGB", (20, 10), "blue").save(other_source)
            original = other_source.read_bytes()

            tool = Tool()
            tool._get_sources = lambda: [str(source), str(other_source)]
            tool.output_entry = Mock()
            tool.output_entry.get.return_value = str(root)
            tool.target_var = Mock()
            tool.target_var.get.return_value = "jpg"
            tool._read_resize = lambda: None
            tool.quality_var = Mock()
            tool.quality_var.get.return_value = 90
            tool.window = Mock()
            tool.log = Mock()
            with patch("tools.file_converter_tool.messagebox.showinfo"):
                tool.run_conversion()

            self.assertEqual(other_source.read_bytes(), original)
            self.assertTrue((root / "picture (2).jpg").is_file())
            self.assertTrue((root / "picture (3).jpg").is_file())


if __name__ == "__main__":
    unittest.main()
