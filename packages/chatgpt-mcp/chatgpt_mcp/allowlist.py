from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mcp_types import ToolAnnotations
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
    ToolSpec("bash", BashTool, False),
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
            "Apply up to 20 writes/edits in one call. All paths are jailed first; "
            "nothing is written if any change is invalid."
        ),
        schema=APPLY_CHANGES_SCHEMA,
    ),
)

SPECS: tuple[ToolSpec, ...] = (
    *(spec for spec in OH_SPECS if spec.read_only),
    BATCH_SPECS[0],
    *(spec for spec in OH_SPECS if not spec.read_only and spec.name != "bash"),
    BATCH_SPECS[1],
    next(spec for spec in OH_SPECS if spec.name == "bash"),
)


def names_for_mode(mode: str) -> tuple[str, ...]:
    if mode == "read":
        return tuple(spec.name for spec in SPECS if spec.read_only)
    if mode == "write":
        return tuple(spec.name for spec in SPECS)
    raise ValueError(f"unknown mode: {mode}")


def specs_for_mode(mode: str) -> tuple[ToolSpec, ...]:
    allowed = set(names_for_mode(mode))
    return tuple(spec for spec in SPECS if spec.name in allowed)


def annotations_for(spec: ToolSpec) -> ToolAnnotations:
    return ToolAnnotations(
        read_only_hint=spec.read_only,
        destructive_hint=not spec.read_only,
        open_world_hint=spec.open_world,
    )
