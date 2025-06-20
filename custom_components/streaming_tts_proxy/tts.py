import logging
import struct
from collections import defaultdict
from typing import AsyncGenerator, Tuple

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
)
from .stream_processor import StreamProcessor
from .api import WyomingApi, CannotConnect

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
    await entity.async_load_voices()

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

    async def async_load_voices(self) -> None:
        """Load voices from the Wyoming server and cache them."""
        _LOGGER.debug("Loading voices for %s", self.name)
        try:
            voice_names = await self._api_client.get_voices()
            language = self.default_language
            
            if language:
                voice_list = [Voice(voice_id=name, name=name) for name in voice_names]
                self._voices[language] = sorted(voice_list, key=lambda v: v.name)
                _LOGGER.debug("Loaded %d voices for language '%s'", len(voice_list), language)

        except (CannotConnect, Exception) as e:
            _LOGGER.error("Could not load voices from TTS server: %s", e)

    @property
    def _config(self) -> dict:
        return {**self._config_entry.data, **self._config_entry.options}

    @property
    def name(self) -> str:
        return f"Streaming TTS Proxy ({self._config.get('tts_host')})"

    @property
    def supported_languages(self) -> list[str]:
        return list(self._voices.keys())

    @property
    def default_language(self) -> str:
        return self._config.get("language")

    @property
    def default_voice(self) -> str:
        return self._config.get(CONF_VOICE)

    @property
    def supported_options(self) -> list[str]:
        return [ATTR_VOICE, ATTR_SPEAKER]

    @callback
    def async_get_supported_voices(self, language: str) -> list[Voice] | None:
        """Return a list of supported voices for a language."""
        return self._voices.get(language)
    
    async def async_get_tts_audio(self, message: str, language: str, options: dict) -> Tuple[str, bytes]:
        voice_name = options.get(ATTR_VOICE, self.default_voice)
        async def single_message_stream():
            yield message
        audio_generator = self._processor.async_process_stream(single_message_stream(), voice_name)
        pcm_chunks = [chunk async for chunk in audio_generator]
        wav_data = create_wav_header(22050, 16, 1, sum(len(c) for c in pcm_chunks)) + b''.join(pcm_chunks)
        return "wav", wav_data

    async def async_stream_tts_audio(self, request: TTSAudioRequest) -> TTSAudioResponse:
        voice_name = request.options.get(ATTR_VOICE, self.default_voice)
        audio_generator = self._processor.async_process_stream(request.message_gen, voice_name)
        wav_header = create_wav_header(22050, 16, 1, 0)
        async def wav_generator() -> AsyncGenerator[bytes, None]:
            yield wav_header
            async for chunk in audio_generator:
                yield chunk
        return TTSAudioResponse(extension="wav", data_gen=wav_generator())
