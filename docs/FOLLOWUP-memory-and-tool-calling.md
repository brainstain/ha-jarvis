# Follow-up: memory search bug (2 more spots) + broken tool-calling

Context for a fresh chat session. Written 2026-09-15 after a session that fixed
router/synthesis latency and reliability (merged: PR #6, #8, #9; deployed to
the agent node). Two more issues were found live in production logs during
that work and intentionally left for a separate session.

## Suggested opening prompt

> I have two known bugs to fix in ha-jarvis, written up in
> `docs/FOLLOWUP-memory-and-tool-calling.md`. Read that file, then let's fix
> issue 1 (the qdrant-client `.search()` API bug — quick, same pattern as an
> already-merged fix) first, then look at issue 2 (broken tool-calling).

---

## Issue 1: `AsyncQdrantClient`/`QdrantClient` `.search()` no longer exists — two more call sites

**Root cause:** every package that talks to Qdrant pins `qdrant-client` with
an open-ended lower bound (`>=1.9` / `>=1.12`), so builds resolve to whatever
the latest `qdrant-client` is. A recent version removed `.search()` in favor
of `.query_points()`, which also has a different response shape
(`response.points`, not a bare list of `ScoredPoint`).

**Already fixed** in `mcp-memory-scoped` (PR #6, commit `56c57e1`) — the
pattern to copy:

```diff
-    results = await client.search(
+    response = await client.query_points(
         COLLECTION,
-        query_vector=vector,
+        query=vector,
         query_filter=_scope_filter(user_id, scope),
         limit=limit,
         with_payload=True,
     )
     return [
         {...}
-        for r in results
+        for r in response.points
     ]
```

**Two more call sites still broken**, same fix needed:

1. `custom-software/agent-orchestrator/src/agent/memory/backend.py`,
   `QdrantMemoryStore.search()` (~line 121). This is the orchestrator's own
   memory recall — a *separate* Qdrant client from `mcp-memory-scoped`'s (the
   MCP server is a standalone subprocess with its own connection). Confirmed
   broken in live production logs, firing on **every single chat request**:
   ```
   {"error": "'AsyncQdrantClient' object has no attribute 'search'", "event": "memory_lookup_failed", "level": "warning"}
   ```
   It degrades gracefully (`agent/api/routes.py`'s `memory_lookup` wrapper
   catches the exception and returns `[]`), so chat still works — it just
   means the assistant currently never recalls anything from memory in
   conversation. Called via `agent/graphs/nodes/memory.py:24`
   (`memory.search(...)`), which is a thin wrapper, not a separate bug.

2. `custom-software/speechbrain-speaker-id/app.py`, the `/identify` endpoint
   (~line 112): `qdrant.search(...)` — a **sync** `QdrantClient`, not async,
   so the fix is `qdrant.query_points(...)` / `.points`, same idea. Not yet
   confirmed broken live (speechbrain wasn't reachable from my test machine
   this session — connection refused on 192.168.13.15:8200, worth checking
   whether that container is even up before or while fixing this), but it's
   the identical bug pattern and should be fixed regardless.

**Before calling it done:** grep for any other `.search(` against a Qdrant
client (`grep -rn "\.search(" custom-software --include="*.py"` and filter
out regex `.search()` false positives) in case a fourth site was missed.

## Issue 2: Native tool-calling doesn't work — no tool ever actually executes

**What's broken:** whenever an LLM call passes OpenAI-style `tools=[...]`
(function-calling schema) to LiteLLM, the response reliably comes back with
**empty content and no `tool_calls`**, regardless of `think:true` or
`think:false`, and regardless of which model (`assistant-fast`/qwen3:4b *and*
`assistant`/qwen3:30b both fail the same way). This was verified directly
against the live LiteLLM proxy with a minimal single-tool schema, and is
visible in live production logs as an `llm_empty_content` event whose
`max_tokens` matches `tool_selection_max_tokens` (400):
```
{"model": "assistant", "finish_reason": "stop", "completion_tokens": 49, "max_tokens": 400, "event": "llm_empty_content"}
```

**Impact:** any real command that should invoke a tool (turn off a light,
check a calendar, etc.) currently never actually calls the tool. The pipeline
silently falls through to a conversational answer instead — no error, no
crash, just quietly non-functional. This is a **correctness** bug, not a
latency one — the broken call fails in well under a second, so it wasn't
contributing to the slow-response investigation this came out of.

**Where:**
- `custom-software/agent-orchestrator/src/agent/api/routes.py` —
  `_run_simple`'s `execute_tool()`, the tool-selection call
  (`llm.complete(..., tools=render_openai_tools(tools), ...)`).
