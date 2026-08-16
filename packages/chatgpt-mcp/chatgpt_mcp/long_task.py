from __future__ import annotations

from pathlib import Path
from typing import Any

from openharness.tasks.manager import BackgroundTaskManager
from openharness.tools.base import ToolResult

from chatgpt_mcp.jail import jail_path


class LongTaskService:
    """local_bash-only wrapper around OpenHarness BackgroundTaskManager."""

    def __init__(self, *, approved_root: Path, manager: BackgroundTaskManager | None = None) -> None:
        self.approved_root = approved_root.resolve()
        self._manager = manager or BackgroundTaskManager()

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        action = str(arguments.get("action") or "start").strip().lower()
        if action == "start":
            return await self._start(arguments)
        if action == "output":
            return self._output(arguments)
        if action == "stop":
            return await self._stop(arguments)
        if action == "list":
            return self._list()
        return ToolResult(output=f"unknown action: {action}", is_error=True)

    async def _start(self, arguments: dict[str, Any]) -> ToolResult:
        description = str(arguments.get("description") or "long_task").strip() or "long_task"
        command = arguments.get("command")
        argv = arguments.get("argv")
        if command in (None, "") and not argv:
            return ToolResult(output="command or argv is required", is_error=True)
        if command not in (None, "") and argv:
            return ToolResult(output="pass only one of command or argv", is_error=True)
        if argv is not None:
            if not isinstance(argv, list) or not all(isinstance(item, str) and item for item in argv):
                return ToolResult(output="argv must be a non-empty list of strings", is_error=True)
        cwd = self.approved_root
        requested = arguments.get("cwd")
        if requested:
            jailed, reason = jail_path(self.approved_root, str(requested))
            if reason or jailed is None:
                return ToolResult(output=reason or "cwd outside approved root is denied", is_error=True)
            cwd = jailed
        try:
            task = await self._manager.create_shell_task(
                command=None if argv else str(command),
                argv=list(argv) if argv else None,
                description=description,
                cwd=cwd,
                task_type="local_bash",
            )
        except Exception as exc:
            return ToolResult(output=str(exc), is_error=True)
        return ToolResult(
            output=f"started {task.id} status={task.status} description={task.description}"
        )

    def _output(self, arguments: dict[str, Any]) -> ToolResult:
        task_id = str(arguments.get("task_id") or "").strip()
        if not task_id:
            return ToolResult(output="task_id is required", is_error=True)
        max_bytes = int(arguments.get("max_bytes") or 12000)
        try:
            task = self._manager.get_task(task_id)
            if task is None:
                return ToolResult(output=f"No task found with ID: {task_id}", is_error=True)
            text = self._manager.read_task_output(task_id, max_bytes=max_bytes) or "(no output)"
        except ValueError as exc:
            return ToolResult(output=str(exc), is_error=True)
        return ToolResult(output=f"status={task.status} rc={task.return_code}\n{text}")

    async def _stop(self, arguments: dict[str, Any]) -> ToolResult:
        task_id = str(arguments.get("task_id") or "").strip()
        if not task_id:
            return ToolResult(output="task_id is required", is_error=True)
        try:
            task = await self._manager.stop_task(task_id)
        except ValueError as exc:
            return ToolResult(output=str(exc), is_error=True)
        return ToolResult(output=f"stopped {task.id} status={task.status}")

    def _list(self) -> ToolResult:
        tasks = self._manager.list_tasks()
        if not tasks:
            return ToolResult(output="(no tasks)")
        lines = [f"{item.id} {item.status} {item.description}" for item in tasks]
        return ToolResult(output="\n".join(lines))
