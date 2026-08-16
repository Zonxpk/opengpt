from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
import re
from typing import Any

from openharness.tools.base import ToolResult

from chatgpt_mcp.allowlist import routing_tool_entries
from chatgpt_mcp.routing import RouteDecision, RouteName, decide_route, recommend_skills, _IDENTIFIER

PrimitiveCaller = Callable[[str, dict[str, Any]], Awaitable[ToolResult]]


def _grep_pattern(decision: RouteDecision, prompt: str) -> str:
    terms = list(decision.search_terms[:6])
    if not terms:
        terms = [
            token
            for token in decision.tokens
            if _IDENTIFIER.match(token) or token.isalnum()
        ][:6]
    if not terms:
        terms = [re.sub(r"[^A-Za-z0-9_]+", " ", prompt).strip().split()[0] or "code"]
    return "|".join(re.escape(term) for term in terms)


def _paths_from_grep(output: str) -> list[str]:
    seen: set[str] = set()
    paths: list[str] = []
    for line in output.splitlines():
        if not line or line.startswith(("(", "[")):
            continue
        path, sep, _rest = line.partition(":")
        if not sep or not path or path in seen:
            continue
        seen.add(path)
        paths.append(path)
    return paths


def _paths_from_lsp(output: str) -> list[str]:
    seen: set[str] = set()
    paths: list[str] = []
    for line in output.splitlines():
        if not line or line.startswith(("(", "[")):
            continue
        marker = " - "
        if marker in line:
            location = line.split(marker, 1)[1]
            path = location.partition(":")[0].strip()
        else:
            path, sep, _rest = line.partition(":")
            if not sep:
                continue
            path = path.strip()
        if not path or path in seen:
            continue
        seen.add(path)
        paths.append(path)
    return paths


_SKILL_MIN_SCORE = 8
_SKILL_CONTENT_CHARS = 3500
_MEMORY_CHARS = 1500


def _header(decision: RouteDecision, file_count: int) -> str:
    terms = ", ".join(decision.search_terms) or "(none)"
    return (
        "[OpenGPT fast_context]\n"
        f"route: {decision.route.value}\n"
        f"terms: {terms}\n"
        f"files: {file_count}\n"
    )


