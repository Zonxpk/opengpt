from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.testclient import TestClient

from chatgpt_mcp.adapter import ToolAdapter
from chatgpt_mcp.allowlist import SKIPPED, names_for_mode
from chatgpt_mcp.connect import main
from chatgpt_mcp.server import create_app


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
async def test_path_outside_root_denied(workspace: Path) -> None:
    adapter = ToolAdapter(approved_root=workspace, mode="write")
    outside = str(workspace.parent / "secret.txt")
    for name, args in (
        ("read_file", {"path": outside}),
        ("write_file", {"path": outside, "content": "x"}),
        ("glob", {"pattern": "*", "root": outside}),
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


def test_tools_list_profile(workspace: Path) -> None:
    read = names_for_mode("read")
    write = names_for_mode("write")
    assert "read_file" in read and "read_many" in read and "glob" in read
    assert "write_file" not in read and "apply_changes" not in read
    assert write[-1] == "bash"
    assert "notebook_edit" in write and "web_search" in write and "apply_changes" in write
    for name in SKIPPED:
        assert name not in write


@pytest.mark.asyncio
async def test_task_create_local_agent_denied(workspace: Path) -> None:
    adapter = ToolAdapter(approved_root=workspace, mode="write")
    result = await adapter.call(
        "task_create",
        {"type": "local_agent", "description": "x", "prompt": "hi"},
    )
    assert result.is_error


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
        deleted = client.delete("/mcp", headers=session_headers)
        assert deleted.status_code < 500
        missing = client.get("/mcp", headers=session_headers)
        assert missing.status_code in {400, 404}
