from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from content_builder.tools.tts import resolve_audio_output_path


class TTSToolTests(unittest.TestCase):
    def test_resolves_virtual_output_wav_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            target = resolve_audio_output_path(
                "/output/storybooks/moon/audio/page-01.wav",
                root=root,
            )

        self.assertEqual(target, root / "storybooks" / "moon" / "audio" / "page-01.wav")

    def test_rejects_non_wav_audio_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            with self.assertRaisesRegex(ValueError, "must end with"):
                resolve_audio_output_path("/output/storybooks/moon/audio/page-01.mp3", root=root)

    def test_rejects_paths_outside_output_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            with self.assertRaisesRegex(ValueError, "under /output/"):
                resolve_audio_output_path("../escape.wav", root=root)


if __name__ == "__main__":
    unittest.main()
