# --- START OF FILE tts.py ---

import logging
import struct
from typing import AsyncGenerator, Tuple

from homeassistant.components.tts import (
    TextToSpeechEntity,
    TTSAudioRequest,
    TTSAudioResponse,
    Voice,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
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
    # ... (код этой функции не меняется)
    header = bytearray()
    chunk_size = 36 + (data_size if data_size > 0 else 0)
    header.extend(b"RIFF")
    header.extend(struct.pack("<L", chunk_size))
    header.extend(b"WAVE")
    header.extend(b"fmt ")
    header.extend(struct.pack("<L", 16))
    header.extend(struct.pack("<H", 1))
    header.extend(struct.pack("<H", channels))
    header.extend(struct.pack("<L", sample_rate))
    header.extend(struct.pack("<L", sample_rate * channels * bits_per_sample // 8))
    header.extend(struct.pack("<H", channels * bits_per_sample // 8))
    header.extend(struct.pack("<H", bits_per_sample))
    header.extend(b"data")
    header.extend(struct.pack("<L", data_size if data_size > 0 else 0))
    return bytes(header)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the TTS entity from a config entry."""
    entry_data = hass.data[DOMAIN][config_entry.entry_id]
    processor = entry_data["processor"]
    api_client = entry_data["api"] # Получаем наш API-клиент

    async_add_entities([StreamingTtsProxyEntity(config_entry, processor, api_client)])


class StreamingTtsProxyEntity(TextToSpeechEntity):
    def __init__(
        self,
        config_entry: ConfigEntry,
        processor: StreamProcessor,
        api_client: WyomingApi, # Добавляем его в конструктор
    ):
        """Initialize the TTS entity."""
        self._config_entry = config_entry
        self._processor = processor
        self._api_client = api_client # Сохраняем его
        self._attr_unique_id = config_entry.entry_id
        self._attr_device_info = {
            "identifiers": {(DOMAIN, config_entry.entry_id)},
            "name": self.name,
            "manufacturer": "Custom Integration",
        }

    @property
    def _config(self) -> dict:
        return {**self._config_entry.data, **self._config_entry.options}

    @property
    def name(self) -> str:
        return f"Streaming TTS Proxy ({self._config.get('tts_host')})"

    @property
    def supported_languages(self) -> list[str]:
        return [self._config.get("language")]

    @property
    def default_language(self) -> str:
        return self._config.get("language")

    @property
    def default_voice(self) -> str:
        return self._config.get(CONF_VOICE)

    @property
    def supported_options(self) -> list[str]:
        return [ATTR_VOICE, ATTR_SPEAKER]

    # --- ГЛАВНОЕ НОВОВВЕДЕНИЕ ---
    async def async_get_voices(self, language: str) -> list[Voice] | None:
        """Return a list of supported voices for a language."""
        _LOGGER.debug("Getting available voices for language: %s", language)
        try:
            voice_names = await self._api_client.get_voices()
            return [Voice(voice_id=name, name=name) for name in voice_names]
        except (CannotConnect, Exception) as e:
            _LOGGER.error("Could not get voices from TTS server: %s", e)
            return None

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