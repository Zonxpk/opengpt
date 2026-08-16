from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from openharness.autopilot.types import RepoVerificationStep
from openharness.tools.base import ToolResult

from chatgpt_mcp.adapter import ToolAdapter
from chatgpt_mcp.verify_project import VerifyProjectService


@pytest.mark.asyncio
async def test_verify_project_uses_openharness_runner(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    def fake_run(policies: dict[str, Any], *, cwd: Path, availability_cwd: Path | None = None):
        seen["cwd"] = Path(cwd)
        seen["commands"] = policies["verification"]["commands"]
        return [
            RepoVerificationStep(command="uv run pytest -q", returncode=0, status="success"),
        ]

    monkeypatch.setattr("chatgpt_mcp.verify_project.run_verification", fake_run)
    monkeypatch.setattr(
        "chatgpt_mcp.verify_project.load_verification_policy",
        lambda cwd: {"commands": ["uv run pytest -q"]},
    )

    result = await VerifyProjectService().run(tmp_path)

    assert seen["cwd"] == tmp_path
    assert not result.is_error
    assert "Overall: passed" in result.output
    assert "uv run pytest -q" in result.output


@pytest.mark.asyncio
async def test_verify_project_marks_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "chatgpt_mcp.verify_project.load_verification_policy",
        lambda cwd: {"commands": ["uv run pytest -q"]},
    )
    monkeypatch.setattr(
        "chatgpt_mcp.verify_project.run_verification",
        lambda policies, *, cwd, availability_cwd=None: [
            RepoVerificationStep(
                command="uv run pytest -q",
                returncode=1,
                status="failed",
                stderr="1 failed",
            )
        ],
    )

    result = await VerifyProjectService().run(tmp_path)

    assert result.is_error
    assert "Overall: failed" in result.output


@pytest.mark.asyncio
async def test_verify_project_is_write_only(tmp_path: Path) -> None:
    read = ToolAdapter(approved_root=tmp_path, mode="read")
    denied = await read.call("verify_project", {})
    assert denied.is_error
    assert "not available" in denied.output


@pytest.mark.asyncio
async def test_adapter_verify_project_dispatches(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    async def fake_run(self, cwd):
        return ToolResult(output="ok-report", is_error=False)

    monkeypatch.setattr(
        "chatgpt_mcp.verify_project.VerifyProjectService.run",
        fake_run,
    )
    adapter = ToolAdapter(approved_root=tmp_path, mode="write")
    result = await adapter.call("verify_project", {})
    assert not result.is_error
    assert result.output == "ok-report"
