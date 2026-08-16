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
from chatgpt_mcp.apply_batch import apply_change_list
from chatgpt_mcp.fast_context import FastContextService
from chatgpt_mcp.isolated_change import IsolatedChangeService
from chatgpt_mcp.long_task import LongTaskService
from chatgpt_mcp.verify_project import VerifyProjectService
from chatgpt_mcp.jail import jail_glob_pattern, jail_path
from chatgpt_mcp.spill import maybe_spill

_EXPLICIT_SETTINGS = Settings(
    sandbox=SandboxSettings(enabled=False),
    permission=PermissionSettings(mode=PermissionMode.FULL_AUTO),
)


class ToolAdapter:
    def __init__(self, *, approved_root: Path, mode: str, debug_tools: bool = False) -> None:
        self.approved_root = approved_root.resolve()
        self.mode = mode
        self.debug_tools = debug_tools
        self.allowed = set(names_for_mode(mode, debug_tools=debug_tools))
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
        self._fast_context = FastContextService(self.call, cwd=self.approved_root)
        self._verify_project = VerifyProjectService()
        self._isolated_change = IsolatedChangeService(
            approved_root=self.approved_root,
            sensitive=self._sensitive,
        )
        self._long_task = LongTaskService(approved_root=self.approved_root)

    def instructions(self) -> str:
        text = (
            f"One approved workspace root: {self.approved_root}. Mode: {self.mode}. "
            "Prefer fast_context for exploratory repository questions. "
            "Use primitives when an exact tool call is already known. "
            "Prefer read_many and apply_changes for batching. "
            "Prefer verify_project for repo checks. "
            "Prefer isolated_change to edit+verify off the main tree. "
            "Use long_task for long shell jobs. "
            "File tools are jailed to the root. "
            "bash is a host-equivalent shell (not jailed); cwd is the root. "
            f"Oversized output spills to {self.approved_root.as_posix()}/.opengpt-spill/."
        )
        return text[:512]

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        if name not in self.allowed:
            return ToolResult(output=f"Tool not available in {self.mode} mode: {name}", is_error=True)
        try:
            if name == "route_preview":
                result = self._route_preview(arguments)
            elif name == "fast_context":
                result = await self._fast_context.run(
                    str(arguments.get("prompt") or ""),
                    max_files=int(arguments.get("max_files") or 6),
                    lines_per_file=int(arguments.get("lines_per_file") or 160),
                    include_lsp=bool(arguments.get("include_lsp", True)),
                )
            elif name == "bash":
                result = await self._bash(arguments)
            elif name == "read_many":
                result = await self._read_many(arguments)
            elif name == "apply_changes":
                result = await self._apply_changes(arguments)
            elif name == "verify_project":
                result = await self._verify_project.run(self.approved_root)
            elif name == "isolated_change":
                result = await self._isolated_change.run(
                    arguments.get("changes"),
                    slug=str(arguments["slug"]) if arguments.get("slug") else None,
                )
            elif name == "long_task":
                result = await self._long_task.run(arguments)
            else:
                result = await self._openharness(name, arguments)
            return maybe_spill(root=self.approved_root, tool=name, result=result)
        except Exception as exc:
            return ToolResult(output=str(exc), is_error=True)

    def _route_preview(self, arguments: dict[str, Any]) -> ToolResult:
        prompt = str(arguments.get("prompt") or "").strip()
        if not prompt:
            return ToolResult(output="prompt is required", is_error=True)
        decision = self._fast_context.preview(prompt)
        lines = [
            f"route: {decision.route.value}",
            f"terms: {', '.join(decision.search_terms)}",
            "candidates:",
        ]
        for candidate in decision.candidates:
            lines.append(f"- {candidate.name}: {candidate.score}")
        return ToolResult(output="\n".join(lines))

    async def _openharness(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        tool = self._tools[name]
        patched = dict(arguments)
        if name == "glob" and "pattern" in patched:
            safe_pattern, reason = jail_glob_pattern(
                self.approved_root,
                str(patched["pattern"]),
            )
            if reason or safe_pattern is None:
                return ToolResult(
                    output=reason or "glob pattern outside approved root is denied",
                    is_error=True,
                )
            patched["pattern"] = safe_pattern
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
        return apply_change_list(
            arguments.get("changes"),
            jail_root=self.approved_root,
            write_root=self.approved_root,
            sensitive=self._sensitive,
        )

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
            raw, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return ToolResult(output=f"timed out after {timeout}s", is_error=True)
        text = (raw or b"").decode("utf-8", errors="replace")
        return ToolResult(output=text, is_error=process.returncode != 0)

    def _sensitive(self, file_path: str) -> str | None:
        decision = self._checker.evaluate("path", is_read_only=True, file_path=file_path)
        if not decision.allowed:
            return decision.reason
        return None
