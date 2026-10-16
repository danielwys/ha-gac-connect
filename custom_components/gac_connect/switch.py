"""Switches: A/C, recirculation, the charge gate, Smart battery temp, sterilisation,
steering-wheel heat, ventilation, flash lights.

Scheduled charging: on returns to the schedule the car last reported (once or
repeating), resent unchanged; off charges whenever plugged in. Neither is a
continuous pause. The car takes a few minutes to act on a change.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from ._vendor.gac_connect.commands import CYCLING_FRESH_AIR, CYCLING_RECIRCULATE
from ._vendor.gac_connect.models import ChargingMode, VehicleStatus

from . import GacConfigEntry, cabin, fridge
from .coordinator import GacCoordinator
from .entity import GacEntity
from .requested import REQUEST_SECONDS, GacRequestedEntity
from .transition import GacTransitionEntity
from .entity_filter import add_configured_entities


@dataclass(frozen=True, kw_only=True)
class GacSwitch(SwitchEntityDescription):
    on_cmd: str
    off_cmd: str
    # None = the car does not report this; the switch keeps the last requested state.
    state: Callable[[VehicleStatus], bool | None] | None = None
    # result events that answer this switch's commands; with none listed, results
    # can only be matched by session id, so a refusal may not clear the state
    events: tuple[str, ...] = ()
    attributes: Callable[[VehicleStatus], dict[str, Any]] | None = None
    # Without a reported state, how long a request shows before the switch returns to unknown.
    hold_seconds: float = REQUEST_SECONDS


PRECONDITION_MODES = {0: "Off", 1: "Immediately", 2: "Once", 3: "Repeating"}


SWITCHES: tuple[GacSwitch, ...] = (
    GacSwitch(
        key="steering_heat",
        translation_key="steering_heat",
        icon="mdi:steering",
        on_cmd="steering-on",
        off_cmd="steering-off",
        state=lambda s: s.steering_heat_on,
        events=("control_steering",),
    ),
    GacSwitch(
        key="lights",
        translation_key="lights",
        icon="mdi:car-light-high",
        on_cmd="flash-on",
        off_cmd="flash-off",
        state=lambda s: s.lights_on,
        events=("control_light",),
    ),
    GacSwitch(
        key="ventilation",
        translation_key="ventilation",
        icon="mdi:fan",
        on_cmd="ventilate-on",
        off_cmd="ventilate-off",
        events=("control_ventilate_mode",),
        # The app: windows drop 2–3 cm and fresh-air circulation runs; the mode lasts 5 minutes.
        hold_seconds=5 * 60,
    ),
    GacSwitch(
        key="precondition_battery",
        translation_key="precondition_battery",
        icon="mdi:battery-heart-variant",
        on_cmd="battery-precondition",
        off_cmd="battery-precondition-off",
        # On while the car reports any requested or scheduled preconditioning; Off cancels it.
        state=lambda s: getattr(s, "battery_precondition_on", None),
        events=("control_battery_pretreatment",),
        attributes=lambda s: {
            "reported_mode": PRECONDITION_MODES.get(getattr(s, "battery_precondition_mode", None)),
            "reported_status": getattr(s, "battery_precondition_status", None),
        },
    ),
    GacSwitch(
        key="sterilisation",
        translation_key="sterilisation",
        icon="mdi:virus-off-outline",
        on_cmd="antivirus-on",
        off_cmd="antivirus-off",
        # Hot-air sterilisation runs 30 minutes, then the car reports it off.
        state=lambda s: getattr(s, "sterilisation_on", None),
        events=("control_air_cleaning_system",),
    ),
)


async def async_setup_entry(hass: HomeAssistant, entry: GacConfigEntry, add_entities: AddEntitiesCallback) -> None:
    coordinator = entry.runtime_data.coordinator
    entities: list[SwitchEntity] = [GacChargeSwitch(coordinator), GacClimateSwitch(coordinator),
                                    GacRecirculationSwitch(coordinator)]
    entities.extend(GacCommandSwitch(coordinator, d) for d in SWITCHES)
    add_configured_entities(entry, add_entities, "switch", entities)
    fridge.async_add_when_fitted(entry, coordinator, add_entities, lambda: [GacFridgeSwitch(coordinator)], platform="switch")


class GacChargeSwitch(GacTransitionEntity, SwitchEntity):
    _attr_translation_key = "charging"
    _attr_icon = "mdi:ev-station"

    def __init__(self, coordinator: GacCoordinator) -> None:
        super().__init__(coordinator, "charge_switch")

    @property
    def is_on(self) -> bool | None:
        return self._requested_state if self._requested_state is not None else self._reported_state()

    def _reported_state(self) -> bool | None:
        mode = self.status.charging_mode if self.status is not None else None
        if mode not in (ChargingMode.FREE, ChargingMode.SCHEDULED, ChargingMode.LOOP):
            return None
        # On means the car follows a schedule (once or repeating), not that it is charging.
        return mode is not ChargingMode.FREE

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "note": "On follows the car's last reported schedule; off charges whenever plugged in. "
                    "Neither pauses charging.",
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._run_transition(True, "charge-resume",
                                   lambda: self.coordinator.client.resume_schedule(self.coordinator.vin))

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._run_transition(False, "charge-now", lambda: self.coordinator.client.charge_now(self.coordinator.vin))


class GacCommandSwitch(GacRequestedEntity, SwitchEntity):
    entity_description: GacSwitch

    def __init__(self, coordinator: GacCoordinator, description: GacSwitch) -> None:
        super().__init__(coordinator, description.key, description.events)
        self.entity_description = description
        self._attr_assumed_state = description.state is None

    @property
    def _reports_value(self) -> bool:
        return self.entity_description.state is not None

    def _reported(self) -> bool | None:
        state = self.entity_description.state
        return state(self.status) if state is not None else None

    def _hold_seconds(self) -> float:
        return self.entity_description.hold_seconds

    @property
    def is_on(self) -> bool | None:
        return self._value

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        attributes = self.entity_description.attributes
        return attributes(self.status) if attributes is not None and self.status is not None else None

    async def _set(self, on: bool) -> None:
        cmd = self.entity_description.on_cmd if on else self.entity_description.off_cmd
        await self._run(on, lambda: self.coordinator.client.command(self.coordinator.vin, cmd), command=cmd)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)


class GacClimateSwitch(GacEntity, SwitchEntity):
    """The cabin A/C, on whenever it runs (the car does not report who started it).

    Turning it on starts a remote run; the climate entity sets the temperature."""

    def __init__(self, coordinator: GacCoordinator) -> None:
        super().__init__(coordinator, "climate_power")
        self._cabin = cabin.controller(coordinator)

    _attr_translation_key = "climate_power"
    _attr_icon = "mdi:air-conditioner"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._cabin.add_listener(self.async_write_ha_state))

    @property
    def is_on(self) -> bool | None:
        return self._cabin.on

    def _handle_coordinator_update(self) -> None:
        self._cabin.sync()
        super()._handle_coordinator_update()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._cabin.async_set_power(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._cabin.async_set_power(False)


class GacRecirculationSwitch(GacEntity, SwitchEntity):
    """Air source as reported: on recirculates, off draws fresh air (or the console's Auto, reported alike).

    Changes are sent only while A/C runs (renewing the timed run); an Aion UT
    ignores a remote air-source change otherwise.
    """

    _attr_translation_key = "recirculation"
    _attr_icon = "mdi:autorenew"

    def __init__(self, coordinator: GacCoordinator) -> None:
        super().__init__(coordinator, "recirculation")
        self._cabin = cabin.controller(coordinator)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._cabin.add_listener(self.async_write_ha_state))

    @property
    def is_on(self) -> bool | None:
        mode = self._cabin.cycling_mode
        return None if mode not in (CYCLING_RECIRCULATE, CYCLING_FRESH_AIR) else mode == CYCLING_RECIRCULATE

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"reported_cycling_mode": getattr(self.status, "ac_cycling_mode", None)}

    def _handle_coordinator_update(self) -> None:
        self._cabin.sync()
        super()._handle_coordinator_update()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._cabin.async_set_cycling_mode(CYCLING_RECIRCULATE)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._cabin.async_set_cycling_mode(CYCLING_FRESH_AIR)


class GacFridgeSwitch(GacEntity, SwitchEntity):
    """Fridge on/off. On resumes the last running mode at its last temperature."""

    _attr_translation_key = "fridge"
    _attr_icon = "mdi:fridge-outline"

    def __init__(self, coordinator: GacCoordinator) -> None:
        super().__init__(coordinator, "fridge")

    @property
    def _fridge(self) -> fridge.FridgeController:
        return fridge.controller(self.coordinator)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._fridge.add_listener(self.async_write_ha_state))

    @property
    def is_on(self) -> bool | None:
        return self._fridge.running

    def _handle_coordinator_update(self) -> None:
        self._fridge.sync()
        super()._handle_coordinator_update()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._fridge.async_turn_on()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._fridge.async_turn_off()
