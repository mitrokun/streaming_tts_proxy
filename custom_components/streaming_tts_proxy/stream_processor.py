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
        Processes an incoming stream of text, forms sentences, and asynchronously returns audio,
        using a single connection for the entire request.
        Splits the text only by '.', '!', '?' or when 200 characters have accumulated.
        """
        text_buffer = []
        last_chunk_time = None
        text_timeout = 2.0  # Timeout for processing slow streams

        try:
            async with AsyncTcpClient(self.tts_host, self.tts_port) as tts_client:
                _LOGGER.debug("TTS client connected for the entire stream.")
                
                async for text_chunk in text_stream:
                    _LOGGER.debug(f"Received text chunk: {text_chunk} characters: {text_chunk[:50]}...")
                    last_chunk_time = asyncio.get_event_loop().time()
                    text_buffer.append(text_chunk)
                    current_text = "".join(text_buffer)

                    # Search for completed sentences
                    while current_text:
                        sentence, rest = self._form_sentence(current_text)
                        if sentence:
                            _LOGGER.debug(f"Synthesizing sentence: {sentence[:50]}...")
                            async for audio_chunk in self._synthesize_sentence(tts_client, sentence, voice_name):
                                yield audio_chunk
                            text_buffer = [rest]
                            current_text = rest
                        else:
                            break  # Waiting for more text

                    # Check for timeout for slow streams
                    if last_chunk_time and (asyncio.get_event_loop().time() - last_chunk_time > text_timeout):
                        _LOGGER.debug("Text stream timeout, synthesizing remaining text")
                        final_text = "".join(text_buffer).strip()
                        if final_text:
                            _LOGGER.debug(f"Synthesizing final text: {final_text[:50]}...")
                            async for audio_chunk in self._synthesize_sentence(tts_client, final_text, voice_name):
                                yield audio_chunk
                        text_buffer = []

                # After the stream ends, synthesize the remainder
                final_text = "".join(text_buffer).strip()
                if final_text:
                    _LOGGER.debug(f"Synthesizing final text: {final_text[:50]}...")
                    async for audio_chunk in self._synthesize_sentence(tts_client, final_text, voice_name):
                        yield audio_chunk

        except Exception as e:
            _LOGGER.error(f"Error processing TTS stream: {e}", exc_info=True)
            raise
        finally:
            _LOGGER.debug("TTS client disconnected after stream completion.")


    def _form_sentence(self, buffer_text: str) -> tuple[str, str]:
        """
        Extracts the first complete sentence from the text, using only '.', '!', '?'.
        Sends the text for synthesis at >= 200 characters if no punctuation is found.
        Returns a tuple (sentence, remaining_text).
        """
        if not buffer_text:
            return "", ""

        min_length = 10
        max_chars = 200

        for punct in ".!?":
            if punct in buffer_text:
                parts = buffer_text.split(punct, 1)
                sentence = parts[0] + punct
                rest = parts[1]
                return sentence.strip(), rest.strip()

        if len(buffer_text) >= max_chars:
            return buffer_text, ""

        return "", buffer_text
    
    async def _synthesize_sentence(
        self, tts_client: AsyncTcpClient, text: str, voice_name: str
    ) -> AsyncIterable[bytes]:
        """
        Synthesizes a single chunk of text using an already open connection to the TTS server.
        """
        clean_text = text.strip()
        if not clean_text or not re.search(r'\w', clean_text):
            _LOGGER.debug(f"Skipping synthesis for non-speakable text: '{text}'")
            return

        _LOGGER.debug(f"Starting synthesis for text: {text[:50]}...")
        try:
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
