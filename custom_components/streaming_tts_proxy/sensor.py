"""Sensor platform for TXT Reader TTS Progress."""
import os
import logging
from datetime import timedelta

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(minutes=1)

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the TXT Reader progress sensor."""
    
    if hass.data[DOMAIN].get("progress_sensor_created"):
        _LOGGER.debug("Global progress sensor already created, skipping for %s", entry.title)
        return

    store = hass.data[DOMAIN].get("store")
    if not store:
        _LOGGER.error("AudiobookStore not initialized")
        return

    hass.data[DOMAIN]["progress_sensor_created"] = True
    async_add_entities([TxtReaderProgressSensor(hass, store)], True)


class TxtReaderProgressSensor(SensorEntity):
    """Global sensor to expose audiobook progress."""

    _attr_has_entity_name = False
    _attr_icon = "mdi:book-open-page-variant"
    _attr_name = "Reader Progress"
    _attr_unique_id = f"{DOMAIN}_global_progress_sensor"

    def __init__(self, hass: HomeAssistant, store) -> None:
        """Initialize the sensor."""
        self.hass = hass
        self._store = store
        self._attr_native_value = "Idle"
        self._attr_extra_state_attributes = {"books": {}}

    async def async_update(self) -> None:
        """Fetch new state data for the sensor."""
        data = self._store._data
        books_attr = {}
        
        for path, block in data.items():
            name = os.path.basename(path).replace(".txt", "").capitalize()
            books_attr[name] = block

        self._attr_extra_state_attributes = {"books": books_attr}

        sessions = self.hass.data[DOMAIN].get("sessions", {})
        active_sessions =[s for s in sessions.values() if not s.get("expired")]
        
        if active_sessions:
            latest = max(active_sessions, key=lambda s: s.get("last_accessed", 0))
            self._attr_native_value = os.path.basename(latest["file_path"]).replace(".txt", "")
        else:
            if books_attr:
                self._attr_native_value = f"Saved: {len(books_attr)}"
            else:
                self._attr_native_value = "Idle"