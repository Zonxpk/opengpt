from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from chatgpt_mcp.adapter import ToolAdapter
from chatgpt_mcp.long_task import LongTaskService
from openharness.tasks.manager import BackgroundTaskManager


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "oh-data"
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(target))
    return target


@pytest.mark.asyncio
async def test_long_task_start_output_argv(tmp_path: Path, data_dir: Path) -> None:
    service = LongTaskService(approved_root=tmp_path, manager=BackgroundTaskManager())
    started = await service.run(
        {
            "action": "start",
            "description": "echo",
            "argv": [sys.executable, "-c", "print('LONG_TASK_OK')"],
        }
    )
    assert not started.is_error
    task_id = started.output.split()[1]
    output = ""
    for _ in range(40):
        result = await service.run({"action": "output", "task_id": task_id})
        output = result.output
        if "LONG_TASK_OK" in output:
            break
        await asyncio.sleep(0.05)
    assert "LONG_TASK_OK" in output
    listed = await service.run({"action": "list"})
    assert task_id in listed.output


@pytest.mark.asyncio
async def test_long_task_rejects_outside_cwd(tmp_path: Path, data_dir: Path) -> None:
    service = LongTaskService(approved_root=tmp_path, manager=BackgroundTaskManager())
    result = await service.run(
        {
            "action": "start",
            "command": "echo hi",
            "cwd": str(tmp_path.parent),
        }
    )
    assert result.is_error


@pytest.mark.asyncio
async def test_long_task_is_write_only(tmp_path: Path) -> None:
    adapter = ToolAdapter(approved_root=tmp_path, mode="read")
    denied = await adapter.call("long_task", {"action": "list"})
    assert denied.is_error
    assert "not available" in denied.output


@pytest.mark.asyncio
async def test_adapter_long_task_dispatches(tmp_path: Path, data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from openharness.tools.base import ToolResult

    async def fake_run(self, arguments):
        return ToolResult(output=f"ok:{arguments.get('action')}", is_error=False)

    monkeypatch.setattr(LongTaskService, "run", fake_run)
    adapter = ToolAdapter(approved_root=tmp_path, mode="write")
    result = await adapter.call("long_task", {"action": "list"})
    assert not result.is_error
    assert result.output == "ok:list"
