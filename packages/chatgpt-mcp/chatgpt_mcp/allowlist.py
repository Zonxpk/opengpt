from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mcp.types import ToolAnnotations
from openharness.tools.bash_tool import BashTool
from openharness.tools.file_edit_tool import FileEditTool
from openharness.tools.file_read_tool import FileReadTool
from openharness.tools.file_write_tool import FileWriteTool
from openharness.tools.glob_tool import GlobTool
from openharness.tools.grep_tool import GrepTool
from openharness.tools.lsp_tool import LspTool

MAX_BATCH = 20

# Default OpenHarness registry is 39 tools. These need an inner LLM, API keys,
# TUI, or load_settings — the bridge cannot run them as ChatGPT tools.
SKIPPED = (
    "agent",
    "ask_user_question",
    "config",
    "enter_plan_mode",
    "exit_plan_mode",
    "image_generation",
    "image_to_text",
    "mcp_auth",
    "list_mcp_resources",
    "read_mcp_resource",
)

PATH_ARG_KEYS = ("path", "root", "file_path", "cwd")

READ_MANY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "paths": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "maxItems": MAX_BATCH,
        },
        "offset": {"type": "integer", "minimum": 0, "default": 0},
        "limit": {"type": "integer", "minimum": 1, "maximum": 2000, "default": 200},
    },
    "required": ["paths"],
}

ROUTE_PREVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "prompt": {
            "type": "string",
            "minLength": 1,
        },
    },
    "required": ["prompt"],
}

FAST_CONTEXT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "prompt": {
            "type": "string",
            "minLength": 1,
        },
        "max_files": {
            "type": "integer",
            "minimum": 1,
            "maximum": 12,
            "default": 6,
        },
        "lines_per_file": {
            "type": "integer",
            "minimum": 20,
            "maximum": 400,
            "default": 160,
        },
        "include_lsp": {
            "type": "boolean",
            "default": True,
        },
    },
    "required": ["prompt"],
}

VERIFY_PROJECT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
}

APPLY_CHANGES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "changes": {
            "type": "array",
            "minItems": 1,
            "maxItems": MAX_BATCH,
            "items": {
                "type": "object",
                "properties": {
                    "op": {"type": "string", "enum": ["write", "edit"]},
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "old_str": {"type": "string"},
                    "new_str": {"type": "string"},
                    "replace_all": {"type": "boolean", "default": False},
                },
                "required": ["op", "path"],
            },
        }
    },
    "required": ["changes"],
}

ISOLATED_CHANGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "changes": APPLY_CHANGES_SCHEMA["properties"]["changes"],
        "slug": {
            "type": "string",
            "minLength": 1,
            "maxLength": 64,
        },
    },
    "required": ["changes"],
}

LONG_TASK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["start", "output", "stop", "list"],
            "default": "start",
        },
        "command": {"type": "string"},
        "argv": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
        },
        "description": {"type": "string"},
        "cwd": {"type": "string"},
        "task_id": {"type": "string"},
        "max_bytes": {"type": "integer", "minimum": 1, "maximum": 100000, "default": 12000},
    },
    "required": ["action"],
}


@dataclass(frozen=True)
class ToolSpec:
    name: str
    factory: type | None
    read_only: bool
    description: str | None = None
    schema: dict[str, Any] | None = None
    open_world: bool = False


OH_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec("read_file", FileReadTool, True),
    ToolSpec("glob", GlobTool, True),
    ToolSpec("grep", GrepTool, True),
    ToolSpec("lsp", LspTool, True),
    ToolSpec("write_file", FileWriteTool, False),
    ToolSpec("edit_file", FileEditTool, False),
    ToolSpec("bash", BashTool, False, open_world=True),
)

BATCH_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        "read_many",
        None,
        True,
        description="Read up to 20 files in one call. Prefer this over many read_file calls.",
        schema=READ_MANY_SCHEMA,
    ),
    ToolSpec(
        "apply_changes",
        None,
        False,
        description=(
            "Apply up to 20 writes/edits in one call. Paths are jailed first. "
            "Edits to the same file stack in order. Invalid changes write nothing; "
            "a filesystem error mid-write is not rolled back."
        ),
        schema=APPLY_CHANGES_SCHEMA,
    ),
)

