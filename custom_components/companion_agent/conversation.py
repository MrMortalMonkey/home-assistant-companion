"""Conversation agent that forwards requests to the AI Companion HTTP API."""
from __future__ import annotations

import logging
from typing import Literal

import aiohttp

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_ENDPOINT_URL, CONF_SECRET, DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([CompanionAgentEntity(config_entry)])


class CompanionAgentEntity(conversation.ConversationEntity):
    """AI Companion conversation agent."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_unique_id = "companion_agent"

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._entry = config_entry
        self._endpoint = config_entry.data.get(CONF_ENDPOINT_URL, "http://localhost:8502")
        self._secret = config_entry.data.get(CONF_SECRET, "")
        self._attr_name = "AI Companion"
        self._attr_unique_id = f"companion_agent_{config_entry.entry_id}"

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        return "*"

    async def async_process(
        self, user_input: conversation.ConversationInput
    ) -> conversation.ConversationResult:
        """Forward user text to the AI Companion HTTP endpoint and return the response."""
        text = user_input.text
        language = user_input.language or "en"

        headers = {"Content-Type": "application/json"}
        if self._secret:
            headers["Authorization"] = f"Bearer {self._secret}"

        response_text = "Sorry, I could not reach the AI Companion."
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self._endpoint}/conversation",
                    json={"text": text, "language": language},
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        response_text = data.get("text", response_text)
                    else:
                        response_text = f"AI Companion returned HTTP {resp.status}."
        except aiohttp.ClientConnectorError:
            response_text = (
                "Cannot connect to AI Companion. "
                f"Make sure the addon is running and accessible at {self._endpoint}."
            )
        except Exception as ex:
            _LOGGER.error("AI Companion conversation error: %s", ex)
            response_text = f"Error: {ex}"

        intent_response = intent.IntentResponse(language=language)
        intent_response.async_set_speech(response_text)
        return conversation.ConversationResult(
            response=intent_response,
            conversation_id=user_input.conversation_id,
        )
