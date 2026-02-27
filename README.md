# HA Jarvis

A Home Assistant custom conversation agent powered by [Ollama](https://ollama.com/) for fully local LLM-based voice and text interactions.

## Features

- **Intent-First Architecture**: Tries Home Assistant's built-in intent system first for fast, reliable device control ("turn on the lights"), then falls back to Ollama for everything else
- **Fully Local**: All processing stays on your network - no cloud APIs needed
- **Conversation Agent**: Integrates with Home Assistant's conversation pipeline for voice assistants
- **Model Selection**: Choose from any model available on your Ollama server
- **Conversation History**: Maintains context across multi-turn conversations
- **Configurable**: Customize system prompt, temperature, top_p, and more via the UI

## How It Works

When you say something to Jarvis, it follows this flow:

1. **Try HA first** (enabled by default): Your input is sent to Home Assistant's built-in DefaultAgent, which uses intent matching to handle device control commands like "turn on the kitchen lights", "set thermostat to 72", or "lock the front door"
2. **Fall back to Ollama**: If the DefaultAgent doesn't match an intent (e.g., "what's a good recipe for pasta?"), the input is sent to your local Ollama LLM for a conversational response

This gives you the best of both worlds: fast, reliable device control through HA's native system, plus the intelligence of a local LLM for general conversation.

## Prerequisites

- [Home Assistant](https://www.home-assistant.io/) 2024.1.0 or later
- [Ollama](https://ollama.com/) running on your network with at least one model pulled
- A machine with enough resources to run your chosen LLM (GPU recommended)

## Installation

### HACS (Recommended)

1. Add this repository as a custom repository in HACS
2. Search for "HA Jarvis" and install
3. Restart Home Assistant
4. Go to **Settings > Devices & Services > Add Integration** and search for "HA Jarvis"

### Manual

1. Copy `custom_components/ha_jarvis` to your Home Assistant `custom_components` directory
2. Restart Home Assistant
3. Go to **Settings > Devices & Services > Add Integration** and search for "HA Jarvis"

## Configuration

### Initial Setup

1. Enter your Ollama server host and port (default: `localhost:11434`)
2. Select a model from the list of available models on your Ollama server

### Options

After setup, you can configure these options:

| Option | Default | Description |
|--------|---------|-------------|
| Try HA First | Yes | Try Home Assistant's built-in intent matching before Ollama. Handles device control commands natively. |
| System Prompt | JARVIS personality | The system prompt that defines the assistant's personality |
| Max History | 10 | Number of conversation turns to keep in context |
| Temperature | 0.7 | Controls randomness (0.0 = deterministic, 2.0 = very random) |
| Top P | 0.9 | Nucleus sampling parameter |
| Keep Alive | 5m | How long to keep the model loaded in memory |

### Using as a Voice Assistant

1. Go to **Settings > Voice Assistants**
2. Create a new assistant or edit an existing one
3. Set the **Conversation Agent** to "Jarvis"
4. Optionally configure STT (Speech-to-Text) and TTS (Text-to-Speech) engines

## Recommended Models

| Model | Size | Best For |
|-------|------|----------|
| `llama3.1` | 8B | General purpose, good balance of speed and quality |
| `mistral` | 7B | Fast responses, good for quick interactions |
| `llama3.1:70b` | 70B | Highest quality, requires significant GPU memory |
| `phi3` | 3.8B | Lightweight, fastest responses |

## Troubleshooting

- **Cannot connect**: Ensure Ollama is running (`ollama serve`) and accessible from your HA instance
- **No models found**: Pull a model first: `ollama pull llama3.1`
- **Slow responses**: Consider using a smaller model or adding GPU acceleration
- **Timeout errors**: Increase the timeout or use a faster model

## License

MIT
