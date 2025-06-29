# --- START OF FILE api.py ---

import asyncio
import logging
from typing import List

from homeassistant.exceptions import HomeAssistantError

from wyoming.client import AsyncTcpClient
from wyoming.info import Describe, Info, TtsVoice

_LOGGER = logging.getLogger(__name__)

class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""

class NoVoicesFound(HomeAssistantError):
    """Error to indicate that no voices were found on the server."""

class WyomingApi:
    """A simple class to manage API interactions with a Wyoming server."""

    def __init__(self, host: str, port: int):
        """Initialize the API client."""
        self.host = host
        self.port = port

    async def get_voices_info(self) -> List[TtsVoice]:
        """Fetch and return the list of available TTS voices with their full info."""
        _LOGGER.debug("Attempting to get voices info from %s:%s", self.host, self.port)
        try:
            async with AsyncTcpClient(self.host, self.port) as client:
                await client.write_event(Describe().event())
                event = await asyncio.wait_for(client.read_event(), timeout=5)

                if event is None or not Info.is_type(event.type):
                    raise NoVoicesFound(f"Server {self.host}:{self.port} did not return Info")

                info = Info.from_event(event)
                
                # Collect a list of full TtsVoice objects
                voices = [
                    voice
                    for tts_program in info.tts
                    if tts_program.voices
                    for voice in tts_program.voices
                    if voice.installed
                ]

                if not voices:
                    raise NoVoicesFound(f"Server {self.host}:{self.port} returned no voices")

                _LOGGER.debug("Found %d voices", len(voices))
                return voices

        except (asyncio.TimeoutError, ConnectionRefusedError, OSError) as err:
            raise CannotConnect(f"Connection failed for {self.host}:{self.port}") from err

# --- END OF FILE api.py ---