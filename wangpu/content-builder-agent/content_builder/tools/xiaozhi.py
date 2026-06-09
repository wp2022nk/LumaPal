"""Tools that let the Agent call connected Xiaozhi hardware MCP tools."""

from __future__ import annotations

import json
import os
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


def _photo_error_content_and_artifact(error: str, **extra: Any) -> tuple[list[Any], dict[str, Any]]:
    artifact = {"success": False, "error": error}
    artifact.update(extra)
    return [], artifact


def _uploaded_photo_content_and_artifact(thread_id: str, upload_result: Any) -> tuple[Any, dict[str, Any]]:
    """Convert a Xiaozhi photo upload payload into an image-only tool result."""

    upload_payload = _as_dict(upload_result)
    if not upload_payload:
        return _photo_error_content_and_artifact("Xiaozhi did not return an image payload.", result=upload_result)
    if upload_payload.get("success") is False:
        return [], upload_payload
    file_payload = upload_payload.get("file")
    if not isinstance(file_payload, dict) or not file_payload.get("path"):
        return _photo_error_content_and_artifact("Xiaozhi photo payload did not include an image file.", result=upload_payload)

    try:
        image_path = resolve_thread_file(str(upload_payload.get("thread_id") or thread_id), str(file_payload["path"]))
        artifact = {
            **upload_payload,
            "image_path": str(image_path),
            "content_type": "image",
        }
        return build_image_content([image_path]), artifact
    except (FileNotFoundError, ValueError) as exc:
        return _photo_error_content_and_artifact(str(exc), **upload_payload)


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


def _call_device_tool(
    thread_id: str,
    name: str,
    arguments: dict[str, Any],
    *,
    photo_content_and_artifact: bool = False,
) -> Any:
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
                message = f"Xiaozhi device is connected, but it does not expose the requested tool: {detail}"
            else:
                message = "No Xiaozhi device is connected for this conversation."
            if photo_content_and_artifact:
                return _photo_error_content_and_artifact(message)
            return message
        if response.status_code == 403:
            detail = _response_detail(response)
            message = f"Device tool {name} is not allowed by the backend whitelist. {detail}"
            if photo_content_and_artifact:
                return _photo_error_content_and_artifact(message)
            return message
        response.raise_for_status()
        result = _extract_mcp_text(response.json())
        if photo_content_and_artifact and _is_photo_like_tool(name):
            return _uploaded_photo_content_and_artifact(thread_id, result)
        return result
    except httpx.HTTPError as exc:
        message = f"Failed to call Xiaozhi device tool: {exc}"
        if photo_content_and_artifact:
            return _photo_error_content_and_artifact(str(exc))
        return message


@tool(response_format="content_and_artifact")
def xiaozhi_take_photo(question: str, runtime: ToolRuntime) -> Any:
    """Take a photo with the connected Xiaozhi device and return the captured image.

    Args:
        question: The user's visual request that caused this photo capture.
    """

    thread_id = runtime_thread_id(runtime)
    result = _call_device_tool(
        thread_id,
        "self.camera.take_photo",
        {"question": question},
        photo_content_and_artifact=True,
    )
    if isinstance(result, tuple) and len(result) == 2:
        return result
    return _photo_error_content_and_artifact("Xiaozhi did not return an image payload.", result=result)


@tool
def xiaozhi_call_device_tool(name: str, arguments: dict[str, Any], runtime: ToolRuntime) -> Any:
    """Call a safe whitelisted MCP tool exposed by connected Xiaozhi hardware.

    Args:
        name: MCP tool name reported by the device.
        arguments: Tool arguments as a JSON object.
    """

    return _call_device_tool(runtime_thread_id(runtime), name, arguments or {})
