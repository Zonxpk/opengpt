from __future__ import annotations

from dataclasses import dataclass

from mcp_types import ToolAnnotations
from openharness.tools.bash_tool import BashTool
from openharness.tools.file_edit_tool import FileEditTool
from openharness.tools.file_read_tool import FileReadTool
from openharness.tools.file_write_tool import FileWriteTool
from openharness.tools.glob_tool import GlobTool
from openharness.tools.grep_tool import GrepTool

READ_TOOLS = ("read_file", "glob", "grep")
WRITE_TOOLS = ("write_file", "edit_file", "bash")


@dataclass(frozen=True)
class ToolSpec:
    name: str
    factory: type
    read_only: bool


SPECS: tuple[ToolSpec, ...] = (
    ToolSpec("read_file", FileReadTool, True),
    ToolSpec("glob", GlobTool, True),
    ToolSpec("grep", GrepTool, True),
    ToolSpec("write_file", FileWriteTool, False),
    ToolSpec("edit_file", FileEditTool, False),
    ToolSpec("bash", BashTool, False),
)


def names_for_mode(mode: str) -> tuple[str, ...]:
    if mode == "read":
        return READ_TOOLS
    if mode == "write":
        return READ_TOOLS + WRITE_TOOLS
    raise ValueError(f"unknown mode: {mode}")


def specs_for_mode(mode: str) -> tuple[ToolSpec, ...]:
    allowed = set(names_for_mode(mode))
    return tuple(spec for spec in SPECS if spec.name in allowed)


def annotations_for(spec: ToolSpec) -> ToolAnnotations:
    return ToolAnnotations(
        read_only_hint=spec.read_only,
        destructive_hint=not spec.read_only,
        open_world_hint=False,
    )
