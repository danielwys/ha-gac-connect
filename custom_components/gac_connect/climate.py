"""Remote cabin A/C with a shared temperature selection and command queue."""

from __future__ import annotations

import math
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.components.climate.const import ATTR_HVAC_MODE
from homeassistant.const import ATTR_TEMPERATURE, PRECISION_HALVES, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import GacConfigEntry, cabin
from .coordinator import GacCoordinator
from .entity import GacEntity
from .entity_filter import add_configured_entities

MIN_TEMP_C, MAX_TEMP_C = 18.0, 32.0


async def async_setup_entry(hass: HomeAssistant, entry: GacConfigEntry, add_entities: AddEntitiesCallback) -> None:
    add_configured_entities(entry, add_entities, "climate", [GacClimate(entry.runtime_data.coordinator)])


class GacClimate(GacEntity, ClimateEntity):
    _attr_translation_key = "climate"
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF
    )
    _attr_min_temp = MIN_TEMP_C
    _attr_max_temp = MAX_TEMP_C
    _attr_target_temperature_step = 0.5
    _attr_precision = PRECISION_HALVES
    _enable_turn_on_off_backwards_compatibility = False

    def __init__(self, coordinator: GacCoordinator) -> None:
        super().__init__(coordinator, "climate")
        self._attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT_COOL]
        self._cabin = cabin.controller(coordinator)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._cabin.add_listener(self.async_write_ha_state))

    @property
    def current_temperature(self) -> float | None:
        return self.status.cabin_temp_c if self.status else None

    @property
    def target_temperature(self) -> float:
        return self._cabin.target

    @property
    def hvac_mode(self) -> HVACMode | None:
        on = self._cabin.on
        return None if on is None else HVACMode.HEAT_COOL if on else HVACMode.OFF

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"run_minutes": self._cabin.run_minutes}

    def _handle_coordinator_update(self) -> None:
        self._cabin.sync()
        super()._handle_coordinator_update()

    @staticmethod
    def _power_for_mode(mode: HVACMode) -> bool:
        if mode not in (HVACMode.OFF, HVACMode.HEAT_COOL):
            raise ServiceValidationError(f"Unsupported mode {mode}; the car offers heat_cool or off")
        return mode == HVACMode.HEAT_COOL

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        await self._cabin.async_set_power(self._power_for_mode(hvac_mode))

    async def async_turn_on(self) -> None:
        await self._cabin.async_set_power(True)

    async def async_turn_off(self) -> None:
        await self._cabin.async_set_power(False)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        mode = kwargs.get(ATTR_HVAC_MODE)
        on = self._power_for_mode(mode) if mode is not None else None
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is None:
            if on is not None:
                await self._cabin.async_set_power(on)
            return
        try:
            value = float(temp)
        except (TypeError, ValueError, OverflowError):
            raise ServiceValidationError(f"temperature must be a number, got {temp!r}") from None
        if isinstance(temp, bool) or not math.isfinite(value) or not MIN_TEMP_C <= value <= MAX_TEMP_C:
            raise ServiceValidationError(f"temperature must be between {MIN_TEMP_C:g} and {MAX_TEMP_C:g} °C")
        await self._cabin.async_set_temperature(round(value * 2) / 2, on=on)
