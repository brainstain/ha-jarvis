# HA Jarvis

A Home Assistant conversation agent that bridges the Assist pipeline to a
[ha-jarvis `agent-orchestrator`](../custom-software/agent-orchestrator) — a
LangGraph-based agent with memory, MCP tool-calling (calendar, notifications,
workflow status, and more), and model routing running elsewhere on your
network.

## How It Works

1. **Try HA first** (enabled by default): Your input is sent to Home
   Assistant's built-in DefaultAgent, which uses intent matching to handle
   device control commands like "turn on the kitchen lights" without a
   network round trip.
2. **Fall back to agent-orchestrator**: If the DefaultAgent doesn't match an
   intent, the input is forwarded via `POST {base_url}/ha/conversation/process`
   to the orchestrator, which owns all further reasoning, memory lookup, and
   MCP tool execution server-side and returns a spoken response.

This component does **not** talk to Ollama or any LLM API directly, and it
does not run its own tool-calling loop — that's entirely the orchestrator's
job. This component is just the transport between HA's Assist pipeline and
the orchestrator's HTTP API.

## Prerequisites

- [Home Assistant](https://www.home-assistant.io/) 2024.1.0 or later
- A reachable `agent-orchestrator` instance (see `custom-software/agent-orchestrator`
  in this repo) with its `/ha/conversation/process` and `/ha/health` endpoints
  up

## Installation

### Manual

1. Copy `custom_components/ha_jarvis` to your Home Assistant `custom_components`
   directory
2. Restart Home Assistant
3. Go to **Settings > Devices & Services > Add Integration** and search for
   "HA Jarvis"

### HACS

1. Add this repository as a custom repository in HACS
2. Search for "HA Jarvis" and install
3. Restart Home Assistant
4. Go to **Settings > Devices & Services > Add Integration** and search for
   "HA Jarvis"

## Configuration

### Initial Setup

1. Enter the base URL of your `agent-orchestrator` instance (e.g.
   `http://192.168.13.22:8100`)
2. Optionally enter a bearer token — the orchestrator's endpoint only checks
   that an `Authorization: Bearer <token>` header is present, not its value,
   so this is mostly for defense-in-depth against stray local clients

### Options

| Option | Default | Description |
|--------|---------|-------------|
| Try HA First | Yes | Try Home Assistant's built-in intent matching before forwarding to the orchestrator. Handles device control commands natively and avoids a network round trip for simple commands. |

### Using as a Voice Assistant

1. Go to **Settings > Voice Assistants**
2. Create a new assistant or edit an existing one
3. Set the **Conversation Agent** to "Jarvis"
4. Configure STT (Speech-to-Text) and TTS (Text-to-Speech) engines to point
   at your Wyoming Whisper/Piper containers

## Troubleshooting

- **Cannot connect** (setup fails): Ensure `agent-orchestrator` is running
  and `GET {base_url}/ha/health` is reachable from the HA host.
- **Errors during conversation**: Check `docker logs agent-orchestrator` on
  the node running it — the orchestrator does all the actual reasoning and
  tool-calling, so failures there surface as a spoken error in HA.
- **Device control seems to bypass the orchestrator**: That's expected when
  "Try HA First" is on and HA's own intent system matches the command — only
  unmatched input is forwarded to the orchestrator.

## License

MIT
