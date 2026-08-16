from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.testclient import TestClient

from chatgpt_mcp.adapter import ToolAdapter
from chatgpt_mcp.allowlist import SKIPPED, SPECS, annotations_for, names_for_mode
from chatgpt_mcp.connect import main
from chatgpt_mcp.server import create_app
from chatgpt_mcp.spill import MAX_BYTES, SPILL_DIR, maybe_spill
from chatgpt_mcp.tunnel import start_cloudflare_tunnel
from openharness.tools.base import ToolResult


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "hello.txt").write_text("hi\n", encoding="utf-8")
    return tmp_path


@pytest.mark.asyncio
async def test_health(workspace: Path) -> None:
    app = create_app(approved_root=workspace, mode="read", token=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["ok"] is True


@pytest.mark.asyncio
async def test_wrong_token_is_404(workspace: Path) -> None:
    app = create_app(approved_root=workspace, mode="read", token="a" * 32)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1") as client:
        missing = await client.post("/mcp", json={})
        wrong = await client.post("/t/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb/mcp", json={})
    assert missing.status_code == 404
    assert wrong.status_code == 404
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1") as client:
        forbidden = await client.get("/health", headers={"origin": "https://evil.example"})
    assert forbidden.status_code == 403


@pytest.mark.asyncio
async def test_glob_absolute_pattern_outside_root_denied(workspace: Path) -> None:
    adapter = ToolAdapter(approved_root=workspace, mode="read")
    outside = workspace.parent / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")

    result = await adapter.call(
        "glob",
        {"pattern": str(outside / "**" / "*")},
    )

    assert result.is_error


@pytest.mark.asyncio
async def test_glob_parent_pattern_outside_root_denied(workspace: Path) -> None:
    adapter = ToolAdapter(approved_root=workspace, mode="read")

    result = await adapter.call(
        "glob",
        {"pattern": "../**/*"},
    )

    assert result.is_error


@pytest.mark.asyncio
async def test_glob_absolute_pattern_inside_root_allowed(workspace: Path) -> None:
    source = workspace / "src"
    source.mkdir()
    (source / "app.py").write_text("print('ok')\n", encoding="utf-8")

    adapter = ToolAdapter(approved_root=workspace, mode="read")
    result = await adapter.call(
        "glob",
        {"pattern": str(source / "**" / "*.py")},
    )

    assert not result.is_error
    assert "app.py" in result.output


@pytest.mark.asyncio
async def test_path_outside_root_denied(workspace: Path) -> None:
    adapter = ToolAdapter(approved_root=workspace, mode="write")
    outside = str(workspace.parent / "secret.txt")
    for name, args in (
        ("read_file", {"path": outside}),
        ("write_file", {"path": outside, "content": "x"}),
        ("glob", {"pattern": "*", "root": outside}),
        ("glob", {"pattern": str(Path(outside) / "**" / "*")}),
        ("grep", {"pattern": "x", "root": outside}),
        ("read_many", {"paths": [outside]}),
        ("apply_changes", {"changes": [{"op": "write", "path": outside, "content": "x"}]}),
    ):
        result = await adapter.call(name, args)
        assert result.is_error, name


@pytest.mark.asyncio
async def test_bash_cwd_outside_root(workspace: Path) -> None:
    adapter = ToolAdapter(approved_root=workspace, mode="write")
    outside = str(workspace.parent)
    denied = await adapter.call("bash", {"command": "echo hi", "cwd": outside})
    assert denied.is_error
    ok = await adapter.call("bash", {"command": "pwd"})
    assert workspace.name in ok.output or str(workspace) in ok.output or ok.output


@pytest.mark.asyncio
async def test_missing_cloudflared_exits_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    from chatgpt_mcp.tunnel import TunnelError, start_cloudflare_tunnel

    monkeypatch.setattr("chatgpt_mcp.tunnel.shutil.which", lambda _name: None)
    with pytest.raises(TunnelError):
        await start_cloudflare_tunnel("http://127.0.0.1:8787")


def test_connect_cli_nonzero_when_tunnel_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from chatgpt_mcp.tunnel import TunnelError

    async def boom(*_args: object, **_kwargs: object) -> None:
        raise TunnelError("cloudflared not found")

    monkeypatch.setattr("chatgpt_mcp.connect.start_cloudflare_tunnel", boom)
    code = main(["--root", str(tmp_path.resolve()), "--mode", "read", "--tunnel", "cloudflare", "--port", "0"])
    assert code != 0


def test_bash_is_marked_open_world() -> None:
    spec = next(item for item in SPECS if item.name == "bash")
    hints = annotations_for(spec)
    assert hints.destructive_hint is True
    assert hints.open_world_hint is True


def test_tools_list_profile(workspace: Path) -> None:
    read = names_for_mode("read")
    write = names_for_mode("write")
    assert read == (
        "fast_context",
        "read_file",
        "glob",
        "grep",
        "lsp",
        "read_many",
    )
    assert "route_preview" not in read
    assert write == (
        "fast_context",
        "read_file",
        "glob",
        "grep",
        "lsp",
        "read_many",
        "write_file",
        "edit_file",
        "apply_changes",
        "verify_project",
        "isolated_change",
        "long_task",
        "bash",
    )
    assert "route_preview" not in write
    debug_read = names_for_mode("read", debug_tools=True)
    assert "route_preview" in debug_read
    for name in (*SKIPPED, "cron_list", "task_create", "web_search", "notebook_edit"):
        assert name not in write


@pytest.mark.asyncio
async def test_hidden_tools_are_unavailable(workspace: Path) -> None:
    adapter = ToolAdapter(approved_root=workspace, mode="write")
    result = await adapter.call(
        "task_create",
        {"type": "local_agent", "description": "x", "prompt": "hi"},
    )
    assert result.is_error
    assert "not available" in result.output


@pytest.mark.asyncio
async def test_route_preview_is_debug_only(workspace: Path) -> None:
    hidden = ToolAdapter(approved_root=workspace, mode="read")
    denied = await hidden.call("route_preview", {"prompt": "Find where hello is"})
    assert denied.is_error
    debug = ToolAdapter(approved_root=workspace, mode="read", debug_tools=True)
    preview = await debug.call("route_preview", {"prompt": "Find where hello is"})
    assert not preview.is_error
    assert "route:" in preview.output


def test_spill_keeps_small_output(workspace: Path) -> None:
    result = maybe_spill(
        root=workspace,
        tool="bash",
        result=ToolResult(output="hi\n"),
    )
    assert result.output == "hi\n"
    assert not (workspace / SPILL_DIR).exists()


def test_spill_writes_full_text_and_preview(workspace: Path) -> None:
    huge = ("x" * 80 + "\n") * 3000
    result = maybe_spill(root=workspace, tool="bash", result=ToolResult(output=huge))
    assert result.output != huge
    assert len(result.output.encode("utf-8")) <= MAX_BYTES
    assert SPILL_DIR in result.output
    spilled = list((workspace / SPILL_DIR).glob("bash-*.txt"))
    assert len(spilled) == 1
    assert spilled[0].read_text(encoding="utf-8") == huge


@pytest.mark.asyncio
async def test_read_many_and_apply_changes(workspace: Path) -> None:
    (workspace / "a.txt").write_text("aaa\n", encoding="utf-8")
    (workspace / "b.txt").write_text("bbb\n", encoding="utf-8")
    adapter = ToolAdapter(approved_root=workspace, mode="write")
    many = await adapter.call("read_many", {"paths": ["a.txt", "b.txt"]})
    assert not many.is_error
    assert "aaa" in many.output and "bbb" in many.output
    applied = await adapter.call(
        "apply_changes",
        {
            "changes": [
                {"op": "write", "path": "c.txt", "content": "ccc\n"},
                {"op": "edit", "path": "a.txt", "old_str": "aaa", "new_str": "AAA"},
            ]
        },
    )
    assert not applied.is_error, applied.output
    assert (workspace / "c.txt").read_text(encoding="utf-8") == "ccc\n"
    assert "AAA" in (workspace / "a.txt").read_text(encoding="utf-8")
    before = (workspace / "b.txt").read_text(encoding="utf-8")
    failed = await adapter.call(
        "apply_changes",
        {
            "changes": [
                {"op": "write", "path": "b.txt", "content": "mutated\n"},
                {"op": "edit", "path": "a.txt", "old_str": "missing", "new_str": "x"},
            ]
        },
    )
    assert failed.is_error
    assert (workspace / "b.txt").read_text(encoding="utf-8") == before
    stacked = await adapter.call(
        "apply_changes",
        {
            "changes": [
                {"op": "edit", "path": "a.txt", "old_str": "AAA", "new_str": "one"},
                {"op": "edit", "path": "a.txt", "old_str": "one", "new_str": "two"},
            ]
        },
    )
    assert not stacked.is_error, stacked.output
    assert "two" in (workspace / "a.txt").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_bash_spill_on_huge_output(workspace: Path) -> None:
    (workspace / "huge.txt").write_text("x" * 60_000, encoding="utf-8")
    adapter = ToolAdapter(approved_root=workspace, mode="write")
    result = await adapter.call("bash", {"command": "cat huge.txt"})
    assert not result.is_error, result.output
    assert SPILL_DIR in result.output
    spilled = list((workspace / SPILL_DIR).glob("bash-*.txt"))
    assert len(spilled) == 1
    assert "x" * 1000 in spilled[0].read_text(encoding="utf-8")


INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"},
    },
}