ROUTE_PREVIEW_SPEC = ToolSpec(
    "route_preview",
    None,
    True,
    description="Preview the zero-LLM OpenGPT route selected for a coding prompt. Does not access files.",
    schema=ROUTE_PREVIEW_SCHEMA,
)

VERIFY_PROJECT_SPEC = ToolSpec(
    "verify_project",
    None,
    False,
    description=(
        "Run the repository verification policy (pytest/ruff/tsc when applicable) "
        "via OpenHarness's model-free runner. Prefer this over ad-hoc bash for checks."
    ),
    schema=VERIFY_PROJECT_SCHEMA,
)

ISOLATED_CHANGE_SPEC = ToolSpec(
    "isolated_change",
    None,
    False,
    description=(
        "Apply up to 20 writes/edits in a git worktree, run verify_project there, "
        "and return diff + verification. The approved root is not modified. "
        "Requires a clean git working tree (commit/stash first, or use apply_changes). "
        "The worktree is kept for ChatGPT to inspect or discard."
    ),
    schema=ISOLATED_CHANGE_SCHEMA,
)

LONG_TASK_SPEC = ToolSpec(
    "long_task",
    None,
    False,
    description=(
        "Background local_bash only (no inner agent): start, output, stop, or list. "
        "Use for long tests/builds/dev servers. Prefer bash or verify_project for short commands."
    ),
    schema=LONG_TASK_SCHEMA,
    open_world=True,
)

FAST_CONTEXT_SPEC = ToolSpec(
    "fast_context",
    None,
    True,
    description=(
        "Use this FIRST when the relevant repository files are not yet known: "
        "locating implementations, tracing flows, understanding unfamiliar code, "
        "or gathering context before editing. "
        "Do not use when an exact file, regex search, or symbol query is already known."
    ),
    schema=FAST_CONTEXT_SCHEMA,
)

SPECS: tuple[ToolSpec, ...] = (
    FAST_CONTEXT_SPEC,
    *(spec for spec in OH_SPECS if spec.read_only),
    BATCH_SPECS[0],
    *(spec for spec in OH_SPECS if not spec.read_only and spec.name != "bash"),
    BATCH_SPECS[1],
    VERIFY_PROJECT_SPEC,
    ISOLATED_CHANGE_SPEC,
    LONG_TASK_SPEC,
    next(spec for spec in OH_SPECS if spec.name == "bash"),
)

ROUTER_SPECS: tuple[ToolSpec, ...] = (
    *(spec for spec in OH_SPECS if spec.read_only),
    BATCH_SPECS[0],
)


def _entry_from_spec(spec: ToolSpec) -> dict[str, object]:
    if spec.factory is not None:
        tool = spec.factory()
        schema = tool.input_model.model_json_schema()
        description = tool.description
    else:
        schema = spec.schema or {}
        description = spec.description or spec.name
    required_args = sorted(schema.get("required", []))
    optional_args = sorted(
        key
        for key in schema.get("properties", {})
        if key not in required_args
    )
    return {
        "name": spec.name,
        "description": description,
        "required_args": required_args,
        "optional_args": optional_args,
    }


def routing_tool_entries() -> list[dict[str, object]]:
    return [_entry_from_spec(spec) for spec in ROUTER_SPECS]


def names_for_mode(mode: str, *, debug_tools: bool = False) -> tuple[str, ...]:
    production = tuple(spec.name for spec in SPECS if spec.read_only)
    debug = (ROUTE_PREVIEW_SPEC.name,) if debug_tools else ()
    if mode == "read":
        return production + debug
    if mode == "write":
        writes = tuple(spec.name for spec in SPECS if not spec.read_only)
        return production + writes + debug
    raise ValueError(f"unknown mode: {mode}")


def specs_for_mode(mode: str, *, debug_tools: bool = False) -> tuple[ToolSpec, ...]:
    allowed = set(names_for_mode(mode, debug_tools=debug_tools))
    catalog = SPECS + ((ROUTE_PREVIEW_SPEC,) if debug_tools else ())
    return tuple(spec for spec in catalog if spec.name in allowed)


def annotations_for(spec: ToolSpec) -> ToolAnnotations:
    return ToolAnnotations(
        read_only_hint=spec.read_only,
        destructive_hint=not spec.read_only,
        open_world_hint=spec.open_world,
    )
