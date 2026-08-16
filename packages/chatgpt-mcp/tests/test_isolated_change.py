from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from openharness.tools.base import ToolResult

from chatgpt_mcp.adapter import ToolAdapter
from chatgpt_mcp.isolated_change import IsolatedChangeService
from chatgpt_mcp.verify_project import VerifyProjectService


def _git_env() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _git_repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    env = _git_env()
    subprocess.run(["git", "init"], cwd=str(root), check=True, capture_output=True, env=env)
    subprocess.run(["git", "config", "user.email", "t@t.example"], cwd=str(root), check=True, capture_output=True, env=env)
    subprocess.run(["git", "config", "user.name", "t"], cwd=str(root), check=True, capture_output=True, env=env)
    (root / "hello.txt").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "hello.txt"], cwd=str(root), check=True, capture_output=True, env=env)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(root), check=True, capture_output=True, env=env)
    return root


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return _git_repo(tmp_path / "main")


@pytest.mark.asyncio
async def test_isolated_change_does_not_touch_main_tree(
    repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_run(self, cwd):
        return ToolResult(output=f"verified:{cwd.name}", is_error=False)

    monkeypatch.setattr(VerifyProjectService, "run", fake_run)
    service = IsolatedChangeService(
        approved_root=repo,
        worktree_base=tmp_path / "wt",
    )
    result = await service.run(
        [{"op": "edit", "path": "hello.txt", "old_str": "hello", "new_str": "world"}],
        slug="demo",
    )
    assert not result.is_error
    assert (repo / "hello.txt").read_text(encoding="utf-8") == "hello\n"
    assert "world" in result.output
    assert "kept: true" in result.output
    assert (tmp_path / "wt" / "demo" / "hello.txt").read_text(encoding="utf-8") == "world\n"


@pytest.mark.asyncio
async def test_isolated_change_keeps_worktree_on_verify_failure(
    repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_run(self, cwd):
        return ToolResult(output="boom", is_error=True)

    monkeypatch.setattr(VerifyProjectService, "run", fake_run)
    service = IsolatedChangeService(approved_root=repo, worktree_base=tmp_path / "wt")
    result = await service.run(
        [{"op": "write", "path": "hello.txt", "content": "x\n"}],
        slug="failcase",
    )
    assert result.is_error
    assert (tmp_path / "wt" / "failcase").exists()
    assert (repo / "hello.txt").read_text(encoding="utf-8") == "hello\n"


@pytest.mark.asyncio
async def test_isolated_change_is_write_only(tmp_path: Path) -> None:
    adapter = ToolAdapter(approved_root=tmp_path, mode="read")
    denied = await adapter.call("isolated_change", {"changes": [{"op": "write", "path": "a", "content": "x"}]})
    assert denied.is_error
    assert "not available" in denied.output


@pytest.mark.asyncio
async def test_isolated_change_rejects_dirty_root(
    repo: Path,
    tmp_path: Path,
) -> None:
    (repo / "hello.txt").write_text("dirty\n", encoding="utf-8")
    service = IsolatedChangeService(approved_root=repo, worktree_base=tmp_path / "wt")
    result = await service.run(
        [{"op": "write", "path": "hello.txt", "content": "x\n"}],
        slug="dirty",
    )
    assert result.is_error
    assert "uncommitted" in result.output
    assert not (tmp_path / "wt" / "dirty").exists()
    assert (repo / "hello.txt").read_text(encoding="utf-8") == "dirty\n"


@pytest.mark.asyncio
@pytest.mark.parametrize("shared", [".venv", "node_modules"])
async def test_isolated_change_does_not_follow_shared_symlinks(
    repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    shared: str,
) -> None:
    async def fake_run(self, cwd):
        return ToolResult(output="ok", is_error=False)

    monkeypatch.setattr(VerifyProjectService, "run", fake_run)
    (repo / shared).mkdir()
    (repo / shared / "keep.txt").write_text("keep\n", encoding="utf-8")
    service = IsolatedChangeService(approved_root=repo, worktree_base=tmp_path / "wt")
    result = await service.run(
        [{"op": "write", "path": f"{shared}/foo", "content": "pwned\n"}],
        slug=f"escape-{shared.strip('.')}",
    )
    assert result.is_error
    assert "escapes write root" in result.output
    assert not (repo / shared / "foo").exists()
    assert (repo / shared / "keep.txt").read_text(encoding="utf-8") == "keep\n"
