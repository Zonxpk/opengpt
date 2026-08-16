from __future__ import annotations

from chatgpt_mcp.allowlist import routing_tool_entries
from chatgpt_mcp.routing import RouteName, decide_route

TEST_TOOL_ENTRIES: list[dict[str, object]] = [
    {
        "name": "grep",
        "description": "Search file contents with a regular expression.",
        "required_args": ["pattern"],
        "optional_args": ["case_sensitive", "file_glob", "limit", "root", "timeout_seconds"],
    },
    {
        "name": "read_file",
        "description": "Read a file from the workspace.",
        "required_args": ["path"],
        "optional_args": ["offset", "limit"],
    },
    {
        "name": "lsp",
        "description": "Language server symbol definition and references.",
        "required_args": ["operation"],
        "optional_args": ["query", "path"],
    },
]


def test_find_where_routes_to_search_and_inspect() -> None:
    decision = decide_route(
        "Find where JWT refresh tokens are validated",
        tool_entries=TEST_TOOL_ENTRIES,
    )

    assert decision.route == RouteName.SEARCH_AND_INSPECT
    assert "jwt" in decision.search_terms
    assert "refresh" in decision.search_terms
    assert "tokens" in decision.search_terms
    assert "validated" in decision.search_terms
    assert "are" not in decision.search_terms


def test_symbol_query_routes_to_symbol() -> None:
    decision = decide_route(
        "Find references to validate_refresh_token",
        tool_entries=TEST_TOOL_ENTRIES,
    )

    assert decision.route == RouteName.SYMBOL
    assert "validate_refresh_token" in decision.search_terms


def test_explicit_grep_uses_dry_run_candidate_signal() -> None:
    decision = decide_route(
        "grep for failing authentication tests",
        tool_entries=TEST_TOOL_ENTRIES,
    )

    names = [candidate.name for candidate in decision.candidates]
    assert "grep" in names


def test_routing_entries_only_use_safe_allowlist() -> None:
    entries = routing_tool_entries()
    names = {entry["name"] for entry in entries}

    assert "grep" in names
    assert "read_file" in names
    assert "lsp" in names

    assert "agent" not in names
    assert "task_create" not in names
    assert "image_generation" not in names
    assert "mcp_auth" not in names
    assert "fast_context" not in names
    assert "route_preview" not in names
