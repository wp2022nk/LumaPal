"""Text-to-speech artifact tool backed by configured Qwen TTS settings."""

from __future__ import annotations

import base64
import struct
from pathlib import Path
from typing import Any

import dashscope
from langchain.tools import ToolRuntime
from langchain_core.tools import tool

from content_builder.config import load_main_config
from content_builder.tools.image import output_root


ALLOWED_AUDIO_SUFFIXES = {".wav"}
MAX_TTS_CHARS = 2000
DASHSCOPE_API_URL = "https://dashscope.aliyuncs.com/api/v1"


def resolve_audio_output_path(output_path: str, *, root: Path | None = None) -> Path:
    """Resolve an audio target while restricting it to the output workspace."""

    raw_path = str(output_path).strip().strip("\"'")
    if not raw_path:
        raise ValueError("output_path must not be empty")

    root = (root or output_root()).resolve()
    normalized = raw_path.replace("\\", "/")
    if normalized == "/output" or normalized.startswith("/output/"):
        relative = normalized.removeprefix("/output").lstrip("/")
        target = (root / relative).resolve()
    else:
        path = Path(raw_path)
        target = path.resolve() if path.is_absolute() else (root / path).resolve()

    if target != root and root not in target.parents:
        raise ValueError("output_path must be located under /output/")
    if target.suffix.lower() not in ALLOWED_AUDIO_SUFFIXES:
        allowed = ", ".join(sorted(ALLOWED_AUDIO_SUFFIXES))
        raise ValueError(f"output_path must end with one of: {allowed}")
    if target == root:
        raise ValueError("output_path must identify an audio file under /output/")
    return target


def _error_path_for(output_path: Path) -> Path:
    return output_path.with_name(f"{output_path.stem}-error.txt")


def _create_wav_header(
    pcm_data_size: int,
    *,
    sample_rate: int,
    channels: int,
    sample_width: int,
) -> bytes:
    byte_rate = sample_rate * channels * sample_width
    block_align = channels * sample_width
    return b"".join(
        [
            b"RIFF",
            struct.pack("<I", pcm_data_size + 36),
            b"WAVE",
            b"fmt ",
            struct.pack("<I", 16),
            struct.pack("<H", 1),
            struct.pack("<H", channels),
            struct.pack("<I", sample_rate),
            struct.pack("<I", byte_rate),
            struct.pack("<H", block_align),
            struct.pack("<H", sample_width * 8),
            b"data",
            struct.pack("<I", pcm_data_size),
        ]
    )


def _response_error(chunk: Any) -> str:
    status_code = getattr(chunk, "status_code", None)
    code = getattr(chunk, "code", None)
    message = getattr(chunk, "message", None)
    return f"status_code={status_code}, code={code}, message={message}"


def _synthesize_wav(text: str, output_path: Path) -> int:
    config = load_main_config()
    tts = config.voice.tts
    api_key = config.secrets.dashscope_api_key or config.secrets.qwen_api_key
    if not api_key:
        raise RuntimeError("DASHSCOPE_API_KEY or QWEN_API_KEY is required for TTS generation")

    dashscope.base_http_api_url = DASHSCOPE_API_URL
    response = dashscope.MultiModalConversation.call(
        api_key=api_key,
        model=tts.model,
        text=text,
        voice=tts.voice,
        language_type=tts.language_type,
        stream=True,
    )

    pcm_chunks: list[bytes] = []
    last_chunk = None
    for chunk in response:
        last_chunk = chunk
        status_code = getattr(chunk, "status_code", None)
        if status_code is not None and status_code != 200:
            raise RuntimeError(f"DashScope streaming TTS failed: {_response_error(chunk)}")

        output = getattr(chunk, "output", None)
        audio = getattr(output, "audio", None) if output is not None else None
        data = getattr(audio, "data", None) if audio is not None else None
        if data:
            pcm_chunk = base64.b64decode(data)
            if pcm_chunk:
                pcm_chunks.append(pcm_chunk)

        if getattr(output, "finish_reason", None) == "stop":
            break

    pcm_data = b"".join(pcm_chunks)
    if not pcm_data:
        detail = _response_error(last_chunk) if last_chunk is not None else "no response chunks"
        raise RuntimeError(f"TTS provider returned no audio data ({detail})")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    wav_header = _create_wav_header(
        len(pcm_data),
        sample_rate=tts.sample_rate,
        channels=tts.channels,
        sample_width=tts.sample_width,
    )
    output_path.write_bytes(wav_header + pcm_data)
    return output_path.stat().st_size


@tool
def generate_tts_audio(
    text: str,
    output_path: str,
    runtime: ToolRuntime,
) -> str:
    """Generate a local WAV narration file for a user artifact.

    Parameters:
        text: The exact read-aloud text to synthesize.
        output_path: Target WAV path below ``/output/``, for example
            ``/output/storybooks/moon-trip/audio/page-01.wav``.
    """

    narration = str(text or "").strip()
    if not narration:
        return "TTS generation failed; text must not be empty."
    if len(narration) > MAX_TTS_CHARS:
        return f"TTS generation failed; text is too long ({len(narration)} chars, max {MAX_TTS_CHARS})."

    try:
        resolved_output_path = resolve_audio_output_path(output_path, root=output_root(runtime))
    except ValueError as exc:
        return f"TTS generation failed; local audio was not saved. Reason: {exc}"

    error_path = _error_path_for(resolved_output_path)
    try:
        print(f"\n[tool:generate_tts_audio] Generating narration: {resolved_output_path}", flush=True)
        byte_count = _synthesize_wav(narration, resolved_output_path)
        error_path.unlink(missing_ok=True)
        print(f"[tool:generate_tts_audio] Audio saved: {resolved_output_path} ({byte_count} bytes)", flush=True)
        return f"Audio saved to {resolved_output_path}"
    except Exception as exc:
        error_path.parent.mkdir(parents=True, exist_ok=True)
        error = f"TTS generation failed; local audio was not saved. Reason: {exc}"
        error_path.write_text(error, encoding="utf-8")
        print(f"[tool:generate_tts_audio] {error}", flush=True)
        return error
