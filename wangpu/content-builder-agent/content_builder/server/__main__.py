"""Run the trusted-LAN FastAPI gateway with a loopback Agent Server."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
from pathlib import Path

import uvicorn


WINDOWS_DISCONNECT_ERRORS = {64, 995, 10053, 10054, 10058}


def _langgraph_command() -> str:
    suffix = ".exe" if os.name == "nt" else ""
    candidate = Path(sys.executable).parent / f"langgraph{suffix}"
    if candidate.is_file():
        return str(candidate)
    resolved = shutil.which("langgraph")
    if not resolved:
        raise RuntimeError("langgraph CLI was not found. Run `uv sync` before starting the LAN server.")
    return resolved


def _stop_process_tree(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()


def _ignore_expected_windows_disconnect(
    loop: asyncio.AbstractEventLoop,
    context: dict[str, object],
) -> None:
    """Hide harmless Windows socket shutdown errors after a client disconnects."""

    error = context.get("exception")
    winerror = getattr(error, "winerror", None)
    if isinstance(error, (ConnectionResetError, BrokenPipeError)) or winerror in WINDOWS_DISCONNECT_ERRORS:
        return
    loop.default_exception_handler(context)


async def _serve_gateway(host: str, port: int) -> None:
    loop = asyncio.get_running_loop()
    if os.name == "nt":
        loop.set_exception_handler(_ignore_expected_windows_disconnect)
    config = uvicorn.Config(
        "content_builder.server.gateway:app",
        host=host,
        port=port,
        reload=False,
        log_level="info",
    )
    await uvicorn.Server(config).serve()


def main() -> None:
    host = os.environ.get("CONTENT_BUILDER_HOST", "0.0.0.0")
    port = int(os.environ.get("CONTENT_BUILDER_PORT", "2024"))
    internal_port = int(os.environ.get("CONTENT_BUILDER_AGENT_SERVER_PORT", "2025"))
    internal_url = f"http://127.0.0.1:{internal_port}"
    gateway_url = os.environ.get("CONTENT_BUILDER_GATEWAY_URL", f"http://127.0.0.1:{port}")
    os.environ["CONTENT_BUILDER_AGENT_SERVER_URL"] = internal_url
    os.environ["CONTENT_BUILDER_GATEWAY_URL"] = gateway_url
    child = subprocess.Popen(
        [
            _langgraph_command(),
            "dev",
            "--host",
            "127.0.0.1",
            "--port",
            str(internal_port),
            "--no-browser",
            "--no-reload",
            "--allow-blocking",
        ]
    )
    try:
        asyncio.run(_serve_gateway(host, port))
    finally:
        _stop_process_tree(child)


if __name__ == "__main__":
    main()
