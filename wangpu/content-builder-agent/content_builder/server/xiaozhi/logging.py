"""Pretty structured logging for Xiaozhi voice turns."""

from __future__ import annotations

import time
from typing import Any


def _structured_log_value(value: Any, *, indent: int = 2, max_text: int = 800) -> list[str]:
    prefix = " " * indent
    if isinstance(value, dict):
        if not value:
            return [f"{prefix}(empty)"]
        lines: list[str] = []
        for key, item in value.items():
            if isinstance(item, (dict, list)):
                lines.append(f"{prefix}{key}:")
                lines.extend(_structured_log_value(item, indent=indent + 2, max_text=max_text))
            else:
                lines.append(f"{prefix}{key}: {_structured_log_scalar(item, max_text=max_text)}")
        return lines
    if isinstance(value, list):
        if not value:
            return [f"{prefix}(empty)"]
        lines = []
        for item in value:
            if isinstance(item, (dict, list)):
                lines.append(f"{prefix}-")
                lines.extend(_structured_log_value(item, indent=indent + 2, max_text=max_text))
            else:
                lines.append(f"{prefix}- {_structured_log_scalar(item, max_text=max_text)}")
        return lines
    return [f"{prefix}{_structured_log_scalar(value, max_text=max_text)}"]


def _structured_log_scalar(value: Any, *, max_text: int = 800) -> str:
    text = str(value)
    return text if len(text) <= max_text else f"{text[:max_text]}... <truncated {len(text) - max_text} chars>"


def _print_structured_panel(title: str, fields: dict[str, Any]) -> None:
    print(f"\n=== {title} ===", flush=True)
    for line in _structured_log_value(fields, indent=2):
        print(line, flush=True)


class XiaozhiTurnLogger:
    """One pretty log timeline for a single user speech/text turn."""

    def __init__(self, *, thread_id: str, session_id: str, mode: str, started_at: float | None = None) -> None:
        self.thread_id = thread_id
        self.session_id = session_id
        self.mode = mode
        self.started_at = started_at if started_at is not None else time.monotonic()
        self._finished = False
        self.stage("turn_start")

    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self.started_at) * 1000)

    def stage(self, stage: str, **fields: Any) -> None:
        payload = {
            "thread_id": self.thread_id,
            "session_id": self.session_id,
            "mode": self.mode,
            "stage": stage,
            "elapsed_ms": self.elapsed_ms(),
            **fields,
        }
        _print_structured_panel("Xiaozhi Direct Voice Turn", payload)

    def finish(self, **fields: Any) -> None:
        if self._finished:
            return
        self._finished = True
        self.stage("turn_finish", **fields)
