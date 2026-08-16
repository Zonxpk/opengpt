from __future__ import annotations

import subprocess
import time
from pathlib import Path

from openharness.swarm.worktree import WorktreeManager
from openharness.tools.base import ToolResult

from chatgpt_mcp.apply_batch import apply_change_list
from chatgpt_mcp.verify_project import VerifyProjectService


class IsolatedChangeService:
    def __init__(
        self,
        *,
        approved_root: Path,
        worktree_base: Path | None = None,
        verify: VerifyProjectService | None = None,
        sensitive=None,
    ) -> None:
        self.approved_root = approved_root.resolve()
        self.worktree_base = worktree_base or (
            self.approved_root.parent / ".opengpt-worktrees" / self.approved_root.name
        )
        self._verify = verify or VerifyProjectService()
        self._sensitive = sensitive

    async def run(self, changes: object, *, slug: str | None = None) -> ToolResult:
        manager = WorktreeManager(base_dir=self.worktree_base)
        slug = (slug or f"opengpt-{int(time.time())}").strip()
        try:
            info = await manager.create_worktree(self.approved_root, slug, agent_id="opengpt")
        except Exception as exc:
            return ToolResult(output=f"worktree create failed: {exc}", is_error=True)

        applied = apply_change_list(
            changes,
            jail_root=self.approved_root,
            write_root=info.path,
            sensitive=self._sensitive,
        )
        if applied.is_error:
            await manager.remove_worktree(slug)
            return applied

        verification = self._verify.run(info.path)
        diff = _git_diff(info.path)
        kept = "true"
        lines = [
            "# Isolated change",
            f"worktree: {info.path}",
            f"slug: {info.slug}",
            f"kept: {kept}",
            "",
            "## apply",
            applied.output,
            "",
            "## diff",
            diff or "(no diff)",
            "",
            "## verification",
            verification.output,
        ]
        return ToolResult(
            output="\n".join(lines).strip() + "\n",
            is_error=verification.is_error,
        )


def _git_diff(cwd: Path) -> str:
    stat = subprocess.run(
        ["git", "diff", "--stat"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    full = subprocess.run(
        ["git", "diff"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    parts = [stat.stdout.strip(), full.stdout.strip()]
    return "\n\n".join(part for part in parts if part)