class FastContextService:
    def __init__(
        self,
        call_primitive: PrimitiveCaller,
        *,
        cwd: Path | None = None,
        skills: Sequence[object] | None = None,
        include_memory: bool = True,
    ) -> None:
        self._call = call_primitive
        self._cwd = cwd
        self._skills = skills
        self._include_memory = include_memory

    def preview(self, prompt: str) -> RouteDecision:
        return decide_route(prompt, tool_entries=routing_tool_entries())

    async def run(
        self,
        prompt: str,
        *,
        max_files: int = 6,
        lines_per_file: int = 160,
        include_lsp: bool = True,
    ) -> ToolResult:
        max_files = min(max(max_files, 1), 12)
        lines_per_file = min(max(lines_per_file, 20), 400)
        decision = self.preview(prompt)
        if decision.route == RouteName.SYMBOL and include_lsp:
            repo = await self._symbol(prompt, decision, max_files, lines_per_file)
        elif decision.route == RouteName.SEARCH:
            repo = await self._search(prompt, decision)
        else:
            repo = await self._search_and_inspect(prompt, decision, max_files, lines_per_file)
        if repo.is_error:
            return repo
        return ToolResult(output=self._with_extras(prompt, repo.output))

    async def _grep(self, prompt: str, decision: RouteDecision) -> ToolResult:
        return await self._call(
            "grep",
            {
                "pattern": _grep_pattern(decision, prompt),
                "root": ".",
                "case_sensitive": False,
                "limit": 100,
                "timeout_seconds": 20,
            },
        )

    async def _search(self, prompt: str, decision: RouteDecision) -> ToolResult:
        grep = await self._grep(prompt, decision)
        if grep.is_error:
            return grep
        return ToolResult(output=f"{_header(decision, 0)}\n{grep.output}")

    async def _search_and_inspect(
        self,
        prompt: str,
        decision: RouteDecision,
        max_files: int,
        lines_per_file: int,
    ) -> ToolResult:
        grep = await self._grep(prompt, decision)
        if grep.is_error:
            return grep
        if "(no matches)" in grep.output:
            return ToolResult(output=f"{_header(decision, 0)}\n{grep.output}")
        paths = _paths_from_grep(grep.output)[:max_files]
        if not paths:
            return ToolResult(output=f"{_header(decision, 0)}\n{grep.output}")
        read = await self._call(
            "read_many",
            {"paths": paths, "limit": lines_per_file},
        )
        if read.is_error:
            return read
        return ToolResult(
            output=f"{_header(decision, len(paths))}\n{read.output}"
        )

    async def _symbol(
        self,
        prompt: str,
        decision: RouteDecision,
        max_files: int,
        lines_per_file: int,
    ) -> ToolResult:
        term = next((item for item in decision.search_terms if _IDENTIFIER.match(item)), "")
        if not term:
            return await self._search_and_inspect(prompt, decision, max_files, lines_per_file)
        lsp = await self._call(
            "lsp",
            {"operation": "workspace_symbol", "query": term},
        )
        useful = (
            not lsp.is_error
            and lsp.output.strip()
            and "(no results)" not in lsp.output
        )
        if not useful:
            return await self._search_and_inspect(prompt, decision, max_files, lines_per_file)
        paths = _paths_from_lsp(lsp.output)[:max_files]
        if not paths:
            return await self._search_and_inspect(prompt, decision, max_files, lines_per_file)
        read = await self._call(
            "read_many",
            {"paths": paths, "limit": lines_per_file},
        )
        if read.is_error:
            return read
        return ToolResult(
            output=f"{_header(decision, len(paths))}\n{lsp.output}\n\n{read.output}"
        )

    def _with_extras(self, prompt: str, repo_output: str) -> str:
        chunks: list[str] = []
        skill = self._skill_block(prompt)
        if skill:
            chunks.append(skill)
        chunks.append(repo_output.rstrip())
        memory = self._memory_block(prompt)
        if memory:
            chunks.append(memory)
        return "\n\n".join(chunks) + "\n"

    def _usable_skills(self) -> list[object]:
        if self._skills is not None:
            return [
                skill
                for skill in self._skills
                if not getattr(skill, "disable_model_invocation", False)
            ]
        if self._cwd is None:
            return []
        from openharness.skills.bundled import get_bundled_skills
        from openharness.skills.loader import discover_project_skill_dirs, load_skills_from_dirs

        skills: list[object] = [
            skill for skill in get_bundled_skills() if not skill.disable_model_invocation
        ]
        skills.extend(
            load_skills_from_dirs(
                discover_project_skill_dirs(self._cwd),
                source="project",
                create_missing=False,
            )
        )
        return skills

    def _skill_block(self, prompt: str) -> str:
        skills = self._usable_skills()
        if not skills:
            return ""
        matches = recommend_skills(prompt, skills)
        if not matches or matches[0].score < _SKILL_MIN_SCORE:
            return ""
        top = matches[0]
        skill = next((item for item in skills if getattr(item, "name", "") == top.name), None)
        if skill is None:
            return ""
        content = str(getattr(skill, "content", "")).strip()[:_SKILL_CONTENT_CHARS]
        return (
            f"## skill hint: {top.name} (score {top.score})\n"
            "Follow these instructions while using the repository evidence below.\n\n"
            f"{content}"
        )

    def _memory_block(self, prompt: str) -> str:
        if not self._include_memory or self._cwd is None:
            return ""
        from openharness.memory.relevance import format_relevant_memories, select_relevant_memories

        selected = select_relevant_memories(prompt, self._cwd, max_results=3, selector=None)
        if not selected:
            return ""
        rendered = format_relevant_memories(selected, max_chars=_MEMORY_CHARS).strip()
        return (
            "## memory (secondary; current repository evidence above wins)\n"
            f"{rendered}"
        )
