"""
Stream Processor for TTS servers.
Handles failover, streaming text generation, and Wyoming protocol events.
"""

import asyncio
import logging
import re
import struct
from typing import AsyncIterable, Optional, Callable, Awaitable, Tuple

from wyoming.event import async_read_event, async_write_event, Event
from wyoming.tts import (
    SynthesizeChunk,
    SynthesizeStop,
    SynthesizeStopped,
)
from wyoming.audio import AudioChunk, AudioStart, AudioStop

from .const import TIMEOUT_SECONDS

_LOGGER = logging.getLogger(__name__)

CONNECTION_TIMEOUT = 0.064


def create_wav_header(
    sample_rate: int, bits_per_sample: int, channels: int, data_size: int = 0
) -> bytes:
    """
    Creates a standard WAV header for streaming audio.

    Args:
        sample_rate: The sample rate of the audio (e.g., 22050).
        bits_per_sample: Audio bit depth (e.g., 16).
        channels: Number of audio channels (1 for mono, 2 for stereo).
        data_size: Size of the audio data. 0 defaults to 0xFFFFFFFF for streams.

    Returns:
        Bytes representing the generated WAV header.
    """
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
        16,
        1,
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bits_per_sample,
        b"data",
        final_data_size,
    )


class StreamProcessor:
    """
    Manages connections to Wyoming TTS servers and streams text to them,
    handling failovers smoothly.
    """

    def __init__(
        self,
        servers: list[dict],
        on_primary_connect_callback: Optional[Callable[[], Awaitable[None]]] = None,
    ):
        """
        Initializes the StreamProcessor.

        Args:
            servers: List of dictionaries containing TTS server configurations.
            on_primary_connect_callback: Async callback fired when the primary server connects.
        """
        self.servers = servers
        self._on_primary_connect_callback = on_primary_connect_callback

    async def async_process_stream(
        self, text_stream: AsyncIterable[str], voice_name: str, language: str
    ) -> AsyncIterable[bytes]:
        """
        Processes a stream of text by sending it to a TTS server and yielding audio chunks.
        Includes a failover mechanism if a server fails during connection or mid-stream.

        Args:
            text_stream: Async generator yielding chunks of text to synthesize.
            voice_name: The name of the voice to use.
            language: The language code (e.g., 'ru', 'en').

        Yields:
            Bytes of synthesized audio.
        """
        last_error = None
        total_servers = len(self.servers)
        header_sent = False  # Prevents sending multiple WAV headers during failover

        for i, server_config in enumerate(self.servers):
            # Give the last (or only) server more time to respond since there's no fallback left.
            current_timeout = 3.0 if i == total_servers - 1 else CONNECTION_TIMEOUT
            target_server_conn = None

            try:
                _LOGGER.debug(
                    "Checking %s server %s:%s (timeout: %ss)",
                    server_config.get("name", "Unknown"),
                    server_config["host"],
                    server_config["port"],
                    current_timeout,
                )

                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(server_config["host"], server_config["port"]),
                    timeout=current_timeout,
                )

                target_server_conn = {
                    "reader": reader,
                    "writer": writer,
                    "host": server_config["host"],
                    "port": server_config["port"],
                    "sample_rate": server_config["sample_rate"],
                    "voice": voice_name if server_config.get("is_primary") else server_config.get("voice"),
                    "is_primary": server_config.get("is_primary", False),
                    "supports_streaming": server_config["supports_streaming"],
                }

                _LOGGER.debug(
                    "%s server %s:%s is alive. Proceeding.",
                    server_config.get("name", "Unknown"),
                    server_config["host"],
                    server_config["port"],
                )

                # Initialize the appropriate stream generator
                if target_server_conn["supports_streaming"]:
                    _LOGGER.debug("Dispatching to NATIVE stream.")
                    stream_generator = self._stream_native_to_target(
                        text_stream, target_server_conn, language, skip_header=header_sent
                    )
                else:
                    _LOGGER.debug("Dispatching to SENTENCE-BASED stream.")
                    stream_generator = self._stream_by_sentence_to_target(
                        text_stream, target_server_conn, language, skip_header=header_sent
                    )

                # Consume the generator and yield audio chunks to the caller
                async for chunk in stream_generator:
                    yield chunk
                    header_sent = True

                # If the loop finishes normally, streaming is complete.
                return

            except (ConnectionError, OSError, ConnectionResetError) as e:
                # Triggers if the server drops the connection while synthesizing
                _LOGGER.warning(
                    "Connection lost with server %s:%s mid-stream: %s. "
                    "Skipping lost chunk and trying next server.",
                    server_config["host"],
                    server_config["port"],
                    e,
                )
                last_error = e
                continue  # Move to the next server and pass the remaining text_stream to it

            except asyncio.TimeoutError as e:
                # Triggers if initial connection or chunk reading times out
                _LOGGER.debug(
                    "Check for %s server %s:%s failed after %ss: %s. Trying next.",
                    server_config.get("name", "Unknown"),
                    server_config["host"],
                    server_config["port"],
                    current_timeout,
                    e,
                )
                last_error = e

            finally:
                # Ensure connection is closed properly before failover
                if target_server_conn and target_server_conn["writer"]:
                    target_server_conn["writer"].close()
                    try:
                        await target_server_conn["writer"].wait_closed()
                    except Exception:
                        pass
                
                if target_server_conn:
                    _LOGGER.debug(
                        "Stream processing finished for %s:%s.",
                        target_server_conn["host"],
                        target_server_conn["port"],
                    )

        # If we exhausted all servers
        _LOGGER.error("All configured TTS servers failed. Last error: %s", last_error)
        raise ConnectionRefusedError(
            "All configured TTS servers are unavailable or died mid-stream."
        ) from last_error

    async def _stream_native_to_target(
        self, text_gen: AsyncIterable[str], server_info: dict, language: str, skip_header: bool = False
    ) -> AsyncIterable[bytes]:
        """
        Streams text natively to a Wyoming server that supports full-duplex generation.
        """
        reader = server_info["reader"]
        writer = server_info["writer"]

        if server_info["is_primary"] and self._on_primary_connect_callback:
            asyncio.create_task(self._on_primary_connect_callback())

        writer_task = None
        try:
            async def _write_text_stream():
                """Writes text chunks to the server in a background task."""
                try:
                    voice_data = {}
                    if server_info.get("voice"):
                        voice_data["name"] = server_info["voice"]
                    if language:
                        voice_data["language"] = language

                    start_event = Event(type="synthesize-start", data={"voice": voice_data})
                    await async_write_event(start_event, writer)

                    async for text_chunk in text_gen:
                        await async_write_event(SynthesizeChunk(text=text_chunk).event(), writer)
                        await asyncio.sleep(0)
                        
                    await async_write_event(SynthesizeStop().event(), writer)
                except Exception as e:
                    _LOGGER.warning("TTS client disconnected while writing: %s", e)

            writer_task = asyncio.create_task(_write_text_stream())
            header_sent_internal = False

            while True:
                event = await async_read_event(reader)

                # Catch unexpected drops and raise them to trigger failover in the main loop
                if event is None:
                    raise ConnectionError("Connection closed unexpectedly by Wyoming server")

                if AudioStart.is_type(event.type):
                    if not skip_header and not header_sent_internal:
                        yield create_wav_header(server_info["sample_rate"], 16, 1)
                        header_sent_internal = True
                elif AudioChunk.is_type(event.type):
                    yield AudioChunk.from_event(event).audio
                elif AudioStop.is_type(event.type):
                    continue
                elif SynthesizeStopped.is_type(event.type):
                    break

        except (ConnectionError, OSError) as e:
            # Re-raise to ensure main loop handles failover
            raise e
        finally:
            if writer_task and not writer_task.done():
                writer_task.cancel()

    async def _stream_by_sentence_to_target(
        self, text_stream: AsyncIterable[str], server_info: dict, language: str, skip_header: bool = False
    ) -> AsyncIterable[bytes]:
        """
        Streams text to a Wyoming server by buffering and sending full sentences.
        """
        reader = server_info["reader"]
        writer = server_info["writer"]

        if not skip_header:
            yield create_wav_header(server_info["sample_rate"], 16, 1)

        if server_info["is_primary"] and self._on_primary_connect_callback:
            asyncio.create_task(self._on_primary_connect_callback())

        text_buffer = ""
        async for text_chunk in text_stream:
            text_buffer += text_chunk
            while True:
                sentence, rest = self._form_sentence(text_buffer)
                if sentence:
                    async for audio_chunk in self._synthesize_sentence(
                        reader, writer, sentence, server_info["voice"], language
                    ):
                        yield audio_chunk
                    text_buffer = rest
                else:
                    break
                    
        final_text = text_buffer.strip()
        if final_text:
            async for audio_chunk in self._synthesize_sentence(
                reader, writer, final_text, server_info["voice"], language
            ):
                yield audio_chunk

    def _form_sentence(self, buffer_text: str) -> Tuple[str, str]:
        """
        Extracts the first complete sentence from the text buffer.

        Returns:
            A tuple of (sentence, remaining_text).
        """
        if not buffer_text:
            return "", ""
            
        DECIMAL_PLACEHOLDER = "##DEC##"
        safe_text = re.sub(r"(\d)\.(\d)", rf"\1{DECIMAL_PLACEHOLDER}\2", buffer_text)
        
        match = re.search(r"[.!?।。]", safe_text)
        if match:
            end_index = match.start() + 1
            sentence_part = safe_text[:end_index].replace(DECIMAL_PLACEHOLDER, ".")
            rest_part = safe_text[end_index:].replace(DECIMAL_PLACEHOLDER, ".")
            return sentence_part.strip(), rest_part.strip()
            
        max_chars = 250
        if len(safe_text) > max_chars:
            search_area = safe_text[: max_chars + 20]
            last_space_index = search_area.rfind(" ")
            if last_space_index > 0:
                sentence_part = safe_text[:last_space_index].replace(DECIMAL_PLACEHOLDER, ".")
                rest_part = safe_text[last_space_index:].replace(DECIMAL_PLACEHOLDER, ".")
                return sentence_part.strip(), rest_part.strip()
                
            sentence_part = safe_text[:max_chars].replace(DECIMAL_PLACEHOLDER, ".")
            rest_part = safe_text[max_chars:].replace(DECIMAL_PLACEHOLDER, ".")
            return sentence_part, rest_part
            
        return "", buffer_text

    async def _synthesize_sentence(
        self, reader, writer, text: str, voice_name: str, language: str
    ) -> AsyncIterable[bytes]:
        """
        Sends a single sentence to the server and yields the resulting audio chunks.
        """
        clean_text = text.strip()
        if not clean_text or not re.search(r"\w", clean_text):
            return

        voice_data = {}
        if voice_name:
            voice_data["name"] = voice_name
        if language:
            voice_data["language"] = language

        synthesize_event = Event(
            type="synthesize", data={"text": clean_text, "voice": voice_data}
        )
        await async_write_event(synthesize_event, writer)

        while True:
            try:
                event = await asyncio.wait_for(
                    async_read_event(reader), timeout=TIMEOUT_SECONDS
                )
            except asyncio.TimeoutError:
                _LOGGER.warning("[SENTENCE-SINGLE] Timeout waiting for audio for text: '%s...'", text[:50])
                raise ConnectionError("Timeout waiting for audio chunk")

            if event is None:
                raise ConnectionError("Connection closed unexpectedly mid-sentence")

            if AudioStop.is_type(event.type):
                break

            if AudioChunk.is_type(event.type):
                yield AudioChunk.from_event(event).audio
