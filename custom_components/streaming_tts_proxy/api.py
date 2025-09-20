import asyncio
import logging
from dataclasses import dataclass
from typing import List

from homeassistant.exceptions import HomeAssistantError

from wyoming.client import AsyncTcpClient
from wyoming.info import Describe, Info, TtsVoice

_LOGGER = logging.getLogger(__name__)

class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""

class NoVoicesFound(HomeAssistantError):
    """Error to indicate that no voices were found on the server."""

@dataclass
class ServerInfo:
    """Holds information about the Wyoming server's capabilities."""
    voices: List[TtsVoice]
    supports_streaming: bool

class WyomingApi:
    """A simple class to manage API interactions with a Wyoming server."""

    def __init__(self, host: str, port: int):
        """Initialize the API client."""
        self.host = host
        self.port = port

    async def get_server_info(self) -> ServerInfo:
        """Fetch and return info about available TTS voices and capabilities."""
        _LOGGER.debug("Attempting to get server info from %s:%s", self.host, self.port)
        try:
            async with AsyncTcpClient(self.host, self.port) as client:
                await client.write_event(Describe().event())
                event = await asyncio.wait_for(client.read_event(), timeout=5)

                if event is None or not Info.is_type(event.type):
                    raise NoVoicesFound(f"Server {self.host}:{self.port} did not return Info")

                info = Info.from_event(event)
                
                # Check if any installed TTS service supports native streaming
                supports_streaming = any(
                    tts.supports_synthesize_streaming
                    for tts in info.tts
                    if tts.installed
                )
                
                # Collect a list of full TtsVoice objects
                voices = [
                    voice
                    for tts_program in info.tts
                    if tts_program.installed and tts_program.voices
                    for voice in tts_program.voices
                    if voice.installed
                ]

                if not voices:
                    raise NoVoicesFound(f"Server {self.host}:{self.port} returned no voices")

                _LOGGER.debug(
                    "Found %d voices. Native streaming support: %s",
                    len(voices),
                    supports_streaming,
                )
                return ServerInfo(voices=voices, supports_streaming=supports_streaming)

        except (asyncio.TimeoutError, ConnectionRefusedError, OSError) as err:
            raise CannotConnect(f"Connection failed for {self.host}:{self.port}") from err