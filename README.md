# OpenGPT

ChatGPT Developer Mode drives local OpenHarness tools through a thin Python MCP bridge.

```
uv run opengpt-connect --root <abs-path> --mode read|write [--tunnel cloudflare|none]
```

Clone:

```
git clone --recurse-submodules <this-repo>
```

OpenHarness is the `upstreams/openharness` submodule (`https://github.com/HKUDS/OpenHarness.git`, pinned SHA).

Paste the printed `ChatGPT MCP URL` into ChatGPT as a Server URL (Streamable HTTP, auth none).

`--tunnel none` is loopback/tests only. It does not print a ChatGPT URL.

v1 file tools are jailed to `--root`. `bash` is a **host-equivalent shell** (cwd pinned, host otherwise reachable). Do not treat the whole MCP as jailed. Workspace `.env` is readable in read mode unless later deny-listed.

Prefer `fast_context` for exploratory questions, then `read_many` and `apply_changes` (up to 20 files per call). Oversized `grep` / `bash` / `glob` / `lsp` / `read_many` / `fast_context` results are written to `--root/.opengpt-spill/` and the model gets a head/tail preview plus that path.

v2 optimizes ChatGPT ↔ MCP round-trips, not Python function-call latency. Typical exploration: v1 used 3–6 tool turns; v2 uses 1 `fast_context` turn plus ChatGPT's semantic decision. Do not treat that as a fixed wall-clock speedup until measured in ChatGPT Web.

The router does not understand code semantically. It uses OpenHarness's local dry-run candidate scoring plus small deterministic intent rules. ChatGPT remains responsible for semantic decisions. `fast_context` is read-only. On a strong skill-name match it may prepend bundled/project skill text. Heuristic project memory is appended after repo evidence and must not override it. Direct write tools remain write-mode-only.

```
ChatGPT
   │
   │ one MCP call
   ▼
fast_context
   │
   ├─ OpenHarness dry-run candidate routing
   │      no LLM
   │
   └─ deterministic recipe
          │
          ├─ grep
          ├─ read_many
          └─ optional LSP
```

Recommended tool use:

```
Exploring an unfamiliar code area:  fast_context
Exact known search:                 grep
Exact known files:                  read_file / read_many
Applying known edits:               apply_changes
Trial edits + verification:         isolated_change
Running verification:               verify_project
Ad-hoc / short shell:               bash
Long test/build/dev server:         long_task
```

## Tools

Read mode: `fast_context`, `read_file`, `glob`, `grep`, `lsp`, `read_many`.

Write mode adds: `write_file`, `edit_file`, `apply_changes`, `verify_project`, `isolated_change`, `long_task`, `bash`.

`route_preview` is local-debug only (`opengpt-connect --debug-tools`). It is not in the production MCP catalog.

Not exposed: agent/plan/image/MCP-client tools, cron, `task_create`/`local_agent`, web, and other OpenHarness extras. `verify_project` / `isolated_change` / `long_task` reuse OpenHarness model-free runners.

## How it works

```
 You ──ask──► ChatGPT Developer Mode
                    │
                    │  Server URL (Streamable HTTP, auth none)
                    ▼
         ┌──────────────────────────┐
         │  Cloudflare quick tunnel │  default --tunnel cloudflare
         │  *.trycloudflare.com     │
         └────────────┬─────────────┘
                      │  HTTPS
                      ▼
         ┌──────────────────────────────────────────────┐
         │  opengpt-connect  (127.0.0.1:8787)           │
         │                                              │
         │  /t/<16-byte-hex>/mcp   POST GET DELETE      │
         │  /health                                     │
         │                                              │
         │  Origin check  →  path token  →  sessions    │
         │  (100 max, 30 min idle)                      │
         └────────────┬─────────────────────────────────┘
                      │  tools/call  1:1
                      ▼
         ┌──────────────────────────────────────────────┐
         │  ToolAdapter                                 │
         │                                              │
         │  small allowlist                             │
         │  OpenHarness: read/grep/glob/lsp/edit/bash   │
         │  + read_many / apply_changes                 │
         │  + spill oversized output to .opengpt-spill  │
         │                                              │
         │  path jail for file/glob/grep/edit           │
         │  SENSITIVE_PATH_PATTERNS extra deny          │
         │  bash: host-equivalent (cwd pin only)        │
         └────────────┬─────────────────────────────────┘
                      │  tool.execute(..., cwd=root)
                      ▼
         ┌──────────────────────────────────────────────┐
         │  OpenHarness tool classes (library)          │
         │  Not exposed: agent, cron, tasks, web, …     │
         └────────────┬─────────────────────────────────┘
                      │
                      ▼
              approved --root workspace
```

No inner LLM. The bridge does not read `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`. ChatGPT is the only model.

`--tunnel none` skips Cloudflare and serves `/mcp` on loopback only.
