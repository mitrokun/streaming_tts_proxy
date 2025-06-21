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

from wyoming.info import TtsVoice
from .api import WyomingApi, CannotConnect, NoVoicesFound

from .const import (
    DOMAIN,
    CONF_TTS_HOST,
    CONF_TTS_PORT,
    CONF_LANGUAGE,
    CONF_VOICE,
    CONF_SAMPLE_RATE,
    CONF_FALLBACK_TTS_HOST,
    CONF_FALLBACK_TTS_PORT,
    CONF_FALLBACK_VOICE,
    CONF_FALLBACK_SAMPLE_RATE,
    DEFAULT_TTS_HOST,
    DEFAULT_TTS_PORT,
    DEFAULT_LANGUAGE,
    DEFAULT_VOICE,
    DEFAULT_SAMPLE_RATE,
    DEFAULT_FALLBACK_SAMPLE_RATE,
)

_LOGGER = logging.getLogger(__name__)

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
                await api.get_voices_info()
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except NoVoicesFound:
                errors["base"] = "no_voices_found"
            except Exception as e:
                _LOGGER.error("Unknown error during setup: %s", e, exc_info=True)
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title=f"TTS Proxy ({user_input[CONF_TTS_HOST]})", data=user_input
                )
        
        return self.async_show_form(step_id="user", data_schema=INITIAL_DATA_SCHEMA, errors=errors)


class OptionsFlowHandler(OptionsFlowWithConfigEntry):
    """Handle an options flow for Streaming TTS Proxy."""
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Manage the options."""
        errors: dict[str, str] = {}
        if user_input is not None:
            # The logic to merge with existing options is handled by Home Assistant
            return self.async_create_entry(title="", data=user_input)
        
        all_voices_info: list[TtsVoice] = []
        supported_languages: list[str] = []
        
        try:
            api = WyomingApi(self.config_entry.data[CONF_TTS_HOST], self.config_entry.data[CONF_TTS_PORT])
            all_voices_info = await api.get_voices_info()
            
            # Extract unique languages from all available voices
            lang_set = set()
            for voice_info in all_voices_info:
                if voice_info.languages:
                    lang_set.update(voice_info.languages)
            supported_languages = sorted(list(lang_set))

        except (CannotConnect, NoVoicesFound) as e:
            _LOGGER.warning("Could not connect to primary TTS to get languages/voices for options UI: %s", e)
            errors["base"] = "cannot_connect"
        
        # Combine config and options to get current values
        current_config = {**self.config_entry.data, **self.options}

        schema_fields = {}
        
        # --- Language Selector ---
        if supported_languages:
            default_lang = current_config.get(CONF_LANGUAGE, DEFAULT_LANGUAGE)
            if default_lang not in supported_languages:
                default_lang = supported_languages[0]
            
            schema_fields[vol.Required(CONF_LANGUAGE, default=default_lang)] = selector({
                "select": {"options": supported_languages, "mode": "dropdown"}
            })
        else:
            schema_fields[vol.Required(
                CONF_LANGUAGE, 
                default=current_config.get(CONF_LANGUAGE, DEFAULT_LANGUAGE)
            )] = str

        # --- Voice Selector ---
        all_voice_names = sorted([v.name for v in all_voices_info])
        if all_voice_names:
            default_voice = current_config.get(CONF_VOICE, DEFAULT_VOICE)
            if default_voice not in all_voice_names:
                default_voice = all_voice_names[0] if all_voice_names else DEFAULT_VOICE

            schema_fields[vol.Required(CONF_VOICE, default=default_voice)] = selector({
                "select": {"options": all_voice_names, "mode": "dropdown"}
            })
        else:
            schema_fields[vol.Required(
                CONF_VOICE, 
                default=current_config.get(CONF_VOICE, DEFAULT_VOICE)
            )] = str

        # --- Other fields ---
        schema_fields[vol.Optional(
            CONF_SAMPLE_RATE,
            description={"suggested_value": current_config.get(CONF_SAMPLE_RATE, DEFAULT_SAMPLE_RATE)}
        )] = int
            
        schema_fields[vol.Optional(
            CONF_FALLBACK_TTS_HOST,
            description={"suggested_value": current_config.get(CONF_FALLBACK_TTS_HOST)}
        )] = str
        
        schema_fields[vol.Optional(
            CONF_FALLBACK_TTS_PORT,
            description={"suggested_value": current_config.get(CONF_FALLBACK_TTS_PORT)}
        )] = int
            
        schema_fields[vol.Optional(
            CONF_FALLBACK_VOICE,
            description={"suggested_value": current_config.get(CONF_FALLBACK_VOICE)}
        )] = str
            
        schema_fields[vol.Optional(
            CONF_FALLBACK_SAMPLE_RATE,
            description={"suggested_value": current_config.get(CONF_FALLBACK_SAMPLE_RATE, DEFAULT_FALLBACK_SAMPLE_RATE)}
        )] = int
            
        return self.async_show_form(step_id="init", data_schema=vol.Schema(schema_fields), errors=errors)

# --- END OF FILE config_flow.py ---