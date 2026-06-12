"""Direct ASR -> local agent -> TTS service for Xiaozhi."""

from __future__ import annotations

import asyncio
import audioop
import contextlib
import json
import logging
import tempfile
import time
import wave
from pathlib import Path
from typing import Any

from content_builder.agent_factory import create_content_writer
from content_builder.config import load_main_config
from content_builder.history import save_thread_daily_messages
from content_builder.streaming import StreamEvent, astream_agent_events
from content_builder.voice.console import MainTokenDeltaFilter, UserEchoFilter, ensure_voice_ready
from content_builder.voice.tts import VoiceResponseSpeaker

from .audio import OpusCodec, XiaozhiWebSocketOpusPlayer
from .constants import (
    OPUS_INPUT_SAMPLE_RATE,
    OPUS_CHANNELS,
    OPUS_SAMPLE_WIDTH,
    XIAOZHI_AUTO_VAD_MAX_SPEECH_MS,
    XIAOZHI_AUTO_VAD_MIN_SPEECH_MS,
    XIAOZHI_AUTO_VAD_RMS_THRESHOLD,
    XIAOZHI_AUTO_VAD_SILENCE_MS,
    XIAOZHI_AUTO_VAD_START_FRAMES,
)
from .logging import XiaozhiTurnLogger, _print_structured_panel
from .session import XiaozhiSession

logger = logging.getLogger(__name__)


def _root_attr(name: str, default: Any) -> Any:
    import content_builder.server.xiaozhi as xiaozhi_root

    return getattr(xiaozhi_root, name, default)


def _opus_codec_class() -> Any:
    return _root_attr("OpusCodec", OpusCodec)


async def _start_voice_turn_task(session: XiaozhiSession, frames: list[bytes]) -> None:
    target = _root_attr("_run_voice_turn_from_audio", _run_voice_turn_from_audio)
    await target(session, frames)


def _turn_logger(session: XiaozhiSession, mode: str) -> XiaozhiTurnLogger:
    existing = getattr(session, "turn_log", None)
    if isinstance(existing, XiaozhiTurnLogger):
        return existing
    started_at = session.turn_started_at or time.monotonic()
    session.turn_log = XiaozhiTurnLogger(
        thread_id=session.thread_id,
        session_id=session.session_id,
        mode=mode,
        started_at=started_at,
    )
    return session.turn_log


def _reset_turn_logger(session: XiaozhiSession) -> None:
    session.turn_log = None
    session.turn_started_at = 0.0


def _agent_input_for_xiaozhi_turn(session: XiaozhiSession, transcript: str) -> str:
    if not session.tools:
        return transcript
    exposed_tools = sorted(session.tools)
    camera_tools = [
        name
        for name in exposed_tools
        if name in {"take_photo", "self.camera.take_photo", "take_screenshot"}
    ]
    lines = [
        "Xiaozhi hardware context:",
        f"- Connected device thread_id: {session.thread_id}",
        f"- Exposed device tools: {', '.join(exposed_tools)}",
    ]
    if camera_tools:
        lines.extend(
            [
                f"- Camera tools available: {', '.join(camera_tools)}",
                "- If the user asks to take a photo, inspect the scene, identify an object, or says '看看这是什么', you MUST call xiaozhi_call_device_tool.",
                "- Use name='take_photo' when available, otherwise use name='self.camera.take_photo'. Pass {'question': original user request}. Do not answer that you cannot access a camera before trying this tool.",
            ]
        )
    return f"{transcript}\n\n" + "\n".join(lines)


async def _publish_agent_event(session: XiaozhiSession, event: StreamEvent, turn_log: XiaozhiTurnLogger) -> None:
    turn_log.stage("llm_event", event_type=event.type, source=event.source, text=event.text)
    await session.publish(
        "agent_event",
        {"thread_id": session.thread_id, "type": event.type, "source": event.source, "text": event.text},
    )
    tool_event = _stream_event_to_tool_event(event)
    if tool_event is not None:
        _print_structured_panel(
            "Agent Tool Event",
            {"thread_id": session.thread_id, "session_id": session.session_id, **tool_event},
        )
        await session.send_tool_event(tool_event)
        await session.publish(
            "tool",
            {"thread_id": session.thread_id, "session_id": session.session_id, **tool_event},
        )


