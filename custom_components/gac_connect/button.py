"""Buttons: refresh, charging, horn and vehicle shutdown."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from ._vendor.gac_connect.client import GacClient

from . import GacConfigEntry
from .coordinator import GacCoordinator
from .entity import GacEntity
from .entity_filter import add_configured_entities


@dataclass(frozen=True, kw_only=True)
class GacButton(ButtonEntityDescription):
    press: Callable[[GacClient, str], Awaitable[object]]
    command: str


BUTTONS: tuple[GacButton, ...] = (
    GacButton(key="vehicle_power_off", command="vehicle-power-off", translation_key="vehicle_power_off",
              icon="mdi:power", press=lambda c, vin: c.command(vin, "vehicle-power-off")),
    GacButton(key="charge_now", command="charge-now", translation_key="charge_now", press=lambda c, vin: c.charge_now(vin)),
    GacButton(key="horn", command="horn-on", translation_key="horn", icon="mdi:bullhorn", press=lambda c, vin: c.command(vin, "horn-on")),
)


async def async_setup_entry(hass: HomeAssistant, entry: GacConfigEntry, add_entities: AddEntitiesCallback) -> None:
    coordinator = entry.runtime_data.coordinator
    entities: list[ButtonEntity] = [GacButtonEntity(coordinator, d) for d in BUTTONS]
    entities.append(GacRefreshButton(coordinator))
    add_configured_entities(entry, add_entities, "button", entities)


class GacButtonEntity(GacEntity, ButtonEntity):
    entity_description: GacButton

    def __init__(self, coordinator: GacCoordinator, description: GacButton) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    async def async_press(self) -> None:
        await self._send(self.entity_description.press(self.coordinator.client, self.coordinator.vin),
                         command=self.entity_description.command)


class GacRefreshButton(GacEntity, ButtonEntity):
    _attr_translation_key = "refresh"
    _attr_entity_category = EntityCategory.DIAGNOSTIC   # reads the cloud status; controls nothing in the car

    def __init__(self, coordinator: GacCoordinator) -> None:
        super().__init__(coordinator, "refresh")

    @property
    def available(self) -> bool:
        return True  # Retrying a failed cloud read must remain possible.

    async def async_press(self) -> None:
        await self.coordinator.async_manual_refresh()
