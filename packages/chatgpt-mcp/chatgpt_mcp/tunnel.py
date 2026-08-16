from __future__ import annotations

import asyncio
import re
import shutil
import time
from collections.abc import Callable
from typing import IO

CLOUDFLARE_URL = re.compile(r"https://[-a-z0-9.]+\.trycloudflare\.com", re.I)
INSTALL_HINT = "cloudflared not found. Install cloudflared, then retry --tunnel cloudflare."


class TunnelError(RuntimeError):
    pass


async def start_cloudflare_tunnel(
    origin: str,
    *,
    timeout_s: float = 30,
    spawn: Callable[..., asyncio.subprocess.Process] | None = None,
) -> tuple[str, asyncio.subprocess.Process]:
    if spawn is None and shutil.which("cloudflared") is None:
        raise TunnelError(INSTALL_HINT)
    create = spawn or asyncio.create_subprocess_exec
    process = await create(
        "cloudflared",
        "tunnel",
        "--url",
        origin,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    deadline = time.monotonic() + timeout_s
    buffer = ""
    while time.monotonic() < deadline:
        if process.stdout is None:
            break
        try:
            chunk = await asyncio.wait_for(process.stdout.readline(), timeout=1)
        except asyncio.TimeoutError:
            continue
        if not chunk:
            break
        buffer += chunk.decode("utf-8", errors="replace")
        match = CLOUDFLARE_URL.search(buffer)
        if match:
            return match.group(0).rstrip("/"), process
    process.kill()
    raise TunnelError("No public Cloudflare URL appeared within 30 seconds. Install/check cloudflared.")
