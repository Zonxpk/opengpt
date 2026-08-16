# wrap

OpenHarness is a **pinned** submodule at `upstreams/openharness`. New behavior lives in `packages/chatgpt-mcp`. Use public OpenHarness APIs as-is. If an API is private or missing, copy the runner into `chatgpt_mcp` rather than editing the submodule.

Done when: `git diff upstreams/openharness` is empty, and new code imports from `chatgpt_mcp`.
