# --- START OF FILE __init__.py ---

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    DOMAIN, 
    CONF_TTS_HOST, 
    CONF_TTS_PORT,
    CONF_FALLBACK_TTS_HOST,
    CONF_FALLBACK_TTS_PORT,
    CONF_FALLBACK_VOICE
)
from .stream_processor import StreamProcessor
from .api import WyomingApi

PLATFORMS: list[str] = ["tts"]

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Streaming TTS Proxy from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    
    config = {**entry.data, **entry.options}
    
    api_client = WyomingApi(
        host=config[CONF_TTS_HOST],
        port=config[CONF_TTS_PORT]
    )
    
    processor = StreamProcessor(
        tts_host=config[CONF_TTS_HOST],
        tts_port=config[CONF_TTS_PORT],
        fallback_tts_host=config.get(CONF_FALLBACK_TTS_HOST),
        fallback_tts_port=config.get(CONF_FALLBACK_TTS_PORT),
        fallback_voice=config.get(CONF_FALLBACK_VOICE),
    )
    
    update_listener = entry.add_update_listener(async_reload_entry)
    
    hass.data[DOMAIN][entry.entry_id] = {
        "api": api_client,
        "processor": processor,
        "update_listener": update_listener,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_forward_entry_unload(entry, "tts"):
        entry_data = hass.data[DOMAIN].pop(entry.entry_id)
        entry_data["update_listener"]()
    return unload_ok

async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry when options are updated."""
    await hass.config_entries.async_reload(entry.entry_id)

# --- END OF FILE __init__.py ---