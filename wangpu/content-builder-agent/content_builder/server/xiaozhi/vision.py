"""Vision upload and analysis helpers for Xiaozhi."""

from __future__ import annotations

import base64
import binascii
import re
import uuid
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from langchain_core.messages import HumanMessage

from content_builder.config import create_qwen_model, load_main_config
from content_builder.multimodal import build_user_content
from content_builder.thread_storage import thread_paths

from .constants import MAX_IMAGE_BYTES

_DATA_IMAGE_URI_RE = re.compile(r"^data:(image/[A-Za-z0-9.+-]+);([^,]*,)(.*)$", re.DOTALL)
_IMAGE_EXTENSIONS = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
    "image/bmp": "bmp",
}


def _image_extension(mime_type: str) -> str:
    if mime_type in _IMAGE_EXTENSIONS:
        return _IMAGE_EXTENSIONS[mime_type]
    subtype = mime_type.partition("/")[2].partition("+")[0]
    extension = "".join(character for character in subtype.lower() if character.isalnum())
    return extension[:12] or "img"


def _save_vision_upload_sync(thread_id: str, content: bytes, question: str) -> tuple[dict[str, Any], Path]:
    if len(content) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="Image exceeds the 20 MB upload limit")
    if not content:
        raise HTTPException(status_code=400, detail="Empty image")

    paths = thread_paths(thread_id)
    image_path = paths.uploads / "images" / f"{uuid.uuid4().hex}.jpg"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(content)
    virtual_path = f"uploads/images/{image_path.name}"
    payload = {
        "success": True,
        "thread_id": thread_id,
        "question": question,
        "file": {"path": virtual_path, "mime_type": "image/jpeg"},
    }
    return payload, image_path


def _save_vision_data_uri_sync(
    thread_id: str,
    data_uri: str,
    question: str,
    metadata: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], Path]:
    match = _DATA_IMAGE_URI_RE.match(str(data_uri or "").strip())
    if not match:
        raise HTTPException(status_code=400, detail="Invalid image data URI")

    mime_type = match.group(1).lower()
    parameters = match.group(2).lower()
    encoded = "".join(match.group(3).split())
    if "base64" not in parameters:
        raise HTTPException(status_code=400, detail="Image data URI must be base64 encoded")
    if not encoded:
        raise HTTPException(status_code=400, detail="Empty image")

    try:
        content = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Invalid base64 image data") from exc
    if len(content) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="Image exceeds the 20 MB upload limit")
    if not content:
        raise HTTPException(status_code=400, detail="Empty image")

    paths = thread_paths(thread_id)
    image_path = paths.uploads / "images" / f"{uuid.uuid4().hex}.{_image_extension(mime_type)}"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(content)
    virtual_path = f"uploads/images/{image_path.name}"
    payload: dict[str, Any] = {
        "success": True,
        "thread_id": thread_id,
        "question": question,
        "file": {"path": virtual_path, "mime_type": mime_type},
    }
    if metadata:
        payload["metadata"] = metadata
    return payload, image_path


def _explain_image_sync(question: str, image_path: Path) -> str:
    config = load_main_config()
    model = create_qwen_model(config.model)
    content = build_user_content(question, [image_path])
    response = model.invoke([HumanMessage(content=content)])
    result = getattr(response, "content", response)
    if isinstance(result, list):
        return "\n".join(str(item.get("text", "")) for item in result if isinstance(item, dict)).strip()
    return str(result).strip()
