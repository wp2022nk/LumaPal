"""Constants for the Xiaozhi hardware gateway."""

from __future__ import annotations

import os
from typing import Any

XIAOZHI_DEFAULT_THREAD_ID = os.environ.get("CONTENT_BUILDER_XIAOZHI_THREAD_ID", "xiaozhi-hardware")
OPUS_FRAME_DURATION_MS = 60
OPUS_INPUT_SAMPLE_RATE = 16000
OPUS_OUTPUT_SAMPLE_RATE = 16000
OPUS_CHANNELS = 1
OPUS_SAMPLE_WIDTH = 2
XIAOZHI_TTS_PREBUFFER_FRAMES = int(os.environ.get("CONTENT_BUILDER_XIAOZHI_TTS_PREBUFFER_FRAMES", "6"))
XIAOZHI_TTS_TAIL_DRAIN_MS = int(os.environ.get("CONTENT_BUILDER_XIAOZHI_TTS_TAIL_DRAIN_MS", "120"))
XIAOZHI_AUTO_VAD_RMS_THRESHOLD = int(os.environ.get("CONTENT_BUILDER_XIAOZHI_VAD_RMS_THRESHOLD", "500"))
XIAOZHI_AUTO_VAD_START_FRAMES = int(os.environ.get("CONTENT_BUILDER_XIAOZHI_VAD_START_FRAMES", "2"))
XIAOZHI_AUTO_VAD_MIN_SPEECH_MS = int(os.environ.get("CONTENT_BUILDER_XIAOZHI_VAD_MIN_SPEECH_MS", "300"))
XIAOZHI_AUTO_VAD_SILENCE_MS = int(os.environ.get("CONTENT_BUILDER_XIAOZHI_VAD_SILENCE_MS", "800"))
XIAOZHI_AUTO_VAD_MAX_SPEECH_MS = int(os.environ.get("CONTENT_BUILDER_XIAOZHI_VAD_MAX_SPEECH_MS", "12000"))
XIAOZHI_PHOTO_UPLOAD_WAIT_TIMEOUT = int(os.environ.get("CONTENT_BUILDER_XIAOZHI_PHOTO_UPLOAD_WAIT_TIMEOUT", "60"))
MAX_IMAGE_BYTES = 20 * 1024 * 1024
ALLOWED_DEVICE_TOOLS = {
    "self.get_device_status",
    "self.audio_speaker.set_volume",
    "self.audio_speaker.get_volume",
    "self.screen.set_brightness",
    "self.screen.set_theme",
    "self.gif.set_gif_mode",
    "self.display.set_mode",
    "self.camera.take_photo",
    "take_photo",
    "take_screenshot",
    "self.AEC.set_mode",
    "self.AEC.get_mode",
}


def _device_tool_key(name: str) -> str:
    return "".join(character for character in name.lower() if character.isalnum())


def _is_photo_like_device_tool(name: str) -> bool:
    return _device_tool_key(name) in {
        "takephoto",
        "selfcameratakephoto",
        "cameratakephoto",
        "takescreenshot",
        "selfcameratakescreenshot",
        "cameratakescreenshot",
    }


DEVICE_TOOL_ALIASES = {
    _device_tool_key("self.setvolume"): ("self.audio_speaker.set_volume",),
    _device_tool_key("setvolume"): ("self.audio_speaker.set_volume",),
    _device_tool_key("set_volume"): ("self.audio_speaker.set_volume",),
    _device_tool_key("self.getvolume"): ("self.audio_speaker.get_volume",),
    _device_tool_key("getvolume"): ("self.audio_speaker.get_volume",),
    _device_tool_key("get_volume"): ("self.audio_speaker.get_volume",),
    _device_tool_key("self.camera.take_photo"): ("take_photo", "self.camera.take_photo"),
    _device_tool_key("camera.take_photo"): ("take_photo", "self.camera.take_photo"),
    _device_tool_key("takephoto"): ("take_photo", "self.camera.take_photo"),
    _device_tool_key("self.camera.take_screenshot"): ("take_screenshot",),
    _device_tool_key("camera.take_screenshot"): ("take_screenshot",),
    _device_tool_key("takescreenshot"): ("take_screenshot",),
}


def _resolve_device_tool_name(name: str, exposed_tools: dict[str, dict[str, Any]]) -> str:
    exposed_names = set(exposed_tools)
    candidates = [name, *DEVICE_TOOL_ALIASES.get(_device_tool_key(name), ())]

    if exposed_names:
        for candidate in candidates:
            if candidate in exposed_names and candidate in ALLOWED_DEVICE_TOOLS:
                return candidate
        exposed_by_key = {
            _device_tool_key(exposed_name): exposed_name
            for exposed_name in exposed_names
            if exposed_name in ALLOWED_DEVICE_TOOLS
        }
        for candidate in candidates:
            exposed_name = exposed_by_key.get(_device_tool_key(candidate))
            if exposed_name:
                return exposed_name

    for candidate in candidates:
        if candidate in ALLOWED_DEVICE_TOOLS:
            return candidate
    return name