def _stream_event_to_tool_event(event: StreamEvent) -> dict[str, Any] | None:
    if event.type == "tool_call":
        return {"state": "started", "source": event.source, "name": _tool_name_from_event(event), "arguments": _tool_payload_from_event(event)}
    if event.type == "tool_result":
        return {"state": "finished", "source": event.source, "name": _tool_name_from_event(event), "result": event.text}
    if event.type == "error":
        return {"state": "error", "source": event.source, "name": _tool_name_from_event(event), "error": event.text}
    return None


def _tool_name_from_event(event: StreamEvent) -> str:
    raw = event.raw
    raw_name = _tool_name_from_raw(raw)
    if raw_name:
        return raw_name
    text_name = _tool_name_from_text(event.text)
    return text_name or "tool"


def _tool_name_from_raw(raw: Any) -> str:
    if isinstance(raw, dict):
        for key in ("tool_name", "name"):
            value = raw.get(key)
            if value:
                return str(value)

        payload = raw.get("payload")
        if isinstance(payload, dict):
            nested_name = _tool_name_from_raw(payload)
            if nested_name:
                return nested_name

    for attr in ("tool_name", "name"):
        value = getattr(raw, attr, None)
        if value:
            return str(value)

    return ""


def _tool_name_from_text(text: str) -> str:
    for marker in ("调用工具:", "工具返回:", "工具错误:", "工具参数流:"):
        if marker not in text:
            continue
        tail = text.split(marker, 1)[1].strip()
        if not tail:
            continue
        return tail.splitlines()[0].strip()
    return ""


def _tool_payload_from_event(event: StreamEvent) -> Any:
    raw = event.raw
    if isinstance(raw, dict):
        return raw.get("input", raw.get("args", raw))
    return event.text