def test_tools_list_and_session_get_delete(workspace: Path) -> None:
    app = create_app(approved_root=workspace, mode="read", token=None, json_response=True)
    headers = {
        "accept": "application/json, text/event-stream",
        "content-type": "application/json",
    }
    with TestClient(app) as client:
        started = client.post("/mcp", json=INIT, headers=headers)
        assert started.status_code < 400, started.text
        session = started.headers.get("mcp-session-id")
        assert session
        session_headers = {**headers, "mcp-session-id": session}
        client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers=session_headers,
        )
        listed = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            headers=session_headers,
        )
        assert listed.status_code < 400, listed.text
        body = listed.json()
        names = [tool["name"] for tool in body["result"]["tools"]]
        assert names == list(names_for_mode("read"))
        assert "write_file" not in names
        called = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "fast_context",
                    "arguments": {"prompt": "Find references to hello"},
                },
            },
            headers=session_headers,
        )
        assert called.status_code < 400, called.text
        called_body = called.json()
        assert called_body["result"]["isError"] is False
        called_text = called_body["result"]["content"][0]["text"]
        assert "fast_context" in called_text or "route:" in called_text
        hidden = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "route_preview",
                    "arguments": {"prompt": "Find references to hello"},
                },
            },
            headers=session_headers,
        )
        hidden_body = hidden.json()
        assert hidden_body["result"]["isError"] is True
        deleted = client.delete("/mcp", headers=session_headers)
        assert deleted.status_code < 500
        missing = client.get("/mcp", headers=session_headers)
        assert missing.status_code in {400, 404}


