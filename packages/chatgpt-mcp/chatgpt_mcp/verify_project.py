from __future__ import annotations

from pathlib import Path

from openharness.tools.base import ToolResult
from openharness.verification import (
    format_verification_report,
    load_verification_policy,
    run_verification,
)


class VerifyProjectService:
    def run(self, cwd: Path) -> ToolResult:
        policy = load_verification_policy(cwd)
        steps = run_verification({"verification": policy}, cwd=cwd)
        report = format_verification_report(steps, title=cwd.name)
        failing = [step for step in steps if step.status in {"failed", "error"}]
        return ToolResult(output=report, is_error=bool(failing))
