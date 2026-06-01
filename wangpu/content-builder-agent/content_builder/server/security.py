"""Local pairing-token and signed-preview helpers."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from pathlib import Path

import yaml

from ..config import PROJECT_DIR


PAIRING_FILE = PROJECT_DIR / "pairing.local.yaml"
PAIRING_ENV = "CONTENT_BUILDER_PAIRING_TOKEN"
PREVIEW_TTL_SECONDS = 60 * 30


def _read_file_token() -> str | None:
    if not PAIRING_FILE.is_file():
        return None
    data = yaml.safe_load(PAIRING_FILE.read_text(encoding="utf-8")) or {}
    value = data.get("pairing_token") if isinstance(data, dict) else None
    return str(value).strip() if value else None


def pairing_token() -> str:
    """Load or create the local secret used to pair the phone and computer."""

    env_token = os.environ.get(PAIRING_ENV, "").strip()
    if env_token:
        return env_token
    file_token = _read_file_token()
    if file_token:
        return file_token
    generated = secrets.token_urlsafe(32)
    PAIRING_FILE.write_text(
        "# Generated locally. Do not commit this file.\n"
        f"pairing_token: {generated}\n",
        encoding="utf-8",
    )
    return generated


def extract_request_token(headers: dict[bytes, bytes] | dict[str, str]) -> str:
    """Extract either an x-api-key header or a Bearer token."""

    normalized = {
        (key.decode() if isinstance(key, bytes) else str(key)).lower(): (
            value.decode() if isinstance(value, bytes) else str(value)
        )
        for key, value in headers.items()
    }
    api_key = normalized.get("x-api-key", "").strip()
    if api_key:
        return api_key
    authorization = normalized.get("authorization", "").strip()
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return ""


def token_is_valid(candidate: str) -> bool:
    return bool(candidate) and hmac.compare_digest(candidate, pairing_token())


def make_preview_token(thread_id: str, path: str, *, expires_at: int | None = None) -> str:
    """Create a short-lived thread signature for headerless preview loads.

    The signature intentionally covers the thread rather than one file so that
    relative HTML assets keep working inside the full-screen iframe preview.
    """

    expiry = int(expires_at or (time.time() + PREVIEW_TTL_SECONDS))
    payload = f"{thread_id}\n{expiry}".encode()
    signature = hmac.new(pairing_token().encode(), payload, hashlib.sha256).hexdigest()
    return f"{expiry}.{signature}"


def preview_token_is_valid(thread_id: str, path: str, token: str) -> bool:
    try:
        raw_expiry, supplied_signature = token.split(".", 1)
        expiry = int(raw_expiry)
    except (AttributeError, TypeError, ValueError):
        return False
    if expiry < int(time.time()):
        return False
    expected = make_preview_token(thread_id, path, expires_at=expiry)
    return hmac.compare_digest(expected, token)
