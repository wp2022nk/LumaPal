"""FastAPI routes consumed by the Android LAN companion app."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import mimetypes
import os
import tempfile
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote

import yaml
from fastapi import (
    Body,
    Depends,
    FastAPI,
    File,
    Header,
    HTTPException,
    Query,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi import Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from content_builder.config import DEFAULT_SECRETS_FILE, WORKSPACE_DIR, load_main_config
from content_builder.history import history_root, save_thread_history_snapshot
from content_builder.server.security import (
    extract_request_token,
    make_preview_token,
    print_pairing_token_hint_once,
    preview_token_is_valid,
    token_is_valid,
)
from content_builder.server.events import app_events, json_sse_line
from content_builder.thread_storage import resolve_thread_file, thread_paths
from content_builder.server.xiaozhi import compat_router as xiaozhi_compat_router
from content_builder.server.xiaozhi import router as xiaozhi_router


logger = logging.getLogger(__name__)
app = FastAPI(title="Content Builder Android API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(xiaozhi_router)
app.include_router(xiaozhi_compat_router)
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
HISTORY_ARTIFACT_SUFFIXES = IMAGE_SUFFIXES | {".html", ".htm", ".pdf", ".md", ".json", ".txt", ".wav", ".mp3"}
TEXT_SUFFIXES = {
    ".css",
    ".html",
    ".js",
    ".json",
    ".jsx",
    ".md",
    ".mjs",
    ".py",
    ".svg",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}
_asr_manager: Any = None
_asr_initialization_error: str | None = None
_asr_lock = threading.Lock()


@app.exception_handler(ValueError)
async def reject_invalid_path(_request: Request, error: ValueError) -> JSONResponse:
    """Turn invalid thread paths into explicit client errors."""

    return JSONResponse(status_code=400, content={"detail": str(error)})


class KeyUpdate(BaseModel):
    qwen: str | None = None
    dashscope: str | None = None
    tavily: str | None = None


def _ensure_asr_manager_sync(*, force_retry: bool = False) -> Any:
    global _asr_initialization_error, _asr_manager
    if _asr_manager is not None:
        return _asr_manager
    with _asr_lock:
        if _asr_manager is not None:
            return _asr_manager
        if _asr_initialization_error and not force_retry:
            raise RuntimeError(_asr_initialization_error)
        try:
            config = load_main_config()
            if not config.voice.enabled:
                raise RuntimeError("Voice is disabled in main_agent.yaml")
            from content_builder.voice.asr import ASRManager

            _asr_manager = ASRManager(config.voice.asr)
            _asr_initialization_error = None
            return _asr_manager
        except (FileNotFoundError, RuntimeError, ValueError) as error:
            _asr_initialization_error = str(error)
            raise


def preload_asr_on_import() -> None:
    try:
        _ensure_asr_manager_sync(force_retry=True)
    except (FileNotFoundError, RuntimeError, ValueError) as error:
        logger.warning("FunASR preload failed: %s", error)


def _require_pairing_token(
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header()] = None,
) -> None:
    headers = {"authorization": authorization or "", "x-api-key": x_api_key or ""}
    if not token_is_valid(extract_request_token(headers)):
        raise HTTPException(status_code=401, detail="Invalid pairing token")


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def _secret_path() -> Path:
    return Path(os.environ.get("CONTENT_BUILDER_SECRETS_FILE", DEFAULT_SECRETS_FILE)).resolve()


def _key_status() -> dict[str, bool]:
    data = _read_yaml(_secret_path())
    return {
        name: bool(str((data.get(name) or {}).get("api_key") or "").strip())
        for name in ("qwen", "dashscope", "tavily")
    }


def _write_keys(update: KeyUpdate) -> None:
    path = _secret_path()
    data = _read_yaml(path)
    for name, value in update.model_dump().items():
        if value is None:
            continue
        section = data.setdefault(name, {})
        if not isinstance(section, dict):
            section = {}
            data[name] = section
        cleaned = value.strip()
        if cleaned:
            section["api_key"] = cleaned
        else:
            section.pop("api_key", None)
        env_name = {"qwen": "QWEN_API_KEY", "dashscope": "DASHSCOPE_API_KEY", "tavily": "TAVILY_API_KEY"}[name]
        if cleaned:
            os.environ[env_name] = cleaned
        else:
            os.environ.pop(env_name, None)

    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=path.parent, encoding="utf-8") as handle:
        yaml.safe_dump(data, handle, allow_unicode=True, sort_keys=True)
        temporary_path = Path(handle.name)
    temporary_path.replace(path)
    from content_builder.agent_factory import clear_content_writer_cache

    clear_content_writer_cache()


def _artifact_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return "image"
    if suffix == ".pdf":
        return "pdf"
    if suffix in {".html", ".htm"}:
        return "html"
    if suffix in TEXT_SUFFIXES:
        return "text"
    return "download"


def _entry(thread_id: str, physical_path: Path, virtual_path: str) -> dict[str, Any]:
    mime_type = mimetypes.guess_type(physical_path.name)[0] or "application/octet-stream"
    signed_token = make_preview_token(thread_id, virtual_path)
    encoded_path = quote(virtual_path, safe="/")
    return {
        "name": physical_path.name,
        "path": virtual_path,
        "size": physical_path.stat().st_size,
        "modified_at": physical_path.stat().st_mtime,
        "mime_type": mime_type,
        "kind": _artifact_kind(physical_path),
        "preview_url": f"/api/content-builder/preview/{thread_id}/{signed_token}/{encoded_path}",
    }


def _virtual_file(thread_id: str, virtual_path: str) -> Path:
    return resolve_thread_file(thread_id, virtual_path)


def _save_upload(thread_id: str, virtual_path: str, content: bytes) -> Path:
    target = _virtual_file(thread_id, virtual_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return target


def _artifact_entries(thread_id: str) -> list[dict[str, Any]]:
    paths = thread_paths(thread_id)
    entries: list[dict[str, Any]] = []
    for namespace, root in (("artifacts", paths.artifacts), ("games", paths.games)):
        for item in root.rglob("*"):
            if item.is_file():
                entries.append(_entry(thread_id, item, f"{namespace}/{item.relative_to(root).as_posix()}"))
    entries.sort(key=lambda item: item["modified_at"], reverse=True)
    return entries


def _history_artifact_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return "image"
    if suffix == ".pdf":
        return "pdf"
    if suffix in {".html", ".htm"}:
        return "html"
    if suffix in TEXT_SUFFIXES:
        return "text"
    return "download"


def _history_artifact_category(path: Path, virtual_path: str) -> str:
    normalized = virtual_path.replace("\\", "/")
    suffix = path.suffix.lower()
    if "growth-report/" in normalized:
        return "growth_report"
    if "/games/" in normalized or normalized.startswith("roadshow-final-products/game/"):
        return "game"
    if "storybook/" in normalized or "/storybooks/" in normalized:
        if suffix == ".html" and (path.parent / "audio").is_dir():
            return "audiobook"
        return "storybook"
    if suffix in IMAGE_SUFFIXES:
        return "image"
    return "document"


def _history_artifact_title(path: Path, virtual_path: str) -> str:
    category = _history_artifact_category(path, virtual_path)
    if category == "growth_report":
        return "成长轨迹报告"
    if category == "game":
        return path.parent.name if path.name == "index.html" else path.stem
    if category == "audiobook":
        return path.parent.name if path.name == "book.html" else path.stem
    if category == "storybook":
        return path.parent.name if path.name in {"book.html", "book.json"} else path.stem
    return path.stem or path.name


def _history_artifact_date(path: Path, virtual_path: str) -> str:
    parts = virtual_path.replace("\\", "/").split("/")
    if parts and parts[0] == "history" and len(parts) > 1:
        try:
            datetime.strptime(parts[1], "%Y-%m-%d")
            return parts[1]
        except ValueError:
            pass
    return datetime.fromtimestamp(path.stat().st_mtime).astimezone().date().isoformat()


def _history_preview_url(virtual_path: str) -> str:
    signed_token = make_preview_token("history", virtual_path)
    encoded_path = quote(virtual_path, safe="/")
    return f"/api/content-builder/history-preview/{signed_token}/{encoded_path}"


def _history_artifact_entry(path: Path, virtual_path: str) -> dict[str, Any]:
    return {
        "name": path.name,
        "title": _history_artifact_title(path, virtual_path),
        "path": virtual_path,
        "date": _history_artifact_date(path, virtual_path),
        "source": "roadshow" if virtual_path.startswith("roadshow-final-products/") else "history",
        "category": _history_artifact_category(path, virtual_path),
        "size": path.stat().st_size,
        "modified_at": path.stat().st_mtime,
        "mime_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        "kind": _history_artifact_kind(path),
        "preview_url": _history_preview_url(virtual_path),
    }


def _iter_history_artifact_files() -> list[tuple[Path, str]]:
    entries: list[tuple[Path, str]] = []
    roots = [
        (history_root(), "history"),
        ((WORKSPACE_DIR / "roadshow-final-products").resolve(), "roadshow-final-products"),
    ]
    for root, prefix in roots:
        if not root.exists():
            continue
        for item in root.rglob("*"):
            if not item.is_file() or item.suffix.lower() not in HISTORY_ARTIFACT_SUFFIXES:
                continue
            relative = item.relative_to(root)
            if item.name == "history.json" or "memory" in relative.parts:
                continue
            entries.append((item, f"{prefix}/{relative.as_posix()}"))
    return entries


def _history_artifact_entries(start_date: str | None = None, end_date: str | None = None) -> list[dict[str, Any]]:
    entries = [_history_artifact_entry(path, virtual_path) for path, virtual_path in _iter_history_artifact_files()]
    if start_date:
        entries = [entry for entry in entries if str(entry["date"]) >= start_date]
    if end_date:
        entries = [entry for entry in entries if str(entry["date"]) <= end_date]
    entries.sort(key=lambda item: (str(item["date"]), float(item["modified_at"])), reverse=True)
    return entries


def _history_preview_target(virtual_path: str) -> Path:
    normalized = virtual_path.replace("\\", "/").strip("/")
    if normalized.startswith("/") or "/../" in f"/{normalized}/" or normalized.startswith("../"):
        raise HTTPException(status_code=400, detail="Invalid history artifact path")
    if normalized.startswith("history/"):
        root = history_root()
        target = (root / normalized.removeprefix("history/")).resolve()
    elif normalized.startswith("roadshow-final-products/"):
        root = (WORKSPACE_DIR / "roadshow-final-products").resolve()
        target = (WORKSPACE_DIR / normalized).resolve()
    else:
        raise HTTPException(status_code=400, detail="History artifact path must be under history/ or roadshow-final-products/")
    if target != root and root not in target.parents:
        raise HTTPException(status_code=400, detail="Invalid history artifact path")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return target


def _sandbox_entries(thread_id: str) -> list[dict[str, Any]]:
    paths = thread_paths(thread_id)
    entries: list[dict[str, Any]] = []
    for namespace, root in (
        ("workspace", paths.workspace),
        ("artifacts", paths.artifacts),
        ("games", paths.games),
        ("uploads", paths.uploads),
    ):
        entries.append({"name": namespace, "path": namespace, "type": "directory", "size": 0})
        for item in root.rglob("*"):
            if "node_modules" in item.parts:
                continue
            entries.append(
                {
                    "name": item.name,
                    "path": f"{namespace}/{item.relative_to(root).as_posix()}",
                    "type": "directory" if item.is_dir() else "file",
                    "size": 0 if item.is_dir() else item.stat().st_size,
                }
            )
    return entries


def _read_sandbox_text(thread_id: str, virtual_path: str) -> str:
    target = _virtual_file(thread_id, virtual_path)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    if target.suffix.lower() not in TEXT_SUFFIXES:
        raise HTTPException(status_code=415, detail="Use the preview URL for binary files")
    return target.read_text(encoding="utf-8", errors="replace")


def _preview_target(thread_id: str, virtual_path: str) -> Path:
    target = _virtual_file(thread_id, virtual_path)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return target


@app.get("/api/content-builder/pairing/status")
async def get_pairing_status(
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header()] = None,
) -> dict[str, bool]:
    """Check a local pairing token before the protected stream client mounts."""

    headers = {"authorization": authorization or "", "x-api-key": x_api_key or ""}
    candidate = extract_request_token(headers)
    return {"paired": await asyncio.to_thread(token_is_valid, candidate)}


@app.get("/api/content-builder/settings/keys", dependencies=[Depends(_require_pairing_token)])
async def get_key_settings() -> dict[str, dict[str, bool]]:
    return {"configured": await asyncio.to_thread(_key_status)}


@app.put("/api/content-builder/settings/keys", dependencies=[Depends(_require_pairing_token)])
async def put_key_settings(update: KeyUpdate) -> dict[str, dict[str, bool]]:
    await asyncio.to_thread(_write_keys, update)
    return {"configured": await asyncio.to_thread(_key_status)}


@app.post("/api/content-builder/settings/keys/verify", dependencies=[Depends(_require_pairing_token)])
async def verify_key_settings() -> dict[str, Any]:
    configured = await asyncio.to_thread(_key_status)
    return {
        "configured": configured,
        "ready": configured["qwen"],
        "message": "Qwen key is configured." if configured["qwen"] else "Configure a Qwen API key first.",
    }


@app.post("/api/content-builder/threads/{thread_id}/uploads/images", dependencies=[Depends(_require_pairing_token)])
async def upload_image(thread_id: str, image: Annotated[UploadFile, File()]) -> dict[str, Any]:
    suffix = Path(image.filename or "").suffix.lower()
    if suffix not in IMAGE_SUFFIXES:
        raise HTTPException(status_code=415, detail="Upload a PNG, JPEG, GIF, or WebP image")
    content = await image.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Image exceeds the 20 MB upload limit")
    relative_path = f"uploads/images/{uuid.uuid4().hex}{suffix}"
    target = await asyncio.to_thread(_save_upload, thread_id, relative_path, content)
    entry = await asyncio.to_thread(_entry, thread_id, target, relative_path)
    await asyncio.to_thread(
        save_thread_history_snapshot,
        thread_id,
        event={"type": "upload_image", "file": entry},
    )
    await app_events.publish("artifact_updated", {"entry": entry}, thread_id=thread_id)
    await app_events.publish("history_snapshot", {"reason": "upload_image"}, thread_id=thread_id)
    return entry


@app.post("/api/content-builder/threads/{thread_id}/voice/asr", dependencies=[Depends(_require_pairing_token)])
async def transcribe_audio(thread_id: str, audio: Annotated[UploadFile, File()]) -> dict[str, str]:
    suffix = Path(audio.filename or "recording.wav").suffix.lower() or ".wav"
    content = await audio.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Audio exceeds the 20 MB upload limit")
    target = await asyncio.to_thread(_save_upload, thread_id, f"uploads/voice/{uuid.uuid4().hex}{suffix}", content)
    try:
        asr_manager = await asyncio.to_thread(_ensure_asr_manager_sync, force_retry=True)
        transcript = await asr_manager.recognize_file(target, session_id=thread_id)
    except (FileNotFoundError, RuntimeError, ValueError) as error:
        raise HTTPException(status_code=503, detail=f"语音识别不可用：{error}") from error
    return {"transcript": transcript}


@app.post("/api/content-builder/threads/{thread_id}/history/snapshot", dependencies=[Depends(_require_pairing_token)])
async def save_history_snapshot(
    thread_id: str,
    snapshot: Annotated[dict[str, Any], Body(default_factory=dict)],
) -> dict[str, str]:
    messages = snapshot.get("messages")
    metadata = snapshot.get("metadata")
    growth_events = snapshot.get("growth_events") if "growth_events" in snapshot else None
    artifact_refs = snapshot.get("artifact_refs") if "artifact_refs" in snapshot else None
    profile_updates = snapshot.get("profile_updates") if "profile_updates" in snapshot else None
    path = await asyncio.to_thread(
        save_thread_history_snapshot,
        thread_id,
        messages=messages if isinstance(messages, list) else [],
        mode=str(snapshot.get("mode") or "replace"),
        metadata=metadata if isinstance(metadata, dict) else {},
        growth_events=growth_events if isinstance(growth_events, list) else None,
        artifact_refs=artifact_refs if isinstance(artifact_refs, list) else None,
        profile_updates=profile_updates if isinstance(profile_updates, dict) else None,
    )
    if isinstance(messages, list):
        source = str(metadata.get("source") or "") if isinstance(metadata, dict) else ""
        for message in messages[-2:]:
            if isinstance(message, dict):
                await app_events.publish("message_appended", {"message": message, "source": source}, thread_id=thread_id)
    await app_events.publish("thread_updated", {"updated_at": path.stat().st_mtime, "source": str(metadata.get("source") or "") if isinstance(metadata, dict) else ""}, thread_id=thread_id)
    await app_events.publish("history_snapshot", {"path": str(path)}, thread_id=thread_id)
    return {"thread_id": thread_id, "path": str(path)}


@app.get("/api/content-builder/events")
async def stream_app_events(
    token: str = Query(""),
    thread_id: str = Query("*"),
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header()] = None,
) -> StreamingResponse:
    headers = {"authorization": authorization or "", "x-api-key": x_api_key or ""}
    candidate = token or extract_request_token(headers)
    if not await asyncio.to_thread(token_is_valid, candidate):
        raise HTTPException(status_code=401, detail="Invalid pairing token")

    async def events():
        async with app_events.subscribe(thread_id) as queue:
            yield json_sse_line("ready", {"thread_id": thread_id})
            while True:
                try:
                    event, payload = await asyncio.wait_for(queue.get(), timeout=20)
                    yield json_sse_line(event, payload)
                except asyncio.TimeoutError:
                    yield json_sse_line("ping", {"thread_id": thread_id})

    return StreamingResponse(events(), media_type="text/event-stream")


@app.get("/api/content-builder/threads/{thread_id}/artifacts", dependencies=[Depends(_require_pairing_token)])
async def list_artifacts(thread_id: str) -> dict[str, Any]:
    return {"thread_id": thread_id, "entries": await asyncio.to_thread(_artifact_entries, thread_id)}


@app.get("/api/content-builder/history/artifacts", dependencies=[Depends(_require_pairing_token)])
async def list_history_artifacts(
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
) -> dict[str, Any]:
    entries = await asyncio.to_thread(_history_artifact_entries, start_date, end_date)
    return {"entries": entries}


@app.get("/api/content-builder/threads/{thread_id}/sandbox/tree", dependencies=[Depends(_require_pairing_token)])
async def list_sandbox_tree(thread_id: str) -> dict[str, Any]:
    return {"thread_id": thread_id, "entries": await asyncio.to_thread(_sandbox_entries, thread_id)}


@app.get("/api/content-builder/threads/{thread_id}/sandbox/file", dependencies=[Depends(_require_pairing_token)])
async def read_sandbox_file(thread_id: str, path: Annotated[str, Query()]) -> dict[str, Any]:
    return {"path": path, "content": await asyncio.to_thread(_read_sandbox_text, thread_id, path)}


@app.get("/api/content-builder/preview/{thread_id}/{token}/{path:path}")
async def preview_file(thread_id: str, token: str, path: str) -> FileResponse:
    if not await asyncio.to_thread(preview_token_is_valid, thread_id, path, token):
        raise HTTPException(status_code=401, detail="Preview link expired or invalid")
    target = await asyncio.to_thread(_preview_target, thread_id, path)
    return FileResponse(target, filename=target.name, content_disposition_type="inline")


@app.get("/api/content-builder/history-preview/{token}/{path:path}")
async def preview_history_file(token: str, path: str) -> FileResponse:
    if not await asyncio.to_thread(preview_token_is_valid, "history", path, token):
        raise HTTPException(status_code=401, detail="Preview link expired or invalid")
    target = await asyncio.to_thread(_history_preview_target, path)
    return FileResponse(target, filename=target.name, content_disposition_type="inline")


class WebSocketPCMPlayer:
    """VoiceResponseSpeaker output adapter that forwards PCM to the phone."""

    def __init__(self, websocket: WebSocket, *, sample_rate: int, channels: int, sample_width: int) -> None:
        self.websocket = websocket
        self.sample_rate = sample_rate
        self.channels = channels
        self.sample_width = sample_width

    async def start(self) -> None:
        await self.websocket.send_json(
            {
                "type": "ready",
                "sample_rate": self.sample_rate,
                "channels": self.channels,
                "sample_width": self.sample_width,
            }
        )

    async def enqueue_pcm(self, pcm_data: bytes, *, segment_start: bool = False) -> None:
        if segment_start:
            await self.websocket.send_json({"type": "segment_start"})
        await self.websocket.send_bytes(pcm_data)

    async def mark_segment_end(self) -> None:
        await self.websocket.send_json({"type": "segment_end"})

    async def send_emotion(self, emotion: dict[str, str | float]) -> None:
        await self.websocket.send_json({"type": "emotion", **emotion})

    async def drain(self) -> None:
        return None

    async def close(self) -> None:
        return None


@app.websocket("/api/content-builder/threads/{thread_id}/voice/tts")
async def stream_tts(websocket: WebSocket, thread_id: str, token: str = Query("")) -> None:
    await websocket.accept()
    if not await asyncio.to_thread(token_is_valid, token):
        await websocket.close(code=4401, reason="Invalid pairing token")
        return

    speaker: Any = None
    try:
        from content_builder.voice.console import ensure_voice_ready
        from content_builder.voice.tts import VoiceResponseSpeaker

        config = await asyncio.to_thread(load_main_config)
        await asyncio.to_thread(ensure_voice_ready, config)
        player = WebSocketPCMPlayer(
            websocket,
            sample_rate=config.voice.tts.sample_rate,
            channels=config.voice.tts.channels,
            sample_width=config.voice.tts.sample_width,
        )
        speaker = VoiceResponseSpeaker(config.voice, config.secrets, pcm_player=player)
        await speaker.start()
        while True:
            message = await websocket.receive_json()
            message_type = message.get("type")
            if message_type == "text":
                await speaker.feed_token(str(message.get("text") or ""))
            elif message_type == "flush":
                await speaker.flush()
                await websocket.send_json({"type": "complete"})
                return
            elif message_type == "cancel":
                await websocket.send_json({"type": "cancelled"})
                return
    except WebSocketDisconnect:
        return
    except Exception as exc:
        await websocket.send_json({"type": "error", "message": str(exc)})
    finally:
        if speaker is not None:
            await speaker.close()
        with contextlib.suppress(Exception):
            await websocket.close()


print_pairing_token_hint_once()
preload_asr_on_import()
