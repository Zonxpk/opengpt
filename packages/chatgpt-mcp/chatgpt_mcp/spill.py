from __future__ import annotations

from datetime import datetime, timezone
from itertools import count
from pathlib import Path

from openharness.tools.base import ToolResult

SPILL_DIR = ".opengpt-spill"
MAX_BYTES = 50 * 1024
MAX_LINES = 2000
HEAD_LINES = 80
TAIL_LINES = 40
HEAD_BYTES = 24 * 1024
TAIL_BYTES = 8 * 1024
SPILL_TOOLS = frozenset({"bash", "glob", "grep", "lsp", "read_many"})

_SEQ = count(1)


def maybe_spill(*, root: Path, tool: str, result: ToolResult) -> ToolResult:
    if tool not in SPILL_TOOLS:
        return result
    text = result.output
    if not _over_budget(text):
        return result
    rel = _write_spill(root, tool, text)
    preview = _preview(text)
    notice = (
        f"(Omitted oversized output. Full text: {rel}. "
        "Use grep on that path, or read_file with offset/limit.)"
    )
    clipped = f"{preview}\n\n{notice}"
    if len(clipped.encode("utf-8")) > MAX_BYTES:
        clipped = notice
    return ToolResult(output=clipped, is_error=result.is_error, metadata=result.metadata)


def _over_budget(text: str) -> bool:
    if not text:
        return False
    if text.count("\n") + 1 > MAX_LINES:
        return True
    return len(text.encode("utf-8")) > MAX_BYTES


def _write_spill(root: Path, tool: str, text: str) -> str:
    folder = root / SPILL_DIR
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    name = f"{tool}-{stamp}-{next(_SEQ)}.txt"
    path = folder / name
    path.write_text(text, encoding="utf-8")
    return f"{SPILL_DIR}/{name}"


def _preview(text: str) -> str:
    lines = text.split("\n")
    if len(lines) > HEAD_LINES + TAIL_LINES + 1:
        head = "\n".join(lines[:HEAD_LINES])
        tail = "\n".join(lines[-TAIL_LINES:])
        omitted = len(lines) - HEAD_LINES - TAIL_LINES
        return f"{head}\n\n...{omitted} lines truncated...\n\n{tail}"
    encoded = text.encode("utf-8")
    if len(encoded) <= HEAD_BYTES + TAIL_BYTES:
        return text
    head = encoded[:HEAD_BYTES].decode("utf-8", errors="ignore")
    tail = encoded[-TAIL_BYTES:].decode("utf-8", errors="ignore")
    omitted = len(encoded) - HEAD_BYTES - TAIL_BYTES
    return f"{head}\n\n...{omitted} bytes truncated...\n\n{tail}"
