# Custom Software: Agent Orchestrator

**Language:** Python 3.12  
**Framework:** FastAPI + LangGraph + Celery  
**Location:** `custom-software/agent-orchestrator/`  
**Deploys to:** Agent Node  
**Priority:** Phase 2 (core system — build first)

---

## Overview

The Agent Orchestrator is the central brain of the system. It receives requests from all interfaces (voice pipeline via HA, Open WebUI, push notification replies), routes them through a planning layer, executes multi-step workflows via LangGraph, and delivers results through the appropriate output channel.

---

## API Specification

### Endpoints

```
POST   /chat                   Synchronous chat (voice pipeline, HA conversation agent)
POST   /chat/async             Queue async task, returns task_id
GET    /tasks/{task_id}        Poll async task status + partial results
POST   /tasks/{task_id}/resume Resume HITL-interrupted workflow with user input
GET    /tasks/pending          List workflows awaiting user input (by user_id)
DELETE /tasks/{task_id}        Cancel a running or pending task
WS     /ws/chat                Streaming WebSocket for Open WebUI integration
GET    /health                 Service health check
GET    /metrics                Prometheus metrics endpoint
```

### Request Schema

```python
class ChatRequest(BaseModel):
    message: str                              # User input text
    user_id: str                              # From Authentik / SpeechBrain
    scope: Literal["family", "personal"]      # Memory scope
    thread_id: str | None = None              # Resume existing thread
    speaker_id: str | None = None             # Voice identification
    source: Literal["voice", "webui", "ha", "notification"] = "webui"
    satellite_id: str | None = None           # Which voice satellite
    metadata: dict | None = None              # Extensible context
```

### Response Schema

```python
class ChatResponse(BaseModel):
    message: str                              # Response text
    thread_id: str                            # For continuity
    output_channel: str                       # Where result was/will be delivered
    task_id: str | None = None                # If async, the task ID
    tools_used: list[str] = []                # Which MCP tools were called
    memory_updates: list[dict] = []           # Memories created/updated
    confidence: float = 1.0                   # Agent confidence in response
```

