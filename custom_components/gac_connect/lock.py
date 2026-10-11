"""Door lock, with opt-in unlocking that does not require a PIN."""
from __future__ import annotations

from typing import Any

from homeassistant.components.lock import LockEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.exceptions import HomeAssistantError

from . import GacConfigEntry
from .const import CONF_EXPERIMENTAL_PINLESS_UNLOCK, CONF_MODEL
from .coordinator import GacCoordinator
from .entity import GacEntity
from .entity_filter import add_configured_entities
from .lock_state import doors_locked


async def async_setup_entry(
    hass: HomeAssistant, entry: GacConfigEntry, add_entities: AddEntitiesCallback
) -> None:
    add_configured_entities(entry, add_entities, "lock", [GacLock(entry.runtime_data.coordinator)])


class GacLock(GacEntity, LockEntity):
    _attr_translation_key = "doors"

    def __init__(self, coordinator: GacCoordinator) -> None:
        super().__init__(coordinator, "doors")

    @property
    def is_locked(self) -> bool | None:
        return doors_locked(self.status, self.coordinator.config_entry.data.get(CONF_MODEL))

    async def async_lock(self, **kwargs: Any) -> None:
        self._attr_is_locking = True
        self.async_write_ha_state()
        try:
            await self._send(self.coordinator.client.lock(self.coordinator.vin), command="lock")
        finally:
            self._attr_is_locking = False
            self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    async def async_unlock(self, **kwargs: Any) -> None:
        if not self.coordinator.config_entry.options.get(CONF_EXPERIMENTAL_PINLESS_UNLOCK, False):
            raise HomeAssistantError(
                "PIN-free unlock is disabled. Enable it in GAC Connect options to use it."
            )
        self._attr_is_unlocking = True
        self.async_write_ha_state()
        try:
            await self._send(self.coordinator.client.unlock(self.coordinator.vin), command="unlock")
        finally:
            self._attr_is_unlocking = False
            self.async_write_ha_state()
        await self.coordinator.async_request_refresh()
