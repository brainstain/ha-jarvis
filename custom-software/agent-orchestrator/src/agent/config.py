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
    # Reasoning and answer share this budget: qwen3:30b writes its reasoning
    # inline in content even with think:false, and the answer comes last, so
    # a cut-off yields no answer at all. 300 gave a ~40-65% empty-content
    # rate; a 3-tool query used ~500. The model stops on its own when done,
    # so this is a runaway guard, not a cost — keep it well above normal use.
    synthesis_max_tokens: int = 1200
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

    # ── Conversation ─────────────────────────────────────────
    # Rolling window of recent turns handed back into the LLM prompt on the
    # same thread_id (see core/history.py) — what actually makes "and the
    # office one too" resolvable against the prior turn. max_turns counts
    # messages (user+assistant), not exchanges, so 8 is ~4 back-and-forths.
    # The TTL is longer than session_timeout_seconds on purpose: that TTL
    # only governs how long an *anonymous* voice/webui request keeps
    # resolving to the same thread_id, not how long a conversation someone
    # is actively having (possibly via an explicit thread_id) stays live.
    conversation_history_max_turns: int = 8
    conversation_history_ttl_seconds: int = 1800

    # ── MCP ──────────────────────────────────────────────────
    # Relative to the working directory (/app in the container).
    mcp_config_path: str = "config/mcp_servers.json"
    # google-workspace's tools take a required user_google_email argument the
    # model has no way to know — this is a single-user home system, so it's
    # injected server-side the same way user_id is, from this fixed value.
    google_workspace_user_email: str = ""
    # The model can correctly identify "Family" as a calendar name from a
    # prior list_calendars call, but google-workspace's get_events needs the
    # real Google Calendar ID, not the display name — confirmed live, it
    # either queried "primary" (finds nothing) or hallucinated the literal
    # string "family" as calendar_id (404s), for every family-calendar query.
    # Single-user home system with a small, static calendar set, so a fixed
    # mapping surfaced in the tool-selection prompt is simpler and more
    # reliable than teaching the model a list-then-query flow it can't do
    # in one turn anyway (see make_tool_executor: one tool call per turn).
    google_calendar_family_id: str = ""

    # ── Memory ───────────────────────────────────────────────
    memory_auto_promote_family: bool = True
    memory_confirmation_required: bool = True
    memory_collection: str = "memories"

    # ── Monitoring ───────────────────────────────────────────
    log_level: str = "INFO"
    enable_metrics: bool = True

    # ── Speaker identification (Phase 2) ────────────────────
    # wyoming-identify-proxy's HTTP side-channel (see
    # custom-software/wyoming-identify-proxy/app.py) — GET /last-speaker
    # returns whoever speechbrain most recently identified, by time only
    # (HA's Wyoming STT protocol carries no device/satellite identifier
    # to correlate against). None/empty disables the lookup entirely.
    wyoming_identify_proxy_url: str | None = "http://192.168.13.15:8300"


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton."""
    return Settings()