def test_session_cap_allows_existing_traffic(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("chatgpt_mcp.server.MAX_SESSIONS", 1)
    app = create_app(approved_root=workspace, mode="read", token=None, json_response=True)
    headers = {
        "accept": "application/json, text/event-stream",
        "content-type": "application/json",
    }
    with TestClient(app) as client:
        first = client.post("/mcp", json=INIT, headers=headers)
        assert first.status_code < 400, first.text
        session = first.headers.get("mcp-session-id")
        assert session
        session_headers = {**headers, "mcp-session-id": session}
        listed = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            headers=session_headers,
        )
        assert listed.status_code < 400, listed.text
        second = client.post("/mcp", json=INIT, headers=headers)
        assert second.status_code == 503
        deleted = client.delete("/mcp", headers=session_headers)
        assert deleted.status_code != 503


class _FakeTunnelProcess:
    def __init__(self) -> None:
        self.returncode = None
        self._lines = [
            b"starting\n",
            b"https://example.trycloudflare.com\n",
        ]
        self._extra = 0
        self.stdout = self

    async def readline(self) -> bytes:
        if self._lines:
            return self._lines.pop(0)
        return b""

    async def read(self, _n: int = -1) -> bytes:
        self._extra += 1
        if self._extra > 8:
            return b""
        await asyncio.sleep(0)
        return b"log line that would fill a pipe\n"

    async def wait(self) -> int:
        return 0

    def kill(self) -> None:
        self.returncode = -1


@pytest.mark.asyncio
async def test_cloudflare_tunnel_keeps_draining_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeTunnelProcess()

    async def spawn(*_args: object, **_kwargs: object) -> _FakeTunnelProcess:
        return fake

    url, proc = await start_cloudflare_tunnel("http://127.0.0.1:8787", spawn=spawn)
    assert url == "https://example.trycloudflare.com"
    assert proc is fake
    await asyncio.sleep(0.05)
    assert fake._extra > 0


@pytest.mark.asyncio
async def test_bash_large_output_does_not_deadlock(workspace: Path) -> None:
    adapter = ToolAdapter(approved_root=workspace, mode="write")

    (workspace / "huge.txt").write_text("x" * 2_000_000, encoding="utf-8")
    result = await adapter.call(
        "bash",
        {
            "command": "cat huge.txt",
            "timeout_seconds": 10,
        },
    )

    assert not result.is_error
    assert ".opengpt-spill/" in result.output
