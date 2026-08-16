from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from openharness.config.settings import PermissionSettings, SandboxSettings, Settings
from openharness.permissions.checker import PermissionChecker
from openharness.permissions.modes import PermissionMode
from openharness.tools.base import ToolExecutionContext, ToolResult
from openharness.tools.file_edit_tool import FileEditTool
from openharness.tools.file_read_tool import FileReadTool
from openharness.tools.file_write_tool import FileWriteTool
from openharness.tools.glob_tool import GlobTool
from openharness.tools.grep_tool import GrepTool
from openharness.utils.shell import create_shell_subprocess

from chatgpt_mcp.allowlist import names_for_mode
from chatgpt_mcp.jail import jail_path

_EXPLICIT_SETTINGS = Settings(
    sandbox=SandboxSettings(enabled=False),
    permission=PermissionSettings(mode=PermissionMode.FULL_AUTO),
)


class ToolAdapter:
    def __init__(self, *, approved_root: Path, mode: str) -> None:
        self.approved_root = approved_root.resolve()
        self.mode = mode
        self.allowed = set(names_for_mode(mode))
        self._checker = PermissionChecker(_EXPLICIT_SETTINGS.permission)
        self._tools = {
            "read_file": FileReadTool(),
            "glob": GlobTool(),
            "grep": GrepTool(),
            "write_file": FileWriteTool(),
            "edit_file": FileEditTool(),
        }
        self._context = ToolExecutionContext(cwd=self.approved_root)

    def instructions(self) -> str:
        text = (
            f"One approved workspace root: {self.approved_root}. Mode: {self.mode}. "
            "All file, glob, and grep paths are jailed to that root after resolve; symlink escape is denied. "
            "bash is a host-equivalent shell with cwd pinned to the root. No Docker jail."
        )
        return text[:512]

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        if name not in self.allowed:
            return ToolResult(output=f"Tool not available in {self.mode} mode: {name}", is_error=True)
        try:
            if name == "bash":
                return await self._bash(arguments)
            return await self._fileish(name, arguments)
        except Exception as exc:
            return ToolResult(output=str(exc), is_error=True)

    async def _fileish(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        path_key = "path" if "path" in arguments or name in {"read_file", "write_file", "edit_file"} else "root"
        raw_path = arguments.get("path") if path_key == "path" else arguments.get("root")
        if name in {"glob", "grep"}:
            candidate = arguments.get("root") or "."
            jailed, reason = jail_path(self.approved_root, candidate)
            if reason:
                return ToolResult(output=reason, is_error=True)
            denied = self._sensitive(str(jailed))
            if denied:
                return ToolResult(output=denied, is_error=True)
            parsed = self._tools[name].input_model.model_validate({**arguments, "root": str(jailed)})
        else:
            jailed, reason = jail_path(self.approved_root, arguments.get("path"))
            if reason:
                return ToolResult(output=reason, is_error=True)
            denied = self._sensitive(str(jailed))
            if denied:
                return ToolResult(output=denied, is_error=True)
            parsed = self._tools[name].input_model.model_validate({**arguments, "path": str(jailed)})
        return await self._tools[name].execute(parsed, self._context)

    async def _bash(self, arguments: dict[str, Any]) -> ToolResult:
        requested_cwd = arguments.get("cwd")
        if requested_cwd:
            jailed, reason = jail_path(self.approved_root, str(requested_cwd))
            if reason or jailed is None:
                return ToolResult(
                    output=reason or "bash cwd outside approved root is denied",
                    is_error=True,
                )
        command = str(arguments.get("command") or "")
        timeout = int(arguments.get("timeout_seconds") or 600)
        process = await create_shell_subprocess(
            command,
            cwd=self.approved_root,
            settings=_EXPLICIT_SETTINGS,
            prefer_pty=True,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            await asyncio.wait_for(process.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return ToolResult(output=f"timed out after {timeout}s", is_error=True)
        raw = b""
        if process.stdout is not None:
            raw = await process.stdout.read()
        text = raw.decode("utf-8", errors="replace")
        return ToolResult(output=text, is_error=process.returncode != 0)

    def _sensitive(self, file_path: str) -> str | None:
        decision = self._checker.evaluate("path", is_read_only=True, file_path=file_path)
        if not decision.allowed:
            return decision.reason
        return None
