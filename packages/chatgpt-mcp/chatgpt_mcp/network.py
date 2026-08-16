from __future__ import annotations

from urllib.parse import urlparse


def is_loopback_hostname(value: str) -> bool:
    normalized = value.lower().replace("[", "").replace("]", "")
    host = normalized.split("%", 1)[0]
    return host in {"127.0.0.1", "::1", "localhost"}


def is_allowed_browser_origin(origin: str | None, host_header: str | None) -> bool:
    if origin is None:
        return True
    try:
        origin_url = urlparse(origin)
        request_host = (host_header or "").split(":", 1)[0]
        return (
            origin_url.scheme == "http"
            and is_loopback_hostname(origin_url.hostname or "")
            and is_loopback_hostname(request_host)
        )
    except ValueError:
        return False


def mcp_path(token: str | None) -> str:
    if token:
        return f"/t/{token}/mcp"
    return "/mcp"


def is_authorized_mcp_path(path: str, token: str | None) -> bool:
    return path == mcp_path(token)
