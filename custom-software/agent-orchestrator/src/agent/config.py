"""Configuration — all settings from environment variables (12-factor)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Field names map to upper-case env vars."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ── Inference ────────────────────────────────────────────
    litellm_base_url: str = "http://litellm:4000/v1"
    litellm_api_key: str = "sk-noauth"  # LiteLLM proxy key; not a real secret
    litellm_model: str = "assistant"
    # "assistant-fast" (qwen3:4b, agent node's GTX 1080 Ti) was the original
    # default for both of these, on the assumption that a smaller model on a
    # secondary GPU would answer router/synthesis-shaped calls faster than
    # the primary reasoning model. Verified directly against the live
    # models (2026-09-15): assistant-fast with think:true reliably burns its
    # entire token budget on invisible reasoning and returns empty content —
    # 5/5 failures on real synthesis prompts, 100% fallback to FALLBACK on
    # routing. assistant (qwen3:30b-A3B, RTX 3090) answered correctly on the
    # same prompts, at comparable or better latency — the MoE architecture
    # keeps its active-parameter cost close to a small dense model, on much
    # faster hardware. "assistant-fast" stays defined in litellm_config.yaml
    # for future use, but isn't a reliable choice for router/synthesis today.
    fast_model: str = "assistant"   # used for simple/conversation graph synthesis
    router_model: str = "assistant"
    # Grammar-constrained JSON output (think:false, see core/router.py) — the
    # model stops as soon as the object closes, so this is a ceiling, not a
    # cost; ~90-120 tokens observed in practice for a full RoutingDecision.
    router_max_tokens: int = 300
    tool_selection_max_tokens: int = 400  # tool-call JSON needs extra room for thinking
    # 300 was tuned for LiteLLM's old "ollama/" provider (/api/generate,
    # thinking leaked into plain content). Under "ollama_chat/" (/api/chat,
    # see litellm_config.yaml), thinking goes to a separate field and the
    # model routinely needs 200-450 tokens of it before any answer content
    # — 300 measured a ~40% empty-content failure rate live. 700 gives
    # headroom; the model stops as soon as it's done, so this is a ceiling.
    synthesis_max_tokens: int = 700
    embeddings_model: str = "embeddings"

    # ── Storage ──────────────────────────────────────────────
    qdrant_host: str = "qdrant"
    qdrant_port: int = 6333
    redis_url: str = "redis://gateway.home.local:6379/0"
    langgraph_db: str = "/data/langgraph/checkpoints.db"

    # ── Safety limits ────────────────────────────────────────
    max_iterations: int = 15
    max_execution_time: int = 120  # seconds
    token_budget: int = 50_000
    session_timeout_seconds: int = 300  # voice session TTL
    max_tools_per_request: int = 7
    circuit_breaker_threshold: int = 3
    circuit_breaker_cooldown: int = 60  # seconds
    loop_detection_window: int = 5
    loop_detection_repeats: int = 3

    # ── MCP ──────────────────────────────────────────────────
    # Relative to the working directory (/app in the container).
    mcp_config_path: str = "config/mcp_servers.json"

    # ── Memory ───────────────────────────────────────────────
    memory_auto_promote_family: bool = True
    memory_confirmation_required: bool = True
    memory_collection: str = "memories"

    # ── Monitoring ───────────────────────────────────────────
    log_level: str = "INFO"
    enable_metrics: bool = True


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton."""
    return Settings()
