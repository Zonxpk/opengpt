from __future__ import annotations

import argparse
import asyncio
import secrets
import sys
from pathlib import Path

import uvicorn

from chatgpt_mcp.network import mcp_path
from chatgpt_mcp.server import create_app
from chatgpt_mcp.tunnel import TunnelError, start_cloudflare_tunnel


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="opengpt-connect")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--mode", choices=("read", "write"), required=True)
    parser.add_argument("--tunnel", choices=("cloudflare", "none"), default="cloudflare")
    parser.add_argument("--port", type=int, default=8787)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.root.expanduser().resolve()
    if not root.is_absolute():
        print("--root must be an absolute path", file=sys.stderr)
        return 2
    return asyncio.run(_run(root, args.mode, args.tunnel, args.port))


async def _run(root: Path, mode: str, tunnel: str, port: int) -> int:
    token = secrets.token_hex(16) if tunnel == "cloudflare" else None
    app = create_app(approved_root=root, mode=mode, token=token)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info")
    server = uvicorn.Server(config)
    serve = asyncio.create_task(server.serve())
    tunnel_proc = None
    try:
        if tunnel == "cloudflare":
            try:
                public, tunnel_proc = await start_cloudflare_tunnel(f"http://127.0.0.1:{port}")
            except TunnelError as exc:
                print(str(exc), file=sys.stderr)
                server.should_exit = True
                await serve
                return 1
            print(f"ChatGPT MCP URL: {public}{mcp_path(token)}")
        else:
            print(f"Loopback health: http://127.0.0.1:{port}/health")
        await serve
        return 0
    finally:
        server.should_exit = True
        if tunnel_proc is not None and tunnel_proc.returncode is None:
            tunnel_proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
