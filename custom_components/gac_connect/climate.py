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
PRESET_HEATING, PRESET_COOLING = "heating", "cooling"
APP_PRESETS = {PRESET_HEATING: MAX_TEMP_C, PRESET_COOLING: MIN_TEMP_C}
FAN_MODES = ["off", *(str(speed) for speed in range(1, cabin.REMOTE_FAN_MAX + 1))]
# Physically verified remote selections with the user's UT ready to drive.
AIRFLOW_MODES = {"cabin": 1, "cabin_feet": 2, "feet": 3, "feet_windscreen": 4}
AIRFLOW_NAMES = {code: name for name, code in AIRFLOW_MODES.items()}
# Console reports can include selections the gateway cannot remotely select.
REPORTED_AIRFLOW_NAMES = {**AIRFLOW_NAMES, 5: "windscreen", 7: "all"}


async def async_setup_entry(hass: HomeAssistant, entry: GacConfigEntry, add_entities: AddEntitiesCallback) -> None:
    add_configured_entities(entry, add_entities, "climate", [GacClimate(entry.runtime_data.coordinator)])


class GacClimate(GacEntity, ClimateEntity):
    _attr_translation_key = "climate"
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF
        | ClimateEntityFeature.PRESET_MODE | ClimateEntityFeature.FAN_MODE | ClimateEntityFeature.SWING_MODE
    )
    _attr_preset_modes = list(APP_PRESETS)
    _attr_fan_modes = FAN_MODES
    _attr_swing_modes = list(AIRFLOW_MODES)
    _attr_min_temp = MIN_TEMP_C
    _attr_max_temp = MAX_TEMP_C
    _attr_target_temperature_step = 1.0
    # The console can report half degrees; the app's remote control uses whole degrees.
    _attr_precision = PRECISION_HALVES
    _enable_turn_on_off_backwards_compatibility = False

    def __init__(self, coordinator: GacCoordinator) -> None:
        super().__init__(coordinator, "climate")
        # Fan only is the console's Fan: the blower runs without the compressor.
        self._attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT_COOL, HVACMode.FAN_ONLY]
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
        if on is None:
            return None
        if not on:
            return HVACMode.OFF
        return HVACMode.FAN_ONLY if self._cabin.compressor is False else HVACMode.HEAT_COOL

    @property
    def preset_mode(self) -> str | None:
        if self._cabin.on is True and self._cabin.compressor is not False:
            return next((preset for preset, target in APP_PRESETS.items() if self._cabin.target == target), None)
        return None

    @property
    def fan_mode(self) -> str | None:
        # Console-only speeds remain in reported_fan_strength and the sensor.
        mode = self._cabin.fan_mode
        return mode if mode in FAN_MODES else None

    @property
    def swing_mode(self) -> str | None:
        return AIRFLOW_NAMES.get(self._cabin.blowing_mode)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        status = self.status
        blowing_mode = getattr(status, "ac_blowing_mode", None)
        return {"run_minutes": self._cabin.run_minutes,
                "reported_fan_strength": getattr(status, "ac_fan_strength", None),
                "reported_auto_mode": getattr(status, "ac_auto_on", None),
                "reported_compressor": getattr(status, "ac_compressor_on", None),
                "reported_blowing_mode": blowing_mode,
                "reported_airflow": REPORTED_AIRFLOW_NAMES.get(blowing_mode) if type(blowing_mode) is int else None,
                "reported_cycling_mode": getattr(status, "ac_cycling_mode", None)}

    def _handle_coordinator_update(self) -> None:
        self._cabin.sync()
        super()._handle_coordinator_update()

    @staticmethod
    def _power_for_mode(mode: HVACMode) -> bool:
        if mode not in (HVACMode.OFF, HVACMode.HEAT_COOL, HVACMode.FAN_ONLY):
            raise ServiceValidationError(f"Unsupported mode {mode}; choose Off, Heat/Cool or Fan only")
        return mode != HVACMode.OFF

    def _compressor_for_mode(self, mode: HVACMode | None) -> bool | None:
        """The compressor choice a mode asks for, or None when it would not change it."""
        if mode == HVACMode.FAN_ONLY:
            return False
        if mode == HVACMode.HEAT_COOL and self._cabin.compressor is False:
            return True
        return None

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if not self._power_for_mode(hvac_mode):
            await self._cabin.async_set_power(False)
        else:
            await self._cabin.async_set_fan_only(hvac_mode == HVACMode.FAN_ONLY)

    async def async_turn_on(self) -> None:
        await self._cabin.async_set_power(True)

    async def async_turn_off(self) -> None:
        await self._cabin.async_set_power(False)

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        if fan_mode not in FAN_MODES:
            raise ServiceValidationError(
                f"Unsupported remote fan mode; choose Off or a speed from 1 to {cabin.REMOTE_FAN_MAX}"
            )
        if fan_mode == "off":
            await self._cabin.async_set_power(False)
        else:
            await self._cabin.async_set_fan_speed(int(fan_mode))

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        if not isinstance(swing_mode, str) or swing_mode not in AIRFLOW_MODES:
            raise ServiceValidationError("Unsupported airflow; choose cabin, cabin_feet, feet or feet_windscreen")
        await self._cabin.async_set_blowing_mode(AIRFLOW_MODES[swing_mode])

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        if preset_mode not in APP_PRESETS:
            raise ServiceValidationError(f"Unsupported preset {preset_mode}; choose heating or cooling")
        # GAC 2.0.32 uses automatic A/C at the maximum/minimum setpoint for these buttons.
        await self._cabin.async_set_temperature(APP_PRESETS[preset_mode], on=True, automatic=True)

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
        await self._cabin.async_set_temperature(value, on=on, compressor=self._compressor_for_mode(mode))
