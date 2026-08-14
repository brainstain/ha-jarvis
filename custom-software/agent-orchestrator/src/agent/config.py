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
    router_model: str = "assistant-fast"
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
