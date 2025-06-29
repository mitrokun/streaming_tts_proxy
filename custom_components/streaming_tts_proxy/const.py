DOMAIN = "streaming_tts_proxy"

# --- Primary Server Config ---
CONF_TTS_HOST = "tts_host"
CONF_TTS_PORT = "tts_port"
CONF_LANGUAGE = "language"
CONF_VOICE = "voice"
CONF_SAMPLE_RATE = "sample_rate"
CONF_SUPPORTS_STREAMING = "supports_streaming"

# --- Fallback Server (Optional) Config ---
CONF_FALLBACK_TTS_HOST = "fallback_tts_host"
CONF_FALLBACK_TTS_PORT = "fallback_tts_port"
CONF_FALLBACK_VOICE = "fallback_voice"
CONF_FALLBACK_SAMPLE_RATE = "fallback_sample_rate"
CONF_FALLBACK_SUPPORTS_STREAMING = "fallback_supports_streaming"

# --- Attributes ---
ATTR_VOICE = "voice"
ATTR_SPEAKER = "speaker"

# --- Defaults ---
DEFAULT_TTS_HOST = "192.168.1.1"
DEFAULT_TTS_PORT = 10200
DEFAULT_LANGUAGE = "ru"
DEFAULT_VOICE = "male_01"
DEFAULT_SAMPLE_RATE = 22050
DEFAULT_FALLBACK_SAMPLE_RATE = 22050

# --- Other ---
TIMEOUT_SECONDS = 10