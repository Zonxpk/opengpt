from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import mcp_types as types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPASGIApp, StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route
from starlette.types import ASGIApp

from chatgpt_mcp.adapter import ToolAdapter
from chatgpt_mcp.allowlist import annotations_for, specs_for_mode
from chatgpt_mcp.network import is_allowed_browser_origin, is_authorized_mcp_path, mcp_path
from chatgpt_mcp.sessions import SessionStore

JSON_LIMIT = 2 * 1024 * 1024
MAX_SESSIONS = 100
SESSION_IDLE_S = 30 * 60


def _mcp_session_id(scope: dict[str, Any]) -> str | None:
    for key, value in scope.get("headers") or []:
        if key.lower() == b"mcp-session-id":
            return value.decode("latin1")
    return None


class OriginAndTokenMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, *, token: str | None) -> None:
        super().__init__(app)
        self.token = token

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        origin = request.headers.get("origin")
        host = request.headers.get("host")
        if not is_allowed_browser_origin(origin, host):
            return PlainTextResponse("Forbidden origin", status_code=403)
        path = request.url.path
        if path == "/health":
            return await call_next(request)
        if path.startswith("/t/") or path == "/mcp":
            if not is_authorized_mcp_path(path, self.token):
                return PlainTextResponse("Not found", status_code=404)
        return await call_next(request)


def build_mcp_server(adapter: ToolAdapter) -> Server[Any]:
    specs = specs_for_mode(adapter.mode, debug_tools=adapter.debug_tools)
    instances = {spec.name: spec.factory() for spec in specs if spec.factory is not None}

    async def on_list_tools(_ctx: Any, _params: Any) -> types.ListToolsResult:
        tools: list[types.Tool] = []
        for spec in specs:
            if spec.schema is not None:
                schema = spec.schema
                description = spec.description or spec.name
            elif spec.name == "bash":
                schema = {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"},
                        "cwd": {"type": "string"},
                        "timeout_seconds": {"type": "integer"},
                    },
                    "required": ["command"],
                }
                description = instances["bash"].description if "bash" in instances else (
                    "Run a shell command in the local repository."
                )
            else:
                tool = instances[spec.name]
                schema = tool.input_model.model_json_schema()
                description = tool.description
            tools.append(
                types.Tool(
                    name=spec.name,
                    description=description,
                    input_schema=schema,
                    annotations=annotations_for(spec),
                )
            )
        return types.ListToolsResult(tools=tools)

    async def on_call_tool(_ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        arguments = params.arguments if isinstance(params.arguments, dict) else {}
        result = await adapter.call(params.name, arguments)
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=result.output)],
            is_error=result.is_error,
        )

    return Server(
        "opengpt-chatgpt-mcp",
        instructions=adapter.instructions(),
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )


def create_app(
    *,
    approved_root: Path,
    mode: str,
    token: str | None,
    json_response: bool = False,
    debug_tools: bool = False,
) -> Starlette:
    adapter = ToolAdapter(approved_root=approved_root, mode=mode, debug_tools=debug_tools)
    mcp = build_mcp_server(adapter)
    path = mcp_path(token)
    tunneled = token is not None
    if tunneled:
        security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    else:
        security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*", "testserver", "testserver:*"],
            allowed_origins=["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"],
        )
    session_manager = StreamableHTTPSessionManager(
        app=mcp,
        json_response=json_response,
        stateless=False,
        security_settings=security,
        session_idle_timeout=SESSION_IDLE_S,
        max_request_body_size=JSON_LIMIT,
    )
    mcp_asgi = StreamableHTTPASGIApp(session_manager)
    store = SessionStore(max_sessions=MAX_SESSIONS, idle_ttl_s=SESSION_IDLE_S)

    async def health(_request: Request) -> JSONResponse:
        return JSONResponse({"ok": True, "name": "opengpt-chatgpt-mcp"})

    class McpASGI:
        def __init__(
            self,
            inner: StreamableHTTPASGIApp,
            sessions: SessionStore,
        ) -> None:
            self.inner = inner
            self.sessions = sessions

        async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
            if scope.get("type") != "http":
                await self.inner(scope, receive, send)
                return
            session_id = _mcp_session_id(scope)
            method = str(scope.get("method") or "")
            if session_id:
                if self.sessions.get(session_id) is None:
                    self.sessions.commit(session_id)
                if method == "DELETE":
                    await self.inner(scope, receive, send)
                    self.sessions.close(session_id)
                    return
                await self.inner(scope, receive, send)
                return
            if not self.sessions.can_create():
                response = PlainTextResponse("MCP session capacity reached", status_code=503)
                await response(scope, receive, send)
                return
            captured: dict[str, str] = {}

            async def send_wrapper(message: dict[str, Any]) -> None:
                if message.get("type") == "http.response.start":
                    for key, value in message.get("headers") or []:
                        if key.lower() == b"mcp-session-id":
                            captured["id"] = value.decode("latin1")
                await send(message)

            await self.inner(scope, receive, send_wrapper)
            if captured.get("id"):
                self.sessions.commit(captured["id"])

    app = Starlette(
        routes=[
            Route("/health", health),
            Route(path, endpoint=McpASGI(mcp_asgi, store)),
        ],
        lifespan=lambda _app: session_manager.run(),
    )
    app.add_middleware(OriginAndTokenMiddleware, token=token)
    return app
