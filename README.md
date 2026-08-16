# OpenGPT

ChatGPT Developer Mode drives local OpenHarness tools through a thin Python MCP bridge.

```
uv run opengpt-connect --root <abs-path> --mode read|write [--tunnel cloudflare|none]
```

Paste the printed `ChatGPT MCP URL` into ChatGPT as a Server URL (Streamable HTTP, auth none).

`--tunnel none` is loopback/tests only. It does not print a ChatGPT URL.

v1 bash is a host-equivalent shell. Workspace `.env` is readable in read mode unless later deny-listed.

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
         │  mode allowlist                              │
         │    read:  read_file  glob  grep              │
         │    write: + write_file  edit_file  bash      │
         │                                              │
         │  path jail (resolve + relative_to root)      │
         │  SENSITIVE_PATH_PATTERNS extra deny          │
         │  bash cwd pinned to --root  (no Docker)      │
         └────────────┬─────────────────────────────────┘
                      │  tool.execute(..., cwd=root)
                      ▼
         ┌──────────────────────────────────────────────┐
         │  OpenHarness tool classes (library only)     │
         │  FileRead / Glob / Grep / Write / Edit / Bash│
         └────────────┬─────────────────────────────────┘
                      │
                      ▼
              approved --root workspace
```

No inner LLM. The bridge does not read `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`. ChatGPT is the only model.

`--tunnel none` skips Cloudflare and serves `/mcp` on loopback only.
