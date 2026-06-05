from __future__ import annotations

import os
import tempfile
import unittest
import json
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
                "CONTENT_BUILDER_HISTORY_DIR": str(Path(self.temporary_dir.name) / "history"),
                "CONTENT_BUILDER_MEMORY_DIR": str(Path(self.temporary_dir.name) / "history" / "memory"),
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

    def test_history_snapshot_saves_messages_and_mirrors_thread_files(self) -> None:
        upload = self.client.post(
            "/api/content-builder/threads/session-a/uploads/images",
            headers=self.headers,
            files={"image": ("camera.png", b"\x89PNG\r\n\x1a\nfake", "image/png")},
        )
        self.assertEqual(upload.status_code, 200)

        response = self.client.post(
            "/api/content-builder/threads/session-a/history/snapshot",
            headers=self.headers,
            json={
                "messages": [
                    {"type": "human", "content": [{"type": "text", "text": "hello"}]},
                    {"type": "ai", "content": "done"},
                ],
                "metadata": {"source": "test"},
            },
        )

        self.assertEqual(response.status_code, 200)
        history_path = Path(response.json()["path"])
        self.assertEqual(history_path.name, "history.json")
        payload = json.loads(history_path.read_text(encoding="utf-8"))
        self.assertIn("session-a", payload["conversations"])
        self.assertEqual(payload["conversations"]["session-a"]["metadata"], {"source": "test"})
        self.assertEqual(payload["conversations"]["session-a"]["messages"][1]["content"], "done")
        self.assertTrue((history_path.parent / "uploads" / "images").is_dir())
        self.assertFalse((history_path.parent / "session-a").exists())
        self.assertTrue(any(item["path"].startswith("uploads/images/") for item in payload["files"]))

    def test_history_snapshot_accepts_growth_fields_and_updates_single_profile(self) -> None:
        response = self.client.post(
            "/api/content-builder/threads/session-a/history/snapshot",
            headers=self.headers,
            json={
                "messages": [{"type": "human", "content": "我喜欢水滴闯关游戏"}],
                "growth_events": [
                    {
                        "type": "game_preference",
                        "summary": "孩子主动选择水循环闯关玩法",
                        "confidence": 0.82,
                    }
                ],
                "artifact_refs": [{"type": "game", "path": "games/didi-cloud-adventure/index.html"}],
                "profile_updates": {
                    "summary": "孩子对水的变化和闯关式探索表现出稳定兴趣。",
                    "interests": ["水的变化"],
                    "game_type_preferences": ["闯关式知识游戏"],
                    "evidence": ["主动选择水滴闯关游戏"],
                },
            },
        )

        self.assertEqual(response.status_code, 200)
        history_path = Path(response.json()["path"])
        payload = json.loads(history_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["growth_events"][0]["type"], "game_preference")
        self.assertEqual(payload["artifact_refs"][0]["type"], "game")
        self.assertEqual(payload["profile_updates"][0]["updates"]["interests"], ["水的变化"])

        memory_dir = Path(os.environ["CONTENT_BUILDER_MEMORY_DIR"])
        profile = json.loads((memory_dir / "profile.json").read_text(encoding="utf-8"))
        self.assertIn("水的变化", profile["interests"])
        self.assertIn("闯关式知识游戏", profile["game_type_preferences"])
        self.assertIn("孩子对水的变化", (memory_dir / "profile.md").read_text(encoding="utf-8"))
        self.assertIn("game_preference", (memory_dir / "events.jsonl").read_text(encoding="utf-8"))

    def test_daily_history_json_collects_all_threads(self) -> None:
        first = self.client.post(
            "/api/content-builder/threads/session-a/history/snapshot",
            headers=self.headers,
            json={"messages": [{"type": "human", "content": "first"}]},
        )
        second = self.client.post(
            "/api/content-builder/threads/session-b/history/snapshot",
            headers=self.headers,
            json={"messages": [{"type": "human", "content": "second"}]},
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()["path"], second.json()["path"])
        payload = json.loads(Path(first.json()["path"]).read_text(encoding="utf-8"))
        self.assertEqual(payload["conversations"]["session-a"]["messages"][0]["content"], "first")
        self.assertEqual(payload["conversations"]["session-b"]["messages"][0]["content"], "second")

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