async def _handle_device_audio(session: XiaozhiSession, audio_frame: bytes) -> None:
    """Handle full-duplex Xiaozhi audio that arrives without a listen/stop event."""

    if session.server_is_speaking and session.agent_task and not session.agent_task.done():
        session.reset_auto_vad()
        return

    try:
        if session.auto_vad_codec is None:
            session.auto_vad_codec = _opus_codec_class()(pcm_sample_rate=OPUS_INPUT_SAMPLE_RATE)
        pcm = session.auto_vad_codec.decode(audio_frame)
    except Exception as exc:
        await session.publish(
            "hardware_error",
            {"thread_id": session.thread_id, "message": f"Failed to decode Xiaozhi audio frame: {exc}"},
        )
        return

    if not pcm:
        return

    now = time.monotonic()
    rms = audioop.rms(pcm, OPUS_SAMPLE_WIDTH)
    is_voice = rms >= _root_attr("XIAOZHI_AUTO_VAD_RMS_THRESHOLD", XIAOZHI_AUTO_VAD_RMS_THRESHOLD)

    if is_voice:
        session.auto_voice_frame_count += 1
        if not session.auto_speech_started:
            session.auto_pending_speech_frames.append(audio_frame)

        start_frames = _root_attr("XIAOZHI_AUTO_VAD_START_FRAMES", XIAOZHI_AUTO_VAD_START_FRAMES)
        confirmed_speech = session.auto_voice_frame_count >= start_frames

        started_this_frame = False
        if not session.auto_speech_started and confirmed_speech:
            session.auto_speech_started = True
            session.auto_speech_started_at = now
            session.auto_speech_frames = list(session.auto_pending_speech_frames)
            session.auto_pending_speech_frames.clear()
            started_this_frame = True
            session.turn_started_at = now
            session.turn_log = XiaozhiTurnLogger(thread_id=session.thread_id, session_id=session.session_id, mode="voice-auto", started_at=now)
            session.turn_log.stage("listen_start", rms=rms, mode="auto")
            await session.publish("listening", {"thread_id": session.thread_id, "state": "auto_speech_start", "rms": rms})

        session.auto_last_voice_at = now
        if session.auto_speech_started and not started_this_frame:
            session.auto_speech_frames.append(audio_frame)
        return

    session.auto_pending_speech_frames.clear()
    session.auto_voice_frame_count = 0
    if not session.auto_speech_started:
        return

    session.auto_speech_frames.append(audio_frame)
    speech_ms = int((now - session.auto_speech_started_at) * 1000)
    silence_ms = int((now - session.auto_last_voice_at) * 1000)
    if speech_ms < _root_attr("XIAOZHI_AUTO_VAD_MIN_SPEECH_MS", XIAOZHI_AUTO_VAD_MIN_SPEECH_MS):
        return
    if silence_ms < _root_attr("XIAOZHI_AUTO_VAD_SILENCE_MS", XIAOZHI_AUTO_VAD_SILENCE_MS) and speech_ms < _root_attr("XIAOZHI_AUTO_VAD_MAX_SPEECH_MS", XIAOZHI_AUTO_VAD_MAX_SPEECH_MS):
        return

    frames = list(session.auto_speech_frames)
    session.reset_auto_vad()
    turn_log = _turn_logger(session, "voice-auto")
    turn_log.stage("listen_stop", speech_ms=speech_ms, silence_ms=silence_ms, frame_count=len(frames))
    await session.publish(
        "listening",
        {"thread_id": session.thread_id, "state": "auto_speech_stop", "speech_ms": speech_ms, "silence_ms": silence_ms},
    )
    if session.agent_task and not session.agent_task.done():
        await session.publish("listening", {"thread_id": session.thread_id, "state": "auto_speech_dropped_busy"})
        return
    session.agent_task = asyncio.create_task(_start_voice_turn_task(session, frames))


async def _handle_device_text(session: XiaozhiSession, text: str) -> None:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        await session.publish("hardware_error", {"thread_id": session.thread_id, "message": "Invalid JSON from device"})
        return

    message_type = payload.get("type")
    if message_type == "listen":
        if session.server_is_speaking and session.agent_task and not session.agent_task.done():
            session.reset_auto_vad()
            session.audio_frames.clear()
            session.manual_listen_started = False
            await session.publish("listening", {"thread_id": session.thread_id, "state": "ignored_busy"})
            return
        state = payload.get("state")
        session.current_listen_mode = str(payload.get("mode") or session.current_listen_mode)
        if state == "start":
            session.manual_listen_started = session.current_listen_mode == "manual"
            if session.manual_listen_started:
                session.reset_auto_vad()
            session.audio_frames.clear()
            session.turn_started_at = time.monotonic()
            session.turn_log = XiaozhiTurnLogger(thread_id=session.thread_id, session_id=session.session_id, mode="voice-manual", started_at=session.turn_started_at)
            session.turn_log.stage("listen_start", mode=session.current_listen_mode)
            await session.publish(
                "listening",
                {"thread_id": session.thread_id, "state": "start", "mode": session.current_listen_mode},
            )
        elif state == "stop":
            manual_frames = list(session.audio_frames)
            session.manual_listen_started = False
            session.audio_frames.clear()
            if manual_frames:
                _turn_logger(session, "voice-manual").stage("listen_stop", frame_count=len(manual_frames), mode=session.current_listen_mode)
                if session.agent_task and not session.agent_task.done():
                    session.agent_task.cancel()
                session.agent_task = asyncio.create_task(_start_voice_turn_task(session, manual_frames))
        elif state == "detect":
            session.manual_listen_started = False
            session.audio_frames.clear()
            session.reset_auto_vad()
            text = str(payload.get("text") or "").strip()
            session.turn_started_at = time.monotonic()
            session.turn_log = XiaozhiTurnLogger(thread_id=session.thread_id, session_id=session.session_id, mode="text-detect", started_at=session.turn_started_at)
            session.turn_log.stage("wake_detect", text=text)
            await session.publish("wake", {"thread_id": session.thread_id, "text": text})
            if text:
                if session.agent_task and not session.agent_task.done():
                    session.agent_task.cancel()
                session.agent_task = asyncio.create_task(_run_text_turn(session, text))
    elif message_type == "abort":
        if session.agent_task and not session.agent_task.done():
            session.agent_task.cancel()
        await session.send_json({"type": "tts", "state": "stop"})
        await session.publish("abort", {"thread_id": session.thread_id, "reason": payload.get("reason", "")})
    elif message_type == "mcp":
        mcp_payload = payload.get("payload")
        if isinstance(mcp_payload, dict):
            await session.handle_mcp_payload(mcp_payload)


