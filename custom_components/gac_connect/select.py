"""Selects: front-seat ventilation level, and fridge mode (off, refrigerate, heat or freeze)."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import GacConfigEntry, fridge
from .coordinator import GacCoordinator
from .entity import GacEntity
from .entity_filter import add_configured_entities
from .requested import GacRequestedEntity
from .seats import reported_seat

# Ventilation strength 0–3, verified on an Aion UT's front seats.
SEAT_LEVELS = {"off": 0, "low": 1, "medium": 2, "high": 3}
SEAT_LEVEL_NAMES = {level: name for name, level in SEAT_LEVELS.items()}


async def async_setup_entry(
    hass: HomeAssistant, entry: GacConfigEntry, add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data.coordinator
    add_configured_entities(entry, add_entities, "select",
                            [GacSeatVentilationSelect(coordinator, seat) for seat in (1, 2)])
    fridge.async_add_when_fitted(entry, coordinator, add_entities, lambda: [GacFridgeModeSelect(coordinator)], platform="select")


class GacSeatVentilationSelect(GacRequestedEntity, SelectEntity):
    """Front-seat ventilation strength. Both seats share a result event, so only a session id settles one.

    The console's Smart mode is not reported (an idle Smart seat reports as off and
    a running one as a level), and choosing a level here replaces it.
    """

    _attr_icon = "mdi:car-seat-cooler"
    _attr_options = list(SEAT_LEVELS)

    def __init__(self, coordinator: GacCoordinator, seat: int) -> None:
        super().__init__(coordinator, f"seat_{seat}_ventilation")
        self._seat = seat
        self._attr_translation_key = f"seat_{seat}_ventilation"

    def _reported(self) -> str | None:
        seat = reported_seat(self.status, self._seat)
        if seat is None or seat.ventilation_on is None:
            return None
        if not seat.ventilation_on:
            return "off"
        strength = seat.ventilation_strength
        return SEAT_LEVEL_NAMES.get(int(strength)) if strength is not None and strength == int(strength) else None

    @property
    def current_option(self) -> str | None:
        return self._value

    async def async_select_option(self, option: str) -> None:
        if option not in SEAT_LEVELS:
            raise ServiceValidationError(f"Unknown seat ventilation level {option!r}")
        level = SEAT_LEVELS[option]
        client, vin = self.coordinator.client, self.coordinator.vin
        await self._run(option, lambda: client.set_seat_ventilation_level(vin, self._seat, level),
                        command=f"seat-{self._seat}-ventilation-{'on' if level else 'off'}")


class GacFridgeModeSelect(GacEntity, SelectEntity):
    _attr_translation_key = "fridge_mode"
    _attr_icon = "mdi:fridge-variant-outline"

    def __init__(self, coordinator: GacCoordinator) -> None:
        super().__init__(coordinator, "fridge_mode")
        self._attr_options = list(fridge.OPTIONS)

    @property
    def _fridge(self) -> fridge.FridgeController:
        return fridge.controller(self.coordinator)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._fridge.add_listener(self.async_write_ha_state))

    @property
    def current_option(self) -> str | None:
        return self._fridge.option

    def _handle_coordinator_update(self) -> None:
        self._fridge.sync()
        super()._handle_coordinator_update()

    async def async_select_option(self, option: str) -> None:
        await self._fridge.async_set_option(option)
