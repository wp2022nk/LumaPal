"""LAN URL resolution helpers for Xiaozhi devices."""

from __future__ import annotations

import ipaddress
import os
import re
import socket
import subprocess
from urllib.parse import urlsplit

from fastapi import Request, WebSocket


def _public_base_url(request: Request) -> str:
    configured = os.environ.get("CONTENT_BUILDER_PUBLIC_BASE_URL", "").strip().rstrip("/")
    fallback_port = _scope_server_port(getattr(request, "scope", {})) or _configured_port()
    if configured:
        return _public_base_url_for_remote(
            configured,
            str(getattr(getattr(request, "client", None), "host", "") or ""),
            fallback_port=fallback_port,
        )
    base = str(request.base_url).rstrip("/")
    return _public_base_url_for_remote(
        base,
        str(getattr(getattr(request, "client", None), "host", "") or ""),
        fallback_port=fallback_port,
    )


def _public_ws_url(request: Request) -> str:
    base = _public_base_url(request)
    return base.replace("https://", "wss://", 1).replace("http://", "ws://", 1)


def _public_base_url_from_websocket(websocket: WebSocket) -> str:
    remote_host = str(getattr(getattr(websocket, "client", None), "host", "") or "")
    configured = os.environ.get("CONTENT_BUILDER_PUBLIC_BASE_URL", "").strip().rstrip("/")
    fallback_port = _scope_server_port(getattr(websocket, "scope", {})) or _configured_port()
    if configured:
        return _public_base_url_for_remote(configured, remote_host, fallback_port=fallback_port)
    return _public_base_url_for_remote(str(websocket.url), remote_host, fallback_port=fallback_port)


def _public_base_url_for_remote(url: str, remote_host: str = "", *, fallback_port: int | None = None) -> str:
    parts = urlsplit(url)
    host = parts.hostname or ""
    port_number = parts.port or fallback_port
    port = f":{port_number}" if port_number and not _is_default_port(parts.scheme, port_number) else ""
    local_candidates = _local_ipv4_candidates()
    if _host_is_not_device_reachable(host) or _host_is_stale_private_ipv4(host, local_candidates):
        host = _best_local_ip_for_remote(remote_host, local_candidates) or host
    host_part = f"[{host}]" if ":" in host and not host.startswith("[") else host
    base = f"{parts.scheme}://{host_part}{port}"
    return base.replace("wss://", "https://", 1).replace("ws://", "http://", 1)


def _configured_port() -> int:
    try:
        return int(os.environ.get("CONTENT_BUILDER_PORT", "2024"))
    except ValueError:
        return 2024


def _scope_server_port(scope: object) -> int:
    if not isinstance(scope, dict):
        return 0
    server = scope.get("server")
    if not isinstance(server, (tuple, list)) or len(server) < 2:
        return 0
    try:
        return int(server[1] or 0)
    except (TypeError, ValueError):
        return 0


def _is_default_port(scheme: str, port: int) -> bool:
    normalized = scheme.lower()
    return (normalized in {"http", "ws"} and port == 80) or (normalized in {"https", "wss"} and port == 443)


def _host_is_not_device_reachable(host: str) -> bool:
    normalized = host.strip().strip("[]").lower()
    if normalized in {"", "localhost", "0.0.0.0", "::", "::1"}:
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _local_ip_for_remote(remote_host: str) -> str:
    remote = remote_host.strip().strip("[]")
    if not remote:
        return ""
    try:
        family = socket.AF_INET6 if ":" in remote else socket.AF_INET
        with socket.socket(family, socket.SOCK_DGRAM) as sock:
            sock.connect((remote, 9))
            local = sock.getsockname()[0]
        if local and not _host_is_not_device_reachable(local):
            return local
    except OSError:
        return ""
    return ""


def _best_local_ip_for_remote(remote_host: str, candidates: list[str] | None = None) -> str:
    routed_ip = _local_ip_for_remote(remote_host)
    if routed_ip:
        return routed_ip
    candidates = candidates if candidates is not None else _local_ipv4_candidates()
    if not candidates:
        return ""
    remote = _parse_ipv4(remote_host)

    def score(candidate: str) -> int:
        candidate_ip = _parse_ipv4(candidate)
        if candidate_ip is None:
            return -100
        value = 0
        if candidate_ip.is_private:
            value += 20
        if str(candidate_ip).endswith(".1"):
            value -= 5
        if remote is not None and _same_ipv4_24(candidate_ip, remote):
            value += 100
        return value

    return max(candidates, key=score)


def _host_is_stale_private_ipv4(host: str, local_candidates: list[str]) -> bool:
    address = _parse_ipv4(host)
    return bool(address and address.is_private and str(address) not in local_candidates)


def _local_ipv4_candidates() -> list[str]:
    candidates: list[str] = []
    for candidate in [*_local_ipv4_candidates_from_hostname(), *_local_ipv4_candidates_from_ipconfig()]:
        if candidate not in candidates and _usable_ipv4(candidate):
            candidates.append(candidate)
    return candidates


def _local_ipv4_candidates_from_hostname() -> list[str]:
    try:
        return [info[-1][0] for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)]
    except OSError:
        return []


def _local_ipv4_candidates_from_ipconfig() -> list[str]:
    if os.name != "nt":
        return []
    try:
        output = subprocess.check_output(["ipconfig"], text=True, encoding="utf-8", errors="ignore")
    except (OSError, subprocess.SubprocessError):
        return []
    return re.findall(r"IPv4[^\r\n:?]*[:?]\s*([0-9]+(?:\.[0-9]+){3})", output)


def _usable_ipv4(candidate: str) -> bool:
    address = _parse_ipv4(candidate)
    return bool(address and not (address.is_loopback or address.is_link_local or address.is_multicast or address.is_unspecified))


def _parse_ipv4(candidate: str) -> ipaddress.IPv4Address | None:
    try:
        address = ipaddress.ip_address(candidate.strip().strip("[]"))
    except ValueError:
        return None
    return address if isinstance(address, ipaddress.IPv4Address) else None


def _same_ipv4_24(left: ipaddress.IPv4Address, right: ipaddress.IPv4Address) -> bool:
    return str(left).split(".")[:3] == str(right).split(".")[:3]
