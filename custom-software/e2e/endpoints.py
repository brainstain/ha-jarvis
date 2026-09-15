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
    mcp_tool_call: float = 5.0
    chat_simple: float = 20.0
    chat_multistep: float = 90.0
    chat_interactive: float = 90.0
    openai_compat: float = 20.0
    websocket_roundtrip: float = 25.0
    wyoming_describe: float = 3.0


@lru_cache
def get_endpoints() -> Endpoints:
    return Endpoints()


@lru_cache
def get_budgets() -> Budgets:
    return Budgets()
