"""LangGraph Agent Server authentication for the local Android companion app."""

from __future__ import annotations

import asyncio

from langgraph_sdk import Auth

from content_builder.server.security import extract_request_token, token_is_valid


auth = Auth()


@auth.authenticate
async def authenticate(headers: dict[bytes, bytes]) -> Auth.types.MinimalUserDict:
    """Protect built-in Agent Server routes with the local pairing token."""

    if not await asyncio.to_thread(token_is_valid, extract_request_token(headers)):
        raise Auth.exceptions.HTTPException(status_code=401, detail="Invalid pairing token")
    return {"identity": "local-paired-user", "is_authenticated": True}
