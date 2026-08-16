from __future__ import annotations

import ast
from pathlib import Path

FORBIDDEN = (
    "load_settings",
    "create_default_tool_registry",
    "openharness.api",
    "openharness.engine.query",
    "openharness.engine.query_engine",
)

ALLOWED_CLI_NAMES = {
    "_recommend_preview_candidates",
    "_tokenize_preview_text",
}

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


def _cli_imported_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "openharness.cli":
            for alias in node.names:
                names.add(alias.name)
    return names


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


def test_routing_imports_only_dry_run_helpers() -> None:
    path = BRIDGE_ROOT / "routing.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = _cli_imported_names(tree)
    assert imported
    assert imported <= ALLOWED_CLI_NAMES
    assert "_build_dry_run_preview" not in imported


def test_v2_modules_import_without_provider_keys(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    import chatgpt_mcp.fast_context
    import chatgpt_mcp.routing

    assert chatgpt_mcp.routing.decide_route
    assert chatgpt_mcp.fast_context.FastContextService
