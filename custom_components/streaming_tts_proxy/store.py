"""Storage for audiobook progress."""
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN

STORAGE_VERSION = 1

STORAGE_KEY = f"{DOMAIN}.progress"

class AudiobookStore:
    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._data: dict[str, int] = {}

    async def async_load(self) -> None:
        data = await self._store.async_load()
        if data is not None:
            self._data = data

    def get_progress(self, file_path: str) -> int:
        return self._data.get(file_path, 0)

    def save_progress(self, file_path: str, index: int) -> None:
        self._data[file_path] = index
        self._store.async_delay_save(self._data_to_save, 5.0)

    def delete_progress(self, file_path: str) -> None:
        if file_path in self._data:
            del self._data[file_path]
            self._store.async_delay_save(self._data_to_save, 5.0)

    def _data_to_save(self) -> dict[str, int]:
        return self._data