async def _run_text_turn(session: XiaozhiSession, transcript: str) -> None:
    try:
        turn_log = _turn_logger(session, "text")
        turn_log.stage("text_turn_start", transcript=transcript)
        config = await asyncio.to_thread(load_main_config)
        ensure_voice_ready(config)
        await session.send_json({"type": "stt", "text": transcript})
        await session.publish("message", {"thread_id": session.thread_id, "role": "human", "content": transcript})
        await asyncio.to_thread(
            save_thread_daily_messages,
            session.thread_id,
            messages=[{"type": "human", "content": transcript}],
            metadata={"source": "xiaozhi-text"},
        )

        session.server_is_speaking = True
        await session.send_json({"type": "tts", "state": "start"})
        await _run_agent_tts_turn(session, transcript, config)
        await session.send_json({"type": "tts", "state": "stop"})
        session.server_is_speaking = False
    except asyncio.CancelledError:
        session.server_is_speaking = False
        await session.send_json({"type": "tts", "state": "stop"})
        raise
    except Exception as exc:
        session.server_is_speaking = False
        message = f"Xiaozhi text turn failed: {exc}"
        await session.publish("hardware_error", {"thread_id": session.thread_id, "message": message})
        with contextlib.suppress(Exception):
            await session.send_json({"type": "alert", "status": "error", "message": message, "emotion": "sad"})


async def _run_voice_turn_from_audio(session: XiaozhiSession, opus_frames: list[bytes]) -> None:
    try:
        turn_log = _turn_logger(session, "voice")
        turn_log.stage("wav_convert_start", frame_count=len(opus_frames))
        wav_path = await asyncio.to_thread(_opus_frames_to_wav, opus_frames)
        turn_log.stage("wav_convert_done", wav_path=str(wav_path))
        config = await asyncio.to_thread(load_main_config)
        ensure_voice_ready(config)
        from content_builder.server.api import _ensure_asr_manager_sync  # reuse existing lazy singleton

        asr_manager = await asyncio.to_thread(_ensure_asr_manager_sync, force_retry=True)
        turn_log.stage("asr_start", wav_path=str(wav_path))
        transcript = await asr_manager.recognize_file(wav_path, session_id=session.thread_id)
        transcript = transcript.strip()
        turn_log.stage("asr_done", transcript=transcript)
        if not transcript:
            await session.publish("stt", {"thread_id": session.thread_id, "text": ""})
            return

        await session.send_json({"type": "stt", "text": transcript})
        await session.publish("message", {"thread_id": session.thread_id, "role": "human", "content": transcript})
        await asyncio.to_thread(
            save_thread_daily_messages,
            session.thread_id,
            messages=[{"type": "human", "content": transcript}],
            metadata={"source": "xiaozhi-hardware"},
        )

        session.server_is_speaking = True
        await session.send_json({"type": "tts", "state": "start"})
        await _run_agent_tts_turn(session, transcript, config)
        await session.send_json({"type": "tts", "state": "stop"})
        session.server_is_speaking = False
    except asyncio.CancelledError:
        session.server_is_speaking = False
        await session.send_json({"type": "tts", "state": "stop"})
        raise
    except Exception as exc:
        session.server_is_speaking = False
        message = f"Xiaozhi voice turn failed: {exc}"
        await session.publish("hardware_error", {"thread_id": session.thread_id, "message": message})
        with contextlib.suppress(Exception):
            await session.send_json({"type": "alert", "status": "error", "message": message, "emotion": "sad"})

