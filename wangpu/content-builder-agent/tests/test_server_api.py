from __future__ import annotations

import os
import tempfile
import unittest
from asyncio import run
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from content_builder.server import api
from content_builder.server.api import WebSocketPCMPlayer, app
from content_builder.server.security import make_preview_token, preview_token_is_valid
from content_builder.thread_storage import resolve_thread_file, thread_paths


class ThreadStorageTests(unittest.TestCase):
    def test_threads_have_separate_physical_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            first = thread_paths("thread-one", output_root=root)
            second = thread_paths("thread-two", output_root=root)

            self.assertNotEqual(first.root, second.root)
            self.assertTrue(first.artifacts.is_dir())
            self.assertTrue(second.workspace.is_dir())

    def test_thread_path_rejects_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            with patch.dict(os.environ, {"CONTENT_BUILDER_OUTPUT_DIR": temporary_dir}):
                with self.assertRaises(ValueError):
                    resolve_thread_file("thread-one", "../thread-two/artifacts/private.txt")


class ServerAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_dir = tempfile.TemporaryDirectory()
        self.secrets_path = Path(self.temporary_dir.name) / "secrets.local.yaml"
        self.environment = patch.dict(
            os.environ,
            {
                "CONTENT_BUILDER_PAIRING_TOKEN": "phone-token",
                "CONTENT_BUILDER_OUTPUT_DIR": self.temporary_dir.name,
                "CONTENT_BUILDER_SECRETS_FILE": str(self.secrets_path),
            },
        )
        self.environment.start()
        self.client = TestClient(app)
        self.headers = {"x-api-key": "phone-token"}

    def tearDown(self) -> None:
        self.environment.stop()
        self.temporary_dir.cleanup()

    def test_settings_require_pairing_token_and_never_return_plaintext(self) -> None:
        self.assertEqual(self.client.get("/api/content-builder/settings/keys").status_code, 401)

        response = self.client.put(
            "/api/content-builder/settings/keys",
            headers=self.headers,
            json={"qwen": "secret-qwen-key", "tavily": "secret-tavily-key"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["configured"], {"qwen": True, "dashscope": False, "tavily": True})
        self.assertNotIn("secret-qwen-key", response.text)
        self.assertIn("secret-qwen-key", self.secrets_path.read_text(encoding="utf-8"))

    def test_pairing_status_is_public_without_exposing_the_token(self) -> None:
        missing = self.client.get("/api/content-builder/pairing/status")
        invalid = self.client.get("/api/content-builder/pairing/status", headers={"x-api-key": "wrong-token"})
        paired = self.client.get("/api/content-builder/pairing/status", headers=self.headers)

        self.assertEqual(missing.json(), {"paired": False})
        self.assertEqual(invalid.json(), {"paired": False})
        self.assertEqual(paired.json(), {"paired": True})
        self.assertNotIn("phone-token", paired.text)

    def test_updating_keys_clears_the_cached_agent(self) -> None:
        with patch("content_builder.agent_factory.clear_content_writer_cache") as clear_cache:
            response = self.client.put(
                "/api/content-builder/settings/keys",
                headers=self.headers,
                json={"qwen": "fresh-key"},
            )

        self.assertEqual(response.status_code, 200)
        clear_cache.assert_called_once_with()

    def test_upload_artifact_listing_and_signed_preview_are_thread_scoped(self) -> None:
        response = self.client.post(
            "/api/content-builder/threads/session-a/uploads/images",
            headers=self.headers,
            files={"image": ("camera.png", b"\x89PNG\r\n\x1a\nfake", "image/png")},
        )

        self.assertEqual(response.status_code, 200)
        entry = response.json()
        self.assertTrue(entry["path"].startswith("uploads/images/"))

        artifact = thread_paths("session-a").artifacts / "notes" / "draft.md"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("# Draft", encoding="utf-8")

        listing = self.client.get("/api/content-builder/threads/session-a/artifacts", headers=self.headers)
        self.assertEqual(listing.status_code, 200)
        [listed] = listing.json()["entries"]
        self.assertEqual(listed["path"], "artifacts/notes/draft.md")

        preview = self.client.get(listed["preview_url"])
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.text, "# Draft")

        wrong_thread_url = listed["preview_url"].replace("session-a", "session-b")
        self.assertEqual(self.client.get(wrong_thread_url).status_code, 401)

    def test_sandbox_file_route_rejects_path_traversal(self) -> None:
        response = self.client.get(
            "/api/content-builder/threads/session-a/sandbox/file",
            headers=self.headers,
            params={"path": "../session-b/artifacts/private.txt"},
        )

        self.assertEqual(response.status_code, 400)

    def test_voice_asr_stores_audio_in_the_selected_thread(self) -> None:
        class FakeASR:
            async def recognize_file(self, path: Path, *, session_id: str) -> str:
                self.path = path
                self.session_id = session_id
                return "voice transcript"

        fake_asr = FakeASR()
        original = api._asr_manager
        api._asr_manager = fake_asr
        try:
            response = self.client.post(
                "/api/content-builder/threads/session-a/voice/asr",
                headers=self.headers,
                files={"audio": ("recording.wav", b"RIFFfake", "audio/wav")},
            )
        finally:
            api._asr_manager = original

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"transcript": "voice transcript"})
        self.assertEqual(fake_asr.session_id, "session-a")
        self.assertIn("session-a", fake_asr.path.parts)

    def test_voice_asr_reports_missing_runtime_dependency_without_internal_error(self) -> None:
        original = api._asr_manager
        api._asr_manager = None
        try:
            with patch("content_builder.voice.asr.ASRManager", side_effect=RuntimeError("No module named 'torch'")):
                response = self.client.post(
                    "/api/content-builder/threads/session-a/voice/asr",
                    headers=self.headers,
                    files={"audio": ("recording.wav", b"RIFFfake", "audio/wav")},
                )
        finally:
            api._asr_manager = original

        self.assertEqual(response.status_code, 503)
        self.assertIn("No module named 'torch'", response.json()["detail"])

    def test_preview_token_rejects_expired_signature(self) -> None:
        expired = make_preview_token("session-a", "artifacts/a.txt", expires_at=1)
        self.assertFalse(preview_token_is_valid("session-a", "artifacts/a.txt", expired))


