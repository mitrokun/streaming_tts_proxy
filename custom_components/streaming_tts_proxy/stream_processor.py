import asyncio
import logging
import re
import struct
from typing import AsyncIterable, Optional, Callable, Awaitable

from wyoming.event import async_read_event, async_write_event
from wyoming.tts import (
    Synthesize,
    SynthesizeVoice,
    SynthesizeStart,
    SynthesizeChunk,
    SynthesizeStop,
    SynthesizeStopped,
)
from wyoming.audio import AudioChunk, AudioStart, AudioStop

from .const import TIMEOUT_SECONDS, DEFAULT_FALLBACK_SAMPLE_RATE

_LOGGER = logging.getLogger(__name__)

CONNECTION_TIMEOUT = 0.064


def create_wav_header(sample_rate: int, bits_per_sample: int, channels: int, data_size: int = 0) -> bytes:
    """Creates a WAV header for streaming."""
    # For streaming, we use 0xFFFFFFFF for chunk sizes
    chunk_size = 36 + data_size if data_size > 0 else 0xFFFFFFFF
    final_data_size = data_size if data_size > 0 else 0xFFFFFFFF
    
    byte_rate = sample_rate * channels * bits_per_sample // 8
    block_align = channels * bits_per_sample // 8
    
    return struct.pack(
        "<4sL4s4sLHHLLHH4sL",
        b"RIFF",
        chunk_size,
        b"WAVE",
        b"fmt ",
        16,          # Sub-chunk 1 size (16 for PCM)
        1,           # Audio format (1 for PCM)
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bits_per_sample,
        b"data",
        final_data_size,
    )


