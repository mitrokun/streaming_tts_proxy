# --- START OF FILE config_flow.py ---

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithConfigEntry,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import selector

from .api import WyomingApi, CannotConnect, NoVoicesFound

from .const import (
    DOMAIN,
    CONF_TTS_HOST,
    CONF_TTS_PORT,
    CONF_LANGUAGE,
    CONF_VOICE,
    CONF_FALLBACK_TTS_HOST,
    CONF_FALLBACK_TTS_PORT,
    CONF_FALLBACK_VOICE,
    DEFAULT_TTS_HOST,
    DEFAULT_TTS_PORT,
    DEFAULT_LANGUAGE,
    DEFAULT_VOICE,
)

_LOGGER = logging.getLogger(__name__)

INITIAL_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_TTS_HOST, default=DEFAULT_TTS_HOST): str,
        vol.Required(CONF_TTS_PORT, default=DEFAULT_TTS_PORT): int,
    }
)


# Класс должен наследоваться от ConfigFlow и иметь параметр domain=DOMAIN
class StreamingTtsProxyConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Streaming TTS Proxy."""
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlowWithConfigEntry:
        """Get the options flow for this handler."""
        return OptionsFlowHandler(config_entry)

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(f"{user_input[CONF_TTS_HOST]}:{user_input[CONF_TTS_PORT]}")
            self._abort_if_unique_id_configured()

            try:
                api = WyomingApi(user_input[CONF_TTS_HOST], user_input[CONF_TTS_PORT])
                await api.get_voices()
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except NoVoicesFound:
                errors["base"] = "no_voices_found"
            except Exception as e:
                _LOGGER.error("Unknown error during setup: %s", e, exc_info=True)
                errors["base"] = "unknown"
            else:
                # Если все хорошо, создаем запись
                return self.async_create_entry(
                    title=f"TTS Proxy ({user_input[CONF_TTS_HOST]})", data=user_input
                )
        
        # Показываем форму пользователю
        return self.async_show_form(step_id="user", data_schema=INITIAL_DATA_SCHEMA, errors=errors)


class OptionsFlowHandler(OptionsFlowWithConfigEntry):
    """Handle an options flow for Streaming TTS Proxy."""
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Manage the options."""
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {k: v for k, v in user_input.items() if v is not None and v != ""}
            return self.async_create_entry(title="", data=data)
        
        voices = []
        try:
            api = WyomingApi(self.config_entry.data[CONF_TTS_HOST], self.config_entry.data[CONF_TTS_PORT])
            voices = await api.get_voices()
        except (CannotConnect, NoVoicesFound) as e:
            _LOGGER.warning("Could not connect to primary TTS to get voices for options UI: %s", e)
            errors["base"] = "cannot_connect"
        
        schema_fields = {
            vol.Required(CONF_LANGUAGE, default=self.options.get(CONF_LANGUAGE, DEFAULT_LANGUAGE)): str,
        }
        if voices:
            default_voice = self.options.get(CONF_VOICE, DEFAULT_VOICE)
            if default_voice not in voices:
                default_voice = voices[0] if voices else DEFAULT_VOICE
            schema_fields[vol.Required(CONF_VOICE, default=default_voice)] = selector({
                "select": {"options": sorted(voices), "mode": "dropdown"}
            })
        else:
            schema_fields[vol.Required(CONF_VOICE, default=self.options.get(CONF_VOICE, DEFAULT_VOICE))] = str
            
        # Настройки Fallback-сервера
        schema_fields[vol.Optional(
            CONF_FALLBACK_TTS_HOST,
            description={"suggested_value": self.options.get(CONF_FALLBACK_TTS_HOST)}
        )] = str
        schema_fields[vol.Optional(
            CONF_FALLBACK_TTS_PORT,
            description={"suggested_value": self.options.get(CONF_FALLBACK_TTS_PORT)}
        )] = int
        schema_fields[vol.Optional(
            CONF_FALLBACK_VOICE,
            description={"suggested_value": self.options.get(CONF_FALLBACK_VOICE)}
        )] = str
            
        return self.async_show_form(step_id="init", data_schema=vol.Schema(schema_fields), errors=errors)

# --- END OF FILE config_flow.py ---