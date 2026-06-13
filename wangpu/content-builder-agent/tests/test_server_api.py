from __future__ import annotations

import asyncio
import io
import os
import tempfile
import unittest
import json
from asyncio import run
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from langchain_core.messages import ToolMessage

from content_builder.server import api
from content_builder.server.api import WebSocketPCMPlayer, app
from content_builder.server.security import make_preview_token, preview_token_is_valid
from content_builder.server.xiaozhi import XiaozhiSession, XiaozhiWebSocketOpusPlayer, _agent_input_for_xiaozhi_turn, _handle_device_audio, _handle_device_text, _public_base_url_from_websocket, _run_agent_tts_turn, session_manager
from content_builder.server.xiaozhi.turn_service import _stream_event_to_tool_event
from content_builder.streaming import StreamEvent
from content_builder.tools.xiaozhi import _call_device_tool, _tool_response_for_agent, xiaozhi_call_device_tool
from content_builder.thread_storage import resolve_thread_file, thread_paths
from content_builder.history import save_thread_daily_messages
from content_builder import archive


class XiaozhiToolEventMappingTests(unittest.TestCase):
    def test_tool_event_uses_tool_message_name(self) -> None:
        event = StreamEvent(
            "tool_result",
            "main",
            "[main] 工具返回: web_search\n{}",
            ToolMessage(content="{}", name="web_search", tool_call_id="call-1"),
        )

        tool_event = _stream_event_to_tool_event(event)

        self.assertIsNotNone(tool_event)
        self.assertEqual(tool_event["name"], "web_search")

    def test_tool_event_uses_nested_payload_tool_name(self) -> None:
        event = StreamEvent(
            "tool_call",
            "main",
            "[main] 调用工具: generate_image\n{}",
            {"payload": {"tool_name": "generate_image"}, "input": {"prompt": "moon"}},
        )

        tool_event = _stream_event_to_tool_event(event)

        self.assertIsNotNone(tool_event)
        self.assertEqual(tool_event["name"], "generate_image")

    def test_tool_event_falls_back_to_text_tool_name(self) -> None:
        event = StreamEvent(
            "tool_call",
            "main",
            "[main] 调用工具: xiaozhi_call_device_tool\n{}",
            object(),
        )

        tool_event = _stream_event_to_tool_event(event)

        self.assertIsNotNone(tool_event)
        self.assertEqual(tool_event["name"], "xiaozhi_call_device_tool")


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

    def test_xiaozhi_ota_generates_new_thread_when_not_explicit(self) -> None:
        first = self.client.get("/api/xiaozhi/ota", params={"token": "phone-token"})
        second = self.client.get("/api/xiaozhi/ota", params={"token": "phone-token"})

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        first_url = first.json()["websocket"]["url"]
        second_url = second.json()["websocket"]["url"]
        self.assertIn("/api/xiaozhi/v1/ws?thread_id=xiaozhi-", first_url)
        self.assertNotEqual(first_url, second_url)

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

    def test_xiaozhi_websocket_ignores_old_query_thread_id(self) -> None:
        with patch("content_builder.server.xiaozhi.XiaozhiSession.initialize_mcp", return_value=None):
            with self.client.websocket_connect(
                "/api/xiaozhi/v1/ws?thread_id=xiaozhi-python&token=phone-token",
                headers={"Device-Id": "device-a", "Client-Id": "client-a", "Protocol-Version": "1"},
            ) as websocket:
                websocket.send_json({"type": "hello", "version": 1, "transport": "websocket"})
                websocket.receive_json()
                status = self.client.get("/api/xiaozhi/v1/status", headers=self.headers)

        [session] = status.json()["sessions"]
        self.assertNotEqual(session["thread_id"], "xiaozhi-python")
        self.assertTrue(session["thread_id"].startswith("xiaozhi-device-a-"))

    def test_xiaozhi_websocket_reconnects_get_distinct_thread_ids(self) -> None:
        thread_ids: list[str] = []
        with patch("content_builder.server.xiaozhi.XiaozhiSession.initialize_mcp", return_value=None):
            for _ in range(2):
                with self.client.websocket_connect(
                    "/api/xiaozhi/v1/ws?thread_id=xiaozhi-python&token=phone-token",
                    headers={"Device-Id": "device-a", "Client-Id": "client-a", "Protocol-Version": "1"},
                ) as websocket:
                    websocket.send_json({"type": "hello", "version": 1, "transport": "websocket"})
                    websocket.receive_json()
                    status = self.client.get("/api/xiaozhi/v1/status", headers=self.headers)
                    [session] = status.json()["sessions"]
                    thread_ids.append(session["thread_id"])

        self.assertEqual(len(set(thread_ids)), 2)
        self.assertTrue(all(thread_id.startswith("xiaozhi-device-a-") for thread_id in thread_ids))

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
            current_user_transcript = "please look at what I am holding"

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
        self.assertEqual(fake.arguments["question"], "please look at what I am holding")

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

    def test_xiaozhi_device_tool_alias_resolves_reference_photo_tool(self) -> None:
        session = XiaozhiSession(websocket=object(), thread_id="session-a", device_id="", client_id="")
        session.tools = {"self.camera.take_photo": {}}

        async def fake_send_mcp_request(method: str, params: dict, **kwargs: object) -> dict:
            return {"method": method, "params": params}

        session.send_mcp_request = fake_send_mcp_request  # type: ignore[method-assign]

        result = run(session.call_tool("take_photo", {"question": "what do you see?"}))

        self.assertEqual(result["method"], "tools/call")
        self.assertEqual(result["params"]["name"], "self.camera.take_photo")
        self.assertEqual(result["params"]["arguments"], {"question": "what do you see?"})

    def test_xiaozhi_mcp_tool_call_prints_structured_call_and_result(self) -> None:
        class FakeWebSocket:
            def __init__(self) -> None:
                self.messages: list[dict[str, Any]] = []

            async def send_text(self, message: str) -> None:
                payload = json.loads(message)
                self.messages.append(payload)
                request_id = payload["payload"]["id"]
                await session.handle_mcp_payload(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {"content": [{"type": "text", "text": "ok"}]},
                    }
                )

        session = XiaozhiSession(websocket=FakeWebSocket(), thread_id="session-a", device_id="", client_id="")
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            result = run(session.call_tool("self.get_device_status", {"verbose": True}))

        output = buffer.getvalue()
        self.assertIn("Xiaozhi MCP Tool Call", output)
        self.assertIn("tool: self.get_device_status", output)
        self.assertIn("arguments:", output)
        self.assertIn("Xiaozhi MCP Tool Result", output)
        self.assertEqual(result["result"]["content"][0]["text"], "ok")

    def test_xiaozhi_session_sends_tool_event_to_firmware(self) -> None:
        class FakeWebSocket:
            def __init__(self) -> None:
                self.messages: list[dict[str, Any]] = []

            async def send_text(self, message: str) -> None:
                self.messages.append(json.loads(message))

        websocket = FakeWebSocket()
        session = XiaozhiSession(websocket=websocket, thread_id="session-a", device_id="", client_id="")

        run(session.send_tool_event({"state": "started", "name": "generate_image", "arguments": {"prompt": "moon"}}))

        self.assertEqual(websocket.messages[0]["type"], "tts")
        self.assertEqual(websocket.messages[0]["state"], "sentence_start")
        self.assertEqual(websocket.messages[0]["text"], "正在执行工具：\ngenerate_image")
        self.assertEqual(websocket.messages[0]["session_id"], session.session_id)
        self.assertEqual(websocket.messages[1]["type"], "tts")
        self.assertEqual(websocket.messages[1]["state"], "sentence_end")

    def test_xiaozhi_direct_agent_turn_streams_local_tokens_without_agent_server(self) -> None:
        class FakeWebSocket:
            def __init__(self) -> None:
                self.messages: list[dict[str, Any]] = []

            async def send_text(self, message: str) -> None:
                self.messages.append(json.loads(message))

        class FakeSpeaker:
            instances: list["FakeSpeaker"] = []

            def __init__(self, *_args: object, **_kwargs: object) -> None:
                self.tokens: list[str] = []
                self.flushes = 0
                self.closed = False
                FakeSpeaker.instances.append(self)

            async def start(self) -> None:
                return None

            async def feed_token(self, text: str) -> None:
                self.tokens.append(text)

            async def flush(self) -> None:
                self.flushes += 1

            async def close(self) -> None:
                self.closed = True

        async def fake_events(*_args: object, **_kwargs: object):
            yield StreamEvent("token", "main", "回答")
            yield StreamEvent("final", "main", "回答")

        config = SimpleNamespace(
            voice=SimpleNamespace(tts=SimpleNamespace(sample_rate=24000)),
            secrets=SimpleNamespace(),
            conversation=SimpleNamespace(max_turns=50),
        )
        session = XiaozhiSession(websocket=FakeWebSocket(), thread_id="session-a", device_id="", client_id="")
        buffer = io.StringIO()
        with (
            patch("content_builder.server.xiaozhi.turn_service.create_content_writer", return_value=object()) as create_agent,
            patch("content_builder.server.xiaozhi.turn_service.astream_agent_events", side_effect=fake_events) as stream_events,
            patch("content_builder.server.xiaozhi.turn_service.VoiceResponseSpeaker", FakeSpeaker),
            patch("content_builder.server.xiaozhi.turn_service.save_thread_daily_messages"),
            redirect_stdout(buffer),
        ):
            run(_run_agent_tts_turn(session, "问题", config))

        create_agent.assert_called_once_with(runtime_mode="server")
        stream_events.assert_called_once()
        self.assertEqual(FakeSpeaker.instances[0].tokens, ["回答"])
        self.assertEqual(FakeSpeaker.instances[0].flushes, 1)
        self.assertTrue(FakeSpeaker.instances[0].closed)
        output = buffer.getvalue()
        self.assertIn("stage: llm_start", output)
        self.assertIn("stage: llm_token", output)
        self.assertIn("stage: llm_final", output)

    def test_xiaozhi_direct_agent_tool_events_are_text_only_for_firmware(self) -> None:
        class FakeWebSocket:
            def __init__(self) -> None:
                self.messages: list[dict[str, Any]] = []

            async def send_text(self, message: str) -> None:
                self.messages.append(json.loads(message))

        class FakeSpeaker:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                self.tokens: list[str] = []

            async def start(self) -> None:
                return None

            async def feed_token(self, text: str) -> None:
                self.tokens.append(text)

            async def flush(self) -> None:
                return None

            async def close(self) -> None:
                return None

        async def fake_events(*_args: object, **_kwargs: object):
            yield StreamEvent(
                "tool_call",
                "main",
                "[main] 调用工具: generate_image",
                {"tool_name": "generate_image", "input": {"prompt": "moon"}},
            )
            yield StreamEvent("final", "main", "完成")

        config = SimpleNamespace(
            voice=SimpleNamespace(tts=SimpleNamespace(sample_rate=24000)),
            secrets=SimpleNamespace(),
            conversation=SimpleNamespace(max_turns=50),
        )
        websocket = FakeWebSocket()
        session = XiaozhiSession(websocket=websocket, thread_id="session-a", device_id="", client_id="")
        with (
            patch("content_builder.server.xiaozhi.turn_service.create_content_writer", return_value=object()),
            patch("content_builder.server.xiaozhi.turn_service.astream_agent_events", side_effect=fake_events),
            patch("content_builder.server.xiaozhi.turn_service.VoiceResponseSpeaker", FakeSpeaker),
            patch("content_builder.server.xiaozhi.turn_service.save_thread_daily_messages"),
            redirect_stdout(io.StringIO()),
        ):
            run(_run_agent_tts_turn(session, "画月亮", config))

        tool_messages = [
            message
            for message in websocket.messages
            if message.get("type") == "tts" and message.get("state") == "sentence_start"
        ]
        self.assertEqual(len(tool_messages), 1)
        self.assertEqual(tool_messages[0]["text"], "正在执行工具：\ngenerate_image")

    def test_xiaozhi_photo_intent_is_prompted_for_agent_tool_call(self) -> None:
        class FakeWebSocket:
            def __init__(self) -> None:
                self.messages: list[dict[str, Any]] = []

            async def send_text(self, message: str) -> None:
                self.messages.append(json.loads(message))

        class FakeSpeaker:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                return None

            async def start(self) -> None:
                return None

            async def feed_token(self, _text: str) -> None:
                return None

            async def flush(self) -> None:
                return None

            async def close(self) -> None:
                return None

        stream_calls: list[tuple[tuple[object, ...], dict[str, Any]]] = []

        async def fake_events(*args: object, **kwargs: object):
            stream_calls.append((args, kwargs))
            yield StreamEvent("final", "main", "这是一个玩具。")

        config = SimpleNamespace(
            voice=SimpleNamespace(tts=SimpleNamespace(sample_rate=24000)),
            secrets=SimpleNamespace(),
            conversation=SimpleNamespace(max_turns=50),
        )
        websocket = FakeWebSocket()
        session = XiaozhiSession(websocket=websocket, thread_id="session-a", device_id="", client_id="")
        session.tools = {"take_photo": {}, "self.audio_speaker.set_volume": {}}

        async def fake_call_photo_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
            raise AssertionError("photo must be initiated by the agent tool, not by turn_service")

        session.call_photo_tool = fake_call_photo_tool  # type: ignore[method-assign]
        with (
            patch("content_builder.server.xiaozhi.turn_service.create_content_writer", return_value=object()),
            patch("content_builder.server.xiaozhi.turn_service.astream_agent_events", side_effect=fake_events),
            patch("content_builder.server.xiaozhi.turn_service.VoiceResponseSpeaker", FakeSpeaker),
            patch("content_builder.server.xiaozhi.turn_service.save_thread_daily_messages"),
            redirect_stdout(io.StringIO()),
        ):
            run(_run_agent_tts_turn(session, "拍张照片看看这是什么。", config))

        agent_message = str(stream_calls[0][0][1])
        self.assertIn("拍张照片看看这是什么。", agent_message)
        self.assertIn("Exposed device tools: self.audio_speaker.set_volume, take_photo", agent_message)
        self.assertIn("MUST call xiaozhi_call_device_tool", agent_message)
        self.assertNotIn("images", stream_calls[0][1])

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

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            run(session.initialize_mcp("http://127.0.0.1:2024"))

        self.assertIn("self.audio_speaker.set_volume", session.tools)
        self.assertIn("take_photo", session.tools)
        output = buffer.getvalue()
        self.assertIn("Xiaozhi MCP Tools Page", output)
        self.assertIn("tool_count: 2", output)
        self.assertIn("camera_tools:", output)
        self.assertIn("take_photo", output)
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
            scope = {"server": ("127.0.0.1", 2024)}
            client = FakeClient()

        with patch("content_builder.server.xiaozhi._local_ip_for_remote", return_value="172.20.10.2"):
            base_url = _public_base_url_from_websocket(FakeWebSocket())  # type: ignore[arg-type]

        self.assertEqual(base_url, "http://172.20.10.2:2024")

    def test_xiaozhi_websocket_public_base_url_fills_missing_server_port(self) -> None:
        class FakeClient:
            host = "192.168.14.213"

        class FakeWebSocket:
            url = "ws://192.168.14.214/api/xiaozhi/v1/ws?thread_id=session-a"
            scope = {"server": ("0.0.0.0", 2024)}
            client = FakeClient()

        with patch("content_builder.server.xiaozhi._local_ipv4_candidates_from_hostname", return_value=["192.168.14.214"]):
            base_url = _public_base_url_from_websocket(FakeWebSocket())  # type: ignore[arg-type]

        self.assertEqual(base_url, "http://192.168.14.214:2024")

    def test_xiaozhi_websocket_public_base_url_keeps_reachable_host(self) -> None:
        class FakeClient:
            host = "172.20.10.8"

        class FakeWebSocket:
            url = "ws://172.20.10.2:2024/api/xiaozhi/v1/ws?thread_id=session-a"
            scope = {"server": ("0.0.0.0", 2024)}
            client = FakeClient()

        with patch("content_builder.server.xiaozhi._local_ipv4_candidates_from_hostname", return_value=["172.20.10.2"]):
            self.assertEqual(_public_base_url_from_websocket(FakeWebSocket()), "http://172.20.10.2:2024")  # type: ignore[arg-type]

    def test_xiaozhi_websocket_public_base_url_falls_back_to_ipconfig_same_subnet(self) -> None:
        class FakeClient:
            host = "192.168.14.99"

        class FakeWebSocket:
            url = "ws://127.0.0.1:2024/api/xiaozhi/v1/ws?thread_id=session-a"
            scope = {"server": ("127.0.0.1", 2024)}
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
            scope = {"server": ("127.0.0.1", 2024)}
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

    def test_xiaozhi_websocket_public_base_url_fills_missing_configured_port(self) -> None:
        class FakeClient:
            host = "192.168.14.213"

        class FakeWebSocket:
            url = "ws://127.0.0.1/api/xiaozhi/v1/ws?thread_id=session-a"
            scope = {"server": ("0.0.0.0", 2024)}
            client = FakeClient()

        with (
            patch.dict(os.environ, {"CONTENT_BUILDER_PUBLIC_BASE_URL": "http://192.168.14.214"}),
            patch("content_builder.server.xiaozhi._local_ipv4_candidates_from_hostname", return_value=["192.168.14.214"]),
        ):
            base_url = _public_base_url_from_websocket(FakeWebSocket())  # type: ignore[arg-type]

        self.assertEqual(base_url, "http://192.168.14.214:2024")

    def test_xiaozhi_agent_input_preserves_original_transcript_without_tools(self) -> None:
        session = XiaozhiSession(websocket=object(), thread_id="session-a", device_id="", client_id="")

        content = _agent_input_for_xiaozhi_turn(session, "What is displayed on the screen?")

        self.assertEqual(content, "What is displayed on the screen?")

    def test_xiaozhi_agent_input_includes_device_tools_for_camera_requests(self) -> None:
        session = XiaozhiSession(websocket=object(), thread_id="session-a", device_id="", client_id="")
        session.tools = {"take_photo": {}, "self.audio_speaker.set_volume": {}}

        content = _agent_input_for_xiaozhi_turn(session, "What is displayed on the screen?")

        self.assertIn("What is displayed on the screen?", content)
        self.assertIn("Exposed device tools: self.audio_speaker.set_volume, take_photo", content)
        self.assertIn("Camera tools available: take_photo", content)
        self.assertIn("MUST call xiaozhi_call_device_tool", content)

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
                await _handle_device_audio(session, b"voice")
                await _handle_device_audio(session, b"silence")
                self.assertIsNotNone(session.agent_task)
                await session.agent_task

        run(exercise())

        self.assertEqual(started_turns, [[b"voice", b"voice", b"silence"]])

    def test_xiaozhi_audio_while_speaking_does_not_start_listening_or_interrupt(self) -> None:
        class FakeCodec:
            def __init__(self, *args, **kwargs) -> None:
                return None

            def decode(self, frame: bytes) -> bytes:
                return (10000).to_bytes(2, "little", signed=True) * 960

        class FakeWebSocket:
            def __init__(self) -> None:
                self.messages: list[dict[str, Any]] = []

            async def send_text(self, message: str) -> None:
                self.messages.append(json.loads(message))

        class FakeTask:
            cancelled = False

            def done(self) -> bool:
                return False

            def cancel(self) -> None:
                self.cancelled = True

        async def exercise() -> tuple[XiaozhiSession, FakeTask]:
            websocket = FakeWebSocket()
            session = XiaozhiSession(websocket=websocket, thread_id="session-a", device_id="", client_id="")
            session.server_is_speaking = True
            task = FakeTask()
            session.agent_task = task
            with patch("content_builder.server.xiaozhi.OpusCodec", FakeCodec):
                await _handle_device_audio(session, b"noise")
                await _handle_device_audio(session, b"noise")
            return session, task

        session, task = run(exercise())

        self.assertFalse(session.auto_speech_started)
        self.assertFalse(task.cancelled)
        self.assertEqual(session.websocket.messages, [])

    def test_xiaozhi_listen_event_while_speaking_is_ignored(self) -> None:
        class FakeTask:
            cancelled = False

            def done(self) -> bool:
                return False

            def cancel(self) -> None:
                self.cancelled = True

        async def exercise() -> tuple[XiaozhiSession, FakeTask]:
            session = XiaozhiSession(websocket=object(), thread_id="session-a", device_id="", client_id="")
            session.server_is_speaking = True
            task = FakeTask()
            session.agent_task = task
            await _handle_device_text(session, json.dumps({"type": "listen", "state": "detect", "text": "hello"}))
            return session, task

        session, task = run(exercise())

        self.assertFalse(task.cancelled)
        self.assertFalse(session.manual_listen_started)
        self.assertFalse(session.auto_speech_started)

    def test_xiaozhi_sustained_voice_starts_after_reply_is_done(self) -> None:
        class FakeCodec:
            def __init__(self, *args, **kwargs) -> None:
                return None

            def decode(self, frame: bytes) -> bytes:
                return (10000).to_bytes(2, "little", signed=True) * 960

        async def exercise() -> XiaozhiSession:
            session = XiaozhiSession(websocket=object(), thread_id="session-a", device_id="", client_id="")
            with patch("content_builder.server.xiaozhi.OpusCodec", FakeCodec):
                await _handle_device_audio(session, b"voice")
                await _handle_device_audio(session, b"voice")
            return session

        session = run(exercise())

        self.assertTrue(session.auto_speech_started)

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

    def test_xiaozhi_vision_explain_accepts_image_field_from_reference_firmware(self) -> None:
        response = self.client.post(
            "/mcp/vision/explain",
            params={"thread_id": "xiaozhi-python", "token": "phone-token"},
            data={"question": "what is this?"},
            files={"image": ("camera.jpg", b"fake-jpeg", "image/jpeg")},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertTrue(payload["file"]["path"].startswith("uploads/images/"))

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

    def test_xiaozhi_photo_tool_saves_image_data_uri_from_mcp_result(self) -> None:
        async def exercise() -> tuple[dict[str, Any], tuple[str, dict[str, Any]]]:
            session = XiaozhiSession(websocket=object(), thread_id="xiaozhi-python", device_id="", client_id="")

            async def fake_call_tool(name: str, arguments: dict | None = None, **kwargs: object) -> dict:
                return {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(
                                    {
                                        "success": True,
                                        "image_data_uri": "data:image/jpeg;base64,ZmFrZS1qcGVn",
                                        "metadata": {"width": 320, "height": 240},
                                    }
                                ),
                            }
                        ],
                        "isError": False,
                    },
                }

            session.call_tool = fake_call_tool  # type: ignore[method-assign]
            async with session_manager.subscribe("xiaozhi-python") as queue:
                result = await session.call_photo_tool("self.camera.take_photo", {"question": "what is this?"})
                event = await asyncio.wait_for(queue.get(), timeout=1)
            return result, event

        result, event = run(exercise())

        self.assertTrue(result["success"])
        self.assertEqual(result["thread_id"], "xiaozhi-python")
        self.assertEqual(result["question"], "what is this?")
        self.assertEqual(result["file"]["mime_type"], "image/jpeg")
        self.assertTrue(result["file"]["path"].startswith("uploads/images/"))
        self.assertEqual(result["metadata"], {"width": 320, "height": 240})
        self.assertNotIn("image_data_uri", result)
        image_path = resolve_thread_file("xiaozhi-python", result["file"]["path"])
        self.assertEqual(image_path.read_bytes(), b"fake-jpeg")
        self.assertEqual(event[0], "photo_uploaded")
        self.assertEqual(event[1]["file"]["path"], result["file"]["path"])

    def test_xiaozhi_vision_analyze_uses_uploaded_thread_id(self) -> None:
        image_path = thread_paths("xiaozhi-python").uploads / "images" / "camera.jpg"
        image_path.parent.mkdir(parents=True, exist_ok=True)
        image_path.write_bytes(b"fake-jpeg")

        with patch("content_builder.server.xiaozhi._explain_image_sync", return_value="book on desk"):
            response = self.client.post(
                "/api/xiaozhi/v1/vision/analyze",
                headers=self.headers,
                json={
                    "thread_id": "xiaozhi-python",
                    "question": "what is on the desk?",
                    "file": {"path": "uploads/images/camera.jpg", "mime_type": "image/jpeg"},
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["result"], "book on desk")
        self.assertEqual(payload["thread_id"], "xiaozhi-python")

    def test_xiaozhi_photo_tool_returns_uploaded_file_payload_from_tool_wrapper(self) -> None:
        upload_payload = {
            "success": True,
            "thread_id": "session-a",
            "question": "what is this?",
            "file": {"path": "uploads/images/camera.jpg", "mime_type": "image/jpeg"},
        }

        class FakeResponse:
            status_code = 200

            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict[str, object]:
                return {
                    "result": {
                        "content": [{"type": "text", "text": json.dumps(upload_payload)}],
                        "isError": False,
                    }
                }

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

        self.assertEqual(result, upload_payload)

    def test_xiaozhi_photo_tool_response_attaches_image_for_multimodal_model(self) -> None:
        image_path = thread_paths("session-a").uploads / "images" / "camera.jpg"
        image_path.parent.mkdir(parents=True, exist_ok=True)
        image_path.write_bytes(b"fake-jpeg")
        upload_payload = {
            "success": True,
            "thread_id": "session-a",
            "question": "what is this?",
            "file": {"path": "uploads/images/camera.jpg", "mime_type": "image/jpeg"},
        }

        content, artifact = _tool_response_for_agent("session-a", upload_payload)

        self.assertEqual(xiaozhi_call_device_tool.response_format, "content_and_artifact")
        self.assertEqual(artifact, upload_payload)
        self.assertIsInstance(content, list)
        self.assertIn("uploads/images/camera.jpg", content[0]["text"])
        self.assertEqual(content[1]["type"], "image_url")
        self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,"))

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

        This test verifies the full chain: REST call -> MCP request to firmware -> firmware
        uploads photo via REST -> backend stores photo -> call_photo_tool returns upload payload.
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
                    "question": "please look at what I am holding",
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

                return await session.call_photo_tool(
                    "take_photo",
                    {"question": "please look at what I am holding"},
                )
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
        listed = next(item for item in listing.json()["entries"] if item["path"].endswith("notes/draft.md"))
        self.assertEqual(listed["path"], "artifacts/files/notes/draft.md")

        preview = self.client.get(listed["preview_url"])
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.text, "# Draft")

        wrong_thread_url = listed["preview_url"].replace("session-a", "session-b")
        self.assertEqual(self.client.get(wrong_thread_url).status_code, 401)

    def test_history_artifacts_read_new_archive_manifests_with_preview(self) -> None:
        history_file = (
            Path(os.environ["CONTENT_BUILDER_HISTORY_DIR"])
            / "2026-06-08"
            / "conversations"
            / "session-a"
            / "artifacts"
            / "storybooks"
            / "moon"
            / "book.html"
        )
        history_file.parent.mkdir(parents=True, exist_ok=True)
        history_file.write_text("<h1>story</h1>", encoding="utf-8")
        old_file = (
            Path(os.environ["CONTENT_BUILDER_HISTORY_DIR"])
            / "2026-06-01"
            / "conversations"
            / "session-a"
            / "artifacts"
            / "games"
            / "old-game"
            / "index.html"
        )
        old_file.parent.mkdir(parents=True, exist_ok=True)
        old_file.write_text("<h1>old</h1>", encoding="utf-8")
        archive.update_manifest("session-a", day="2026-06-08")
        archive.update_manifest("session-a", day="2026-06-01")

        response = self.client.get(
            "/api/content-builder/history/artifacts",
            headers=self.headers,
            params={"start_date": "2026-06-08", "end_date": "2099-12-31"},
        )
        self.assertEqual(response.status_code, 200)
        entries = response.json()["entries"]
        paths = {entry["path"] for entry in entries}
        self.assertIn("artifacts/storybooks/moon/book.html", paths)
        self.assertNotIn("artifacts/games/old-game/index.html", paths)

        story = next(entry for entry in entries if entry["path"].endswith("book.html"))
        self.assertEqual(story["category"], "storybook")
        self.assertEqual(story["source"], "history")
        preview = self.client.get(story["preview_url"])
        self.assertEqual(preview.status_code, 200)
        self.assertIn("story", preview.text)

        bad_path = "history/2026-06-08/%2E%2E/secret.txt"
        token = make_preview_token("history", bad_path)
        rejected = self.client.get(f"/api/content-builder/history-preview/{token}/{bad_path}")
        self.assertEqual(rejected.status_code, 400)

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
        self.assertEqual(history_path.name, "chat.json")
        payload = json.loads(history_path.read_text(encoding="utf-8"))
        self.assertEqual([message["content"] for message in payload], ["hello", "done"])
        self.assertEqual(payload[0]["source"], "test")
        self.assertTrue((history_path.parent / "uploads" / "images").is_dir())
        manifest = json.loads((history_path.parent / "manifest.json").read_text(encoding="utf-8"))
        self.assertTrue(any(item["path"].startswith("uploads/images/") for item in manifest["artifacts"]))

    def test_history_snapshot_accepts_growth_fields_and_updates_single_profile(self) -> None:
        response = self.client.post(
            "/api/content-builder/threads/session-a/history/snapshot",
            headers=self.headers,
            json={
                "messages": [{"type": "human", "content": "I like water drop puzzle games"}],
                "growth_events": [
                    {
                        "type": "game_preference",
                        "summary": "child actively chose a water-cycle puzzle game",
                        "confidence": 0.82,
                    }
                ],
                "artifact_refs": [{"type": "game", "path": "games/didi-cloud-adventure/index.html"}],
                "profile_updates": {
                    "summary": "child shows steady interest in water changes and puzzle exploration",
                    "interests": ["water changes"],
                    "game_type_preferences": ["knowledge puzzle games"],
                    "evidence": ["actively chose the water drop puzzle game"],
                },
            },
        )

        self.assertEqual(response.status_code, 200)
        history_path = Path(response.json()["path"])
        events = [
            json.loads(line)
            for line in (history_path.parent / "events.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        self.assertTrue(any(item["type"] == "growth_event" and item["summary"].startswith("child") for item in events))
        self.assertTrue(any(item["type"] == "artifact_ref" and item["path"].endswith("index.html") for item in events))
        self.assertTrue(any(item["type"] == "profile_update" and item["updates"]["interests"] == ["water changes"] for item in events))

        memory_dir = Path(os.environ["CONTENT_BUILDER_MEMORY_DIR"])
        profile = json.loads((memory_dir / "profile.json").read_text(encoding="utf-8"))
        self.assertIn("water changes", profile["interests"])
        self.assertIn("knowledge puzzle games", profile["game_type_preferences"])
        self.assertIn("water changes", (memory_dir / "profile.md").read_text(encoding="utf-8"))
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
        self.assertNotEqual(first.json()["path"], second.json()["path"])
        index_path = Path(first.json()["path"]).parents[2] / "day_index.json"
        payload = json.loads(index_path.read_text(encoding="utf-8"))
        conversations = {item["thread_id"]: item for item in payload["conversations"]}
        self.assertIn("session-a", conversations)
        self.assertIn("session-b", conversations)

    def test_history_snapshot_append_mode_keeps_existing_daily_messages_once(self) -> None:
        first = self.client.post(
            "/api/content-builder/threads/session-a/history/snapshot",
            headers=self.headers,
            json={"mode": "append", "messages": [{"type": "human", "content": "first"}]},
        )
        second = self.client.post(
            "/api/content-builder/threads/session-a/history/snapshot",
            headers=self.headers,
            json={"mode": "append", "messages": [{"type": "ai", "content": "second"}]},
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        payload = json.loads(Path(second.json()["path"]).read_text(encoding="utf-8"))
        self.assertEqual([message["content"] for message in payload], ["first", "second"])

    def test_daily_message_append_keeps_only_new_turn_messages(self) -> None:
        with patch("content_builder.history._today", return_value="2026-06-11"):
            save_thread_daily_messages(
                "session-a",
                [
                    {"type": "human", "content": "old question"},
                    {"type": "ai", "content": "old answer"},
                ],
                metadata={"source": "xiaozhi-test"},
            )

        with patch("content_builder.history._today", return_value="2026-06-12"):
            history_path = save_thread_daily_messages(
                "session-a",
                [{"type": "human", "content": "new question"}],
                metadata={"source": "xiaozhi-test"},
            )

        payload = json.loads(history_path.read_text(encoding="utf-8"))
        self.assertEqual([message["content"] for message in payload], ["new question"])

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

        self.assertEqual([message["content"] for message in first_payload], ["day one question", "day one answer"])
        self.assertEqual(
            [message["content"] for message in second_payload],
            ["day one question", "day one answer", "day two question"],
        )

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
    def test_pcm_player_sends_audio_and_emotion_events(self) -> None:
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
                    "text": "done",
                    "emotion_en": "happy",
                    "emotion_cn": "happy",
                    "emoji": ":)",
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
                    "text": "done",
                    "emotion_en": "happy",
                    "emotion_cn": "happy",
                    "emoji": ":)",
                    "confidence": 1.0,
                },
                {"type": "segment_end"},
            ],
        )
        self.assertEqual(websocket.chunks, [b"\x00\x01"])


if __name__ == "__main__":
    unittest.main()
