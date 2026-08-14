# Agent Orchestrator

LangGraph-based agent orchestrator — the central brain of the ha-jarvis system.
Full design in [SPEC.md](SPEC.md).

## Build status

This is a **partial implementation**. What's built and tested vs. what remains:

### Implemented
| Module | Status |
|--------|--------|
| `config.py` | Complete — all settings from env vars |
| `api/schemas.py` | Complete — request/response models per spec |
| `api/routes.py` | All 7 endpoints exist; `/chat` executes the simple graph, 501 for the other three |
| `core/session.py` | Complete — all 4 thread-resolution rules + TTL expiry |
| `core/safety.py` | Complete — iteration limits, token budget, loop detection, circuit breakers, observation-masking compression |
| `core/router.py` | Complete — meta-reasoning router w/ deterministic fallback |
| `core/output.py` | Complete — channel resolution |
| `memory/scoping.py` | Complete — family/personal filters + auto-promotion |
| `mcp/tool_filter.py` | Complete — registry, max-7 cap, usage-based priority |
| `mcp/client.py` | Complete — stdio + SSE transports, cached sessions, envelope results, health ping |
| `mcp/registry.py` | Complete — config-driven discovery, category mapping, OpenAI/LiteLLM rendering |
| `core/llm.py` | Complete — LiteLLM chat client for tool selection and synthesis |
| `graphs/state.py` | Complete — AgentState with append reducers for parallel fan-out |
| `graphs/simple.py` | Complete — dependency-injected nodes |
| `graphs/multistep.py` | Complete — plan/execute/replan loop w/ budget compression |

### Not yet built
- `graphs/research.py`, `graphs/interactive.py` — async research and HITL diagnostic graphs
- `graphs/nodes/*` — concrete planner/executor/validator/summarizer/memory nodes
  (the graphs take these as injected callables today)
- `memory/mem0_client.py`, `memory/qdrant_client.py` — `ScopedMemory` is written
  against a `VectorStore` protocol; the Qdrant implementation is pending
- `tasks/*` — Celery app and async research task; `/chat/async` currently tracks
  tasks in memory only
- `api/websocket.py` — WS streaming for Open WebUI
- `integrations/*` — HA conversation protocol, RAG pipeline

## Running

```bash
pip install -e .
PYTHONPATH=src uvicorn agent.main:app --host 0.0.0.0 --port 8100
```

MCP servers are declared in [`config/mcp_servers.json`](config/mcp_servers.json)
(path overridable via `MCP_CONFIG_PATH`). Servers are discovered once at
startup; a server that fails to start is logged and skipped, and shows up in
`/health` under `mcp:<name>`. Only `ha-mcp` ships enabled — the custom servers
in `custom-software/mcp-servers/` are declared but disabled until they exist.

## Tests

```bash
python -m pytest -q
```

100 tests covering session resolution, safety limits, memory scoping, tool
filtering, router parsing/fallback, simple-graph execution, MCP config
validation and discovery, tool-call envelopes, and the `/chat` + `/health`
wiring. No test touches the network or spawns a subprocess.
