"""Constants for the HA Jarvis integration."""

DOMAIN = "ha_jarvis"

CONF_MODEL = "model"
CONF_HOST = "host"
CONF_PORT = "port"
CONF_PROMPT = "prompt"
CONF_MAX_HISTORY = "max_history"
CONF_KEEP_ALIVE = "keep_alive"
CONF_TEMPERATURE = "temperature"
CONF_TOP_P = "top_p"
CONF_TRY_HA_FIRST = "try_ha_first"

DEFAULT_HOST = "localhost"
DEFAULT_PORT = 11434
DEFAULT_MODEL = "llama3.1"
DEFAULT_PROMPT = (
    "You are JARVIS, a helpful and witty AI home assistant running on Home Assistant. "
    "You help the user control their smart home and answer questions. "
    "Be concise but friendly in your responses. "
    "When the user asks to control devices, explain what you would do. "
    "Keep responses brief and suitable for voice output."
)
DEFAULT_MAX_HISTORY = 10
DEFAULT_KEEP_ALIVE = "5m"
DEFAULT_TEMPERATURE = 0.7
DEFAULT_TOP_P = 0.9
DEFAULT_TRY_HA_FIRST = True

OLLAMA_CHAT_ENDPOINT = "/api/chat"
OLLAMA_TAGS_ENDPOINT = "/api/tags"
