# End-to-end test suite

Exercises the deployed stack the way a real user does — HTTP, WebSocket, and
MCP stdio calls against live services — and records how long every call
took. This is **not** a load test: each check drives at most a handful of
sequential requests, one at a time. Traffic on this system is one household,
so the goal is catching a regression (a model that stopped staying warm, a
health check that started blocking on DNS), not modeling concurrency.

## What's covered

| File | Exercises |
|---|---|
| `test_infra_health.py` | litellm, qdrant, open-webui, ollama (agent + inference) — reachability + latency |
| `test_orchestrator_chat.py` | `/health`, `/chat` (sync + voice channel), `/chat/async` + `/tasks/{id}` |
| `test_orchestrator_openai_compat.py` | `/v1/models`, `/v1/chat/completions` (the Open WebUI path) |
| `test_orchestrator_websocket.py` | `/ws/chat` streaming round trip |
| `test_ha_integration.py` | `/ha/conversation/process`, `/ha/health` |
| `test_mcp_servers.py` | mcp-memory-scoped, mcp-notifications, mcp-workflow-status — spawned via the real `MCPClientHub`, read-only tool calls only |
| `test_voice_pipeline.py` | whisper / piper / openWakeWord (Wyoming `describe`), speechbrain-speaker-id `/health` + `/speakers` |

Every test skips (never fails) when its backing service isn't reachable —
see "Reachability gating" below. That makes it safe to run this against a
partial deployment (e.g. only Phase 1 up) and get a report for whatever
*is* running.

## Installing

From `custom-software/e2e/`:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
```

To also run the direct MCP stdio tests (`test_mcp_servers.py`), install the
three server packages the same interpreter will spawn:

```bash
pip install -e ../mcp-memory-scoped -e ../mcp-notifications -e ../mcp-workflow-status
```

Without those, `test_mcp_servers.py` skips itself (`pytest.importorskip`)
instead of failing.

## Running

**Against a local `docker compose` stack** (defaults assume `localhost` for
every port the phase1/phase2 compose files publish):

```bash
docker compose -f ../../servers/agent/docker-compose.yml --profile phase2 up -d
pytest
```

**Against the live homelab**, point the suite at your deploy `.env` — it
reads the same `AGENT_IP` / `INFERENCE_IP` / `HA_URL` / `HA_TOKEN` names
your `.env` already has, so no separate config file is needed:

```bash
set -a; source ../../.env; set +a
pytest
```

Override any single service if it lives somewhere non-standard, e.g.
`ORCHESTRATOR_URL=http://192.168.13.22:8100 pytest`. See `endpoints.py` for
every overridable name.

## Reachability gating

A session-scoped fixture (`reachability`, in `conftest.py`) probes every
backing service once at the start of the run. Each test calls
`require(reachability, "litellm", "qdrant", ...)` as its first line and
`pytest.skip`s if anything it needs is down, rather than hanging on
per-request timeouts or failing on infrastructure that was never meant to
be up in this environment (e.g. Home Assistant, which lives outside this
repo's deploy scope).

## Response-time budgets

Each check times itself with `perf.timed(...)` / `perf.atimed(...)` and, in
most cases, asserts a budget in the same call — see `endpoints.py`'s
`Budgets` class for the full list, all overridable via `E2E_BUDGET_*` env
vars (e.g. `E2E_BUDGET_CHAT_SIMPLE=30`). Defaults are deliberately generous
for a single GTX 1080 Ti / RTX 3090 homelab; tune them after your first
real run against your own hardware rather than trusting the defaults as an
SLO.

## Reading the results

At the end of every run, `conftest.py` prints a min/mean/p50/p95/max table
per operation and writes it to `reports/latest.json` (plus a timestamped
copy). `reports/` is gitignored — treat it as scratch output, not a
tracked artifact.

## What's intentionally out of scope

- **Load / concurrency testing.** Not needed at this traffic level; see the
  top of this file. `agent-orchestrator`'s own dev dependencies include
  `locust` for if that ever changes — this suite doesn't use it.
- **Async task completion.** `/chat/async` enqueues a task, but Celery
  dispatch for it isn't wired up yet (see the docstring in
  `test_orchestrator_chat.py`); the test only checks the contract that
  exists today.
- **Sending real audio** through whisper/piper/openWakeWord. `describe` is
  a genuine protocol round trip that proves each service loaded a model and
  responds, without the suite needing to ship audio fixtures around.
