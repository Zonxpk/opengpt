from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mcp_types import ToolAnnotations
from openharness.tools.bash_tool import BashTool
from openharness.tools.brief_tool import BriefTool
from openharness.tools.cron_create_tool import CronCreateTool
from openharness.tools.cron_delete_tool import CronDeleteTool
from openharness.tools.cron_list_tool import CronListTool
from openharness.tools.cron_toggle_tool import CronToggleTool
from openharness.tools.enter_worktree_tool import EnterWorktreeTool
from openharness.tools.exit_worktree_tool import ExitWorktreeTool
from openharness.tools.file_edit_tool import FileEditTool
from openharness.tools.file_read_tool import FileReadTool
from openharness.tools.file_write_tool import FileWriteTool
from openharness.tools.glob_tool import GlobTool
from openharness.tools.grep_tool import GrepTool
from openharness.tools.lsp_tool import LspTool
from openharness.tools.notebook_edit_tool import NotebookEditTool
from openharness.tools.remote_trigger_tool import RemoteTriggerTool
from openharness.tools.send_message_tool import SendMessageTool
from openharness.tools.skill_tool import SkillTool
from openharness.tools.sleep_tool import SleepTool
from openharness.tools.task_create_tool import TaskCreateTool
from openharness.tools.task_get_tool import TaskGetTool
from openharness.tools.task_list_tool import TaskListTool
from openharness.tools.task_output_tool import TaskOutputTool
from openharness.tools.task_stop_tool import TaskStopTool
from openharness.tools.task_update_tool import TaskUpdateTool
from openharness.tools.team_create_tool import TeamCreateTool
from openharness.tools.team_delete_tool import TeamDeleteTool
from openharness.tools.todo_write_tool import TodoWriteTool
from openharness.tools.tool_search_tool import ToolSearchTool
from openharness.tools.web_fetch_tool import WebFetchTool
from openharness.tools.web_search_tool import WebSearchTool

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
    ToolSpec("skill", SkillTool, True),
    ToolSpec("tool_search", ToolSearchTool, True),
    ToolSpec("brief", BriefTool, True),
    ToolSpec("sleep", SleepTool, True),
    ToolSpec("web_fetch", WebFetchTool, True, open_world=True),
    ToolSpec("web_search", WebSearchTool, True, open_world=True),
    ToolSpec("cron_list", CronListTool, True),
    ToolSpec("task_get", TaskGetTool, True),
    ToolSpec("task_list", TaskListTool, True),
    ToolSpec("task_output", TaskOutputTool, True),
    ToolSpec("write_file", FileWriteTool, False),
    ToolSpec("edit_file", FileEditTool, False),
    ToolSpec("notebook_edit", NotebookEditTool, False),
    ToolSpec("todo_write", TodoWriteTool, False),
    ToolSpec("enter_worktree", EnterWorktreeTool, False),
    ToolSpec("exit_worktree", ExitWorktreeTool, False),
    ToolSpec("cron_create", CronCreateTool, False),
    ToolSpec("cron_delete", CronDeleteTool, False),
    ToolSpec("cron_toggle", CronToggleTool, False),
    ToolSpec("task_create", TaskCreateTool, False),
    ToolSpec("task_stop", TaskStopTool, False),
    ToolSpec("task_update", TaskUpdateTool, False),
    ToolSpec("remote_trigger", RemoteTriggerTool, False),
    ToolSpec("send_message", SendMessageTool, False),
    ToolSpec("team_create", TeamCreateTool, False),
    ToolSpec("team_delete", TeamDeleteTool, False),
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
