# --- START OF FILE stream_processor.py ---

import asyncio
import logging
import re
from wyoming.client import AsyncTcpClient
from wyoming.tts import Synthesize, SynthesizeVoice
from wyoming.audio import AudioChunk, AudioStart, AudioStop
from typing import AsyncIterable

from .const import TIMEOUT_SECONDS

_LOGGER = logging.getLogger(__name__)

class StreamProcessor:
    def __init__(self, tts_host: str, tts_port: int):
        self.tts_host = tts_host
        self.tts_port = tts_port

    async def async_process_stream(
        self, text_stream: AsyncIterable[str], voice_name: str
    ) -> AsyncIterable[bytes]:
        """
        Processes the incoming text stream, forms sentences, and asynchronously returns audio.
        Splits the text only at '.', '!', '?' or when 200 characters are accumulated.
        """
        text_buffer = []
        last_chunk_time = None
        text_timeout = 2.0
        try:
            async for text_chunk in text_stream:
                _LOGGER.debug(f"Received text chunk: {text_chunk} characters: {text_chunk[:50]}...")
                last_chunk_time = asyncio.get_event_loop().time()
                text_buffer.append(text_chunk)
                current_text = "".join(text_buffer)

                while current_text:
                    sentence, rest = self._form_sentence(current_text)
                    if sentence:
                        _LOGGER.debug(f"Synthesizing sentence: {sentence[:50]}...")
                        async for audio_chunk in self._synthesize_and_stream(sentence, voice_name):
                            yield audio_chunk
                        text_buffer = [rest]
                        current_text = rest
                    else:
                        break

                if last_chunk_time and (asyncio.get_event_loop().time() - last_chunk_time > text_timeout):
                    _LOGGER.debug("Text stream timeout, synthesizing remaining text")
                    final_text = "".join(text_buffer).strip()
                    if final_text:
                        _LOGGER.debug(f"Synthesizing final text: {final_text[:50]}...")
                        async for audio_chunk in self._synthesize_and_stream(final_text, voice_name):
                            yield audio_chunk
                    text_buffer = []

            final_text = "".join(text_buffer).strip()
            if final_text:
                _LOGGER.debug(f"Synthesizing final text: {final_text[:50]}...")
                async for audio_chunk in self._synthesize_and_stream(final_text, voice_name):
                    yield audio_chunk

        except Exception as e:
            _LOGGER.error(f"Error processing TTS stream: {e}", exc_info=True)
            raise

    def _form_sentence(self, buffer_text: str) -> tuple[str, str]:
        """
        Extracts the first complete sentence from the text, using only '.', '!', '?'.
        Sends the text for synthesis if ≥200 characters without punctuation.
        Returns a tuple (sentence, remaining_text).
        """
        if not buffer_text:
            _LOGGER.debug("Empty buffer text")
            return "", ""

        min_length = 10
        max_chars = 200

        for punct in ".!?":
            if punct in buffer_text:
                sentence, rest = buffer_text.split(punct, 1)
                sentence = sentence + punct
                _LOGGER.debug(f"Found sentence: {sentence[:50]}..., rest: {rest[:50]}...")
                return sentence, rest

        if len(buffer_text) >= max_chars:
            if len(buffer_text) >= min_length:
                _LOGGER.debug(f"No punctuation, sending {len(buffer_text)} chars: {buffer_text[:50]}...")
                return buffer_text, ""
            else:
                _LOGGER.debug(f"Text too short ({len(buffer_text)} chars), waiting for more")
                return "", buffer_text

        _LOGGER.debug(f"Waiting for more text: {buffer_text[:30]}...")
        return "", buffer_text

    async def _synthesize_and_stream(self, text: str, voice_name: str) -> AsyncIterable[bytes]:
        """
        Connects to TTS, synthesizes text, and streams audio in chunks.
        """

        clean_text = text.strip()
        # re.search(r'\w', ...) find any alphanumeric symbol.
        # If it's not there, there's nothing to synthesize.
        if not clean_text or not re.search(r'\w', clean_text):
            _LOGGER.debug(f"Skipping synthesis for non-speakable text: '{text}'")
            return

        _LOGGER.debug(f"Starting synthesis for text: {text[:50]}...")
        try:
            async with AsyncTcpClient(self.tts_host, self.tts_port) as tts_client:
                synthesize_event = Synthesize(
                    text=text,
                    voice=SynthesizeVoice(name=voice_name) if voice_name else None
                )
                await tts_client.write_event(synthesize_event.event())
                
                chunk_count = 0
                while True:
                    event = await asyncio.wait_for(tts_client.read_event(), timeout=TIMEOUT_SECONDS)
                    if event is None or AudioStop.is_type(event.type):
                        _LOGGER.debug(f"Synthesis completed for text: {text[:50]}...")
                        break
                    if AudioChunk.is_type(event.type):
                        audio = AudioChunk.from_event(event).audio
                        chunk_count += 1
                        if chunk_count == 1 or chunk_count % 100 == 0:
                            _LOGGER.debug(f"Yielding chunk #{chunk_count} of size: {len(audio)} bytes")
                        yield audio

        except asyncio.TimeoutError as e:
            _LOGGER.error(f"Timeout waiting for TTS server response for text '{text[:50]}...': {e}", exc_info=True)
            raise
        except Exception as e:
            _LOGGER.error(f"Failed to synthesize text '{text[:50]}...': {e}", exc_info=True)
            raise
