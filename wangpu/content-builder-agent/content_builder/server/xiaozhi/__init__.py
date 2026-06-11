"""Xiaozhi ESP32 hardware gateway package."""

from __future__ import annotations

import asyncio
import subprocess

from .audio import OpusCodec, XiaozhiWebSocketOpusPlayer
from .constants import *
from .logging import XiaozhiTurnLogger, _print_structured_panel
from . import network as _network
from .routes import compat_router, router
from .session import XiaozhiSession, XiaozhiSessionManager, _agent_thread_id, session_manager
from .turn_service import (
    _agent_input_for_xiaozhi_turn,
    _handle_device_audio,
    _handle_device_text,
    _opus_frames_to_wav,
    _run_agent_tts_turn,
    _run_text_turn,
    _run_voice_turn_from_audio,
)
from .vision import _explain_image_sync, _save_vision_data_uri_sync, _save_vision_upload_sync

_ORIGINAL_LOCAL_IP_FOR_REMOTE = _network._local_ip_for_remote
_ORIGINAL_LOCAL_IPV4_CANDIDATES_FROM_HOSTNAME = _network._local_ipv4_candidates_from_hostname
_ORIGINAL_BEST_LOCAL_IP_FOR_REMOTE = _network._best_local_ip_for_remote


def _local_ip_for_remote(remote_host: str) -> str:
    return _ORIGINAL_LOCAL_IP_FOR_REMOTE(remote_host)


def _local_ipv4_candidates_from_hostname() -> list[str]:
    return _ORIGINAL_LOCAL_IPV4_CANDIDATES_FROM_HOSTNAME()


def _best_local_ip_for_remote(remote_host: str, candidates: list[str] | None = None) -> str:
    return _ORIGINAL_BEST_LOCAL_IP_FOR_REMOTE(remote_host, candidates)


def _public_base_url_from_websocket(websocket):
    _network._local_ip_for_remote = _local_ip_for_remote
    _network._local_ipv4_candidates_from_hostname = _local_ipv4_candidates_from_hostname
    _network.subprocess = subprocess
    return _network._public_base_url_from_websocket(websocket)

__all__ = [
    "router",
    "compat_router",
    "session_manager",
    "XiaozhiSession",
    "XiaozhiSessionManager",
    "OpusCodec",
    "XiaozhiWebSocketOpusPlayer",
    "XiaozhiTurnLogger",
    "_agent_thread_id",
    "_agent_input_for_xiaozhi_turn",
    "_handle_device_audio",
    "_handle_device_text",
    "_run_text_turn",
    "_run_voice_turn_from_audio",
    "_run_agent_tts_turn",
    "_opus_frames_to_wav",
    "_public_base_url_from_websocket",
    "_local_ip_for_remote",
    "_local_ipv4_candidates_from_hostname",
    "_best_local_ip_for_remote",
    "_print_structured_panel",
    "_explain_image_sync",
    "_save_vision_data_uri_sync",
    "_save_vision_upload_sync",
]
