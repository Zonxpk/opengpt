"""OpenGPT copy of Autopilot's model-free verification runner.

Lives here so OpenGPT does not patch the OpenHarness submodule.
Policy format matches OpenHarness ``verification_policy.yaml``.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from openharness.autopilot.types import RepoVerificationStep
from openharness.config.paths import get_project_verification_policy_path

DEFAULT_VERIFICATION_POLICY: dict[str, Any] = {
    "commands": [
        "uv run pytest -q",
        "uv run ruff check src tests",
    ],
}

_SHELL_METACHARS = frozenset(";&|`$<>\n\r")
_OUTPUT_CAP = 4000
_TIMEOUT_SECONDS = 1800


@dataclass(frozen=True)
class VerificationCommand:
    raw: str
    argv: tuple[str, ...]
    shell: bool
    error: str | None = None


def parse_verification_entry(entry: object) -> VerificationCommand:
    if isinstance(entry, dict):
        raw = str(entry.get("command", "")).strip()
        if not raw:
            return VerificationCommand(raw=str(entry), argv=(), shell=False, error="empty command")
        if bool(entry.get("shell", False)):
            return VerificationCommand(raw=raw, argv=(), shell=True)
    elif isinstance(entry, str):
        raw = entry.strip()
        if not raw:
            return VerificationCommand(raw=entry, argv=(), shell=False, error="empty command")
    else:
        return VerificationCommand(
            raw=str(entry),
            argv=(),
            shell=False,
            error="entry must be a string or a mapping with a 'command' key",
        )

    if any(ch in _SHELL_METACHARS for ch in raw):
        return VerificationCommand(
            raw=raw,
            argv=(),
            shell=False,
            error=(
                "command contains shell metacharacters; use the mapping form "
                "{command: '...', shell: true} in verification_policy.yaml to opt in"
            ),
        )
    try:
        posix = os.name != "nt" or "\\" not in raw
        argv = shlex.split(raw, posix=posix)
    except ValueError as exc:
        return VerificationCommand(
            raw=raw,
            argv=(),
            shell=False,
            error=f"could not tokenize command: {exc}",
        )
    if not argv:
        return VerificationCommand(raw=raw, argv=(), shell=False, error="empty command")
    return VerificationCommand(raw=raw, argv=tuple(argv), shell=False)


def looks_available(command: str, cwd: Path) -> bool:
    lowered = command.lower()
    if lowered.startswith("uv "):
        return (cwd / "pyproject.toml").exists()
    if "ruff check" in lowered:
        return (cwd / "pyproject.toml").exists()
    if "pytest" in lowered:
        return (cwd / "tests").exists()
    if "tsc" in lowered or "frontend/terminal" in lowered:
        return (cwd / "frontend" / "terminal" / "package.json").exists()
    return True


def load_verification_policy(cwd: str | Path) -> dict[str, Any]:
    path = get_project_verification_policy_path(cwd)
    if not path.exists():
        return dict(DEFAULT_VERIFICATION_POLICY)
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return dict(DEFAULT_VERIFICATION_POLICY)
    if not isinstance(payload, dict):
        return dict(DEFAULT_VERIFICATION_POLICY)
    return payload


def select_verification_commands(
    policies: dict[str, Any],
    *,
    availability_cwd: Path,
) -> list[VerificationCommand]:
    configured = policies.get("verification", {}).get("commands", [])
    parsed = [parse_verification_entry(entry) for entry in configured]
    selected: list[VerificationCommand] = []
    for cmd in parsed:
        if cmd.error is not None:
            selected.append(cmd)
            continue
        if looks_available(cmd.raw, availability_cwd):
            selected.append(cmd)
    return selected


def run_verification(
    policies: dict[str, Any],
    *,
    cwd: str | Path,
    availability_cwd: str | Path | None = None,
) -> list[RepoVerificationStep]:
    target_cwd = Path(cwd)
    avail_cwd = Path(availability_cwd) if availability_cwd is not None else target_cwd
    steps: list[RepoVerificationStep] = []
    for cmd in select_verification_commands(policies, availability_cwd=avail_cwd):
        if cmd.error is not None:
            steps.append(
                RepoVerificationStep(
                    command=cmd.raw,
                    returncode=-1,
                    status="error",
                    stderr=f"verification policy error: {cmd.error}",
                )
            )
            continue
        target: str | list[str] = cmd.raw if cmd.shell else list(cmd.argv)
        try:
            completed = subprocess.run(
                target,
                cwd=target_cwd,
                shell=cmd.shell,
                text=True,
                capture_output=True,
                check=False,
                timeout=_TIMEOUT_SECONDS,
            )
            steps.append(
                RepoVerificationStep(
                    command=cmd.raw,
                    returncode=completed.returncode,
                    status="success" if completed.returncode == 0 else "failed",
                    stdout=(completed.stdout or "")[-_OUTPUT_CAP:],
                    stderr=(completed.stderr or "")[-_OUTPUT_CAP:],
                )
            )
        except FileNotFoundError as exc:
            steps.append(
                RepoVerificationStep(
                    command=cmd.raw,
                    returncode=-1,
                    status="error",
                    stderr=f"executable not found: {exc}",
                )
            )
        except subprocess.TimeoutExpired as exc:
            steps.append(
                RepoVerificationStep(
                    command=cmd.raw,
                    returncode=-1,
                    status="error",
                    stdout=_safe_text(getattr(exc, "stdout", ""))[-_OUTPUT_CAP:],
                    stderr=f"Timed out after {exc.timeout}s",
                )
            )
        except Exception as exc:  # pragma: no cover - defensive
            steps.append(
                RepoVerificationStep(
                    command=cmd.raw,
                    returncode=-1,
                    status="error",
                    stderr=str(exc),
                )
            )
    return steps


def format_verification_report(steps: list[RepoVerificationStep], *, title: str = "project") -> str:
    lines = [
        f"# Verification Report: {title}",
        "",
    ]
    if not steps:
        lines.append("No verification commands were applicable.")
        return "\n".join(lines).strip() + "\n"
    failing = [step for step in steps if step.status in {"failed", "error"}]
    overall = "failed" if failing else "passed"
    lines.extend([f"Overall: {overall}", ""])
    for step in steps:
        lines.extend(
            [
                f"## {step.status.upper()} :: {step.command}",
                "",
                f"Return code: {step.returncode}",
                "",
            ]
        )
        if step.stdout:
            lines.extend(["### stdout", "```text", step.stdout, "```", ""])
        if step.stderr:
            lines.extend(["### stderr", "```text", step.stderr, "```", ""])
    return "\n".join(lines).strip() + "\n"


def _safe_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()
