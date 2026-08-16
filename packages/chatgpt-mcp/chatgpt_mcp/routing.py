from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re

try:
    from openharness.cli import (
        _recommend_preview_candidates,
        _tokenize_preview_text,
    )
except ImportError as exc:
    raise RuntimeError(
        "Pinned OpenHarness dry-run routing API changed; "
        "update chatgpt_mcp.routing before bumping the submodule."
    ) from exc

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

SEARCH_CUES = frozenset({
    "find",
    "locate",
    "where",
    "search",
    "grep",
    "lookup",
})

INSPECT_CUES = frozenset({
    "explain",
    "understand",
    "trace",
    "review",
    "inspect",
    "flow",
})

SYMBOL_CUES = frozenset({
    "symbol",
    "definition",
    "definitions",
    "reference",
    "references",
    "caller",
    "callers",
    "function",
    "method",
    "class",
})

ROUTING_ONLY_TERMS = (
    SEARCH_CUES
    | INSPECT_CUES
    | SYMBOL_CUES
    | frozenset({
        "code",
        "file",
        "files",
        "repo",
        "repository",
        "project",
    })
)


class RouteName(str, Enum):
    SEARCH = "search"
    SEARCH_AND_INSPECT = "search_and_inspect"
    SYMBOL = "symbol"


@dataclass(frozen=True)
class ToolCandidate:
    name: str
    score: int
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class RouteDecision:
    route: RouteName
    tokens: tuple[str, ...]
    search_terms: tuple[str, ...]
    candidates: tuple[ToolCandidate, ...]
    reasons: tuple[str, ...]


def _candidates_from_preview(preview: dict[str, list[dict[str, object]]]) -> tuple[ToolCandidate, ...]:
    tools = preview.get("tools") or []
    return tuple(
        ToolCandidate(
            name=str(item.get("name") or ""),
            score=int(item.get("score") or 0),
            reasons=tuple(str(reason) for reason in (item.get("reasons") or [])),
        )
        for item in tools
        if item.get("name")
    )


def _extract_search_terms(prompt: str, tokens: tuple[str, ...]) -> tuple[str, ...]:
    preferred = [
        token
        for token in tokens
        if token not in ROUTING_ONLY_TERMS and _IDENTIFIER.match(token)
    ]
    if not preferred:
        preferred = [
            token
            for token in _tokenize_preview_text(prompt)
            if _IDENTIFIER.match(token) or token.isalnum()
        ]
    return tuple(preferred[:6])


def _score(candidates: tuple[ToolCandidate, ...], name: str) -> int:
    for candidate in candidates:
        if candidate.name == name:
            return candidate.score
    return 0


def _choose_route(
    tokens: set[str],
    candidates: tuple[ToolCandidate, ...],
) -> tuple[RouteName, tuple[str, ...]]:
    has_search = bool(tokens & SEARCH_CUES)
    has_inspect = bool(tokens & INSPECT_CUES)
    has_symbol = bool(tokens & SYMBOL_CUES)
    grep_score = _score(candidates, "grep")
    read_score = max(_score(candidates, "read_file"), _score(candidates, "read_many"))
    lsp_score = _score(candidates, "lsp")

    if has_symbol:
        return RouteName.SYMBOL, ("symbol cue",)
    if lsp_score and lsp_score >= grep_score + 4 and lsp_score > read_score:
        return RouteName.SYMBOL, ("lsp dry-run score",)
    if has_search and has_inspect:
        return RouteName.SEARCH_AND_INSPECT, ("search+inspect cues",)
    if has_search and "where" in tokens:
        return RouteName.SEARCH_AND_INSPECT, ("locate-where",)
    if has_search and not has_inspect and grep_score and not read_score:
        return RouteName.SEARCH, ("search cue",)
    if has_search and not has_inspect and "grep" in tokens:
        return RouteName.SEARCH, ("explicit grep",)
    if grep_score and read_score:
        return RouteName.SEARCH_AND_INSPECT, ("grep+read candidates",)
    if grep_score and not read_score:
        return RouteName.SEARCH, ("grep candidate",)
    if has_search:
        return RouteName.SEARCH, ("search cue",)
    return RouteName.SEARCH_AND_INSPECT, ("default exploratory",)


def decide_route(
    prompt: str,
    *,
    tool_entries: list[dict[str, object]],
) -> RouteDecision:
    tokens = tuple(_tokenize_preview_text(prompt))
    preview = _recommend_preview_candidates(
        prompt,
        skills=[],
        tool_schemas=tool_entries,
        command_entries=[],
    )
    candidates = _candidates_from_preview(preview)
    route, reasons = _choose_route(set(tokens), candidates)
    search_terms = _extract_search_terms(prompt, tokens)
    return RouteDecision(
        route=route,
        tokens=tokens,
        search_terms=search_terms,
        candidates=candidates,
        reasons=reasons,
    )
