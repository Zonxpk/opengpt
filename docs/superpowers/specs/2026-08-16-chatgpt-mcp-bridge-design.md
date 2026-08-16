# OpenGPT: ChatGPT web MCP bridge

Date: 2026-08-16  
Status: draft after Grok 4.6 review (must-fixes applied)  
Repos: `D:\Projects\OpenHarness`, `D:\Projects\gpt-repo-mcp`, new `D:\Projects\opengpt`

## Goal

ChatGPT Developer Mode drives local OpenHarness tools. ChatGPT is the only LLM. `opengpt` is a monorepo that pins OpenHarness as a git submodule and owns a thin Python MCP bridge.

Success: paste one HTTPS **Server URL** into ChatGPT (not a Tunnel ID), select the connector, and have ChatGPT read/edit files and run bash inside one approved workspace. No Anthropic/OpenAI API key is required for `opengpt` to run.

ChatGPT connector settings for v1: type **Server URL**, auth **none**, protocol **Streamable HTTP** (SSE not required). No `search` / `fetch` tools. Some ChatGPT plans may refuse write MCP; that is a product gate, not a bridge bug.

## Non-goals (v1)

- OpenHarness agent loop, swarm, cron, web, image, or subagent tools
- Copying `gpt-repo-mcp` TypeScript tools, git policy, secret scanner, or repo registry
- Default ngrok
- Docker/OS jail for bash (document host-equivalent shell)
- Push, deploy, or any LLM provider call from the bridge
- Editing `OpenHarness` in place (submodule stays read-mostly)
- `config.local.json` as a second source of workspace root

## Layout

```
opengpt/
  upstreams/openharness/      # git submodule → https://github.com/HKUDS/OpenHarness.git
  packages/chatgpt-mcp/       # owned Python package: HTTP MCP + adapter + connect
  docs/superpowers/specs/     # this design
```

`packages/chatgpt-mcp` depends on OpenHarness **as a library** (path install of `upstreams/openharness`). It does not vendor tool implementations.

Do **not** import `openharness.tools.create_default_tool_registry` or `openharness.api`. Instantiate only: `FileReadTool`, `GlobTool`, `GrepTool`, `FileWriteTool`, `FileEditTool`, `BashTool`.

`gpt-repo-mcp` is not a runtime dependency. Copy these behaviors into Python:

- Streamable HTTP on **POST + GET + DELETE**
- `/t/<16-byte-hex-token>/mcp` when tunneled; `/mcp` only for `--tunnel none`
- Bind `127.0.0.1`
- Session store: max 100, idle 30 minutes, `mcp-session-id` (same idea as `GPT_REPO_MAX_SESSIONS` / `GPT_REPO_SESSION_IDLE_TTL_MS`)
- Origin: missing Origin allowed; non-loopback browser Origin → 403 (match `network-boundary.ts`)
- `/health`
- Connect starts server + tunnel and prints `ChatGPT MCP URL: https://<trycloudflare-host>/t/<token>/mcp`
- JSON body limit 2 MB (match gpt-repo-mcp)

When using FastMCP `streamable_http_app()`, disable DNS-rebinding Host allowlisting while tunneled so `Host: *.trycloudflare.com` is not rejected. Alternatively allow that host explicitly.

## Runtime data flow

```
ChatGPT Developer Mode (Server URL)
  → HTTPS connector URL
  → Cloudflare quick tunnel (default)
  → 127.0.0.1:8787 /t/<token>/mcp
  → Streamable HTTP (POST/GET/DELETE)
  → allowlist
  → parse with tool.input_model
  → adapter sandbox (path jail, bash cwd pin)
  → SENSITIVE_PATH_PATTERNS extra deny
  → await tool.execute(args, ToolExecutionContext(cwd=approved_root))
  → map ToolResult.output / is_error to MCP
```

No inner model. `tools/call` maps 1:1 to one OpenHarness tool. `cwd` lives on `ToolExecutionContext`, not on `execute`.

Permission mode is **FULL_AUTO** with no `permission_prompt`. Do not use OpenHarness `DEFAULT` / `PLAN` (those set `requires_confirmation` and would stall; there is no TUI). ChatGPT’s own write confirmation is not server policy.

`PermissionChecker` is **extra deny-list only** (`SENSITIVE_PATH_PATTERNS`). It does **not** enforce the workspace root.

## Tunnel

Default: `--tunnel cloudflare` via Cloudflare quick tunnel:

```
cloudflared tunnel --url http://127.0.0.1:8787
```

No region picker. Generate a 16-byte hex public path token and wire it into both the listener and the printed URL.

Parse `https://[-a-z0-9.]+\.trycloudflare.com` from `cloudflared` stdout/stderr (same as `gpt-repo-mcp/scripts/connect-cloudflare.mjs`). Printed URL:

```
ChatGPT MCP URL: https://<trycloudflare-host>/t/<token>/mcp
```

The random path token is guess-resistance only, not authentication. Treat the URL as a temporary endpoint. Stop the process when done.

If `--tunnel cloudflare` and `cloudflared` is missing, or no public URL appears within 30 seconds: print the install hint and **exit non-zero**. Do not print a ChatGPT URL. Do not claim localhost is usable from ChatGPT.

`--tunnel none`: loopback/tests only. Serves `/mcp` on `127.0.0.1`. Does **not** print a ChatGPT URL.

