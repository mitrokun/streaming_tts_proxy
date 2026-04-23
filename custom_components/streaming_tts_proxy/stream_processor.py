import asyncio
import logging
import re
import struct
from typing import AsyncIterable, Optional, Callable, Awaitable

from wyoming.event import async_read_event, async_write_event, Event
from wyoming.tts import (
    Synthesize,
    SynthesizeVoice,
    SynthesizeStart,
    SynthesizeChunk,
    SynthesizeStop,
    SynthesizeStopped,
)
from wyoming.audio import AudioChunk, AudioStart, AudioStop

from .const import TIMEOUT_SECONDS

_LOGGER = logging.getLogger(__name__)

CONNECTION_TIMEOUT = 0.064


def create_wav_header(sample_rate: int, bits_per_sample: int, channels: int, data_size: int = 0) -> bytes:
    """Creates a WAV header for streaming."""
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
    def __init__(
        self,
        servers: list[dict],
        on_primary_connect_callback: Optional[Callable[[], Awaitable[None]]] = None,
    ):
        self.servers = servers
        self._on_primary_connect_callback = on_primary_connect_callback

    async def async_process_stream(
        self, text_stream: AsyncIterable[str], voice_name: str, language: str
    ) -> AsyncIterable[bytes]:
        target_server_conn = None
        last_error = None

        for server_config in self.servers:
            try:
                _LOGGER.debug("Checking %s server %s:%s", 
                              server_config.get("name", "Unknown"), 
                              server_config["host"], 
                              server_config["port"])
                
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(server_config["host"], server_config["port"]),
                    timeout=CONNECTION_TIMEOUT,
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
                
                _LOGGER.debug("%s server %s:%s is alive. Proceeding.",
                              server_config.get("name", "Unknown"), 
                              server_config["host"],
                              server_config["port"])
                break
            
            except (ConnectionRefusedError, asyncio.TimeoutError, OSError) as e:
                _LOGGER.debug("Check for %s server %s:%s failed: %s. Trying next.",
                              server_config.get("name", "Unknown"),
                              server_config["host"],
                              server_config["port"], e)
                last_error = e

        if target_server_conn is None:
            _LOGGER.error("All configured TTS servers failed to connect. Last error: %s", last_error)
            raise ConnectionRefusedError("All configured TTS servers are unavailable.") from last_error

        try:
            should_use_native_stream = target_server_conn["supports_streaming"]

            if should_use_native_stream:
                _LOGGER.debug("Dispatching to NATIVE stream for primary server.")
                async for chunk in self._stream_native_to_target(text_stream, target_server_conn, language):
                    yield chunk
            else:
                _LOGGER.debug("Dispatching to SENTENCE-BASED stream for primary server.")
                async for chunk in self._stream_by_sentence_to_target(text_stream, target_server_conn, language):
                    yield chunk
        finally:
            if target_server_conn and target_server_conn["writer"]:
                target_server_conn["writer"].close()
                try:
                    await target_server_conn["writer"].wait_closed()
                except Exception:
                    pass
            _LOGGER.debug("Stream processing finished for %s:%s.", target_server_conn['host'], target_server_conn['port'])

    async def _stream_native_to_target(self, text_gen: AsyncIterable[str], server_info: dict, language: str) -> AsyncIterable[bytes]:
            reader = server_info["reader"]
            writer = server_info["writer"]
            
            if server_info["is_primary"] and self._on_primary_connect_callback:
                asyncio.create_task(self._on_primary_connect_callback())

            writer_task = None
            try:
                async def _write_text_stream():
                    """Writes text chunks to the server in a fire-and-forget background task."""
                    try:
                        # --- ИСПРАВЛЕНО: Собираем словарь вручную, чтобы обойти баг Wyoming ---
                        voice_data = {}
                        if server_info.get("voice"):
                            voice_data["name"] = server_info["voice"]
                        if language:
                            voice_data["language"] = language
                        
                        # Создаем базовое событие Event с уже готовым словарем
                        start_event = Event(type="synthesize-start", data={"voice": voice_data})
                        # --- КОНЕЦ ИСПРАВЛЕНИЯ ---

                        await async_write_event(start_event, writer)

                        async for text_chunk in text_gen:
                            await async_write_event(SynthesizeChunk(text=text_chunk).event(), writer)
                            await asyncio.sleep(0)
                        await async_write_event(SynthesizeStop().event(), writer)
                    except (ConnectionError, OSError) as e:
                        _LOGGER.warning("TTS client disconnected while writing: %s", e)
                    except Exception:
                        _LOGGER.exception("Unexpected error while writing to TTS client")

                writer_task = asyncio.create_task(_write_text_stream())
                header_sent = False
                
                while event := await async_read_event(reader):
                    if AudioStart.is_type(event.type):
                        if not header_sent:
                            yield create_wav_header(server_info["sample_rate"], 16, 1)
                            header_sent = True
                    elif AudioChunk.is_type(event.type):
                        yield AudioChunk.from_event(event).audio
                    elif AudioStop.is_type(event.type):
                        _LOGGER.debug("Received intermediate AudioStop, continuing stream.")
                        continue
                    elif SynthesizeStopped.is_type(event.type):
                        _LOGGER.debug("Received final SynthesizeStopped, ending stream.")
                        break
            
            except (ConnectionError, OSError) as e:
                _LOGGER.warning("TTS client disconnected while reading: %s", e)
            except Exception:
                _LOGGER.exception("Unexpected error while reading from TTS client")
            finally:
                if writer_task and not writer_task.done():
                    writer_task.cancel()
                    await asyncio.sleep(0)

    async def _stream_by_sentence_to_target(self, text_stream: AsyncIterable[str], server_info: dict, language: str) -> AsyncIterable[bytes]:
        reader = server_info["reader"]
        writer = server_info["writer"]
        yield create_wav_header(server_info["sample_rate"], 16, 1)
        if server_info["is_primary"] and self._on_primary_connect_callback:
            asyncio.create_task(self._on_primary_connect_callback())
        
        text_buffer = ""
        async for text_chunk in text_stream:
            text_buffer += text_chunk
            while True:
                sentence, rest = self._form_sentence(text_buffer)
                if sentence:
                    async for audio_chunk in self._synthesize_sentence(reader, writer, sentence, server_info["voice"], language):
                        yield audio_chunk
                    text_buffer = rest
                else:
                    break
        final_text = text_buffer.strip()
        if final_text:
            async for audio_chunk in self._synthesize_sentence(reader, writer, final_text, server_info["voice"], language):
                yield audio_chunk
    
    def _form_sentence(self, buffer_text: str) -> tuple[str, str]:
        if not buffer_text: return "", ""
        DECIMAL_PLACEHOLDER = "##DEC##"
        safe_text = re.sub(r'(\d)\.(\d)', fr'\1{DECIMAL_PLACEHOLDER}\2', buffer_text)
        match = re.search(r"[.!?।。]", safe_text)
        if match:
            end_index = match.start() + 1
            sentence_part = safe_text[:end_index].replace(DECIMAL_PLACEHOLDER, '.')
            rest_part = safe_text[end_index:].replace(DECIMAL_PLACEHOLDER, '.')
            return sentence_part.strip(), rest_part.strip()
        max_chars = 250
        if len(safe_text) > max_chars:
            search_area = safe_text[:max_chars + 20]
            last_space_index = search_area.rfind(" ")
            if last_space_index > 0:
                sentence_part = safe_text[:last_space_index].replace(DECIMAL_PLACEHOLDER, '.')
                rest_part = safe_text[last_space_index:].replace(DECIMAL_PLACEHOLDER, '.')
                return sentence_part.strip(), rest_part.strip()
            sentence_part = safe_text[:max_chars].replace(DECIMAL_PLACEHOLDER, '.')
            rest_part = safe_text[max_chars:].replace(DECIMAL_PLACEHOLDER, '.')
            return sentence_part, rest_part
        return "", buffer_text
    
    async def _synthesize_sentence(self, reader, writer, text, voice_name, language: str) -> AsyncIterable[bytes]:
        clean_text = text.strip()
        if not clean_text or not re.search(r'\w', clean_text): return

        # --- ИСПРАВЛЕНО: Собираем словарь вручную, чтобы обойти баг Wyoming ---
        voice_data = {}
        if voice_name:
            voice_data["name"] = voice_name
        if language:
            voice_data["language"] = language

        # Создаем базовое событие Event с уже готовым словарем
        synthesize_event = Event(type="synthesize", data={"text": clean_text, "voice": voice_data})
        # --- КОНЕЦ ИСПРАВЛЕНИЯ ---

        await async_write_event(synthesize_event, writer)
        
        while True:
            try:
                event = await asyncio.wait_for(async_read_event(reader), timeout=TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                _LOGGER.warning(f"[SENTENCE-SINGLE] Timeout waiting for audio for text: '{text[:50]}...'")
                break
            if event is None or AudioStop.is_type(event.type): break
            if AudioChunk.is_type(event.type):
                yield AudioChunk.from_event(event).audio