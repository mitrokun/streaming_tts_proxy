# --- START OF FILE stream_processor.py ---

import asyncio
import logging
import re
from typing import AsyncIterable, Optional, Callable, Awaitable

from wyoming.event import async_read_event, async_write_event
from wyoming.tts import Synthesize, SynthesizeVoice
from wyoming.audio import AudioChunk, AudioStop

from .const import TIMEOUT_SECONDS

_LOGGER = logging.getLogger(__name__)

CONNECTION_TIMEOUT = 0.5

class StreamProcessor:
    def __init__(
        self, 
        tts_host: str, 
        tts_port: int,
        fallback_tts_host: Optional[str] = None,
        fallback_tts_port: Optional[int] = None,
        fallback_voice: Optional[str] = None,
        on_primary_connect_callback: Optional[Callable[[], Awaitable[None]]] = None
    ):
        """Initialize the stream processor."""
        self.tts_host = tts_host
        self.tts_port = tts_port
        self.fallback_tts_host = fallback_tts_host
        self.fallback_tts_port = fallback_tts_port
        self.fallback_voice = fallback_voice
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
                    is_primary=False
                ):
                    yield audio_chunk
            except Exception as fallback_e:
                _LOGGER.error(f"Fallback TTS server also failed: {fallback_e}", exc_info=True)
                raise fallback_e from e

    async def _stream_from_server(
        self, host: str, port: int, text_stream: AsyncIterable[str], voice_name: str, is_primary: bool = False
    ) -> AsyncIterable[bytes]:
        """Helper method to stream from a single server."""
        reader = writer = None
        try:
            _LOGGER.debug(f"Opening connection to {host}:{port} with {CONNECTION_TIMEOUT}s timeout...")
            open_coro = asyncio.open_connection(host, port)
            reader, writer = await asyncio.wait_for(open_coro, timeout=CONNECTION_TIMEOUT)
            _LOGGER.debug(f"Connection to {host}:{port} established.")

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
        """Extracts the first complete sentence from the buffer."""
        if not buffer_text:
            return "", ""

        match = re.search(r"[.!?]", buffer_text)
        if match:
            end_index = match.start() + 1
            return buffer_text[:end_index].strip(), buffer_text[end_index:].strip()

        max_chars = 200
        if len(buffer_text) > max_chars:
            search_area = buffer_text[:max_chars + 20]
            last_space_index = search_area.rfind(" ")
            if last_space_index > 0:
                return buffer_text[:last_space_index].strip(), buffer_text[last_space_index:].strip()
            else:
                return buffer_text[:max_chars], buffer_text[max_chars:]

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
            event = await asyncio.wait_for(async_read_event(reader), timeout=TIMEOUT_SECONDS)
            
            if event is None or AudioStop.is_type(event.type):
                break
            if AudioChunk.is_type(event.type):
                yield AudioChunk.from_event(event).audio

# --- END OF FILE stream_processor.py ---