---

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                    Agent Orchestrator                      │
│                                                            │
│  ┌─────────────┐     ┌───────────────────────────┐        │
│  │   FastAPI    │────▶│    Session Manager         │        │
│  │  /chat       │     │  (satellite,speaker)→thread│        │
│  │  /ws/chat    │     └────────────┬──────────────┘        │
│  │  /tasks/*    │                  │                        │
│  └─────────────┘                  ▼                        │
│                        ┌──────────────────────┐            │
│                        │  Meta-Reasoning       │            │
│                        │  Router (4B model)    │            │
│                        │  - Intent classify    │            │
│                        │  - Tool selection     │            │
│                        │  - Execution strategy │            │
│                        └─────────┬────────────┘            │
│                                  │                          │
│              ┌───────────────────┼───────────────────┐     │
│              ▼                   ▼                   ▼     │
│  ┌──────────────────┐ ┌─────────────────┐ ┌────────────┐ │
│  │  Simple Command   │ │  Multi-Step      │ │  Async     │ │
│  │  (single tool)    │ │  (LangGraph)     │ │  (Celery)  │ │
│  └────────┬─────────┘ └────────┬────────┘ └─────┬──────┘ │
│           │                    │                  │        │
│           └────────────────────┼──────────────────┘        │
│                                ▼                            │
│                     ┌──────────────────────┐               │
│                     │   MCP Client Hub      │               │
│                     │   (tool execution)    │               │
│                     └──────────┬───────────┘               │
│                                ▼                            │
│                     ┌──────────────────────┐               │
│                     │   Output Router       │               │
│                     │   voice/webui/push    │               │
│                     └──────────────────────┘               │
└──────────────────────────────────────────────────────────┘
```

---

## Module Structure

```
agent-orchestrator/
├── Dockerfile
├── pyproject.toml
├── README.md
├── src/
│   └── agent/
│       ├── __init__.py
│       ├── main.py                 # FastAPI app entry point
│       ├── config.py               # Pydantic Settings from env vars
│       ├── api/
│       │   ├── __init__.py
│       │   ├── routes.py           # HTTP endpoints
│       │   ├── websocket.py        # WebSocket streaming handler
│       │   └── schemas.py          # Request/Response models
│       ├── core/
│       │   ├── __init__.py
│       │   ├── session.py          # Session manager (thread resolution)
│       │   ├── router.py           # Meta-reasoning router (4B model)
│       │   ├── safety.py           # Circuit breakers, loop detection, token budget
│       │   └── output.py           # Output channel routing logic
│       ├── graphs/
│       │   ├── __init__.py
│       │   ├── state.py            # AgentState TypedDict
│       │   ├── simple.py           # Single-tool command graph
│       │   ├── multistep.py        # Plan-then-execute graph
│       │   ├── research.py         # Async research graph
│       │   ├── interactive.py      # ReAct + HITL diagnostic graph
│       │   └── nodes/
│       │       ├── __init__.py
│       │       ├── planner.py      # Task decomposition node
│       │       ├── executor.py     # Tool execution node
│       │       ├── validator.py    # Result validation node
│       │       ├── summarizer.py   # Context compression node
│       │       └── memory.py       # Memory read/write node
│       ├── mcp/
│       │   ├── __init__.py
│       │   ├── client.py           # MCP client connection manager
│       │   ├── registry.py         # Tool registry (load from config)
│       │   └── tool_filter.py      # Semantic tool filtering (max 7)
│       ├── memory/
│       │   ├── __init__.py
│       │   ├── mem0_client.py      # Mem0 integration for auto-memory
│       │   ├── qdrant_client.py    # Direct Qdrant for RAG + search
│       │   ├── scoping.py          # Family/personal scope enforcement
│       │   └── states.py           # Pending/confirmed/pattern transitions
│       ├── tasks/
│       │   ├── __init__.py
│       │   ├── celery_app.py       # Celery configuration
│       │   ├── research.py         # Async research task
│       │   └── background.py       # Scheduled background tasks
│       └── integrations/
│           ├── __init__.py
│           ├── ha_conversation.py  # HA conversation agent protocol
│           └── rag_pipeline.py     # Paperless-NGX → Qdrant indexing
└── tests/
    ├── conftest.py
    ├── test_router.py
    ├── test_session.py
    ├── test_graphs/
    │   ├── test_simple.py
    │   ├── test_multistep.py
    │   └── test_research.py
    ├── test_mcp/
    │   └── test_tool_filter.py
    └── test_memory/
        ├── test_scoping.py
        └── test_states.py
```

---

## Key Module Specifications

### Session Manager (`core/session.py`)

Resolves or creates LangGraph thread_ids based on input context.

```python
class SessionManager:
    """Maps (source, user_id, context) → thread_id with TTL."""
    
    def resolve_thread(self, request: ChatRequest) -> str:
        """
        Rules:
        1. If request.thread_id provided → use it (explicit continuation)
        2. If source == "voice" and same satellite + speaker within 5 min → reuse thread
        3. If source == "webui" and same Open WebUI conversation → map to thread
        4. Otherwise → create new thread_id (uuid4)
        """
    
    def get_pending_threads(self, user_id: str) -> list[ThreadInfo]:
        """Return threads with HITL interrupts waiting for this user."""
    
    def expire_sessions(self):
        """Background task: expire voice sessions after SESSION_TIMEOUT_SECONDS."""
```

### Meta-Reasoning Router (`core/router.py`)

Uses the fast 4B model to classify intent and plan execution strategy WITHOUT loading all tool schemas into context.

```python
class MetaRouter:
    """
    Fast classification using Qwen3-4B (assistant-fast model via LiteLLM).
    Runs BEFORE the main agent graph to minimize latency and token usage.
    """
    
    async def route(self, message: str, user_context: dict) -> RoutingDecision:
        """
        Returns:
          - intent: str (command, question, research, diagnostic, conversation)
          - graph: str (simple, multistep, research, interactive)
          - tools_needed: list[str] (max 7 tool categories)
          - execution_mode: str (sync, async)
          - output_channel: str (voice, webui, push)
          - parallel_steps: bool (can steps run in parallel?)
        
        Prompt template:
          System: You are a routing classifier. Given the user message and context,
                  determine intent, required tool categories, and execution strategy.
                  Respond in JSON only.
          
          Available tool categories: [home_assistant, memory, calendar, search,
                                      documents, notifications, browser, filesystem]
          
          User context: {scope, recent_topics, time_of_day, source_interface}
        """
```

### Safety Stack (`core/safety.py`)

```python
class SafetyGuard:
    """Enforces all safety limits on agent execution."""
    
    def check_iteration_limit(self, state: AgentState) -> bool:
        """Returns False if iteration_count >= max_iterations (default 15)."""
    
    def check_token_budget(self, state: AgentState) -> str:
        """Returns 'ok' | 'compress' (at 80%) | 'stop' (at 100%)."""
    
    def detect_loop(self, state: AgentState) -> bool:
        """True if same tool+similar_args called 3 times in sliding window."""
    
    def check_circuit_breaker(self, tool_name: str) -> bool:
        """True if tool has 3+ consecutive failures (60s cooldown)."""

class TokenBudgetManager:
    """Tracks token usage across a multi-step chain."""
    
    def count_tokens(self, text: str) -> int:
        """Approximate token count (chars/4 for fast estimation)."""
    
    def should_compress(self, state: AgentState) -> bool:
        """True when accumulated tokens > 80% of budget."""
    
    def compress_context(self, state: AgentState) -> AgentState:
        """
        Strategy (from research):
        1. Observation masking: replace old tool outputs with '[Result: summary]'
        2. If still over budget: LLM summarization of older messages
        3. Preserve: system prompt, last 3 messages, current plan step
        """
```

### Tool Filter (`mcp/tool_filter.py`)

```python
class ToolFilter:
    """
    Reduces tool hallucination by limiting visible tools per request.
    Research finding: hallucinations increase significantly above 7 tools.
    """
    
    def __init__(self, registry: ToolRegistry):
        self.registry = registry  # All available MCP tools
    
    def select_tools(self, categories: list[str], max_tools: int = 7) -> list[ToolSchema]:
        """
        Given tool categories from MetaRouter, return only relevant tool schemas.
        
        Each category maps to specific MCP server tools:
          home_assistant → ha-mcp tools (filtered to relevant domain)
          memory → mcp-memory-scoped tools
          calendar → mcp-calendar tools
          search → mcp-searxng tools
          documents → mcp-filesystem + Paperless API tools
          notifications → mcp-notifications tools
          browser → mcp-playwright tools
          filesystem → mcp-filesystem tools
        
        If combined tools > max_tools, prioritize by:
        1. First category in list (most relevant to intent)
        2. Most frequently used tools (from usage stats)
        """
```

### Memory Scoping (`memory/scoping.py`)

```python
class ScopedMemory:
    """Enforces family/personal memory boundaries at every access point."""
    
    async def search(self, query: str, user_id: str, scope: str, limit: int = 5) -> list[Memory]:
        """
        scope == "family" → filter: scope="family"
        scope == "personal" → filter: scope="family" OR (scope="personal" AND user_id=user_id)
        """
    
    async def store(self, memory: Memory, user_id: str, scope: str) -> str:
        """
        Store with metadata: {scope, user_id, memory_type, timestamp, tags, source}
        Auto-promote to family if:
          - Contains calendar events involving family members
          - Contains shared logistics keywords
          - Contains home maintenance references
        """
    
    async def update_state(self, memory_id: str, new_state: MemoryState) -> bool:
        """Transition: pending → confirmed, pattern → confirmed, etc."""
```

---

## LangGraph Workflows

### Simple Command Graph (`graphs/simple.py`)

```
[Input] → [Memory Lookup] → [Execute Single Tool] → [Validate] → [Respond]
```

Used for: "turn off the lights", "what's the weather", "add milk to shopping list"

### Multi-Step Graph (`graphs/multistep.py`)

```
[Input] → [Memory Lookup] → [Plan Steps] → [Execute Step N] → [Validate]
                                  ▲              │
                                  │              ▼
                                  ├──── [More Steps?] ──Yes──┘
                                  │         │
                                  │        No
                                  │         ▼
                            [Replan?] ← [Synthesize Response]
```

Used for: "plan dinner party", "morning briefing", multi-device automations.
Key feature: parallel fan-out via `Send` API when steps are independent.

### Research Graph (`graphs/research.py`)

```
[Input] → [Memory Enrichment] → [Search Strategy] → [Parallel Search]
                                                         │
                                                    [Merge Results]
                                                         │
                                                    [Synthesize Report]
                                                         │
                                                    [Deliver via Push/WebUI]
```

Always runs async via Celery. Delivers results via mcp-notifications.

### Interactive Diagnostic Graph (`graphs/interactive.py`)

```
[Input] → [Memory + RAG Lookup] → [Generate Question] → [HITL Interrupt]
                                         ▲                      │
                                         │                      ▼
                                   [Branch Logic] ←── [User Response]
                                         │
                                        ...
                                         ▼
                                   [Final Recommendation]
                                         │
                                   [Store in Memory (pending)]
```

Used for: troubleshooting, interactive decision-making.
Each HITL interrupt checkpoints state via SQLite — can resume from any interface.

---

## Dependencies

```toml
# pyproject.toml [project.dependencies]
fastapi >= 0.115
uvicorn[standard] >= 0.30
langgraph >= 0.4
langchain-core >= 0.3
langchain-openai >= 0.3          # LiteLLM is OpenAI-compatible
celery[redis] >= 5.4
qdrant-client >= 1.12
mem0ai >= 0.1
pydantic >= 2.10
httpx >= 0.28
websockets >= 13.0
mcp >= 1.0                       # MCP Python SDK
prometheus-fastapi-instrumentator >= 7.0
structlog >= 24.0                # Structured logging
tenacity >= 9.0                  # Retry logic
```

---

## Configuration (`config.py`)

All configuration via environment variables (12-factor app):

```python
class Settings(BaseSettings):
    # Inference
    litellm_base_url: str = "http://litellm:4000/v1"
    litellm_model: str = "assistant"
    router_model: str = "assistant-fast"
    embeddings_model: str = "embeddings"
    
    # Storage
    qdrant_host: str = "qdrant"
    qdrant_port: int = 6333
    redis_url: str = "redis://gateway.home.local:6379/0"
    langgraph_db: str = "/data/langgraph/checkpoints.db"
    
    # Safety limits
    max_iterations: int = 15
    max_execution_time: int = 120        # seconds
    token_budget: int = 50_000
    session_timeout_seconds: int = 300   # voice session TTL
    max_tools_per_request: int = 7
    
    # Memory
    memory_auto_promote_family: bool = True
    memory_confirmation_required: bool = True
    
    # Monitoring
    log_level: str = "INFO"
    enable_metrics: bool = True
```

---

## Testing Strategy

| Layer | Tool | Coverage Target |
|-------|------|----------------|
| Unit tests | pytest + pytest-asyncio | Core logic, scoping, safety |
| Integration tests | pytest + testcontainers | Qdrant, Redis, LangGraph persistence |
| LLM behavior tests | pytest + VCR cassettes | Router decisions, tool call formatting |
| End-to-end tests | pytest + httpx | Full API flow with mock LLM |
| Load tests | locust | Concurrent voice + webui requests |
