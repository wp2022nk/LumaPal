"""FastAPI routes for Xiaozhi hardware."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, File, Form, Header, HTTPException, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response, StreamingResponse

from content_builder.history import save_thread_history_snapshot
from content_builder.server.security import extract_request_token, token_is_valid
from content_builder.thread_storage import resolve_thread_file

from .audio import OPUS_CHANNELS, OPUS_FRAME_DURATION_MS, OPUS_OUTPUT_SAMPLE_RATE
from .constants import MAX_IMAGE_BYTES, XIAOZHI_DEFAULT_THREAD_ID, _is_photo_like_device_tool
from .network import _public_base_url_from_websocket, _public_ws_url
from .session import XiaozhiSession, session_manager
from .turn_service import _handle_device_audio, _handle_device_text
from .vision import _explain_image_sync, _save_vision_upload_sync

router = APIRouter(prefix="/api/xiaozhi", tags=["xiaozhi"])
compat_router = APIRouter(tags=["xiaozhi"])
logger = logging.getLogger(__name__)


def _require_pairing_token(
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header()] = None,
) -> None:
    headers = {"authorization": authorization or "", "x-api-key": x_api_key or ""}
    if not token_is_valid(extract_request_token(headers)):
        raise HTTPException(status_code=401, detail="Invalid pairing token")


def _extract_ws_token(websocket: WebSocket, query_token: str) -> str:
    if query_token:
        return query_token
    return extract_request_token(dict(websocket.headers))


def _vision_token_valid(
    *,
    authorization: str | None = None,
    x_api_key: str | None = None,
    token: str = "",
) -> bool:
    candidate = token or extract_request_token({"authorization": authorization or "", "x-api-key": x_api_key or ""})
    return token_is_valid(candidate)


def _json_line(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _root_callable(name: str, default):
    import content_builder.server.xiaozhi as xiaozhi_root

    return getattr(xiaozhi_root, name, default)


def _new_xiaozhi_thread_id(*, device_id: str = "", client_id: str = "") -> str:
    label = (device_id or client_id or "hardware").strip()
    safe_label = "".join(character if character.isalnum() or character in "-_" else "-" for character in label)
    safe_label = safe_label.strip("-_")[:32] or "hardware"
    return f"xiaozhi-{safe_label}-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"

@router.get("/ota")
@router.post("/ota")
async def get_ota_config(request: Request, token: str = Query("")) -> dict[str, Any]:
    if not await asyncio.to_thread(token_is_valid, token):
        raise HTTPException(status_code=401, detail="Invalid pairing token")
    thread_id = request.query_params.get("thread_id") or _new_xiaozhi_thread_id()
    ws_base = _public_ws_url(request)
    return {
        "websocket": {
            "url": f"{ws_base}/api/xiaozhi/v1/ws?thread_id={thread_id}",
            "token": token,
            "version": 1,
        },
        "server_time": {
            "timestamp": int(time.time() * 1000),
            "timezone_offset": -time.timezone // 60,
        },
        "firmware": None,
    }


@router.get("/v1/status", dependencies=[Depends(_require_pairing_token)])
async def get_status() -> dict[str, Any]:
    return {
        "sessions": [
            {
                "session_id": session.session_id,
                "thread_id": session.thread_id,
                "device_id": session.device_id,
                "client_id": session.client_id,
                "tools": sorted(session.tools),
            }
            for session in session_manager.by_session.values()
        ]
    }


@router.get("/v1/threads/{thread_id}/events")
async def stream_thread_events(
    thread_id: str,
    token: str = Query(""),
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header()] = None,
) -> StreamingResponse:
    headers = {"authorization": authorization or "", "x-api-key": x_api_key or ""}
    candidate = token or extract_request_token(headers)
    if not await asyncio.to_thread(token_is_valid, candidate):
        raise HTTPException(status_code=401, detail="Invalid pairing token")

    async def events():
        async with session_manager.subscribe(thread_id) as queue:
            yield _json_line("ready", {"thread_id": thread_id})
            while True:
                try:
                    event, payload = await asyncio.wait_for(queue.get(), timeout=20)
                    yield _json_line(event, payload)
                except asyncio.TimeoutError:
                    yield _json_line("ping", {"thread_id": thread_id, "time": int(time.time())})

    return StreamingResponse(events(), media_type="text/event-stream")


@router.post("/v1/sessions/{thread_id}/mcp/tools/call", dependencies=[Depends(_require_pairing_token)])
async def call_device_tool(thread_id: str, payload: Annotated[dict[str, Any], Body(default_factory=dict)]) -> dict[str, Any]:
    session = session_manager.active_for_thread(thread_id)
    if session is None:
        raise HTTPException(status_code=404, detail="No connected Xiaozhi device for this thread")
    name = str(payload.get("name") or "")
    arguments = payload.get("arguments") if isinstance(payload.get("arguments"), dict) else {}
    original_user_text = str(getattr(session, "current_user_transcript", "") or "")
    if original_user_text and name in {"self.camera.take_photo", "take_photo", "take_screenshot"}:
        arguments = {**arguments, "question": original_user_text}
    try:
        if _is_photo_like_device_tool(name) and isinstance(session, XiaozhiSession):
            return await session.call_photo_tool(name, arguments)
        return await session.call_tool(name, arguments)
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail=f"Xiaozhi device tool timed out: {name}") from exc


@compat_router.get("/mcp/vision/explain")
async def explain_vision_health() -> Response:
    return Response("MCP Vision interface is running", media_type="text/plain")


async def _handle_vision_upload(
    *,
    route_name: str,
    thread_id: str,
    token: str,
    authorization: str | None,
    x_api_key: str | None,
    file: UploadFile | None,
    question: str,
) -> JSONResponse:
    if not await asyncio.to_thread(_vision_token_valid, authorization=authorization, x_api_key=x_api_key, token=token):
        raise HTTPException(status_code=401, detail="Invalid vision token")
    if file is None:
        raise HTTPException(status_code=400, detail="Missing image file")
    content = await file.read(MAX_IMAGE_BYTES + 1)
    upload_payload, _image_path = await asyncio.to_thread(_save_vision_upload_sync, thread_id, content, question)
    active_session = session_manager.active_for_thread(thread_id)
    if active_session is not None:
        await active_session.remember_photo_upload(upload_payload)
    logger.info("Xiaozhi photo uploaded via %s thread=%s file=%s", route_name, thread_id, upload_payload["file"]["path"])
    await asyncio.to_thread(
        save_thread_history_snapshot,
        thread_id,
        event={"type": "xiaozhi_photo_upload", "question": question, "file": upload_payload["file"]},
    )
    await session_manager.publish(thread_id, "photo_uploaded", upload_payload)
    return JSONResponse(upload_payload)


@compat_router.post("/mcp/vision/explain")
async def explain_vision_compat(
    thread_id: str = Query(XIAOZHI_DEFAULT_THREAD_ID),
    token: str = Query(""),
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header()] = None,
    file: Annotated[UploadFile | None, File()] = None,
    image: Annotated[UploadFile | None, File()] = None,
    question: Annotated[str, Form()] = "",
) -> JSONResponse:
    return await _handle_vision_upload(
        route_name="mcp/vision/explain",
        thread_id=thread_id,
        token=token,
        authorization=authorization,
        x_api_key=x_api_key,
        file=file or image,
        question=question,
    )


@router.post("/v1/vision/explain")
async def explain_vision(
    thread_id: str = Query(XIAOZHI_DEFAULT_THREAD_ID),
    token: str = Query(""),
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header()] = None,
    file: Annotated[UploadFile | None, File()] = None,
    image: Annotated[UploadFile | None, File()] = None,
    question: Annotated[str, Form()] = "",
) -> JSONResponse:
    return await _handle_vision_upload(
        route_name="api/vision/explain",
        thread_id=thread_id,
        token=token,
        authorization=authorization,
        x_api_key=x_api_key,
        file=file or image,
        question=question,
    )


@router.post("/v1/vision/upload")
async def upload_vision(
    thread_id: str = Query(XIAOZHI_DEFAULT_THREAD_ID),
    token: str = Query(""),
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header()] = None,
    file: Annotated[UploadFile | None, File()] = None,
    image: Annotated[UploadFile | None, File()] = None,
    question: Annotated[str, Form()] = "",
) -> JSONResponse:
    if not await asyncio.to_thread(_vision_token_valid, authorization=authorization, x_api_key=x_api_key, token=token):
        raise HTTPException(status_code=401, detail="Invalid vision token")
    file = file or image
    if file is None:
        raise HTTPException(status_code=400, detail="Missing image file")
    content = await file.read(MAX_IMAGE_BYTES + 1)
    payload, _image_path = await asyncio.to_thread(_save_vision_upload_sync, thread_id, content, question)
    active_session = session_manager.active_for_thread(thread_id)
    if active_session is not None:
        await active_session.remember_photo_upload(payload)
    logger.info("Xiaozhi photo uploaded via upload thread=%s file=%s", thread_id, payload["file"]["path"])
    await session_manager.publish(thread_id, "photo_uploaded", payload)
    return JSONResponse(payload)


@router.post("/v1/vision/analyze", dependencies=[Depends(_require_pairing_token)])
async def analyze_uploaded_vision(payload: Annotated[dict[str, Any], Body(default_factory=dict)]) -> JSONResponse:
    upload_thread_id = str(payload.get("thread_id") or XIAOZHI_DEFAULT_THREAD_ID)
    question = str(payload.get("question") or "请描述这张图片。")
    file_payload = payload.get("file") if isinstance(payload.get("file"), dict) else {}
    relative_path = str(payload.get("path") or file_payload.get("path") or "")
    if not relative_path:
        raise HTTPException(status_code=400, detail="Missing uploaded image path")
    try:
        image_path = resolve_thread_file(upload_thread_id, relative_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not image_path.is_file():
        raise HTTPException(status_code=404, detail="Uploaded image file was not found")

    result_text = await asyncio.to_thread(_root_callable("_explain_image_sync", _explain_image_sync), question, image_path)
    result = {
        "success": True,
        "result": result_text,
        "thread_id": upload_thread_id,
        "question": question,
        "file": {"path": relative_path, "mime_type": str(file_payload.get("mime_type") or "image/jpeg")},
    }
    await asyncio.to_thread(
        save_thread_history_snapshot,
        upload_thread_id,
        event={"type": "xiaozhi_photo", "question": question, "file": result["file"], "result": result_text},
    )
    await session_manager.publish(upload_thread_id, "photo", result)
    return JSONResponse(result)


@router.websocket("/v1/ws")
async def websocket_endpoint(websocket: WebSocket, thread_id: str = Query(""), token: str = Query("")) -> None:
    await websocket.accept()
    if not await asyncio.to_thread(token_is_valid, _extract_ws_token(websocket, token)):
        await websocket.close(code=4401, reason="Invalid pairing token")
        return

    device_id = websocket.headers.get("Device-Id", "")
    client_id = websocket.headers.get("Client-Id", "")
    thread_id = thread_id or _new_xiaozhi_thread_id(device_id=device_id, client_id=client_id)
    protocol_version = websocket.headers.get("Protocol-Version", "1")
    try:
        version = int(protocol_version)
    except ValueError:
        version = 1

    session = XiaozhiSession(
        websocket=websocket,
        thread_id=thread_id,
        device_id=device_id,
        client_id=client_id,
        version=version,
    )
    session_manager.register(session)
    await session.publish(
        "connected",
        {"thread_id": thread_id, "session_id": session.session_id, "device_id": device_id, "client_id": client_id},
    )

    try:
        initial = await websocket.receive_text()
        hello = json.loads(initial)
        if hello.get("type") != "hello":
            await websocket.close(code=4400, reason="Expected hello")
            return
        await session.send_json(
            {
                "type": "hello",
                "transport": "websocket",
                "audio_params": {
                    "format": "opus",
                    "sample_rate": OPUS_OUTPUT_SAMPLE_RATE,
                    "channels": OPUS_CHANNELS,
                    "frame_duration": OPUS_FRAME_DURATION_MS,
                },
            }
        )
        asyncio.create_task(session.initialize_mcp(_public_base_url_from_websocket(websocket)))

        while True:
            message = await websocket.receive()
            if "bytes" in message and message["bytes"] is not None:
                audio_frame = session.unpack_audio(message["bytes"])
                if audio_frame:
                    if session.manual_listen_started:
                        session.audio_frames.append(audio_frame)
                    else:
                        await _handle_device_audio(session, audio_frame)
                continue
            if "text" not in message or message["text"] is None:
                continue
            await _handle_device_text(session, message["text"])
    except WebSocketDisconnect as exc:
        await session.publish("disconnected", {"thread_id": thread_id, "session_id": session.session_id, "reason": f"disconnect:{exc.code}"})
    except Exception as exc:
        await session.publish("disconnected", {"thread_id": thread_id, "session_id": session.session_id, "reason": str(exc)})
    finally:
        session_manager.unregister(session)
