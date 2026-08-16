from __future__ import annotations

from pathlib import Path

from openharness.tools.base import ToolResult

from chatgpt_mcp.allowlist import MAX_BATCH
from chatgpt_mcp.jail import jail_path


def apply_change_list(
    changes: object,
    *,
    jail_root: Path,
    write_root: Path,
    sensitive=None,
) -> ToolResult:
    if not isinstance(changes, list) or not changes:
        return ToolResult(output="changes must be a non-empty list", is_error=True)
    if len(changes) > MAX_BATCH:
        return ToolResult(output=f"at most {MAX_BATCH} changes per apply_changes", is_error=True)

    staged: dict[Path, str] = {}
    order: list[Path] = []
    jail_root = jail_root.resolve()
    write_root = write_root.resolve()

    for index, change in enumerate(changes):
        if not isinstance(change, dict):
            return ToolResult(output=f"change {index} must be an object", is_error=True)
        op = change.get("op")
        jailed, reason = jail_path(jail_root, str(change.get("path") or ""))
        if reason or jailed is None:
            return ToolResult(output=f"change {index}: {reason or 'denied'}", is_error=True)
        if sensitive is not None:
            denied = sensitive(str(jailed))
            if denied:
                return ToolResult(output=f"change {index}: {denied}", is_error=True)
        try:
            rel = jailed.resolve().relative_to(jail_root)
        except ValueError:
            return ToolResult(output=f"change {index}: denied", is_error=True)
        dest = write_root / rel
        if op == "write":
            if "content" not in change:
                return ToolResult(output=f"change {index}: write requires content", is_error=True)
            if dest not in staged:
                order.append(dest)
            staged[dest] = str(change["content"])
        elif op == "edit":
            old = change.get("old_str")
            new = change.get("new_str")
            if not isinstance(old, str) or not isinstance(new, str):
                return ToolResult(output=f"change {index}: edit requires old_str and new_str", is_error=True)
            if dest in staged:
                original = staged[dest]
            elif dest.exists():
                original = dest.read_text(encoding="utf-8")
            else:
                return ToolResult(output=f"change {index}: file not found: {rel.as_posix()}", is_error=True)
            if old not in original:
                return ToolResult(
                    output=f"change {index}: old_str was not found in {rel.as_posix()}",
                    is_error=True,
                )
            updated = original.replace(old, new) if change.get("replace_all") else original.replace(old, new, 1)
            if dest not in staged:
                order.append(dest)
            staged[dest] = updated
        else:
            return ToolResult(output=f"change {index}: op must be write or edit", is_error=True)

    written: list[str] = []
    for path in order:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(staged[path], encoding="utf-8")
        written.append(path.resolve().relative_to(write_root).as_posix())
    return ToolResult(output="applied:\n" + "\n".join(written))
