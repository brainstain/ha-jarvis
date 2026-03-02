# Custom Software: MCP Servers

**Language:** Python 3.12 (FastMCP SDK)  
**Location:** `custom-software/mcp-servers/`  
**Deploys to:** Agent Node (alongside Agent Orchestrator)  
**Protocol:** MCP over stdio (spawned by orchestrator) or SSE (standalone)

---

## Overview

Each MCP server exposes tools the Agent Orchestrator can call during workflow execution. Third-party MCP servers are deployed as-is. The servers below are **custom-built** to fill gaps identified in the architecture simulation.

All custom servers share:
- FastMCP SDK (`pip install fastmcp`)
- Pydantic models for all tool arguments
- Structured error responses (not raw exceptions)
- Health check via `tools/list` ping
- JSON logging to stdout

---

## 1. mcp-memory-scoped

**Purpose:** Dual-mode memory (family/personal) over Qdrant + Mem0  
**Priority:** Phase 2 — required for personalized agent  
**Complexity:** High

### Tools

| Tool | Arguments | Returns | Description |
|------|-----------|---------|-------------|
| `memory_search` | `query: str, scope: family\|personal, user_id: str, limit: int=5` | `list[MemoryEntry]` | Semantic search over memories |
| `memory_store` | `content: str, scope: family\|personal, user_id: str, memory_type: fact\|preference\|event\|pending, tags: list[str], doc_ref: str\|None` | `MemoryEntry` | Store a new memory |
| `memory_confirm` | `memory_id: str` | `MemoryEntry` | Promote pending → confirmed |
| `memory_promote` | `memory_id: str, target_scope: family` | `MemoryEntry` | Promote personal → family |
| `memory_update` | `memory_id: str, content: str` | `MemoryEntry` | Update existing memory |
| `memory_delete` | `memory_id: str` | `bool` | Delete a memory |
| `memory_list_pending` | `user_id: str` | `list[MemoryEntry]` | List unconfirmed memories |

### Data Model

```python
class MemoryEntry(BaseModel):
    id: str                          # Qdrant point ID
    content: str                     # The memory text
    scope: Literal["family", "personal"]
    user_id: str
    memory_type: Literal["fact", "preference", "event", "pending", "pattern"]
    tags: list[str]
    doc_ref: str | None              # Paperless-NGX document ID
    created_at: datetime
    updated_at: datetime
    embedding_model: str             # "nomic-embed-text-v1.5"
```

### Implementation Notes

- Uses Mem0 SDK for memory management, with Qdrant as the vector backend
- Embedding via LiteLLM embeddings endpoint (`/embeddings`)
- `memory_search` applies scope filtering as Qdrant metadata filters:
  - Family mode: `{"scope": "family"}`
  - Personal mode: `{"$or": [{"scope": "family"}, {"$and": [{"scope": "personal"}, {"user_id": user_id}]}]}`
- `memory_store` generates embedding via nomic-embed-text, writes to Qdrant with full metadata
- Mem0 conflict resolution: if new memory contradicts existing, mark old as superseded

### Dependencies

```
fastmcp>=0.1.0
qdrant-client>=1.9.0
mem0ai>=0.1.0
httpx>=0.27.0
pydantic>=2.0
```

---

## 2. mcp-notifications

**Purpose:** Send results to users across channels (HA, Open WebUI, push)  
**Priority:** Phase 2 — required for output channel routing  
**Complexity:** Medium

### Tools

| Tool | Arguments | Returns | Description |
|------|-----------|---------|-------------|
| `notify_ha` | `message: str, title: str\|None, target: str="mobile_app"` | `bool` | Send HA notification (appears on phone) |
| `notify_push` | `message: str, title: str, topic: str, priority: low\|default\|high` | `bool` | Send via ntfy (self-hosted push) |
| `notify_webui` | `message: str, user_id: str, thread_id: str\|None` | `bool` | Post message into Open WebUI conversation |
| `notify_tts` | `message: str, satellite_id: str` | `bool` | Speak via specific voice satellite |

### Implementation Notes

