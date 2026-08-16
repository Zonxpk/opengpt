from __future__ import annotations

from pathlib import Path

from openharness.sandbox.path_validator import validate_sandbox_path


def resolve_under_root(approved_root: Path, candidate: str | None) -> Path:
    base = approved_root.resolve()
    path = Path(candidate or ".").expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def jail_path(approved_root: Path, candidate: str | None) -> tuple[Path | None, str | None]:
    resolved = resolve_under_root(approved_root, candidate)
    allowed, reason = validate_sandbox_path(resolved, approved_root.resolve())
    if not allowed:
        return None, reason
    return resolved, None


def glob_pattern_jail_reason(pattern: str | None) -> str | None:
    if pattern is None or not str(pattern).strip():
        return None
    text = str(pattern)
    if Path(text).expanduser().is_absolute():
        return "glob pattern must be relative to the approved workspace root"
    if ".." in Path(text).parts:
        return "glob pattern must not contain .."
    return None
