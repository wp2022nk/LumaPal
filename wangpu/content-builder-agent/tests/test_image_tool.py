from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from content_builder.tools.image import generate_image, resolve_image_output_path


class GenerateImageToolTests(unittest.TestCase):
    def test_resolve_virtual_output_path_under_configured_output_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir).resolve()
            with patch("content_builder.tools.image.output_root", return_value=root):
                target = resolve_image_output_path("/output/storybooks/moon/images/page-01.png")
        self.assertEqual(target, root / "storybooks" / "moon" / "images" / "page-01.png")

    def test_rejects_path_outside_output_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir).resolve()
            with patch("content_builder.tools.image.output_root", return_value=root):
                with self.assertRaises(ValueError):
                    resolve_image_output_path("/output/../outside.png")

    def test_failed_generation_creates_sibling_error_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir).resolve()
            with (
                patch("content_builder.tools.image.output_root", return_value=root),
                patch(
                    "content_builder.tools.image.generate_qwen_image",
                    side_effect=RuntimeError("provider unavailable"),
                ),
            ):
                result = generate_image.func(
                    prompt="scene",
                    output_path="/output/storybooks/moon/images/page-01.png",
                )
            error_path = root / "storybooks" / "moon" / "images" / "page-01-error.txt"
            self.assertIn("Image generation failed", result)
            self.assertTrue(error_path.is_file())
            self.assertIn("provider unavailable", error_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