async def _run_agent_tts_turn(session: XiaozhiSession, transcript: str, config: Any) -> None:
    turn_log = _turn_logger(session, "text" if not session.audio_frames and not session.auto_speech_frames else "voice")
    session.current_user_transcript = transcript
    session.tts_sample_rate = int(getattr(config.voice.tts, "sample_rate", 24000) or 24000)
    speaker = VoiceResponseSpeaker(config.voice, config.secrets, pcm_player=XiaozhiWebSocketOpusPlayer(session))
    await speaker.start()
    delta_filter = MainTokenDeltaFilter()
    echo_filter = UserEchoFilter(transcript)
    saw_main_token = False
    final_text = ""
    agent_input = _agent_input_for_xiaozhi_turn(session, transcript)
    agent = create_content_writer(runtime_mode="cli")
    turn_log.stage("llm_start", transcript=transcript, exposed_tools=sorted(session.tools))
    logger.info("Xiaozhi direct ASR transcript for thread %s: %s", session.thread_id, transcript)
    try:
        async for event in astream_agent_events(
            agent,
            agent_input,
            thread_id=session.thread_id,
            max_turns=getattr(getattr(config, "conversation", None), "max_turns", None),
        ):
            if event.type == "token" and event.source == "main":
                delta = delta_filter.delta(event.text)
                if delta and not echo_filter.is_user_echo(delta):
                    saw_main_token = True
                    final_text += delta
                    turn_log.stage("llm_token", text=delta)
                    await speaker.feed_token(delta)
                continue

            if event.type == "final" and event.source == "main":
                if event.text:
                    final_text = event.text
                turn_log.stage("llm_final", text=final_text)
                if saw_main_token:
                    await speaker.flush()
                elif final_text and not echo_filter.is_user_echo(final_text):
                    await speaker.feed_token(final_text)
                    await speaker.flush()
                continue

            await _publish_agent_event(session, event, turn_log)
    finally:
        await speaker.close()

    final_text = final_text.strip()
    if final_text:
        logger.info("Xiaozhi assistant final for thread %s: %s", session.thread_id, final_text[:500])
        await session.publish("message", {"thread_id": session.thread_id, "role": "assistant", "content": final_text})
        await asyncio.to_thread(
            save_thread_daily_messages,
            session.thread_id,
            messages=[{"type": "ai", "content": final_text}],
            metadata={"source": "xiaozhi-hardware-direct"},
        )
        turn_log.finish(final_text=final_text)
    else:
        logger.warning("Xiaozhi direct agent run finished without assistant text for thread %s", session.thread_id)
        turn_log.finish(final_text="")
    _reset_turn_logger(session)

def _opus_frames_to_wav(opus_frames: list[bytes]) -> Path:
    if not opus_frames:
        raise RuntimeError("No audio frames received from Xiaozhi device")
    codec = _opus_codec_class()()
    pcm = bytearray()
    for frame in opus_frames:
        pcm.extend(codec.decode(frame))
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_file:
        wav_path = Path(temp_file.name)
    with wave.open(str(wav_path), "wb") as wav_file:
        wav_file.setnchannels(OPUS_CHANNELS)
        wav_file.setsampwidth(2)
        wav_file.setframerate(OPUS_INPUT_SAMPLE_RATE)
        wav_file.writeframes(bytes(pcm))
    return wav_path
