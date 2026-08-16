from __future__ import annotations

from pathlib import Path

from openharness.sandbox.path_validator import validate_sandbox_path

_GLOB_MAGIC = "*?["


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


def _has_glob_magic(part: str) -> bool:
    return any(char in part for char in _GLOB_MAGIC)


def jail_glob_pattern(
    approved_root: Path,
    pattern: str,
) -> tuple[str | None, str | None]:
    raw = str(pattern).strip()
    if not raw:
        return None, "glob pattern is required"

    normalized = Path(raw)
    parts = list(normalized.parts)
    if not parts:
        return None, "glob pattern is required"

    static: list[str] = []
    glob_index = 0
    for index, part in enumerate(parts):
        if _has_glob_magic(part):
            glob_index = index
            break
        static.append(part)
        glob_index = index + 1
    else:
        glob_index = len(parts)

    glob_suffix = parts[glob_index:]
    prefix = Path(*static) if static else Path(".")
    if prefix.is_absolute():
        prefix_path = prefix
    else:
        prefix_path = approved_root / prefix

    jailed, reason = jail_path(approved_root, str(prefix_path))
    if reason or jailed is None:
        return None, reason or "glob pattern outside approved root is denied"

    try:
        relative_prefix = jailed.resolve().relative_to(approved_root.resolve())
    except ValueError:
        return None, "glob pattern outside approved root is denied"

    relative_parts = relative_prefix.parts
    rewritten = Path(*relative_parts, *glob_suffix) if glob_suffix else Path(*relative_parts)
    text = rewritten.as_posix()
    if text == ".":
        text = "*"
    return text, None
