"""Where the stack lives, and how fast it's supposed to answer.

Defaults point at localhost so the suite works unmodified against a
`docker compose --profile phase2 up -d` run on the box you're testing from.
To run against the live homelab, export AGENT_IP / INFERENCE_IP / HA_URL /
HA_TOKEN the same way `.env` does for deploys — this module reads exactly
those names, so a deploy `.env` doubles as an e2e config with zero edits.
Per-endpoint overrides (e.g. ORCHESTRATOR_URL, QDRANT_URL — see the field
names below) win when a single service lives somewhere non-standard, such as
a port forwarded to a different host.

Latency budgets are deliberately generous: this is a single GTX 1080 Ti /
RTX 3090 homelab serving one household, not a latency-sensitive service with
an SLO. The point of a budget is to catch a regression (a warm model that
stopped staying warm, a health check that started blocking on DNS) — not to
chase milliseconds. Tune E2E_BUDGET_* after your first real run if a
number is consistently too tight or too loose for your hardware.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Endpoints(BaseSettings):
    """Base URLs / hosts for every service the suite talks to."""

    model_config = SettingsConfigDict(extra="ignore")

    # ── Node IPs (same names as the repo-root .env.template) ───────────
    agent_ip: str = "127.0.0.1"
    inference_ip: str = "127.0.0.1"
    gateway_ip: str = "127.0.0.1"

    # ── Home Assistant (external VM, out of deploy scope) ───────────────
    ha_url: str = ""
    ha_token: str = ""

    # ── Per-service overrides (E2E_ prefix); computed from the IPs above
    #    when left blank ────────────────────────────────────────────────
    orchestrator_url: str = ""
    litellm_url: str = ""
    qdrant_url: str = ""
    open_webui_url: str = ""
    ollama_agent_url: str = ""
    ollama_inference_url: str = ""
    speechbrain_url: str = ""

    wyoming_whisper_host: str = ""
    wyoming_whisper_port: int = 10300
    wyoming_piper_host: str = ""
    wyoming_piper_port: int = 10200
    wyoming_wakeword_host: str = ""
    wyoming_wakeword_port: int = 10400

    def model_post_init(self, __context) -> None:
        self.orchestrator_url = self.orchestrator_url or f"http://{self.agent_ip}:8100"
        self.litellm_url = self.litellm_url or f"http://{self.agent_ip}:4000"
        self.qdrant_url = self.qdrant_url or f"http://{self.agent_ip}:6333"
        self.open_webui_url = self.open_webui_url or f"http://{self.agent_ip}:3000"
        self.ollama_agent_url = self.ollama_agent_url or f"http://{self.agent_ip}:11434"
        self.ollama_inference_url = self.ollama_inference_url or f"http://{self.inference_ip}:11434"
        self.speechbrain_url = self.speechbrain_url or f"http://{self.inference_ip}:8200"
        self.wyoming_whisper_host = self.wyoming_whisper_host or self.inference_ip
        self.wyoming_piper_host = self.wyoming_piper_host or self.inference_ip
        self.wyoming_wakeword_host = self.wyoming_wakeword_host or self.inference_ip


class Budgets(BaseSettings):
    """Max acceptable latency (seconds) per operation class. All E2E_BUDGET_*."""

    model_config = SettingsConfigDict(env_prefix="E2E_BUDGET_", extra="ignore")

    health: float = 2.0
    tcp_connect: float = 2.0
    # Cold-starting an MCP stdio server (interpreter + import + handshake) is
    # its own, one-time cost — separate from steady-state call latency, and
    # much larger. Budgeted generously and measured/asserted separately from
    # mcp_tool_call; see test_mcp_servers.py.
    mcp_cold_start: float = 20.0
    mcp_tool_call: float = 5.0
    # Calibrated against the live homelab on 2026-09-15 (RTX 3090 + GTX 1080
    # Ti, qwen3:30b/8b/4b). /chat, /v1/chat/completions and
    # /ha/conversation/process all route through the same handler
    # (agent/api/routes.py:chat), which pays for two sequential LLM calls —
    # MetaRouter's own intent classification, then the graph's synthesis —
    # before answering. Observed 23-36s end to end; budgeted with headroom,
    # not tightened to the sample, since qwen3 latency varies with how much
    # `think:true` fallback/retry the synthesis step needs (see
    # [[feedback-qwen3-synthesis]] in project memory).
    chat_simple: float = 45.0
    # Never exercised end-to-end yet (no test drives an intent the router
    # classifies into these graphs) — set to just above the server's own
    # MAX_EXECUTION_TIME=120s safety-guard ceiling (agent/config.py) rather
    # than guessed, since that's the one number the system itself commits to.
    chat_multistep: float = 130.0
    chat_interactive: float = 130.0
    # Same handler as chat_simple (openai_compat.py wraps routes.chat
    # directly) — same budget for the same reason.
    openai_compat: float = 45.0
    # /ws/chat is a genuinely different, faster path: it talks to LiteLLM
    # directly and skips MetaRouter + LangGraph entirely. Observed 3.6s.
    websocket_roundtrip: float = 10.0
    wyoming_describe: float = 3.0


@lru_cache
def get_endpoints() -> Endpoints:
    return Endpoints()


@lru_cache
def get_budgets() -> Budgets:
    return Budgets()
