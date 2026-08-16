# Gauntlet progress

Bar C: HTTP vs `gpt-repo-mcp`; tools/jail vs `OpenHarness`; spec tests 1–8.

| Piece | Status | Critic |
| --- | --- | --- |
| Repo layout + submodules | ours | local file-URL submodules present |
| Connect + Cloudflare URL | ours | matches `connect-cloudflare.mjs` URL regex and non-zero exit |
| Streamable HTTP + sessions | ours | POST/GET/DELETE on token path; idle 30m; 2MB body |
| Allowlist + annotations | ours | read vs write names; `readOnlyHint` / `destructiveHint` / `openWorldHint` |
| Adapter path jail | ours | `validate_sandbox_path` after resolve |
| Bash cwd pin | ours | outside cwd denied; `Settings(sandbox.enabled=False)` |
| Import-guard CI | ours | no `load_settings` / `openharness.api` / `create_default_tool_registry` |
| Spec tests 1–8 | ours | pytest 9 passed |

Last update: critic picked ours on the split bar. Known documented gap: workspace `.env` still readable in read mode.
