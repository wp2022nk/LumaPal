"""Tools that let the Agent call connected Xiaozhi hardware MCP tools."""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
from langchain.tools import ToolRuntime
from langchain_core.tools import tool

from content_builder.multimodal import build_user_content
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


def _as_dict(payload: Any) -> dict[str, Any] | None:
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, str):
        try:
            decoded = json.loads(payload)
        except ValueError:
            return None
        return decoded if isinstance(decoded, dict) else None
    return None


def _uploaded_photo_content(thread_id: str, upload_result: Any, question: str) -> Any:
    upload_payload = _as_dict(upload_result)
    if not upload_payload:
        return upload_result
    if upload_payload.get("success") is False:
        return upload_payload
    file_payload = upload_payload.get("file")
    if not isinstance(file_payload, dict) or not file_payload.get("path"):
        return upload_payload

    try:
        image_path = resolve_thread_file(str(upload_payload.get("thread_id") or thread_id), str(file_payload["path"]))
        requested_question = str(upload_payload.get("question") or question or "请描述这张图片。")
        return build_user_content(requested_question, [image_path])
    except (FileNotFoundError, ValueError) as exc:
        return f"\u7167\u7247\u5df2\u4e0a\u4f20\uff0c\u4f46\u8bfb\u53d6\u56fe\u7247\u5931\u8d25\uff1a{exc}"


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


def _call_device_tool(thread_id: str, name: str, arguments: dict[str, Any], *, analyze_upload: bool = True) -> Any:
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
                return f"\u5df2\u8fde\u63a5 xiaozhi \u8bbe\u5907\uff0c\u4f46\u5b83\u6ca1\u6709\u66b4\u9732\u8bf7\u6c42\u7684\u5de5\u5177\uff1a{detail}"
            return "\u5f53\u524d\u6ca1\u6709\u8fde\u63a5\u5230\u8fd9\u4e2a\u4f1a\u8bdd\u7684 xiaozhi \u786c\u4ef6\uff0c\u65e0\u6cd5\u8c03\u7528\u8bbe\u5907\u5de5\u5177\u3002"
        if response.status_code == 403:
            detail = _response_detail(response)
            return f"\u8bbe\u5907\u5de5\u5177 {name} \u4e0d\u5728\u540e\u7aef\u767d\u540d\u5355\u5185\uff0c\u5df2\u62d2\u7edd\u8c03\u7528\u3002{detail}"
        response.raise_for_status()
        result = _extract_mcp_text(response.json())
        if analyze_upload and _is_photo_like_tool(name):
            question = str(arguments.get("question") or arguments.get("prompt") or "")
            return _uploaded_photo_content(thread_id, result, question)
        return result
    except httpx.HTTPError as exc:
        return f"\u8c03\u7528 xiaozhi \u8bbe\u5907\u5de5\u5177\u5931\u8d25\uff1a{exc}"


@tool
def xiaozhi_take_photo(question: str, runtime: ToolRuntime) -> Any:
    """Take a photo with the connected Xiaozhi device and answer a visual question.

    Args:
        question: The visual question to ask about the captured photo.
    """

    thread_id = runtime_thread_id(runtime)
    return _call_device_tool(
        thread_id,
        "self.camera.take_photo",
        {"question": question},
    )


@tool
def xiaozhi_call_device_tool(name: str, arguments: dict[str, Any], runtime: ToolRuntime) -> Any:
    """Call a safe whitelisted MCP tool exposed by connected Xiaozhi hardware.

    Args:
        name: MCP tool name reported by the device.
        arguments: Tool arguments as a JSON object.
    """

    return _call_device_tool(runtime_thread_id(runtime), name, arguments or {})
