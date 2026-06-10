"""Tools that let the Agent call connected Xiaozhi hardware MCP tools."""

from __future__ import annotations

import os
from typing import Any

import httpx
from langchain.tools import ToolRuntime
from langchain_core.tools import tool

from content_builder.server.security import pairing_token
from content_builder.thread_storage import runtime_thread_id


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
        if _is_photo_like_tool(name):
            return "成功"
        return _extract_mcp_text(response.json())
    except httpx.HTTPError as exc:
        return f"Failed to call Xiaozhi device tool: {exc}"


@tool
def xiaozhi_call_device_tool(name: str, arguments: dict[str, Any], runtime: ToolRuntime) -> Any:
    """Call a safe whitelisted MCP tool exposed by connected Xiaozhi hardware.

    Args:
        name: MCP tool name reported by the device.
        arguments: Tool arguments as a JSON object.
    """

    return _call_device_tool(runtime_thread_id(runtime), name, arguments or {})
