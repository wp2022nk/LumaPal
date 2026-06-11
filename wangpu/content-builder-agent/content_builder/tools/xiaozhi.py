"""Tools that let the Agent call connected Xiaozhi hardware MCP tools."""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
from typing import Any

import httpx
from langchain.tools import ToolRuntime
from langchain_core.tools import tool

from content_builder.multimodal import build_image_content
from content_builder.server.security import pairing_token
from content_builder.thread_storage import resolve_thread_file, runtime_thread_id


def _gateway_url() -> str:
    return os.environ.get("CONTENT_BUILDER_GATEWAY_URL", "http://127.0.0.1:2024").rstrip("/")


def _extract_mcp_text(response_payload: dict[str, Any]) -> Any:
    result = response_payload.get("result")
    if not isinstance(result, dict):
        return response_payload
    content = result.get("content")
    if isinstance(content, list):
        texts = [item.get("text") for item in content if isinstance(item, dict) and item.get("type") == "text"]
        if texts:
            return "\n".join(str(text) for text in texts)
    return result


def _photo_payload_has_file(payload: Any) -> bool:
    return isinstance(payload, dict) and isinstance(payload.get("file"), dict) and bool(payload["file"].get("path"))


def _extract_photo_payload(response_payload: dict[str, Any]) -> dict[str, Any] | None:
    if _photo_payload_has_file(response_payload):
        return response_payload
    result = response_payload.get("result")
    if _photo_payload_has_file(result):
        return result
    content = result.get("content") if isinstance(result, dict) else None
    if not isinstance(content, list):
        return None
    for item in content:
        if not isinstance(item, dict) or item.get("text") is None:
            continue
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            parsed = json.loads(str(item["text"]))
            if _photo_payload_has_file(parsed):
                return parsed
            if isinstance(parsed, dict) and _photo_payload_has_file(parsed.get("result")):
                return parsed["result"]
    return None


def _response_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, str):
            return detail
    return response.text


def _is_photo_like_tool(name: str) -> bool:
    normalized = "".join(character for character in name.lower() if character.isalnum())
    return normalized in {
        "takephoto",
        "selfcameratakephoto",
        "cameratakephoto",
        "takescreenshot",
        "selfcameratakescreenshot",
        "cameratakescreenshot",
    }


def _call_device_tool(thread_id: str, name: str, arguments: dict[str, Any]) -> Any:
    url = f"{_gateway_url()}/api/xiaozhi/v1/sessions/{thread_id}/mcp/tools/call"
    try:
        with httpx.Client(timeout=60) as client:
            response = client.post(
                url,
                headers={"x-api-key": pairing_token()},
                json={"name": name, "arguments": arguments},
            )
        if response.status_code == 404:
            detail = _response_detail(response)
            if "does not expose tool" in detail:
                return f"Xiaozhi device is connected, but it does not expose the requested tool: {detail}"
            return "No Xiaozhi device is connected for this conversation."
        if response.status_code == 403:
            detail = _response_detail(response)
            return f"Device tool {name} is not allowed by the backend whitelist. {detail}"
        response.raise_for_status()
        payload = response.json()
        if _is_photo_like_tool(name):
            photo_payload = _extract_photo_payload(payload)
            if photo_payload is not None:
                return photo_payload
        return _extract_mcp_text(payload)
    except httpx.HTTPError as exc:
        return f"Failed to call Xiaozhi device tool: {exc}"


def _tool_response_for_agent(thread_id: str, result: Any) -> tuple[Any, Any]:
    if not _photo_payload_has_file(result):
        return result, result

    file_payload = result.get("file") if isinstance(result, dict) else {}
    relative_path = str(file_payload.get("path") or "") if isinstance(file_payload, dict) else ""
    text_payload = json.dumps(result, ensure_ascii=False)
    text_block = {
        "type": "text",
        "text": (
            "Xiaozhi photo captured. Inspect the attached image directly before answering. "
            f"Photo payload: {text_payload}"
        ),
    }
    try:
        image_path = resolve_thread_file(thread_id, relative_path)
        return [text_block, *build_image_content([Path(image_path)])], result
    except (OSError, ValueError, FileNotFoundError):
        return text_payload, result


@tool(response_format="content_and_artifact")
def xiaozhi_call_device_tool(name: str, arguments: dict[str, Any], runtime: ToolRuntime) -> Any:
    """Call a safe whitelisted MCP tool exposed by connected Xiaozhi hardware.

    Args:
        name: MCP tool name reported by the device.
        arguments: Tool arguments as a JSON object.
    """

    thread_id = runtime_thread_id(runtime)
    return _tool_response_for_agent(thread_id, _call_device_tool(thread_id, name, arguments or {}))
