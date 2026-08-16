from __future__ import annotations

import time
from pathlib import Path

import pytest
from openharness.tools.base import ToolResult

from chatgpt_mcp.fast_context import FastContextService
from chatgpt_mcp.routing import decide_route
from chatgpt_mcp.spill import MAX_BYTES, SPILL_DIR, maybe_spill
from test_routing import TEST_TOOL_ENTRIES


class FakeCaller:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.grep_output = (
            "src/auth/token.py:10:validate_refresh_token\n"
            "src/auth/session.py:44:refresh token\n"
        )
        self.read_output = (
            "=== src/auth/token.py ===\n"
            "10\tdef validate_refresh_token(...):\n\n"
            "=== src/auth/session.py ===\n"
            "44\tdef refresh_session(...):\n"
        )

    async def __call__(
        self,
        name: str,
        arguments: dict[str, object],
    ) -> ToolResult:
        self.calls.append((name, arguments))
        if name == "grep":
            return ToolResult(output=self.grep_output)
        if name == "read_many":
            return ToolResult(output=self.read_output)
        if name == "lsp":
            return ToolResult(output="(no results)")
        raise AssertionError(name)


@pytest.mark.asyncio
async def test_fast_context_searches_then_reads_matching_files() -> None:
    caller = FakeCaller()
    service = FastContextService(caller)

    result = await service.run(
        "Find where JWT refresh tokens are validated",
        max_files=6,
        lines_per_file=160,
    )

    assert not result.is_error
    assert caller.calls[0][0] == "grep"
    assert caller.calls[1][0] == "read_many"
    assert "src/auth/token.py" in result.output
    assert len(caller.calls) <= 3


@pytest.mark.asyncio
async def test_fast_context_dedupes_and_caps_files() -> None:
    caller = FakeCaller()
    caller.grep_output = (
        "src/a.py:1:jwt\n"
        "src/a.py:2:jwt\n"
        "src/b.py:1:jwt\n"
        "src/c.py:1:jwt\n"
        "src/d.py:1:jwt\n"
    )
    service = FastContextService(caller)
    await service.run("Find where jwt is used", max_files=2)
    read_args = [args for name, args in caller.calls if name == "read_many"]
    assert len(read_args) == 1
    assert read_args[0]["paths"] == ["src/a.py", "src/b.py"]


@pytest.mark.asyncio
async def test_fast_context_skips_read_when_grep_has_no_matches() -> None:
    caller = FakeCaller()
    caller.grep_output = "(no matches)"
    service = FastContextService(caller)
    result = await service.run("Find where JWT refresh tokens are validated")
    assert "(no matches)" in result.output
    assert all(name != "read_many" for name, _args in caller.calls)


@pytest.mark.asyncio
async def test_fast_context_uses_bounded_primitive_calls() -> None:
    caller = FakeCaller()
    service = FastContextService(caller)
    await service.run("Find where JWT refresh tokens are validated")
    assert len(caller.calls) <= 4


@pytest.mark.asyncio
async def test_fast_context_reads_one_batch() -> None:
    caller = FakeCaller()
    caller.grep_output = "\n".join(f"src/f{i}.py:1:jwt" for i in range(6)) + "\n"
    service = FastContextService(caller)
    await service.run("Find where JWT refresh tokens are validated", max_files=6)
    read_calls = [args for name, args in caller.calls if name == "read_many"]
    assert len(read_calls) == 1
    assert len(read_calls[0]["paths"]) <= 6


def test_route_decision_overhead_stays_bounded() -> None:
    started = time.perf_counter()
    for _ in range(1000):
        decide_route(
            "Find where JWT refresh tokens are validated",
            tool_entries=TEST_TOOL_ENTRIES,
        )
    elapsed = time.perf_counter() - started
    assert elapsed < 1.0


@pytest.mark.asyncio
async def test_fast_context_large_output_uses_existing_spill(tmp_path: Path) -> None:
    caller = FakeCaller()
    caller.read_output = ("x" * 80 + "\n") * 3000
    service = FastContextService(caller)
    result = await service.run("Find where JWT refresh tokens are validated")
    spilled = maybe_spill(root=tmp_path, tool="fast_context", result=result)
    assert spilled.output != result.output
    assert len(spilled.output.encode("utf-8")) <= MAX_BYTES
    assert SPILL_DIR in spilled.output
