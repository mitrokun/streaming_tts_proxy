"""Sensor platform for TXT Reader TTS Progress."""
import os
import logging

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the TXT Reader progress sensor."""
    if hass.data[DOMAIN].get("progress_sensor_created"):
        return

    store = hass.data[DOMAIN].get("store")
    if not store:
        return

    hass.data[DOMAIN]["progress_sensor_created"] = True
    async_add_entities([TxtReaderProgressSensor(hass, store)], True)


class TxtReaderProgressSensor(SensorEntity):
    """Global sensor to expose audiobook progress."""

    _attr_has_entity_name = False
    _attr_icon = "mdi:book-open-page-variant"
    _attr_name = "Reader Progress"
    _attr_unique_id = f"{DOMAIN}_global_progress_sensor"
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, store) -> None:
        """Initialize the sensor."""
        self.hass = hass
        self._store = store
        self._attr_native_value = "Idle"
        self._attr_extra_state_attributes = {"books": {}}

    async def async_added_to_hass(self) -> None:
        """Register callbacks when added to Home Assistant."""
        @callback
        def update_sensor_state():
            self.async_schedule_update_ha_state(True)

        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{DOMAIN}_progress_updated",
                update_sensor_state,
            )
        )

    async def async_update(self) -> None:
        """Fetch new state data for the sensor."""
        data = self._store._data
        books_attr = {}
        
        for path, block_data in data.items():
            name = os.path.basename(path).replace(".txt", "")
            books_attr[name] = {
                "current": block_data.get("index", 0),
                "total": block_data.get("total_blocks", 0)
            }

        self._attr_extra_state_attributes = {"books": books_attr}

        sessions = self.hass.data[DOMAIN].get("sessions", {})
        active_sessions = [s for s in sessions.values() if not s.get("expired")]
        
        if active_sessions:
            latest = max(active_sessions, key=lambda s: s.get("last_accessed", 0))
            self._attr_native_value = os.path.basename(latest["file_path"]).replace(".txt", "")
        else:
            if books_attr:
                self._attr_native_value = f"Saved: {len(books_attr)}"
            else:
                self._attr_native_value = "Idle"