Optional later (not v1): ngrok, OpenAI Secure MCP Tunnel.

## Workspace and safety

- `--root <abs-path>` is the **only** approved root for that process. Do not read a conflicting `config.local.json` `root` in v1.
- Modes: `read` (`read_file`, `glob`, `grep`) and `write` (those plus `write_file`, `edit_file`, `bash`).
- **Adapter path jail (required):** after `Path.resolve()`, every file/glob/grep path must satisfy `relative_to(approved_root)`. Symlink escape = deny. Clamp glob/grep `root` arguments the same way. Model this on `openharness.sandbox.path_validator.validate_sandbox_path` without requiring Docker.
- `PermissionChecker` / `SENSITIVE_PATH_PATTERNS`: extra deny after the jail.
- Server binds `127.0.0.1` only. External bind is out of v1.
- MCP annotations, static per tool (not per-call): `readOnlyHint` true for `read_file`/`glob`/`grep`; `destructiveHint` true for `write_file`/`edit_file`/`bash`; `openWorldHint` true for `bash`, false for file tools.
- MCP `instructions` first 512 characters must be self-contained: one root, mode, file-tool jail, bash is host-equivalent (not jailed).
- Max concurrent MCP sessions: 100. Idle TTL: 30 minutes.

Known v1 gap (document, do not pretend it is fixed): `SENSITIVE_PATH_PATTERNS` does not cover `.env` or generic `credentials.json`. Workspace `.env` is readable in read mode unless later deny-listed.

## Tool profile (v1 allowlist)

Bridge OpenHarness tools 1:1. ChatGPT sees the same `BaseTool.name` values.

Keep `read_many` / `apply_changes` as bridge-only batch helpers.

Do **not** expose tools that need an inner LLM, `load_settings`, a TUI, or an MCP client:

`agent`, `image_generation`, `image_to_text`, `ask_user_question`, `config`,
`enter_plan_mode`, `exit_plan_mode`, `mcp_auth`, `list_mcp_resources`, `read_mcp_resource`.

`task_create` with `type=local_agent` is denied at the adapter. `local_bash` is allowed.

New OpenHarness tools stay **off** until added to `OH_SPECS`. Submodule bump alone must not expose them.

### Bash rules

- Ignore `BashToolInput.cwd`. Process cwd is only `approved_root`. A cwd outside the root is denied/ignored.
- Call `create_shell_subprocess(..., settings=explicit Settings)` with `SandboxSettings(enabled=False)`. **Never** call `load_settings()` (it reads `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`).
- No Docker/OS jail in v1. `bash` is a **host-equivalent shell**. `curl | sh` still runs. gpt-repo-mcp refuses arbitrary shell; this spec accepts that blast radius in write mode and documents it.
- ChatGPT `openWorldHint: false` does not make bash safe.

## No LLM API tokens (invariant)

`packages/chatgpt-mcp` must not:

- Read `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, or OpenHarness provider/settings API keys
- Import `openharness.api`, image generation/vision clients, or `load_settings`
- Import or call `create_default_tool_registry`
- Register token-sink tools: `agent`, `task_create`, `image_generation`, `image_to_text`, or any tool that constructs an LLM client

CI: a static import-guard test fails if the bridge or an allowlisted tool module imports those clients / `load_settings` / `openharness.api`.

`connect` must succeed with those env vars unset.

## Errors

- Tool exception or `ToolResult.is_error`: return MCP tool error (`isError`); keep the HTTP process running
- Path outside root, symlink escape, or sensitive path: deny with a short reason
- Wrong path token: HTTP 404
- Tunnel dies after a URL was printed: local `/health` still works; user re-runs `connect` for a new public URL

## Tests (no ChatGPT account)

1. `/health` returns ok
2. Wrong or missing public path token → 404 when token mode is on
3. `tools/list` returns exactly the profile for the configured mode
4. Absolute path outside root denied for `read_file` / `write_file` / `glob` / `grep`
5. `bash` cwd outside root ignored/denied; subprocess cwd is approved root
6. GET/DELETE session behavior
7. `--tunnel cloudflare` with missing `cloudflared` → non-zero exit
8. Import-guard: no LLM client, no `load_settings`, no `openharness.api`, no `create_default_tool_registry`

## Upstream update process

1. `git submodule update --remote upstreams/openharness`
2. Run chatgpt-mcp tests
3. If a new OpenHarness tool is useful and is not a token sink, add it to the allowlist in a separate change
4. Pattern copies from other MCP servers stay in git history / design notes; no runtime submodule for them

Pin submodule SHAs in the parent repo so clones are reproducible. `upstreams/openharness` points at `https://github.com/HKUDS/OpenHarness.git`.

## v1 CLI

```
uv run opengpt-connect --root <abs-path> --mode read|write [--tunnel cloudflare|none]
```

`--root` is required. Default `--tunnel cloudflare`.

Cloudflare success: print the ChatGPT MCP URL. Ctrl+C stops server and tunnel.

`--tunnel none`: print loopback `/health` only; no ChatGPT URL.

## Human gates already decided

- New repo `opengpt`: approved
- Submodules of existing local remotes: approved
- New Python deps (`mcp` SDK, Cloudflare as external binary): approved for v1
- No commit of this spec unless explicitly requested
- Reviewer Block items above: applied 2026-08-16
