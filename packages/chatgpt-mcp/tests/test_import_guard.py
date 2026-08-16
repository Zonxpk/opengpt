from __future__ import annotations

import ast
from pathlib import Path

FORBIDDEN = (
    "load_settings",
    "create_default_tool_registry",
    "openharness.api",
)

BRIDGE_ROOT = Path(__file__).resolve().parents[1] / "chatgpt_mcp"


def _imports_and_names(tree: ast.AST) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            found.add(module)
            for alias in node.names:
                found.add(alias.name)
                if module:
                    found.add(f"{module}.{alias.name}")
        elif isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
    return found


def test_bridge_does_not_import_llm_clients_or_load_settings() -> None:
    hits: list[str] = []
    for path in BRIDGE_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = _imports_and_names(tree)
        for item in FORBIDDEN:
            if item in names or any(item in name for name in names):
                hits.append(f"{path.name}: {item}")
        if "openharness.api" in names or any(n.startswith("openharness.api") for n in names):
            hits.append(f"{path.name}: openharness.api")
    assert hits == []
