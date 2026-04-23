import logging
import uuid
import time
import os
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.network import get_url
from homeassistant.helpers.storage import Store
from homeassistant.helpers import config_validation as cv

from .const import (
    DOMAIN, 
    CONF_TTS_HOST, 
    CONF_TTS_PORT,
    CONF_VOICE,
    CONF_SAMPLE_RATE,
    CONF_SUPPORTS_STREAMING,
    CONF_FALLBACK_TTS_HOST,
    CONF_FALLBACK_TTS_PORT,
    CONF_FALLBACK_VOICE,
    CONF_FALLBACK_SAMPLE_RATE,
    CONF_FALLBACK_SUPPORTS_STREAMING,
    CONF_TERTIARY_TTS_HOST,
    CONF_TERTIARY_TTS_PORT,
    CONF_TERTIARY_VOICE,
    CONF_TERTIARY_SAMPLE_RATE,
    CONF_TERTIARY_SUPPORTS_STREAMING,
    DEFAULT_SAMPLE_RATE,
    DEFAULT_FALLBACK_SAMPLE_RATE,
    MAX_CHUNK_LENGTH,
)
from .stream_processor import StreamProcessor
from .api import WyomingApi
from .tts import VoiceCache
from .store import AudiobookStore
from .utils import get_book_chunks
from .view import TxtReaderStreamView

_LOGGER = logging.getLogger(__name__)
PLATFORMS: list[str] = ["tts", "sensor"]

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Streaming TTS Proxy from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    
    if "store" not in hass.data[DOMAIN]:
        store = AudiobookStore(hass)
        await store.async_load()
        hass.data[DOMAIN]["store"] = store
        hass.data[DOMAIN]["sessions"] = {}
        hass.http.register_view(TxtReaderStreamView(hass))

    config = {**entry.data, **entry.options}
    api_client = WyomingApi(host=config[CONF_TTS_HOST], port=config[CONF_TTS_PORT])
    
    servers =[]
    servers.append({
        "name": "Primary", "host": config[CONF_TTS_HOST], "port": config[CONF_TTS_PORT],
        "voice": config.get(CONF_VOICE), "sample_rate": config.get(CONF_SAMPLE_RATE, DEFAULT_SAMPLE_RATE),
        "supports_streaming": config.get(CONF_SUPPORTS_STREAMING, False), "is_primary": True,
    })
    
    if config.get(CONF_FALLBACK_TTS_HOST) and config.get(CONF_FALLBACK_TTS_PORT):
        servers.append({
            "name": "Fallback", "host": config[CONF_FALLBACK_TTS_HOST], "port": config[CONF_FALLBACK_TTS_PORT],
            "voice": config.get(CONF_FALLBACK_VOICE), "sample_rate": config.get(CONF_FALLBACK_SAMPLE_RATE, DEFAULT_FALLBACK_SAMPLE_RATE),
            "supports_streaming": config.get(CONF_FALLBACK_SUPPORTS_STREAMING, False), "is_primary": False,
        })
        
    if config.get(CONF_TERTIARY_TTS_HOST) and config.get(CONF_TERTIARY_TTS_PORT):
        servers.append({
            "name": "Tertiary", "host": config[CONF_TERTIARY_TTS_HOST], "port": config[CONF_TERTIARY_TTS_PORT],
            "voice": config.get(CONF_TERTIARY_VOICE), "sample_rate": config.get(CONF_TERTIARY_SAMPLE_RATE, DEFAULT_FALLBACK_SAMPLE_RATE),
            "supports_streaming": config.get(CONF_TERTIARY_SUPPORTS_STREAMING, False), "is_primary": False,
        })
        
    processor = StreamProcessor(servers=servers)
    update_listener = entry.add_update_listener(async_reload_entry)
    
    hass.data[DOMAIN][entry.entry_id] = {
        "api": api_client,
        "processor": processor,
        "update_listener": update_listener,
        "config": config,
    }

    async def handle_play(call: ServiceCall):
        conf_entry = hass.config_entries.async_get_entry(call.data["config_entry"])
        if not conf_entry: return
        
        entry_data = hass.data[DOMAIN].get(conf_entry.entry_id)
        if not entry_data: return
        
        config = entry_data["config"].copy()
        if call.data.get("voice"): config["voice"] = call.data["voice"]
        
        player_id = call.data["entity_id"]
        file_path = call.data["file_path"]
        manual_idx = call.data.get("block_index")
        
        timer_min = call.data.get("timer", 0)
        timer_sec = timer_min * 60 if timer_min > 0 else None
        
        global_store = hass.data[DOMAIN]["store"]
        book_title = os.path.basename(file_path).replace(".txt", "").capitalize()

        chunks = await hass.async_add_executor_job(get_book_chunks, file_path, MAX_CHUNK_LENGTH)
        if not chunks: return

        if manual_idx is not None:
            start_index = manual_idx
            global_store.save_progress(file_path, start_index)
        else:
            start_index = global_store.get_progress(file_path)
            if start_index >= len(chunks):
                _LOGGER.info("Book was finished. Resetting progress to the beginning.")
                start_index = 0
                global_store.save_progress(file_path, start_index)

        now = time.time()
        sessions = hass.data[DOMAIN]["sessions"]
        
        # ОЧИСТКА И ОСТАНОВКА ХВОСТОВ
        for sid in list(sessions.keys()):
            sess = sessions[sid]
            if now - sess.get("last_accessed", now) > 43200: 
                sessions.pop(sid, None)
            elif sess.get("player_id") == player_id:
                sess["expired"] = True

        session_id = uuid.uuid4().hex
        sessions[session_id] = {
            "processor": entry_data["processor"], 
            "config": config, 
            "file_path": file_path, 
            "chunks": chunks,
            "store": global_store, 
            "start_index": start_index, 
            "last_accessed": now,
            "player_id": player_id,
            "timer_sec": timer_sec,
            "expired": False  # Флаг актуальности
        }

        await hass.services.async_call("media_player", "play_media", {
            "entity_id": player_id,
            "media_content_id": f"{get_url(hass)}/api/{DOMAIN}/stream/{session_id}",
            "media_content_type": "music",
            "extra": {
                "title": book_title,
                "artist": "TXT Reader TTS"
            }
        })

    if not hass.services.has_service(DOMAIN, "play"):
        hass.services.async_register(
            DOMAIN, 
            "play", 
            handle_play,
            schema=vol.Schema({
                vol.Required("config_entry"): cv.string,
                vol.Required("entity_id"): cv.entity_id,
                vol.Required("file_path"): cv.string,
                vol.Optional("voice"): cv.string,
                vol.Optional("block_index"): cv.positive_int,
                vol.Optional("timer"): cv.positive_int,
            })
        )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if unload_ok := await hass.config_entries.async_forward_entry_unload(entry, "tts"):
        entry_data = hass.data[DOMAIN].pop(entry.entry_id)
        entry_data["update_listener"]()
        
    if not hass.config_entries.async_entries(DOMAIN):
        hass.services.async_remove(DOMAIN, "play")
        hass.data.pop(DOMAIN, None)
    return unload_ok

async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)

async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    store: Store[VoiceCache] = Store(hass, 1, f"{DOMAIN}_voices_{entry.entry_id}")
    await store.async_remove()