- `notify_ha`: REST call to HA API (`POST /api/services/notify/{target}`)
- `notify_push`: POST to ntfy server (`POST /topic`)
- `notify_webui`: REST call to Open WebUI API or direct DB insert
- `notify_tts`: Triggers HA TTS service targeting specific media_player entity

### Configuration

```json
{
  "ha_url": "http://gateway.home.local:8123",
  "ha_token": "${HA_LONG_LIVED_TOKEN}",
  "ntfy_url": "http://gateway.home.local:8090",
  "webui_url": "http://localhost:3000"
}
```

---

## 3. mcp-calendar

**Purpose:** CalDAV calendar integration (Nextcloud, Google, or local Radicale)  
**Priority:** Phase 4  
**Complexity:** Medium

### Tools

| Tool | Arguments | Returns | Description |
|------|-----------|---------|-------------|
| `calendar_list_events` | `start: datetime, end: datetime, calendar: str\|None` | `list[CalEvent]` | Events in range |
| `calendar_create_event` | `title: str, start: datetime, end: datetime, description: str\|None, location: str\|None, calendar: str="default"` | `CalEvent` | Create event |
| `calendar_update_event` | `event_id: str, **fields` | `CalEvent` | Update event |
| `calendar_delete_event` | `event_id: str` | `bool` | Delete event |
| `calendar_find_free_time` | `start: datetime, end: datetime, duration_minutes: int` | `list[TimeSlot]` | Find open slots |

### Implementation Notes

- Uses `caldav` Python library against CalDAV server
- HA calendar entities used as secondary source for device-linked calendars
- Returns structured data, agent decides how to present

---

## 4. mcp-shopping-list

**Purpose:** Shopping list and simple task management  
**Priority:** Phase 4  
**Complexity:** Low

### Tools

| Tool | Arguments | Returns | Description |
|------|-----------|---------|-------------|
| `shopping_list_add` | `item: str, quantity: str\|None` | `ShoppingItem` | Add item |
| `shopping_list_remove` | `item_id: str` | `bool` | Remove item |
| `shopping_list_view` | `completed: bool=False` | `list[ShoppingItem]` | View current list |
| `shopping_list_complete` | `item_id: str` | `ShoppingItem` | Mark complete |
| `shopping_list_clear_completed` | — | `int` | Remove completed items |

### Implementation Notes

- Wraps HA shopping list REST API (`/api/shopping_list`)
- Can extend to Todoist API for more complex task management later

---

## 5. mcp-routines

**Purpose:** CRUD for user-defined automation routines (morning briefing, movie night, etc.)  
**Priority:** Phase 4  
**Complexity:** Medium

### Tools

| Tool | Arguments | Returns | Description |
|------|-----------|---------|-------------|
| `routine_list` | `user_id: str` | `list[Routine]` | List all routines |
| `routine_get` | `name: str` | `Routine` | Get routine by name |
| `routine_create` | `name: str, trigger: str, actions: list[Action], schedule: str\|None` | `Routine` | Create routine |
| `routine_update` | `name: str, **fields` | `Routine` | Update routine |
| `routine_delete` | `name: str` | `bool` | Delete routine |
| `routine_execute` | `name: str` | `RoutineResult` | Execute a routine |

### Data Model

```python
class Routine(BaseModel):
    name: str                    # "morning_briefing", "movie_night"
    user_id: str
    trigger: str                 # "voice_command" | "schedule" | "event"
    schedule: str | None         # Cron expression if trigger=schedule
    sources: list[str]           # For briefings: ["weather", "calendar", "tasks", "news"]
    actions: list[Action]        # For scenes: list of HA service calls
    voice_length: str            # "30s" | "45s" | "60s"
    active: bool

class Action(BaseModel):
    service: str                 # "light.turn_on", "climate.set_temperature"
    entity_id: str
    data: dict                   # Service call data (brightness, temperature, etc.)
```

### Implementation Notes

- Routines stored as Qdrant documents (memory_type="pattern") with structured JSON payload
- `routine_execute` fans out HA service calls in parallel via ha-mcp
- Briefing routines trigger parallel data gathering, synthesized by agent
- Detected repeated patterns (Gap 19) are proposed as new routines

