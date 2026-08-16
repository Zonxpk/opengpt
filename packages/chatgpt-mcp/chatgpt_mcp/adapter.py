from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from openharness.config.settings import PermissionSettings, SandboxSettings, Settings
from openharness.permissions.checker import PermissionChecker
from openharness.permissions.modes import PermissionMode
from openharness.tools.base import ToolExecutionContext, ToolRegistry, ToolResult
from openharness.utils.shell import create_shell_subprocess

from chatgpt_mcp.allowlist import MAX_BATCH, PATH_ARG_KEYS, names_for_mode, specs_for_mode
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
            spec.name: spec.factory()
            for spec in specs_for_mode(mode)
            if spec.factory is not None
        }
        registry = ToolRegistry()
        for tool in self._tools.values():
            registry.register(tool)
        self._context = ToolExecutionContext(
            cwd=self.approved_root,
            metadata={"tool_registry": registry},
        )

    def instructions(self) -> str:
        text = (
            f"One approved workspace root: {self.approved_root}. Mode: {self.mode}. "
            "OpenHarness tools are bridged locally; ChatGPT is the only LLM. "
            "Prefer read_many and apply_changes. Paths jailed to the root. "
            "bash cwd is the root. task_create local_agent is denied."
        )
        return text[:512]

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        if name not in self.allowed:
            return ToolResult(output=f"Tool not available in {self.mode} mode: {name}", is_error=True)
        try:
            if name == "bash":
                return await self._bash(arguments)
            if name == "read_many":
                return await self._read_many(arguments)
            if name == "apply_changes":
                return await self._apply_changes(arguments)
            if name == "task_create" and str(arguments.get("type") or "local_bash") == "local_agent":
                return ToolResult(
                    output="task_create type=local_agent is denied; ChatGPT is the only LLM. Use local_bash.",
                    is_error=True,
                )
            return await self._openharness(name, arguments)
        except Exception as exc:
            return ToolResult(output=str(exc), is_error=True)

    async def _openharness(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        tool = self._tools[name]
        patched = dict(arguments)
        for key in PATH_ARG_KEYS:
            if key not in patched or patched[key] in (None, ""):
                continue
            jailed, reason = await self._gate(str(patched[key]))
            if reason or jailed is None:
                return ToolResult(output=reason or "denied", is_error=True)
            patched[key] = str(jailed)
        parsed = tool.input_model.model_validate(patched)
        return await tool.execute(parsed, self._context)

    async def _gate(self, candidate: str | None) -> tuple[Path | None, str | None]:
        jailed, reason = jail_path(self.approved_root, candidate)
        if reason:
            return None, reason
        denied = self._sensitive(str(jailed))
        if denied:
            return None, denied
        return jailed, None

    def _rel(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.approved_root).as_posix()
        except ValueError:
            return str(path)

    async def _read_many(self, arguments: dict[str, Any]) -> ToolResult:
        paths = arguments.get("paths")
        if not isinstance(paths, list) or not paths:
            return ToolResult(output="paths must be a non-empty list", is_error=True)
        if len(paths) > MAX_BATCH:
            return ToolResult(output=f"at most {MAX_BATCH} paths per read_many", is_error=True)
        offset = int(arguments.get("offset") or 0)
        limit = int(arguments.get("limit") or 200)
        chunks: list[str] = []
        for raw in paths:
            jailed, reason = await self._gate(str(raw))
            if reason or jailed is None:
                return ToolResult(output=reason or "denied", is_error=True)
            result = await self._tools["read_file"].execute(
                self._tools["read_file"].input_model.model_validate(
                    {"path": str(jailed), "offset": offset, "limit": limit}
                ),
                self._context,
            )
            if result.is_error:
                return result
            chunks.append(f"=== {self._rel(jailed)} ===\n{result.output}")
        return ToolResult(output="\n\n".join(chunks))

    async def _apply_changes(self, arguments: dict[str, Any]) -> ToolResult:
        changes = arguments.get("changes")
        if not isinstance(changes, list) or not changes:
            return ToolResult(output="changes must be a non-empty list", is_error=True)
        if len(changes) > MAX_BATCH:
            return ToolResult(output=f"at most {MAX_BATCH} changes per apply_changes", is_error=True)
        planned: list[tuple[Path, str]] = []
        for index, change in enumerate(changes):
            if not isinstance(change, dict):
                return ToolResult(output=f"change {index} must be an object", is_error=True)
            op = change.get("op")
            jailed, reason = await self._gate(str(change.get("path") or ""))
            if reason or jailed is None:
                return ToolResult(output=f"change {index}: {reason or 'denied'}", is_error=True)
            if op == "write":
                if "content" not in change:
                    return ToolResult(output=f"change {index}: write requires content", is_error=True)
                planned.append((jailed, str(change["content"])))
            elif op == "edit":
                old = change.get("old_str")
                new = change.get("new_str")
                if not isinstance(old, str) or not isinstance(new, str):
                    return ToolResult(output=f"change {index}: edit requires old_str and new_str", is_error=True)
                if not jailed.exists():
                    return ToolResult(output=f"change {index}: file not found: {self._rel(jailed)}", is_error=True)
                original = jailed.read_text(encoding="utf-8")
                if old not in original:
                    return ToolResult(
                        output=f"change {index}: old_str was not found in {self._rel(jailed)}",
                        is_error=True,
                    )
                if change.get("replace_all"):
                    planned.append((jailed, original.replace(old, new)))
                else:
                    planned.append((jailed, original.replace(old, new, 1)))
            else:
                return ToolResult(output=f"change {index}: op must be write or edit", is_error=True)
        written: list[str] = []
        for path, content in planned:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            written.append(self._rel(path))
        return ToolResult(output="applied:\n" + "\n".join(written))

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
