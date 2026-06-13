from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "storybook"
    / "scripts"
    / "render_storybook.py"
)
SPEC = importlib.util.spec_from_file_location("render_storybook", SCRIPT_PATH)
assert SPEC and SPEC.loader
render_storybook = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(render_storybook)


class RenderStorybookTests(unittest.TestCase):
    def test_html_contains_square_page_layout_and_manifest_text(self) -> None:
        book = {
            "title": "月亮邮差",
            "pages": [
                {
                    "type": "cover",
                    "layout": "full-bleed-title",
                    "text": "晚安，小小旅行家",
                    "alt_text": "封面",
                    "_image_uri": "file:///tmp/cover.png",
                },
                {
                    "type": "page",
                    "layout": "image-top-text-bottom",
                    "text": "小星星轻轻敲门。",
                    "alt_text": "内页",
                    "_image_uri": "file:///tmp/page.png",
                },
            ],
        }
        output = render_storybook.render_print_html(book)
        self.assertIn("@page { size: 210mm 210mm; margin: 0; }", output)
        self.assertIn("月亮邮差", output)
        self.assertIn("小星星轻轻敲门。", output)

    def test_resolves_output_relative_paths_under_active_output_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            output_root = Path(temporary_dir).resolve()
            original_root = render_storybook.OUTPUT_ROOT
            try:
                render_storybook.OUTPUT_ROOT = output_root
                target = render_storybook.resolve_artifact_path("output/storybooks/moon/book.json")
                bare_target = render_storybook.resolve_artifact_path("storybooks/moon/book.json")
            finally:
                render_storybook.OUTPUT_ROOT = original_root

        self.assertEqual(target, output_root / "storybooks" / "moon" / "book.json")
        self.assertEqual(bare_target, output_root / "storybooks" / "moon" / "book.json")

    def test_manifest_rejects_missing_page_image(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            output_dir = Path(temporary_dir)
            manifest_path = output_dir / "book.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "title": "测试",
                        "slug": "test-book",
                        "format": {"page_size": "square-210mm", "page_count": 1},
                        "pages": [
                            {
                                "id": "page-00-cover",
                                "layout": "full-bleed-title",
                                "image_path": "images/page-00-cover.png",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8-sig",
            )
            with self.assertRaisesRegex(ValueError, "Required page image not found"):
                render_storybook.load_manifest(manifest_path, output_dir)


if __name__ == "__main__":
    unittest.main()
