# --- START OF FILE tts.py ---

import logging
from collections import defaultdict
import struct
from typing import AsyncGenerator, Tuple, Callable, Awaitable

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
from .api import WyomingApi, CannotConnect, NoVoicesFound

_LOGGER = logging.getLogger(__name__)


def create_wav_header(sample_rate: int, bits_per_sample: int, channels: int, data_size: int) -> bytes:
    """Creates a WAV header. data_size can be 0 for streaming."""
    is_streaming = data_size == 0
    if is_streaming:
        chunk_size = 0xFFFFFFFF
        data_size = 0xFFFFFFFF
    else:
        chunk_size = 36 + data_size

    byte_rate = sample_rate * channels * bits_per_sample // 8
    block_align = channels * bits_per_sample // 8
    
    header = struct.pack('<4sL4s4sLHHLLHH4sL',
                         b'RIFF', chunk_size, b'WAVE', b'fmt ',
                         16, 1, channels, sample_rate,
                         byte_rate, block_align, bits_per_sample,
                         b'data', data_size)
    return header

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the TTS entity from a config entry."""
    entry_data = hass.data[DOMAIN][config_entry.entry_id]
    processor = entry_data["processor"]
    api_client = entry_data["api"]

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
            "manufacturer": "Custom Integration",
        }
        self._voices_loaded = False

    async def async_added_to_hass(self) -> None:
        """Handle entity which will be added to HA, ensuring non-blocking startup."""
        await super().async_added_to_hass()
        
        # Устанавливаем callback для будущих успешных соединений
        self._processor._on_primary_connect_callback = self.trigger_voice_reload
        
        _LOGGER.info("Scheduling initial voice list load...")
        self.hass.async_create_task(self.async_load_voices())

    async def trigger_voice_reload(self) -> None:
        """A callback triggered by StreamProcessor on successful primary connection."""
        if not self._voices_loaded:
            _LOGGER.info("Primary TTS is back online, attempting to load voice list.")
            # Здесь await уместен, так как это уже фоновая задача
            await self.async_load_voices()

    async def async_load_voices(self) -> None:
        """Load voices from the Wyoming server."""
        if self._voices_loaded:
            _LOGGER.debug("Voice list already loaded, skipping.")
            return

        _LOGGER.debug("Attempting to load voices for %s", self.name)
        try:
            voice_names = await self._api_client.get_voices()
            language = self.default_language
            
            self._voices.clear()

            if language and voice_names:
                voice_list = [Voice(voice_id=name, name=name) for name in voice_names]
                self._voices[language] = sorted(voice_list, key=lambda v: v.name)
                _LOGGER.info("Successfully loaded %d voices for language '%s'.", len(voice_list), language)
                self._voices_loaded = True
            else:
                _LOGGER.debug("No voices found or language not configured for voice list.")
                self._voices_loaded = True

        except (CannotConnect, NoVoicesFound) as e:
            _LOGGER.warning(
                "Could not load voices from primary TTS server. Will try again later. Error: %s", e
            )
            self._voices.clear()
            self._voices_loaded = False


    @property
    def _config(self) -> dict:
        """Combine base data and options."""
        return {**self._config_entry.data, **self._config_entry.options}

    @property
    def name(self) -> str:
        """Return the name of the TTS entity."""
        return f"Streaming TTS Proxy ({self._config_entry.data.get('tts_host')})"

    @property
    def supported_languages(self) -> list[str]:
        """Return a list of supported languages."""
        langs = set(self._voices.keys())
        if self.default_language:
            langs.add(self.default_language)
        return sorted(list(langs))

    @property
    def default_language(self) -> str:
        """Return the default language."""
        return self._config.get("language", DEFAULT_LANGUAGE)

    @property
    def default_voice(self) -> str:
        """Return the default voice."""
        return self._config.get(CONF_VOICE, DEFAULT_VOICE)

    @property
    def supported_options(self) -> list[str]:
        """Return a list of supported options."""
        return [ATTR_VOICE, ATTR_SPEAKER]

    @callback
    def async_get_supported_voices(self, language: str) -> list[Voice] | None:
        """Return a list of supported voices for a language."""
        return self._voices.get(language)
    
    async def async_get_tts_audio(self, message: str, language: str, options: dict) -> Tuple[str, bytes]:
        """Legacy TTS method for non-streaming playback."""
        voice_name = options.get(ATTR_VOICE, self.default_voice)
        
        async def single_message_stream():
            yield message

        audio_generator = self._processor.async_process_stream(single_message_stream(), voice_name)
        
        pcm_chunks = [chunk async for chunk in audio_generator]
        wav_data = create_wav_header(22050, 16, 1, sum(len(c) for c in pcm_chunks)) + b''.join(pcm_chunks)
        
        return "wav", wav_data

    async def async_stream_tts_audio(self, request: TTSAudioRequest) -> TTSAudioResponse:
        """Main streaming TTS method."""
        voice_name = request.options.get(ATTR_VOICE, self.default_voice)
        
        audio_generator = self._processor.async_process_stream(request.message_gen, voice_name)
        
        wav_header = create_wav_header(22050, 16, 1, 0)
        
        async def wav_generator() -> AsyncGenerator[bytes, None]:
            yield wav_header
            async for chunk in audio_generator:
                yield chunk
                
        return TTSAudioResponse(extension="wav", data_gen=wav_generator())

# --- END OF FILE tts.py ---