- `custom-software/agent-orchestrator/src/agent/graphs/nodes/tools.py` —
  `make_tool_executor()`, same mechanism, used by the interactive graph
  (currently unwired/501s per existing tests, so unverified live, but almost
  certainly has the identical problem).
- `custom-software/agent-orchestrator/src/agent/core/llm.py` —
  `LLMClient.complete()`, where `tools`/`tool_choice`/`extra_body` get set on
  the outgoing request. This is the shared mechanism both call sites go
  through.
- `custom-software/agent-orchestrator/src/agent/mcp/registry.py` —
  `render_openai_tools()`, which converts MCP tool schemas into the OpenAI
  function-calling format currently being sent.

**Not yet root-caused. Ideas to investigate, roughly in order of effort:**

1. Isolate which layer is broken: call Ollama's own native `/api/chat`
   endpoint directly (bypassing LiteLLM) with a `tools` array, using the
   exact same schema. If that also fails, the bug is in Ollama's
   tool-calling support for these qwen3 tags on the currently-installed
   Ollama version (check `ollama --version` on both the agent and inference
   nodes). If Ollama-direct works but LiteLLM-proxied doesn't, the bug is in
   LiteLLM's OpenAI-tools → Ollama-native-tools translation for this
   LiteLLM version.
2. Try `tool_choice` forced to a specific function (not `"auto"`) to see if
   forcing the model into "must call this tool" mode engages a different,
   working code path — would confirm whether the issue is specifically in
   the model's *decision* of whether to call a tool, vs. the mechanism
   overall.
3. **Most promising, given what already worked for the router**: stop
   relying on native function-calling entirely. `agent/core/router.py`'s
   `_routing_schema()` / `_ROUTING_SCHEMA` (added this session, PR #9) proved
   that Ollama's grammar-constrained JSON decoding (`extra_body: {"think":
   false, "format": <json-schema>}`) reliably produces valid, on-topic
   output where native mechanisms were failing outright. The same technique
   could replace tool-selection: ask the model for a small JSON object
   naming which tool (if any) to call and its arguments — schema built from
   the available tools' names/parameters (enum-constrain the tool name field
   the same way `_ROUTING_SCHEMA` enum-constrains `tools_needed`, since an
   unconstrained free-string field is exactly where garbage leaked in for
   the router). Would need a schema-building analog to
   `render_openai_tools()` in `mcp/registry.py`, and a parser analog to
   `MetaRouter._parse()`.

## General context carried over from this session

- PRs #6, #8, #9 are merged into `main` and deployed to `agent-orchestrator`
  on the agent node (192.168.13.22) as of 2026-09-15 ~18:49 UTC. Deploy
  command used: `ssh michael@192.168.13.22 "cd /opt/homelab-ai/agent &&
  docker compose pull agent-orchestrator && docker compose up -d --no-deps
  --force-recreate agent-orchestrator"`.
- `fast_model` / `router_model` now default to `"assistant"` (qwen3:30b, RTX
  3090), not `"assistant-fast"` (qwen3:4b, GTX 1080 Ti) — the 1080Ti tier was
  found unreliable for both router classification and synthesis (100%
  empty-content failure on 5/5 real synthesis prompts under `think:true`;
  reproducible literal garbage under `think:false`). It isn't used for
  anything currently except embeddings (`nomic-embed-text`, still on the
  1080 Ti).
- **Safety note from a separate incident this same day:** do NOT invoke
  `assistant-local` (qwen3:8b, also on the 1080 Ti) without warning the user
  first — testing it caused a ~5 minute `assistant-fast` outage and a GPU
  crash-loop needing two `docker restart ollama-agent`s to clear. Treat it as
  unsafe/untested.
- An e2e test suite exists at `custom-software/e2e/` (README there explains
  usage) — real HTTP/WebSocket/MCP-stdio checks against the live stack, with
  latency budgets and graceful skipping of unreachable services. Useful for
  validating whichever fix comes out of this. Point it at the live homelab
  via `AGENT_IP=192.168.13.22 INFERENCE_IP=192.168.13.15`.
- Traffic is low (single household) — no need to load-test a fix, a handful
  of live requests is enough to validate it.
- Local dev machine only has Python 3.9; `mcp` package and some
  agent-orchestrator deps need 3.12+. A `python:3.12-slim` Docker container
  with the repo mounted in was used throughout this session to run the real
  test suite and verify fixes — same approach works here.
