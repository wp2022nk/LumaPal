from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
import json
from asyncio import run
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from content_builder.server import api
from content_builder.server.api import WebSocketPCMPlayer, app
from content_builder.server.security import make_preview_token, preview_token_is_valid
from content_builder.server.xiaozhi import XiaozhiSession, XiaozhiWebSocketOpusPlayer, _agent_input_for_xiaozhi_turn, _handle_device_audio, _handle_device_text, _public_base_url_from_websocket, session_manager
from content_builder.tools.xiaozhi import _call_device_tool
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

    def test_xiaozhi_ota_returns_websocket_config_from_pairing_token(self) -> None:
        missing = self.client.get("/api/xiaozhi/ota")
        self.assertEqual(missing.status_code, 401)

        response = self.client.get("/api/xiaozhi/ota", params={"token": "phone-token", "thread_id": "kid-room"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["websocket"]["token"], "phone-token")
        self.assertEqual(payload["websocket"]["version"], 1)
        self.assertIn("/api/xiaozhi/v1/ws?thread_id=kid-room", payload["websocket"]["url"])
        self.assertIn("server_time", payload)

    def test_xiaozhi_websocket_hello_uses_reference_audio_params(self) -> None:
        with patch("content_builder.server.xiaozhi.XiaozhiSession.initialize_mcp", return_value=None):
            with self.client.websocket_connect(
                "/api/xiaozhi/v1/ws?thread_id=kid-room&token=phone-token",
                headers={"Device-Id": "device-a", "Client-Id": "client-a", "Protocol-Version": "1"},
            ) as websocket:
                websocket.send_json(
                    {
                        "type": "hello",
                        "version": 1,
                        "features": {"mcp": True},
                        "transport": "websocket",
                        "audio_params": {
                            "format": "opus",
                            "sample_rate": 16000,
                            "channels": 1,
                            "frame_duration": 60,
                        },
                    }
                )
                response = websocket.receive_json()

        self.assertEqual(response["type"], "hello")
        self.assertEqual(response["audio_params"], {"format": "opus", "sample_rate": 16000, "channels": 1, "frame_duration": 60})

    def test_xiaozhi_device_tool_call_requires_connected_session(self) -> None:
        response = self.client.post(
            "/api/xiaozhi/v1/sessions/missing/mcp/tools/call",
            headers=self.headers,
            json={"name": "self.get_device_status", "arguments": {}},
        )

        self.assertEqual(response.status_code, 404)

    def test_xiaozhi_device_tool_call_uses_active_session(self) -> None:
        class FakeSession:
            async def call_tool(self, name: str, arguments: dict) -> dict:
                self.name = name
                self.arguments = arguments
                return {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": "ok"}]}}

        fake = FakeSession()
        with patch.object(session_manager, "active_for_thread", return_value=fake):
            response = self.client.post(
                "/api/xiaozhi/v1/sessions/session-a/mcp/tools/call",
                headers=self.headers,
                json={"name": "self.get_device_status", "arguments": {"verbose": True}},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["result"]["content"][0]["text"], "ok")
        self.assertEqual(fake.name, "self.get_device_status")
        self.assertEqual(fake.arguments, {"verbose": True})

    def test_xiaozhi_photo_tool_call_uses_original_turn_text(self) -> None:
        class FakeSession:
            current_user_transcript = "帮我看看手里拿的是什么"

            async def call_tool(self, name: str, arguments: dict) -> dict:
                self.name = name
                self.arguments = arguments
                return {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": "ok"}]}}

        fake = FakeSession()
        with patch.object(session_manager, "active_for_thread", return_value=fake):
            response = self.client.post(
                "/api/xiaozhi/v1/sessions/session-a/mcp/tools/call",
                headers=self.headers,
                json={"name": "take_photo", "arguments": {"question": "What is displayed on the screen?"}},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(fake.name, "take_photo")
        self.assertEqual(fake.arguments["question"], "帮我看看手里拿的是什么")

    def test_xiaozhi_session_manager_indexes_agent_thread_alias(self) -> None:
        class FakeSession:
            session_id = "session-a"
            thread_id = "xiaozhi-python"
            agent_task = None

            def close_pending(self, reason: str) -> None:
                self.close_reason = reason

        fake = FakeSession()
        agent_thread_id = "24fe96c1-054f-5d53-91cd-5f00dddc6577"
        session_manager.register(fake)
        try:
            self.assertIs(session_manager.active_for_thread("xiaozhi-python"), fake)
            self.assertIs(session_manager.active_for_thread(agent_thread_id), fake)
        finally:
            session_manager.unregister(fake)

    def test_xiaozhi_device_tool_alias_resolves_volume_tool(self) -> None:
        session = XiaozhiSession(websocket=object(), thread_id="session-a", device_id="", client_id="")
        session.tools = {"self.audio_speaker.set_volume": {}}

        async def fake_send_mcp_request(method: str, params: dict, **kwargs: object) -> dict:
            return {"method": method, "params": params}

        session.send_mcp_request = fake_send_mcp_request  # type: ignore[method-assign]

        result = run(session.call_tool("self.setvolume", {"volume": 80}))

        self.assertEqual(result["method"], "tools/call")
        self.assertEqual(result["params"]["name"], "self.audio_speaker.set_volume")
        self.assertEqual(result["params"]["arguments"], {"volume": 80})

    def test_xiaozhi_device_tool_alias_resolves_python_photo_tool(self) -> None:
        session = XiaozhiSession(websocket=object(), thread_id="session-a", device_id="", client_id="")
        session.tools = {"take_photo": {}}

        async def fake_send_mcp_request(method: str, params: dict, **kwargs: object) -> dict:
            return {"method": method, "params": params}

        session.send_mcp_request = fake_send_mcp_request  # type: ignore[method-assign]

        result = run(session.call_tool("self.camera.take_photo", {"question": "what do you see?"}))

        self.assertEqual(result["method"], "tools/call")
        self.assertEqual(result["params"]["name"], "take_photo")
        self.assertEqual(result["params"]["arguments"], {"question": "what do you see?"})

    def test_xiaozhi_initialize_mcp_reads_paginated_tools(self) -> None:
        session = XiaozhiSession(websocket=object(), thread_id="session-a", device_id="", client_id="")
        requests: list[tuple[str, dict]] = []

        async def fake_send_mcp_request(method: str, params: dict) -> dict:
            requests.append((method, params))
            if method == "initialize":
                return {"result": {}}
            if params.get("cursor") == "":
                return {
                    "result": {
                        "tools": [{"name": "self.audio_speaker.set_volume"}],
                        "nextCursor": "page-2",
                    }
                }
            return {"result": {"tools": [{"name": "take_photo"}]}}

        session.send_mcp_request = fake_send_mcp_request  # type: ignore[method-assign]

        run(session.initialize_mcp("http://127.0.0.1:2024"))

        self.assertIn("self.audio_speaker.set_volume", session.tools)
        self.assertIn("take_photo", session.tools)
        initialize_request = requests[0]
        self.assertEqual(initialize_request[0], "initialize")
        self.assertEqual(initialize_request[1]["protocolVersion"], "2024-11-05")
        self.assertEqual(initialize_request[1]["clientInfo"]["name"], "XiaozhiClient")
        self.assertIn("/mcp/vision/explain?thread_id=session-a&token=", initialize_request[1]["capabilities"]["vision"]["url"])
        self.assertEqual(initialize_request[1]["capabilities"]["vision"]["token"], "phone-token")
        self.assertIn("roots", initialize_request[1]["capabilities"])
        self.assertIn("sampling", initialize_request[1]["capabilities"])
        self.assertIn(("tools/list", {"cursor": "page-2"}), requests)

    def test_xiaozhi_websocket_public_base_url_uses_lan_ip_when_request_host_is_loopback(self) -> None:
        class FakeClient:
            host = "172.20.10.8"

        class FakeWebSocket:
            url = "ws://127.0.0.1:2024/api/xiaozhi/v1/ws?thread_id=session-a"
            client = FakeClient()

        with patch("content_builder.server.xiaozhi._local_ip_for_remote", return_value="172.20.10.2"):
            base_url = _public_base_url_from_websocket(FakeWebSocket())  # type: ignore[arg-type]

        self.assertEqual(base_url, "http://172.20.10.2:2024")

    def test_xiaozhi_websocket_public_base_url_keeps_reachable_host(self) -> None:
        class FakeClient:
            host = "172.20.10.8"

        class FakeWebSocket:
            url = "ws://172.20.10.2:2024/api/xiaozhi/v1/ws?thread_id=session-a"
            client = FakeClient()

        with patch("content_builder.server.xiaozhi._local_ipv4_candidates_from_hostname", return_value=["172.20.10.2"]):
            self.assertEqual(_public_base_url_from_websocket(FakeWebSocket()), "http://172.20.10.2:2024")  # type: ignore[arg-type]

    def test_xiaozhi_websocket_public_base_url_falls_back_to_ipconfig_same_subnet(self) -> None:
        class FakeClient:
            host = "192.168.14.99"

        class FakeWebSocket:
            url = "ws://127.0.0.1:2024/api/xiaozhi/v1/ws?thread_id=session-a"
            client = FakeClient()

        ipconfig_output = """
Ethernet adapter VMware Network Adapter VMnet1:
   IPv4 Address. . . . . . . . . . . : 192.168.117.1
Wireless LAN adapter WLAN:
   IPv4 Address. . . . . . . . . . . : 192.168.14.214
"""
        with (
            patch("content_builder.server.xiaozhi._local_ip_for_remote", return_value=""),
            patch("content_builder.server.xiaozhi._local_ipv4_candidates_from_hostname", return_value=[]),
            patch("content_builder.server.xiaozhi.subprocess.check_output", return_value=ipconfig_output),
        ):
            base_url = _public_base_url_from_websocket(FakeWebSocket())  # type: ignore[arg-type]

        self.assertEqual(base_url, "http://192.168.14.214:2024")

    def test_xiaozhi_websocket_public_base_url_replaces_stale_configured_private_ip(self) -> None:
        class FakeClient:
            host = "172.20.10.8"

        class FakeWebSocket:
            url = "ws://127.0.0.1:2024/api/xiaozhi/v1/ws?thread_id=session-a"
            client = FakeClient()

        ipconfig_output = """
Wireless LAN adapter WLAN:
   IPv4 Address. . . . . . . . . . . : 172.20.10.2
"""
        with (
            patch.dict(os.environ, {"CONTENT_BUILDER_PUBLIC_BASE_URL": "http://192.168.1.5:2024"}),
            patch("content_builder.server.xiaozhi._local_ip_for_remote", return_value=""),
            patch("content_builder.server.xiaozhi._local_ipv4_candidates_from_hostname", return_value=[]),
            patch("content_builder.server.xiaozhi.subprocess.check_output", return_value=ipconfig_output),
        ):
            base_url = _public_base_url_from_websocket(FakeWebSocket())  # type: ignore[arg-type]

        self.assertEqual(base_url, "http://172.20.10.2:2024")

    def test_xiaozhi_agent_input_preserves_original_transcript(self) -> None:
        session = XiaozhiSession(websocket=object(), thread_id="session-a", device_id="", client_id="")
        session.tools = {"take_photo": {}, "self.audio_speaker.set_volume": {}}

        content = _agent_input_for_xiaozhi_turn(session, "What is displayed on the screen?")

        self.assertEqual(content, "What is displayed on the screen?")

    def test_xiaozhi_opus_player_closes_each_tts_sentence(self) -> None:
        class FakeCodec:
            def __init__(self, *args, **kwargs) -> None:
                self.index = 0

            def encode_pcm_stream(self, pcm: bytes) -> list[bytes]:
                self.index += 1
                return [b"frame-" + str(self.index).encode("ascii") + b":" + pcm]

            def flush(self) -> list[bytes]:
                return [b"flush"]

        class FakeSession:
            tts_sample_rate = 24000

            def __init__(self) -> None:
                self.events: list[tuple[str, object]] = []

            async def send_tts_sentence_start(self, text: str = "") -> None:
                self.events.append(("json", {"type": "tts", "state": "sentence_start", "text": text}))

            async def send_tts_sentence_end(self) -> None:
                self.events.append(("json", {"type": "tts", "state": "sentence_end"}))

            async def send_binary(self, payload: bytes) -> None:
                self.events.append(("bytes", payload))

        async def exercise() -> list[tuple[str, object]]:
            session = FakeSession()
            with (
                patch("content_builder.server.xiaozhi.OpusCodec", FakeCodec),
                patch("content_builder.server.xiaozhi.XIAOZHI_TTS_TAIL_DRAIN_MS", 0),
            ):
                player = XiaozhiWebSocketOpusPlayer(session)  # type: ignore[arg-type]
                await player.start()
                await player.send_segment_text("first")
                await player.enqueue_pcm(b"a", segment_start=True)
                await player.send_segment_text("second")
                await player.enqueue_pcm(b"b", segment_start=True)
                await player.mark_segment_end()
                await player.close()
            return session.events

        self.assertEqual(
            run(exercise()),
            [
                ("json", {"type": "tts", "state": "sentence_start", "text": "first"}),
                ("bytes", b"frame-1:a"),
                ("json", {"type": "tts", "state": "sentence_end"}),
                ("json", {"type": "tts", "state": "sentence_start", "text": "second"}),
                ("bytes", b"frame-2:b"),
                ("bytes", b"flush"),
                ("json", {"type": "tts", "state": "sentence_end"}),
            ],
        )

    def test_xiaozhi_opus_player_buffers_segment_before_playback(self) -> None:
        class FakeCodec:
            def __init__(self, *args, **kwargs) -> None:
                return None

            def encode_pcm_stream(self, pcm: bytes) -> list[bytes]:
                return [b"frame:" + pcm]

            def flush(self) -> list[bytes]:
                return []

        class FakeSession:
            tts_sample_rate = 24000

            def __init__(self) -> None:
                self.events: list[tuple[str, object]] = []

            async def send_tts_sentence_start(self, text: str = "") -> None:
                self.events.append(("json", {"type": "tts", "state": "sentence_start", "text": text}))

            async def send_tts_sentence_end(self) -> None:
                self.events.append(("json", {"type": "tts", "state": "sentence_end"}))

            async def send_binary(self, payload: bytes) -> None:
                self.events.append(("bytes", payload))

        async def exercise() -> tuple[list[tuple[str, object]], list[tuple[str, object]]]:
            session = FakeSession()
            with (
                patch("content_builder.server.xiaozhi.OpusCodec", FakeCodec),
                patch("content_builder.server.xiaozhi.XIAOZHI_TTS_TAIL_DRAIN_MS", 0),
            ):
                player = XiaozhiWebSocketOpusPlayer(session)  # type: ignore[arg-type]
                await player.start()
                await player.send_segment_text("buffered")
                await player.enqueue_pcm(b"a", segment_start=True)
                await asyncio.sleep(0)
                before_end = list(session.events)
                await player.mark_segment_end()
                await player.close()
            return before_end, session.events

        before_end, after_end = run(exercise())

        self.assertEqual(before_end, [])
        self.assertEqual(
            after_end,
            [
                ("json", {"type": "tts", "state": "sentence_start", "text": "buffered"}),
                ("bytes", b"frame:a"),
                ("json", {"type": "tts", "state": "sentence_end"}),
            ],
        )

    def test_xiaozhi_opus_player_streams_after_initial_prebuffer(self) -> None:
        class FakeCodec:
            def __init__(self, *args, **kwargs) -> None:
                return None

            def encode_pcm_stream(self, pcm: bytes) -> list[bytes]:
                return [b"frame:" + pcm]

            def flush(self) -> list[bytes]:
                return []

        class FakeSession:
            tts_sample_rate = 24000

            def __init__(self) -> None:
                self.events: list[tuple[str, object]] = []

            async def send_tts_sentence_start(self, text: str = "") -> None:
                self.events.append(("json", {"type": "tts", "state": "sentence_start", "text": text}))

            async def send_tts_sentence_end(self) -> None:
                self.events.append(("json", {"type": "tts", "state": "sentence_end"}))

            async def send_binary(self, payload: bytes) -> None:
                self.events.append(("bytes", payload))

        async def exercise() -> tuple[list[tuple[str, object]], list[tuple[str, object]]]:
            session = FakeSession()
            with (
                patch("content_builder.server.xiaozhi.OpusCodec", FakeCodec),
                patch("content_builder.server.xiaozhi.XIAOZHI_TTS_TAIL_DRAIN_MS", 0),
            ):
                player = XiaozhiWebSocketOpusPlayer(session)  # type: ignore[arg-type]
                await player.start()
                await player.send_segment_text("streaming")
                await player.enqueue_pcm(b"a", segment_start=True)
                await player.enqueue_pcm(b"b")
                await player.enqueue_pcm(b"c")
                await player.drain()
                before_end = list(session.events)
                await player.mark_segment_end()
                await player.close()
            return before_end, session.events

        before_end, after_end = run(exercise())

        self.assertEqual(
            before_end,
            [
                ("json", {"type": "tts", "state": "sentence_start", "text": "streaming"}),
                ("bytes", b"frame:a"),
                ("bytes", b"frame:b"),
                ("bytes", b"frame:c"),
            ],
        )
        self.assertEqual(after_end[-1], ("json", {"type": "tts", "state": "sentence_end"}))

    def test_xiaozhi_opus_player_waits_before_sentence_end_to_preserve_tail(self) -> None:
        class FakeCodec:
            def __init__(self, *args, **kwargs) -> None:
                return None

            def encode_pcm_stream(self, pcm: bytes) -> list[bytes]:
                return [b"frame:" + pcm]

            def flush(self) -> list[bytes]:
                return []

        class FakeSession:
            tts_sample_rate = 24000

            def __init__(self) -> None:
                self.events: list[tuple[str, object]] = []

            async def send_tts_sentence_start(self, text: str = "") -> None:
                self.events.append(("json", {"state": "sentence_start", "text": text}))

            async def send_tts_sentence_end(self) -> None:
                self.events.append(("json", {"state": "sentence_end"}))

            async def send_binary(self, payload: bytes) -> None:
                self.events.append(("bytes", payload))

        async def exercise() -> list[tuple[str, object]]:
            session = FakeSession()
            original_sleep = asyncio.sleep

            async def fake_sleep(delay: float) -> None:
                session.events.append(("sleep", delay))
                await original_sleep(0)

            with (
                patch("content_builder.server.xiaozhi.OpusCodec", FakeCodec),
                patch("content_builder.server.xiaozhi.XIAOZHI_TTS_TAIL_DRAIN_MS", 90),
                patch("content_builder.server.xiaozhi.asyncio.sleep", fake_sleep),
            ):
                player = XiaozhiWebSocketOpusPlayer(session)  # type: ignore[arg-type]
                await player.start()
                await player.send_segment_text("tail")
                await player.enqueue_pcm(b"a", segment_start=True)
                await player.mark_segment_end()
                await player.close()
            return session.events

        events = run(exercise())

        self.assertEqual(
            events,
            [
                ("json", {"state": "sentence_start", "text": "tail"}),
                ("bytes", b"frame:a"),
                ("sleep", 0.09),
                ("json", {"state": "sentence_end"}),
            ],
        )

    def test_xiaozhi_full_duplex_audio_triggers_voice_turn_without_listen_stop(self) -> None:
        class FakeCodec:
            def __init__(self, *args, **kwargs) -> None:
                return None

            def decode(self, frame: bytes) -> bytes:
                if frame == b"voice":
                    return (10000).to_bytes(2, "little", signed=True) * 960
                return b"\x00\x00" * 960

        started_turns: list[list[bytes]] = []

        async def fake_voice_turn(session: XiaozhiSession, frames: list[bytes]) -> None:
            started_turns.append(frames)

        async def exercise() -> None:
            session = XiaozhiSession(websocket=object(), thread_id="session-a", device_id="", client_id="")
            with (
                patch("content_builder.server.xiaozhi.OpusCodec", FakeCodec),
                patch("content_builder.server.xiaozhi.XIAOZHI_AUTO_VAD_MIN_SPEECH_MS", 0),
                patch("content_builder.server.xiaozhi.XIAOZHI_AUTO_VAD_SILENCE_MS", 0),
                patch("content_builder.server.xiaozhi._run_voice_turn_from_audio", fake_voice_turn),
            ):
                await _handle_device_audio(session, b"voice")
                await _handle_device_audio(session, b"silence")
                self.assertIsNotNone(session.agent_task)
                await session.agent_task

        run(exercise())

        self.assertEqual(started_turns, [[b"voice", b"silence"]])

    def test_xiaozhi_listen_start_defaults_to_full_duplex_auto_mode(self) -> None:
        async def exercise() -> XiaozhiSession:
            session = XiaozhiSession(websocket=object(), thread_id="session-a", device_id="", client_id="")
            await _handle_device_text(session, json.dumps({"type": "listen", "state": "start"}))
            return session

        session = run(exercise())

        self.assertEqual(session.current_listen_mode, "auto")
        self.assertFalse(session.manual_listen_started)

    def test_xiaozhi_vision_upload_returns_saved_photo_payload(self) -> None:
        response = self.client.post(
            "/api/xiaozhi/v1/vision/upload",
            params={"thread_id": "xiaozhi-python"},
            headers={"authorization": "Bearer phone-token"},
            data={"question": "what is this?"},
            files={"file": ("camera.jpg", b"fake-jpeg", "image/jpeg")},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["thread_id"], "xiaozhi-python")
        self.assertEqual(payload["question"], "what is this?")
        self.assertTrue(payload["file"]["path"].startswith("uploads/images/"))
        self.assertNotIn("action", payload)
        self.assertNotIn("response", payload)
        self.assertNotIn("result", payload)
        self.assertTrue(resolve_thread_file("xiaozhi-python", payload["file"]["path"]).is_file())

    def test_xiaozhi_vision_explain_only_uploads_photo_without_model_analysis(self) -> None:
        with patch("content_builder.server.xiaozhi._explain_image_sync") as explain:
            response = self.client.post(
                "/api/xiaozhi/v1/vision/explain",
                params={"thread_id": "xiaozhi-python"},
                headers={"authorization": "Bearer phone-token"},
                data={"question": "what is this?"},
                files={"file": ("camera.jpg", b"fake-jpeg", "image/jpeg")},
            )

        self.assertEqual(response.status_code, 200)
        explain.assert_not_called()
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertTrue(payload["file"]["path"].startswith("uploads/images/"))
        self.assertNotIn("response", payload)
        self.assertNotIn("result", payload)

    def test_xiaozhi_vision_explain_accepts_query_token_from_firmware(self) -> None:
        response = self.client.post(
            "/api/xiaozhi/v1/vision/explain",
            params={"thread_id": "xiaozhi-python", "token": "phone-token"},
            data={"question": "what is this?"},
            files={"file": ("camera.jpg", b"fake-jpeg", "image/jpeg")},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["file"]["path"].startswith("uploads/images/"))

    def test_xiaozhi_mcp_vision_explain_compat_route_matches_reference_service_path(self) -> None:
        health = self.client.get("/mcp/vision/explain")
        self.assertEqual(health.status_code, 200)
        self.assertIn("MCP Vision", health.text)

        response = self.client.post(
            "/mcp/vision/explain",
            params={"thread_id": "xiaozhi-python", "token": "phone-token"},
            data={"question": "what is this?"},
            files={"file": ("camera.jpg", b"\xff\xd8\xfffake-jpeg", "image/jpeg")},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["file"]["path"].startswith("uploads/images/"))
        self.assertNotIn("action", payload)
        self.assertNotIn("response", payload)
        self.assertNotIn("result", payload)

    def test_xiaozhi_photo_tool_returns_uploaded_image_when_mcp_response_hangs(self) -> None:
        async def exercise() -> dict[str, object]:
            session = XiaozhiSession(websocket=object(), thread_id="xiaozhi-python", device_id="", client_id="")

            async def hanging_call_tool(name: str, arguments: dict | None = None, **kwargs: object) -> dict:
                await asyncio.sleep(10)
                return {"result": "late"}

            session.call_tool = hanging_call_tool  # type: ignore[method-assign]
            with patch("content_builder.server.xiaozhi.XIAOZHI_PHOTO_UPLOAD_WAIT_TIMEOUT", 1):
                task = asyncio.create_task(session.call_photo_tool("take_photo", {"question": "what is this?"}))
                await asyncio.sleep(0)
                await session.remember_photo_upload(
                    {
                        "success": True,
                        "thread_id": "xiaozhi-python",
                        "question": "what is this?",
                        "file": {"path": "uploads/images/camera.jpg", "mime_type": "image/jpeg"},
                    }
                )
                return await task

        result = run(exercise())

        self.assertTrue(result["success"])
        self.assertEqual(result["file"]["path"], "uploads/images/camera.jpg")

    def test_xiaozhi_vision_analyze_uses_uploaded_thread_id(self) -> None:
        image_path = thread_paths("xiaozhi-python").uploads / "images" / "camera.jpg"
        image_path.parent.mkdir(parents=True, exist_ok=True)
        image_path.write_bytes(b"fake-jpeg")

        with patch("content_builder.server.xiaozhi._explain_image_sync", return_value="桌上有一本书。"):
            response = self.client.post(
                "/api/xiaozhi/v1/vision/analyze",
                headers=self.headers,
                json={
                    "thread_id": "xiaozhi-python",
                    "question": "桌上有什么？",
                    "file": {"path": "uploads/images/camera.jpg", "mime_type": "image/jpeg"},
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["result"], "桌上有一本书。")
        self.assertEqual(payload["thread_id"], "xiaozhi-python")

    def test_xiaozhi_photo_tool_returns_success_string(self) -> None:
        class FakeResponse:
            status_code = 200

            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict[str, object]:
                return {"result": {"content": [{"type": "text", "text": "ignored"}]}}

        class FakeClient:
            def __enter__(self) -> "FakeClient":
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def post(self, *args: object, **kwargs: object) -> FakeResponse:
                return FakeResponse()

        with patch("content_builder.tools.xiaozhi.httpx.Client", return_value=FakeClient()):
            result = _call_device_tool(
                "session-a",
                "self.camera.take_photo",
                {"question": "what is this?"},
            )

        self.assertEqual(result, "成功")

    def test_xiaozhi_photo_tool_returns_error_message_on_http_failure(self) -> None:
        class FakeClient:
            def __enter__(self) -> "FakeClient":
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def post(self, *args: object, **kwargs: object) -> object:
                raise httpx.ReadTimeout("timed out")

        with patch("content_builder.tools.xiaozhi.httpx.Client", return_value=FakeClient()):
            result = _call_device_tool(
                "session-a",
                "self.camera.take_photo",
                {"question": "what is this?"},
            )

        self.assertIn("timed out", str(result))

    def test_xiaozhi_photo_tool_call_returns_uploaded_file_payload(self) -> None:
        """Simulate the agent calling take_photo via the REST endpoint, with the firmware
        sending the tool response via MCP and uploading the photo via /mcp/vision/explain.

        This test verifies the full chain: REST call → MCP request to firmware → firmware
        uploads photo via REST → backend stores photo → call_photo_tool returns upload payload.
        """

        async def exercise() -> dict[str, Any]:
            from content_builder.server.xiaozhi import session_manager

            session = XiaozhiSession(websocket=object(), thread_id="xiaozhi-python", device_id="", client_id="")
            session.vision_url = "http://testserver/mcp/vision/explain?thread_id=xiaozhi-python&token=phone-token"
            session.tools = {"take_photo": {}}
            session_manager.register(session)
            try:
                pending_upload: dict[str, Any] = {
                    "success": True,
                    "thread_id": "xiaozhi-python",
                    "question": "帮我看看手里拿的是什么",
                    "file": {"path": "uploads/images/abc123.jpg", "mime_type": "image/jpeg"},
                }

                async def fake_call_tool(name: str, arguments: dict[str, Any] | None = None, **kwargs: object) -> dict[str, Any]:
                    # Simulate the firmware uploading the photo before responding
                    await session.remember_photo_upload(pending_upload)
                    # Return the MCP response that the firmware would send back
                    return {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "result": {
                            "content": [{"type": "text", "text": json.dumps(pending_upload)}],
                            "isError": False,
                        },
                    }

                session.call_tool = fake_call_tool  # type: ignore[method-assign]

                return await session.call_photo_tool("take_photo", {"question": "帮我看看手里拿的是什么"})
            finally:
                session_manager.unregister(session)

        result = run(exercise())
        self.assertTrue(result["success"])
        self.assertEqual(result["file"]["path"], "uploads/images/abc123.jpg")

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

    def test_daily_history_snapshot_keeps_only_new_messages_after_prior_day(self) -> None:
        with patch("content_builder.history._today", return_value="2026-06-04"):
            first_day = self.client.post(
                "/api/content-builder/threads/session-a/history/snapshot",
                headers=self.headers,
                json={
                    "messages": [
                        {"type": "human", "content": "day one question"},
                        {"type": "ai", "content": "day one answer"},
                    ],
                },
            )

        with patch("content_builder.history._today", return_value="2026-06-05"):
            second_day = self.client.post(
                "/api/content-builder/threads/session-a/history/snapshot",
                headers=self.headers,
                json={
                    "messages": [
                        {"type": "human", "content": "day one question"},
                        {"type": "ai", "content": "day one answer"},
                        {"type": "human", "content": "day two question"},
                    ],
                },
            )

        self.assertEqual(first_day.status_code, 200)
        self.assertEqual(second_day.status_code, 200)
        first_payload = json.loads(Path(first_day.json()["path"]).read_text(encoding="utf-8"))
        second_payload = json.loads(Path(second_day.json()["path"]).read_text(encoding="utf-8"))

        self.assertEqual(first_payload["conversations"]["session-a"]["message_window_start"], 0)
        self.assertEqual(first_payload["conversations"]["session-a"]["total_message_count"], 2)
        self.assertEqual(
            [message["content"] for message in second_payload["conversations"]["session-a"]["messages"]],
            ["day two question"],
        )
        self.assertEqual(second_payload["conversations"]["session-a"]["message_window_start"], 2)
        self.assertEqual(second_payload["conversations"]["session-a"]["total_message_count"], 3)

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
