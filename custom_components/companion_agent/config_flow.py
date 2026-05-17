"""Config flow for AI Companion conversation agent."""
from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult

from .const import CONF_ENDPOINT_URL, CONF_SECRET, DEFAULT_ENDPOINT, DOMAIN

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ENDPOINT_URL, default=DEFAULT_ENDPOINT): str,
        vol.Optional(CONF_SECRET, default=""): str,
    }
)


class CompanionAgentConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for AI Companion."""

    VERSION = 1

    async def async_step_user(self, user_input=None) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="AI Companion", data=user_input)
        return self.async_show_form(step_id="user", data_schema=STEP_USER_DATA_SCHEMA)
