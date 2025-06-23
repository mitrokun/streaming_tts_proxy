# --- START OF FILE stream_processor.py ---

import asyncio
import logging
import re
import struct
from typing import AsyncIterable, Optional, Callable, Awaitable

from wyoming.event import async_read_event, async_write_event
from wyoming.tts import Synthesize, SynthesizeVoice
from wyoming.audio import AudioChunk, AudioStop

from .const import TIMEOUT_SECONDS, DEFAULT_FALLBACK_SAMPLE_RATE

_LOGGER = logging.getLogger(__name__)

CONNECTION_TIMEOUT = 0.1

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


class StreamProcessor:
    def __init__(
        self,
        tts_host: str,
        tts_port: int,
        sample_rate: int,
        fallback_tts_host: Optional[str] = None,
        fallback_tts_port: Optional[int] = None,
        fallback_voice: Optional[str] = None,
        fallback_sample_rate: Optional[int] = None,
        on_primary_connect_callback: Optional[Callable[[], Awaitable[None]]] = None,
    ):
        """Initialize the stream processor."""
        self.tts_host = tts_host
        self.tts_port = tts_port
        self.sample_rate = sample_rate
        self.fallback_tts_host = fallback_tts_host
        self.fallback_tts_port = fallback_tts_port
        self.fallback_voice = fallback_voice
        self.fallback_sample_rate = fallback_sample_rate
        self._on_primary_connect_callback = on_primary_connect_callback

    async def async_process_stream(
        self, text_stream: AsyncIterable[str], voice_name: str
    ) -> AsyncIterable[bytes]:
        """Processes a text stream with failover logic."""
        try:
            _LOGGER.debug(f"Attempting to stream from primary TTS: {self.tts_host}:{self.tts_port}")
            async for audio_chunk in self._stream_from_server(
                host=self.tts_host,
                port=self.tts_port,
                text_stream=text_stream,
                voice_name=voice_name,
                sample_rate=self.sample_rate,
                is_primary=True
            ):
                yield audio_chunk
            return

        except (ConnectionRefusedError, asyncio.TimeoutError, OSError) as e:
            _LOGGER.debug(f"Primary TTS server {self.tts_host}:{self.tts_port} failed: {e}")

            if not self.fallback_tts_host:
                _LOGGER.error("Primary TTS failed and no fallback is configured.")
                raise

            _LOGGER.debug(f"Switching to fallback TTS: {self.fallback_tts_host}:{self.fallback_tts_port}")
            try:
                async for audio_chunk in self._stream_from_server(
                    host=self.fallback_tts_host,
                    port=self.fallback_tts_port,
                    text_stream=text_stream,
                    voice_name=self.fallback_voice,
                    sample_rate=self.fallback_sample_rate or DEFAULT_FALLBACK_SAMPLE_RATE,
                    is_primary=False
                ):
                    yield audio_chunk
            except Exception as fallback_e:
                _LOGGER.error(f"Fallback TTS server also failed: {fallback_e}", exc_info=True)
                raise fallback_e from e

    async def _stream_from_server(
        self, host: str, port: int, text_stream: AsyncIterable[str], voice_name: str, sample_rate: int, is_primary: bool = False
    ) -> AsyncIterable[bytes]:
        """Helper method to stream from a single server. It now handles the WAV header."""
        reader = writer = None
        try:
            _LOGGER.debug(f"Opening connection to {host}:{port} with {CONNECTION_TIMEOUT}s timeout...")
            open_coro = asyncio.open_connection(host, port)
            reader, writer = await asyncio.wait_for(open_coro, timeout=CONNECTION_TIMEOUT)
            _LOGGER.debug(f"Connection to {host}:{port} established.")

            # WAV header is now generated and yielded here, only ONCE per successful connection.
            yield create_wav_header(sample_rate, 16, 1, 0)

            if is_primary and self._on_primary_connect_callback:
                _LOGGER.debug("Primary server connected, triggering voice reload callback.")
                asyncio.create_task(self._on_primary_connect_callback())

            text_buffer = []
            async for text_chunk in text_stream:
                text_buffer.append(text_chunk)
                current_text = "".join(text_buffer)

                while current_text:
                    sentence, rest = self._form_sentence(current_text)
                    if sentence:
                        async for audio_chunk in self._synthesize_sentence(reader, writer, sentence, voice_name):
                            yield audio_chunk
                        text_buffer = [rest]
                        current_text = rest
                    else:
                        break

            final_text = "".join(text_buffer).strip()
            if final_text:
                async for audio_chunk in self._synthesize_sentence(reader, writer, final_text, voice_name):
                    yield audio_chunk
        
        except asyncio.TimeoutError as e:
            _LOGGER.debug(f"Connection to {host}:{port} timed out.")
            raise e
        except (ConnectionRefusedError, OSError) as e:
            _LOGGER.debug(f"Connection failed for {host}:{port}: {e}")
            raise e
        except Exception as e:
            _LOGGER.error(f"Error processing TTS stream with {host}:{port}: {e}", exc_info=True)
            raise
        finally:
            if writer:
                writer.close()
                await writer.wait_closed()
            _LOGGER.debug(f"Resources for {host}:{port} cleaned up.")

    def _form_sentence(self, buffer_text: str) -> tuple[str, str]:
        """
        Extracts the first complete sentence from the buffer.
        This method is language-agnostic regarding decimal separators.
        """
        if not buffer_text:
            return "", ""

        # Use a unique placeholder to temporarily replace decimal points within numbers.
        DECIMAL_PLACEHOLDER = "##DEC##"
        safe_text = re.sub(r'(\d)\.(\d)', fr'\1{DECIMAL_PLACEHOLDER}\2', buffer_text)

        # Added more sentence terminators
        match = re.search(r"[.!?।。]", safe_text)
        if match:
            end_index = match.start() + 1
            
            sentence_part = safe_text[:end_index]
            rest_part = safe_text[end_index:]
            
            final_sentence = sentence_part.replace(DECIMAL_PLACEHOLDER, '.')
            final_rest = rest_part.replace(DECIMAL_PLACEHOLDER, '.')
            
            return final_sentence.strip(), final_rest.strip()

        max_chars = 200
        if len(safe_text) > max_chars:
            search_area = safe_text[:max_chars + 20]
            last_space_index = search_area.rfind(" ")
            if last_space_index > 0:
                sentence_part = safe_text[:last_space_index]
                rest_part = safe_text[last_space_index:]
                
                final_sentence = sentence_part.replace(DECIMAL_PLACEHOLDER, '.')
                final_rest = rest_part.replace(DECIMAL_PLACEHOLDER, '.')
                
                return final_sentence.strip(), final_rest.strip()
            else:
                sentence_part = safe_text[:max_chars]
                rest_part = safe_text[max_chars:]

                final_sentence = sentence_part.replace(DECIMAL_PLACEHOLDER, '.')
                final_rest = rest_part.replace(DECIMAL_PLACEHOLDER, '.')

                return final_sentence, final_rest

        return "", buffer_text
    
    async def _synthesize_sentence(
        self, 
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        text: str, 
        voice_name: str
    ) -> AsyncIterable[bytes]:
        """Synthesizes a single sentence using the provided streams."""
        clean_text = text.strip()
        if not clean_text or not re.search(r'\w', clean_text):
            return

        synthesize_event = Synthesize(
            text=text,
            voice=SynthesizeVoice(name=voice_name) if voice_name else None
        ).event()

        await async_write_event(synthesize_event, writer)
        
        while True:
            try:
                event = await asyncio.wait_for(async_read_event(reader), timeout=TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                _LOGGER.warning(f"Timeout waiting for audio from TTS server for text: '{text[:50]}...'")
                break
            
            if event is None or AudioStop.is_type(event.type):
                break
            if AudioChunk.is_type(event.type):
                yield AudioChunk.from_event(event).audio

# --- END OF FILE stream_processor.py ---
