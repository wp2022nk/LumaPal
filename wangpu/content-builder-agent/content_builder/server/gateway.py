"""Standalone FastAPI gateway for the Android app and official Agent Server."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from content_builder.server.api import app as content_builder_app
from content_builder.server.security import extract_request_token, token_is_valid


UPSTREAM = os.environ.get("CONTENT_BUILDER_AGENT_SERVER_URL", "http://127.0.0.1:2025").rstrip("/")
HOP_BY_HOP_HEADERS = {
    "connection",
    "content-length",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}

app = FastAPI(title="Content Builder LAN Gateway")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(content_builder_app.router)


@app.exception_handler(ValueError)
async def reject_invalid_path(_request: Request, error: ValueError) -> JSONResponse:
    """Turn invalid thread paths into explicit client errors."""

    return JSONResponse(status_code=400, content={"detail": str(error)})


@app.get("/api/content-builder/gateway/status")
async def get_gateway_status(request: Request) -> dict[str, bool]:
    """Check pairing and wait for the loopback Agent Server before mounting useStream."""

    token = extract_request_token(dict(request.headers))
    if not token_is_valid(token):
        return {"paired": False, "agent_server_ready": False}
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            response = await client.get(f"{UPSTREAM}/api/content-builder/pairing/status", headers={"x-api-key": token})
            payload = response.json()
    except (httpx.HTTPError, ValueError):
        return {"paired": True, "agent_server_ready": False}
    return {"paired": True, "agent_server_ready": response.status_code == 200 and payload.get("paired") is True}


@app.api_route(
    "/{path:path}",
    methods=["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"],
)
async def proxy_agent_server(request: Request, path: str) -> StreamingResponse:
    """Proxy official LangGraph Agent Server HTTP and SSE routes unchanged."""

    client = httpx.AsyncClient(timeout=None)
    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in HOP_BY_HOP_HEADERS and key.lower() != "host"
    }
    upstream_url = f"{UPSTREAM}/{path}"
    if request.url.query:
        upstream_url = f"{upstream_url}?{request.url.query}"
    try:
        upstream_request = client.build_request(
            request.method,
            upstream_url,
            headers=headers,
            content=request.stream(),
        )
        upstream_response = await client.send(upstream_request, stream=True)
    except httpx.HTTPError as error:
        await client.aclose()
        return JSONResponse(
            status_code=503,
            content={"detail": f"Agent Server is starting or unavailable: {error}"},
        )

    response_headers = {
        key: value
        for key, value in upstream_response.headers.items()
        if key.lower() not in HOP_BY_HOP_HEADERS
    }

    async def body() -> AsyncIterator[bytes]:
        try:
            async for chunk in upstream_response.aiter_raw():
                yield chunk
        finally:
            await upstream_response.aclose()
            await client.aclose()

    return StreamingResponse(
        body(),
        status_code=upstream_response.status_code,
        headers=response_headers,
        media_type=upstream_response.headers.get("content-type"),
    )

