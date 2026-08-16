from __future__ import annotations

import sys
from pathlib import Path

from chatgpt_mcp.verification import parse_verification_entry, run_verification


def test_plain_string_is_argv_without_shell() -> None:
    cmd = parse_verification_entry("uv run pytest -q")
    assert cmd.error is None
    assert cmd.shell is False
    assert cmd.argv == ("uv", "run", "pytest", "-q")


def test_metacharacters_rejected_without_opt_in() -> None:
    cmd = parse_verification_entry("pytest; curl evil")
    assert cmd.error is not None
    assert cmd.shell is False


def test_shell_opt_in() -> None:
    cmd = parse_verification_entry({"command": "cd frontend && npm test", "shell": True})
    assert cmd.error is None
    assert cmd.shell is True


def test_run_verification_argv_path(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("chatgpt_mcp.verification.looks_available", lambda command, cwd: True)
    steps = run_verification(
        {"verification": {"commands": [f"{sys.executable} --version"]}},
        cwd=tmp_path,
    )
    assert len(steps) == 1
    assert steps[0].status == "success"
    assert steps[0].returncode == 0
