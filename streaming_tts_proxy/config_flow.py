# --- START OF FILE config_flow.py ---

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

# Импортируем наш API и ошибки из центрального файла
from .api import WyomingApi, CannotConnect, NoVoicesFound

from .const import (
    DOMAIN,
    CONF_TTS_HOST,
    CONF_TTS_PORT,
    CONF_LANGUAGE,
    CONF_VOICE,
    DEFAULT_TTS_HOST,
    DEFAULT_TTS_PORT,
    DEFAULT_LANGUAGE,
    DEFAULT_VOICE,
)

INITIAL_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_TTS_HOST, default=DEFAULT_TTS_HOST): str,
        vol.Required(CONF_TTS_PORT, default=DEFAULT_TTS_PORT): int,
    }
)

class StreamingTtsProxyConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Streaming TTS Proxy."""
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlowWithConfigEntry:
        return OptionsFlowHandler(config_entry)

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(f"{user_input[CONF_TTS_HOST]}:{user_input[CONF_TTS_PORT]}")
            self._abort_if_unique_id_configured()
            try:
                # Используем наш новый API для валидации
                api = WyomingApi(user_input[CONF_TTS_HOST], user_input[CONF_TTS_PORT])
                await api.get_voices()
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except NoVoicesFound:
                errors["base"] = "no_voices_found"
            else:
                return self.async_create_entry(
                    title=f"TTS Proxy ({user_input[CONF_TTS_HOST]})", data=user_input
                )
        return self.async_show_form(step_id="user", data_schema=INITIAL_DATA_SCHEMA, errors=errors)


class OptionsFlowHandler(OptionsFlowWithConfigEntry):
    """Handle an options flow for Streaming TTS Proxy."""
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        
        try:
            # Используем наш новый API для получения голосов
            api = WyomingApi(self.config_entry.data[CONF_TTS_HOST], self.config_entry.data[CONF_TTS_PORT])
            voices = await api.get_voices()
        except (CannotConnect, NoVoicesFound) as e:
            errors["base"] = "cannot_connect" if isinstance(e, CannotConnect) else "no_voices_found"
            voices = []
        
        schema_fields = {
            vol.Required(CONF_LANGUAGE, default=self.options.get(CONF_LANGUAGE, DEFAULT_LANGUAGE)): str,
        }
        if voices:
            default_voice = self.options.get(CONF_VOICE, DEFAULT_VOICE)
            if default_voice not in voices:
                default_voice = voices[0]
            schema_fields[vol.Required(CONF_VOICE, default=default_voice)] = selector({
                "select": {"options": voices, "mode": "dropdown", "sort": False}
            })
        else:
            schema_fields[vol.Required(CONF_VOICE, default=self.options.get(CONF_VOICE, DEFAULT_VOICE))] = str
            
        return self.async_show_form(step_id="init", data_schema=vol.Schema(schema_fields), errors=errors)