class StreamProcessor:
    def __init__(
        self,
        primary_supports_streaming: bool,
        fallback_supports_streaming: bool,
        tts_host: str,
        tts_port: int,
        sample_rate: int,
        fallback_tts_host: Optional[str] = None,
        fallback_tts_port: Optional[int] = None,
        fallback_voice: Optional[str] = None,
        fallback_sample_rate: Optional[int] = None,
        on_primary_connect_callback: Optional[Callable[[], Awaitable[None]]] = None,
    ):
        self.primary_supports_streaming = primary_supports_streaming
        self.fallback_supports_streaming = fallback_supports_streaming
        self.tts_host = tts_host
        self.tts_port = tts_port
        self.sample_rate = sample_rate
        self.fallback_tts_host = fallback_tts_host
        self.fallback_tts_port = fallback_tts_port
        self.fallback_voice = fallback_voice
        self.fallback_sample_rate = fallback_sample_rate or DEFAULT_FALLBACK_SAMPLE_RATE
        self._on_primary_connect_callback = on_primary_connect_callback


    async def async_process_stream(
        self, text_stream: AsyncIterable[str], voice_name: str
    ) -> AsyncIterable[bytes]:
        """
        Attempts to connect to the primary server quickly. If it fails,
        it immediately tries the fallback server. The processing mode (native
        or sentence-based) is chosen based on the capabilities of the
        successfully connected server.
        """
        target_server = None

        try:
            _LOGGER.debug("Quick-checking PRIMARY server %s:%s", self.tts_host, self.tts_port)
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.tts_host, self.tts_port),
                timeout=CONNECTION_TIMEOUT,
            )
            target_server = {
                "reader": reader, "writer": writer, "host": self.tts_host,
                "port": self.tts_port, "sample_rate": self.sample_rate,
                "voice": voice_name, "is_primary": True,
            }
            _LOGGER.debug("PRIMARY server is alive. Proceeding.")
        except (ConnectionRefusedError, asyncio.TimeoutError, OSError) as e:
            _LOGGER.debug("Quick-check for PRIMARY server failed: %s. Trying fallback.", e)

        if target_server is None:
            if not self.fallback_tts_host or not self.fallback_tts_port:
                _LOGGER.error("Primary server failed and no fallback is configured.")
                raise ConnectionRefusedError("Primary TTS server is unavailable and no fallback is configured.")
            try:
                _LOGGER.debug("Quick-checking FALLBACK server %s:%s", self.fallback_tts_host, self.fallback_tts_port)
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(self.fallback_tts_host, self.fallback_tts_port),
                    timeout=CONNECTION_TIMEOUT,
                )
                target_server = {
                    "reader": reader, "writer": writer, "host": self.fallback_tts_host,
                    "port": self.fallback_tts_port, "sample_rate": self.fallback_sample_rate,
                    "voice": self.fallback_voice, "is_primary": False,
                }
                _LOGGER.debug("FALLBACK server is alive. Proceeding.")
            except (ConnectionRefusedError, asyncio.TimeoutError, OSError) as e:
                _LOGGER.error("Fallback server also failed to connect: %s", e)
                raise ConnectionRefusedError("Both primary and fallback TTS servers are unavailable.")

        try:
            should_use_native_stream = (
                target_server["is_primary"] and self.primary_supports_streaming
            ) or (
                not target_server["is_primary"] and self.fallback_supports_streaming
            )

            if should_use_native_stream:
                _LOGGER.debug(
                    "Dispatching to NATIVE stream for %s server.",
                    "primary" if target_server["is_primary"] else "fallback"
                )
                async for chunk in self._stream_native_to_target(text_stream, target_server):
                    yield chunk
            else:
                _LOGGER.debug(
                    "Dispatching to SENTENCE-BASED stream for %s server.",
                    "primary" if target_server["is_primary"] else "fallback"
                )
                async for chunk in self._stream_by_sentence_to_target(text_stream, target_server):
                    yield chunk
        finally:
            if target_server and target_server["writer"]:
                target_server["writer"].close()
                try:
                    await target_server["writer"].wait_closed()
                except Exception:
                    pass
            _LOGGER.debug("Stream processing finished for %s:%s.", target_server['host'], target_server['port'])

    async def _stream_native_to_target(self, text_gen: AsyncIterable[str], server_info: dict) -> AsyncIterable[bytes]:
        """Core logic for native streaming to a single, already connected server."""
        reader = server_info["reader"]
        writer = server_info["writer"]
        
        yield create_wav_header(server_info["sample_rate"], 16, 1)

        if server_info["is_primary"] and self._on_primary_connect_callback:
            asyncio.create_task(self._on_primary_connect_callback())

        audio_queue = asyncio.Queue()
        writer_task = None
        reader_task = None

        try:
            async def _write_text():
                try:
                    voice = SynthesizeVoice(name=server_info["voice"]) if server_info["voice"] else None
                    await async_write_event(SynthesizeStart(voice=voice).event(), writer)
                    async for text_chunk in text_gen:
                        await async_write_event(SynthesizeChunk(text=text_chunk).event(), writer)
                    await async_write_event(SynthesizeStop().event(), writer)
                except Exception as e:
                    await audio_queue.put(e)

            async def _read_audio():
                try:
                    while True:
                        event = await async_read_event(reader)
                        if event is None or SynthesizeStopped.is_type(event.type):
                            break
                        if AudioChunk.is_type(event.type):
                            await audio_queue.put(AudioChunk.from_event(event).audio)
                except Exception as e:
                    await audio_queue.put(e)
                finally:
                    await audio_queue.put(None)

            writer_task = asyncio.create_task(_write_text())
            reader_task = asyncio.create_task(_read_audio())

            while True:
                item = await audio_queue.get()
                if item is None:
                    break
                if isinstance(item, Exception):
                    raise item
                yield item
        finally:
            if writer_task: writer_task.cancel()
            if reader_task: reader_task.cancel()


    async def _stream_by_sentence_to_target(self, text_stream: AsyncIterable[str], server_info: dict) -> AsyncIterable[bytes]:
        """Core logic for sentence-based streaming to a single, already connected server."""
        reader = server_info["reader"]
        writer = server_info["writer"]

        yield create_wav_header(server_info["sample_rate"], 16, 1)

        if server_info["is_primary"] and self._on_primary_connect_callback:
            asyncio.create_task(self._on_primary_connect_callback())
        
        text_buffer = []
        async for text_chunk in text_stream:
            text_buffer.append(text_chunk)
            current_text = "".join(text_buffer)
            
            while current_text:
                sentence, rest = self._form_sentence(current_text)
                if sentence:
                    async for audio_chunk in self._synthesize_sentence(reader, writer, sentence, server_info["voice"]):
                        yield audio_chunk
                    text_buffer = [rest]
                    current_text = rest
                else:
                    break
        
        final_text = "".join(text_buffer).strip()
        if final_text:
            async for audio_chunk in self._synthesize_sentence(reader, writer, final_text, server_info["voice"]):
                yield audio_chunk

    
    def _form_sentence(self, buffer_text: str) -> tuple[str, str]:
        """Splits text into a sentence and the remainder."""
        if not buffer_text:
            return "", ""

        # Use a placeholder for decimals to avoid splitting on them
        DECIMAL_PLACEHOLDER = "##DEC##"
        safe_text = re.sub(r'(\d)\.(\d)', fr'\1{DECIMAL_PLACEHOLDER}\2', buffer_text)

        # Split by common sentence terminators
        match = re.search(r"[.!?।。]", safe_text)
        if match:
            end_index = match.start() + 1
            sentence_part = safe_text[:end_index].replace(DECIMAL_PLACEHOLDER, '.')
            rest_part = safe_text[end_index:].replace(DECIMAL_PLACEHOLDER, '.')
            return sentence_part.strip(), rest_part.strip()

        # Fallback for long text without terminators: split by last space before a limit
        max_chars = 250
        if len(safe_text) > max_chars:
            search_area = safe_text[:max_chars + 20]
            last_space_index = search_area.rfind(" ")
            if last_space_index > 0:
                sentence_part = safe_text[:last_space_index].replace(DECIMAL_PLACEHOLDER, '.')
                rest_part = safe_text[last_space_index:].replace(DECIMAL_PLACEHOLDER, '.')
                return sentence_part.strip(), rest_part.strip()
            
            # If no space is found, just cut at the max length
            sentence_part = safe_text[:max_chars].replace(DECIMAL_PLACEHOLDER, '.')
            rest_part = safe_text[max_chars:].replace(DECIMAL_PLACEHOLDER, '.')
            return sentence_part, rest_part

        # If no sentence can be formed yet, return the buffer as is
        return "", buffer_text
    
    async def _synthesize_sentence(self, reader, writer, text, voice_name) -> AsyncIterable[bytes]:
        """Synthesizes a single sentence using the legacy Synthesize event."""
        clean_text = text.strip()
        if not clean_text or not re.search(r'\w', clean_text): # Ignore empty/whitespace-only
            return

        synthesize_event = Synthesize(
            text=clean_text,
            voice=SynthesizeVoice(name=voice_name) if voice_name else None
        ).event()

        await async_write_event(synthesize_event, writer)
        
        while True:
            try:
                event = await asyncio.wait_for(async_read_event(reader), timeout=TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                _LOGGER.warning(f"[SENTENCE-SINGLE] Timeout waiting for audio for text: '{text[:50]}...'")
                break
            
            if event is None or AudioStop.is_type(event.type):
                break
            if AudioChunk.is_type(event.type):
                yield AudioChunk.from_event(event).audio
