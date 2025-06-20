# --- START OF FILE const.py ---

DOMAIN = "streaming_tts_proxy"

# --- Primary Server Config ---
CONF_TTS_HOST = "tts_host"
CONF_TTS_PORT = "tts_port"
CONF_LANGUAGE = "language"
CONF_VOICE = "voice"

# --- Fallback Server (Optional) Config ---
CONF_FALLBACK_TTS_HOST = "fallback_tts_host"
CONF_FALLBACK_TTS_PORT = "fallback_tts_port"
CONF_FALLBACK_VOICE = "fallback_voice"

# --- Attributes ---
ATTR_VOICE = "voice"
ATTR_SPEAKER = "speaker"

# --- Defaults ---
DEFAULT_TTS_HOST = "192.168.1.1"
DEFAULT_TTS_PORT = 10205
DEFAULT_LANGUAGE = "ru"
DEFAULT_VOICE = "male_01"

# --- Other ---
TIMEOUT_SECONDS = 10

# --- END OF FILE const.py ---