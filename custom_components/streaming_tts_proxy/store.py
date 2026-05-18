"""Storage for audiobook progress and player context."""
import time
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN

STORAGE_VERSION = 1
STORAGE_KEY = f"{DOMAIN}.progress"

class AudiobookStore:
    """Manages persistence for book progress and player-specific settings."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the store."""
        self.hass = hass
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._data: dict[str, dict] = {}

    async def async_load(self) -> None:
        """Load data from the filesystem."""
        data = await self._store.async_load()
        if data:
            self._data = data

    def get_progress(self, file_path: str) -> int:
        """Get the current block index for a book."""
        return self._data.get(file_path, {}).get("index", 0)

    def save_progress(self, file_path: str, index: int, total_blocks: int = 0) -> None:
        """Save block index, optionally total blocks, and update global book timestamp."""
        if file_path not in self._data:
            self._data[file_path] = {"index": 0, "total_blocks": total_blocks, "last_accessed": time.time(), "players": {}}
        
        self._data[file_path]["index"] = index
        # Сохраняем total_blocks, только если оно передано (больше 0)
        if total_blocks > 0:
            self._data[file_path]["total_blocks"] = total_blocks
            
        self._data[file_path]["last_accessed"] = time.time()
        self._store.async_delay_save(self._data_to_save, 5.0)

    def save_player_context(self, file_path: str, player_id: str, config_entry: str, voice: str | None) -> None:
        """Save settings and timestamp for a specific player and book."""
        if file_path not in self._data:
            self._data[file_path] = {"index": 0, "total_blocks": 0, "last_accessed": time.time(), "players": {}}
        
        if "players" not in self._data[file_path]:
            self._data[file_path]["players"] = {}
            
        self._data[file_path]["players"][player_id] = {
            "config_entry": config_entry,
            "voice": voice,
            "last_accessed": time.time()
        }
        self._data[file_path]["last_accessed"] = time.time()
        self._store.async_delay_save(self._data_to_save, 5.0)

    def get_player_last_book(self, player_id: str) -> str | None:
        """Find the book path that this specific player accessed most recently."""
        latest_time = 0.0
        latest_book = None
        for path, book_data in self._data.items():
            p_data = book_data.get("players", {}).get(player_id)
            if p_data and p_data.get("last_accessed", 0.0) > latest_time:
                latest_time = p_data["last_accessed"]
                latest_book = path
        return latest_book

    def get_player_context(self, file_path: str, player_id: str) -> dict | None:
        """Get saved player settings for a specific book."""
        return self._data.get(file_path, {}).get("players", {}).get(player_id)

    def delete_progress(self, file_path: str) -> None:
        """Remove a book from the store."""
        if file_path in self._data:
            del self._data[file_path]
            self._store.async_delay_save(self._data_to_save, 5.0)

    def clear_all(self) -> None:
        """Wipe all data."""
        self._data = {}
        self._store.async_delay_save(self._data_to_save, 1.0)

    def _data_to_save(self) -> dict:
        return self._data