import asyncio
import logging
from collections import defaultdict
from typing import Tuple

from homeassistant.components.tts import (
    TextToSpeechEntity,
    TTSAudioRequest,
    TTSAudioResponse,
    Voice,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    CONF_VOICE,
    ATTR_VOICE,
    ATTR_SPEAKER,
    DEFAULT_LANGUAGE,
    DEFAULT_VOICE,
)
from .stream_processor import StreamProcessor
from .api import WyomingApi, CannotConnect, NoVoicesFound, ServerInfo

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the TTS entity from a config entry. This setup must be fast."""
    entry_data = hass.data[DOMAIN][config_entry.entry_id]
    processor = entry_data["processor"]
    api_client = entry_data["api"]

    # We no longer check API here to avoid blocking startup.
    # The check is moved to a background task in the entity itself.
    entity = StreamingTtsProxyEntity(config_entry, processor, api_client)
    async_add_entities([entity])


class StreamingTtsProxyEntity(TextToSpeechEntity):
    def __init__(
        self,
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
        self._attr_device_info = {
            "identifiers": {(DOMAIN, config_entry.entry_id)},
            "name": self.name,
        }
        self._voices_loaded = False

    async def async_added_to_hass(self) -> None:
        """Handle entity which will be added to HA. Start background tasks here."""
        await super().async_added_to_hass()
        self._processor._on_primary_connect_callback = self.trigger_voice_reload
        _LOGGER.info("Scheduling initial load of voices and capabilities...")
        self.hass.async_create_task(self.async_load_voices())

    async def trigger_voice_reload(self) -> None:
        """A callback triggered on successful primary connection."""
        if not self._voices_loaded:
            _LOGGER.info("Primary TTS is back online, attempting to load voices and capabilities.")
            await self.async_load_voices()

    async def async_load_voices(self) -> None:
        """
        Load voices and capabilities from the Wyoming server in the background.
        This method sets the operational mode of the StreamProcessor.
        """
        _LOGGER.debug("Attempting to load voices and capabilities for %s", self.name)
        try:
            server_info: ServerInfo = await self._api_client.get_server_info()
            
            # --- ГЛАВНОЕ ИЗМЕНЕНИЕ ---
            # Устанавливаем режим работы процессора здесь, в фоновой задаче
            self._processor.use_native_streaming = server_info.supports_streaming
            
            voice_languages: set[str] = set()
            new_voices_map: dict[str, list[Voice]] = defaultdict(list)

            for voice_info in server_info.voices:
                if voice_info.languages:
                    voice_languages.update(voice_info.languages)
                for lang in voice_info.languages or [self.default_language]:
                    new_voices_map[lang].append(
                        Voice(voice_id=voice_info.name, name=voice_info.description or voice_info.name)
                    )

            for lang in new_voices_map:
                new_voices_map[lang] = sorted(new_voices_map[lang], key=lambda v: v.name)

            self._voices = new_voices_map
            self._attr_supported_languages = sorted(list(voice_languages))
            self._voices_loaded = True
            
            _LOGGER.info(
                "Successfully loaded voices. Effective mode set to: %s",
                "Native Streaming" if self._processor.use_native_streaming else "Sentence-Based",
            )
            self.async_write_ha_state()

        except (CannotConnect, NoVoicesFound) as e:
            _LOGGER.warning(
                "Could not load voices from primary TTS server. Will operate in safe (sentence-based) mode. Error: %s", e
            )
            self._voices_loaded = False


    # ... все остальные свойства и методы (@property, async_get_tts_audio, async_stream_tts_audio) остаются без изменений ...
    # Они уже вызывают универсальный self._processor.async_process_stream, так что их трогать не нужно.
    @property
    def _config(self) -> dict:
        return {**self._config_entry.data, **self._config_entry.options}

    @property
    def name(self) -> str:
        return f"Streaming TTS Proxy ({self._config_entry.data.get('tts_host')})"

    @property
    def supported_languages(self) -> list[str]:
        langs = set(self._voices.keys())
        if self.default_language:
            langs.add(self.default_language)
        return sorted(list(langs))

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
        """Legacy TTS method for non-streaming playback."""
        voice_name = options.get(ATTR_VOICE, self.default_voice)
        
        async def single_message_stream():
            yield message

        audio_generator = self._processor.async_process_stream(single_message_stream(), voice_name)
        
        all_chunks = [chunk async for chunk in audio_generator]
        return "wav", b"".join(all_chunks)

    async def async_stream_tts_audio(self, request: TTSAudioRequest) -> TTSAudioResponse:
        """Main streaming TTS method, now fully trusting the processor."""
        voice_name = request.options.get(ATTR_VOICE, self.default_voice)
        
        return TTSAudioResponse(
            extension="wav",
            data_gen=self._processor.async_process_stream(request.message_gen, voice_name)
        )