class WebSocketPCMPlayerTests(unittest.TestCase):
    def test_pcm_player_forwards_metadata_and_binary_chunks(self) -> None:
        class FakeWebSocket:
            def __init__(self) -> None:
                self.json: list[dict[str, object]] = []
                self.chunks: list[bytes] = []

            async def send_json(self, value: dict[str, object]) -> None:
                self.json.append(value)

            async def send_bytes(self, value: bytes) -> None:
                self.chunks.append(value)

        async def exercise() -> FakeWebSocket:
            websocket = FakeWebSocket()
            player = WebSocketPCMPlayer(websocket, sample_rate=24000, channels=1, sample_width=2)
            await player.start()
            await player.enqueue_pcm(b"\x00\x01", segment_start=True)
            await player.send_emotion(
                {
                    "text": "完成了。",
                    "emotion_en": "happy",
                    "emotion_cn": "开心",
                    "emoji": "🙂",
                    "confidence": 1.0,
                }
            )
            await player.mark_segment_end()
            return websocket

        websocket = run(exercise())
        self.assertEqual(websocket.json[0]["type"], "ready")
        self.assertEqual(
            websocket.json[1:],
            [
                {"type": "segment_start"},
                {
                    "type": "emotion",
                    "text": "完成了。",
                    "emotion_en": "happy",
                    "emotion_cn": "开心",
                    "emoji": "🙂",
                    "confidence": 1.0,
                },
                {"type": "segment_end"},
            ],
        )
        self.assertEqual(websocket.chunks, [b"\x00\x01"])


if __name__ == "__main__":
    unittest.main()
