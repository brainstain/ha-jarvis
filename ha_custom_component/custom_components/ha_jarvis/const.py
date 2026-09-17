"""Constants for the HA Jarvis integration."""

DOMAIN = "ha_jarvis"

CONF_BASE_URL = "base_url"
CONF_API_KEY = "api_key"
CONF_TRY_HA_FIRST = "try_ha_first"
CONF_TIMEOUT = "timeout"

DEFAULT_BASE_URL = "http://192.168.13.22:8100"
DEFAULT_API_KEY = ""
DEFAULT_TRY_HA_FIRST = True
DEFAULT_TIMEOUT = 30

HEALTH_ENDPOINT = "/ha/health"
CONVERSATION_ENDPOINT = "/ha/conversation/process"