---

## 6. mcp-workflow-status

**Purpose:** Query and manage async workflows and HITL interrupts  
**Priority:** Phase 3  
**Complexity:** Medium

### Tools

| Tool | Arguments | Returns | Description |
|------|-----------|---------|-------------|
| `workflow_list_pending` | `user_id: str` | `list[WorkflowSummary]` | Workflows awaiting input |
| `workflow_get_status` | `task_id: str` | `WorkflowStatus` | Detailed status |
| `workflow_resume` | `task_id: str, user_input: str` | `WorkflowStatus` | Provide input to paused workflow |
| `workflow_cancel` | `task_id: str` | `bool` | Cancel workflow |
| `workflow_list_recent` | `user_id: str, limit: int=10` | `list[WorkflowSummary]` | Recent completed workflows |

### Implementation Notes

- Reads from LangGraph SQLite checkpoint database
- Queries Celery result backend for async task status
- `workflow_resume` maps to `Command(resume={...})` in LangGraph

---

## Third-Party MCP Servers (No Custom Code)

These are deployed as-is from their repositories:

| Server | Repository | Transport | Purpose |
|--------|-----------|-----------|---------|
| ha-mcp | `homeassistant-ai/mcp` | SSE | 80+ HA device tools |
| mcp-filesystem | `modelcontextprotocol/servers` | stdio | File access on NAS |
| mcp-fetch | `modelcontextprotocol/servers` | stdio | Web content retrieval |
| mcp-playwright | `executeautomation/playwright-mcp-server` | stdio | Browser automation |
| mcp-memory (graph) | `modelcontextprotocol/servers` | stdio | Knowledge graph (supplements Qdrant) |

---

## MCP Server Registry Configuration

The orchestrator loads available MCP servers from a JSON config:

```json
{
  "servers": [
    {
      "name": "home_assistant",
      "type": "sse",
      "url": "http://gateway.home.local:8123/mcp",
      "category": "home_automation",
      "always_available": true
    },
    {
      "name": "memory",
      "type": "stdio",
      "command": "python",
      "args": ["-m", "mcp_memory_scoped"],
      "category": "memory",
      "always_available": true
    },
    {
      "name": "notifications",
      "type": "stdio",
      "command": "python",
      "args": ["-m", "mcp_notifications"],
      "category": "notifications",
      "always_available": true
    },
    {
      "name": "searxng",
      "type": "stdio",
      "command": "python",
      "args": ["-m", "mcp_searxng"],
      "category": "search",
      "always_available": false
    },
    {
      "name": "calendar",
      "type": "stdio",
      "command": "python",
      "args": ["-m", "mcp_calendar"],
      "category": "calendar",
      "always_available": false
    },
    {
      "name": "shopping_list",
      "type": "stdio",
      "command": "python",
      "args": ["-m", "mcp_shopping_list"],
      "category": "tasks",
      "always_available": false
    },
    {
      "name": "routines",
      "type": "stdio",
      "command": "python",
      "args": ["-m", "mcp_routines"],
      "category": "routines",
      "always_available": false
    },
    {
      "name": "filesystem",
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/mnt/nas"],
      "category": "files",
      "always_available": false
    },
    {
      "name": "fetch",
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-fetch"],
      "category": "web",
      "always_available": false
    },
    {
      "name": "playwright",
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@anthropic/playwright-mcp-server"],
      "category": "browser",
      "always_available": false
    },
    {
      "name": "workflow_status",
      "type": "stdio",
      "command": "python",
      "args": ["-m", "mcp_workflow_status"],
      "category": "system",
      "always_available": true
    }
  ],
  "tool_routing": {
    "max_tools_per_request": 7,
    "category_priority": ["home_automation", "memory", "notifications"],
    "always_include_categories": ["memory", "notifications"]
  }
}
```

The **meta-reasoning router** (Qwen3-4B) classifies the user's intent, then selects which server categories to activate for that request, keeping total exposed tools ≤ 7.
