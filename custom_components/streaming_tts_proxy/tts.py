import asyncio
import logging
from collections import defaultdict
from typing import Tuple, TypedDict

from homeassistant.components.tts import (
    TextToSpeechEntity,
    TTSAudioRequest,
    TTSAudioResponse,
    Voice,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.storage import Store

from .const import (
    DOMAIN,
    CONF_VOICE,
    CONF_FALLBACK_VOICE,
    ATTR_VOICE,
    ATTR_SPEAKER,
    DEFAULT_LANGUAGE,
    DEFAULT_VOICE,
)
from .stream_processor import StreamProcessor
from .api import WyomingApi, CannotConnect, NoVoicesFound, ServerInfo

_LOGGER = logging.getLogger(__name__)

class VoiceCache(TypedDict):
    voices: dict[str, list[dict]]
    languages: list[str]

CACHE_VERSION = 1


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the TTS entity from a config entry. This setup must be fast."""
    entry_data = hass.data[DOMAIN][config_entry.entry_id]
    processor = entry_data["processor"]
    api_client = entry_data["api"]

    entity = StreamingTtsProxyEntity(hass, config_entry, processor, api_client)
    async_add_entities([entity])


class StreamingTtsProxyEntity(TextToSpeechEntity):
    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        processor: StreamProcessor,
        api_client: WyomingApi,
    ):
        """Initialize the TTS entity."""
        self._config_entry = config_entry
        self._processor = processor
        self._api_client = api_client
        self._attr_unique_id = config_entry.entry_id
        self._voices: dict[str, list[Voice]] = defaultdict(list)
        self._attr_name = config_entry.title
        self._attr_device_info = {
            "identifiers": {(DOMAIN, config_entry.entry_id)},
            "name": config_entry.title,
        }
        self._voices_loaded = False
        self._attr_supported_languages: list[str] = []
        self._store: Store[VoiceCache] = Store(hass, CACHE_VERSION, f"{DOMAIN}_voices_{config_entry.entry_id}")


    async def async_added_to_hass(self) -> None:
        """Handle entity which will be added to HA. Start background tasks here."""
        await super().async_added_to_hass()
        self._processor._on_primary_connect_callback = self.trigger_voice_reload
        _LOGGER.info("Scheduling initial load of voices and capabilities for %s...", self.name)
        self.hass.async_create_task(self.async_load_voices())

    async def trigger_voice_reload(self) -> None:
        """A callback triggered on successful primary connection."""
        _LOGGER.info("Primary TTS is back online for %s, refreshing voices and cache.", self.name)
        await self.async_load_voices()

    async def async_load_voices(self) -> None:
        """
        Load voices from the server, save to cache. On failure, load from cache.
        """
        _LOGGER.debug("Attempting to load voices and capabilities for %s", self.name)
        try:
            server_info: ServerInfo = await self._api_client.get_server_info()

            self._processor.primary_supports_streaming = server_info.supports_streaming

            voice_languages: set[str] = set()
            new_voices_map: dict[str, list[Voice]] = defaultdict(list)

            for voice_info in server_info.voices:
                if voice_info.languages:
                    voice_languages.update(voice_info.languages)
                for lang in voice_info.languages or [self.default_language]:
                    new_voices_map[lang].append(
                        Voice(voice_id=voice_info.name, name=voice_info.description or voice_info.name)
                    )
            
            self._voices = {lang: sorted(v_list, key=lambda v: v.name) for lang, v_list in new_voices_map.items()}
            self._attr_supported_languages = sorted(list(voice_languages))
            self._voices_loaded = True

            _LOGGER.info("Successfully loaded %d voices for %s from server.", len(server_info.voices), self.name)

            cache_data: VoiceCache = {
                "voices": {lang: [{'voice_id': v.voice_id, 'name': v.name} for v in v_list] for lang, v_list in self._voices.items()},
                "languages": self._attr_supported_languages,
            }
            await self._store.async_save(cache_data)
            _LOGGER.debug("Voice cache saved for %s", self.name)

        except (CannotConnect, NoVoicesFound) as e:
            _LOGGER.warning("Could not load voices from primary server for %s: %s. Attempting to load from cache.", self.name, e)
            
            if (cached_data := await self._store.async_load()):
                self._voices = {
                    lang: [Voice(v["voice_id"], v["name"]) for v in v_list]
                    for lang, v_list in cached_data["voices"].items()
                }
                self._attr_supported_languages = cached_data["languages"]
                self._voices_loaded = True
                _LOGGER.info("Successfully loaded %d voices for %s from cache.", sum(len(v) for v in self._voices.values()), self.name)
            else:
                _LOGGER.warning("Voice cache not found. TTS for %s will be unavailable until the primary server is connected.", self.name)
                self._voices.clear()
                self._attr_supported_languages = []
                self._voices_loaded = False
        
        self.async_write_ha_state()

    @property
    def _config(self) -> dict:
        return {**self._config_entry.data, **self._config_entry.options}

    @property
    def default_language(self) -> str:
        return self._config.get("language", DEFAULT_LANGUAGE)

    @property
    def default_voice(self) -> str:
        return self._config.get(CONF_VOICE, DEFAULT_VOICE)

    @property
    def supported_options(self) -> list[str]:
        return [ATTR_VOICE, ATTR_SPEAKER]

    @callback
    def async_get_supported_voices(self, language: str) -> list[Voice] | None:
        return self._voices.get(language)
    
    async def async_get_tts_audio(self, message: str, language: str, options: dict) -> Tuple[str, bytes]:
        voice_name = options.get(ATTR_VOICE, self.default_voice)
        
        async def single_message_stream():
            yield message

        audio_generator = self._processor.async_process_stream(single_message_stream(), voice_name)
        
        all_chunks = [chunk async for chunk in audio_generator]
        return "wav", b"".join(all_chunks)

    async def async_stream_tts_audio(self, request: TTSAudioRequest) -> TTSAudioResponse:
        voice_name = request.options.get(ATTR_VOICE, self.default_voice)
        
        return TTSAudioResponse(
            extension="wav",
            data_gen=self._processor.async_process_stream(request.message_gen, voice_name)
        )