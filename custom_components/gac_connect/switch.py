"""Switches: pre-conditioning, the charge gate, steering-wheel heat, ventilation, flash lights.

Scheduled charging: on means unrestricted charging; off means scheduled mode.
Turning on is supported. Turning off cannot continuously pause charging, so
that action is disabled. Resuming takes the car a few minutes to act on.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later

from gac_connect import command_session_id
from gac_connect.models import ChargingMode, VehicleStatus
from gac_connect.push import PushResult

from . import GacConfigEntry, cabin, fridge
from .api import PAUSE_UNSUPPORTED
from .coordinator import GacCoordinator
from .entity import GacEntity
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


_LOGGER = logging.getLogger(__name__)

REQUEST_SECONDS = 180  # a requested state overrides the car's report at most this long


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
    ),
)


async def async_setup_entry(hass: HomeAssistant, entry: GacConfigEntry, add_entities: AddEntitiesCallback) -> None:
    coordinator = entry.runtime_data.coordinator
    entities: list[SwitchEntity] = [GacChargeSwitch(coordinator), GacClimateSwitch(coordinator)]
    entities.extend(GacCommandSwitch(coordinator, d) for d in SWITCHES)
    add_configured_entities(entry, add_entities, "switch", entities)
    fridge.async_add_when_fitted(entry, coordinator, add_entities, lambda: [GacFridgeSwitch(coordinator)])


class GacChargeSwitch(GacEntity, SwitchEntity):
    _attr_translation_key = "charging"
    _attr_icon = "mdi:ev-station"

    def __init__(self, coordinator: GacCoordinator) -> None:
        super().__init__(coordinator, "charge_switch")

    @property
    def is_on(self) -> bool | None:
        if self.status is None or self.status.charging_mode not in (ChargingMode.FREE, ChargingMode.SCHEDULED):
            return None
        # Off means the car uses a schedule, not that charging is continuously paused.
        return self.status.charging_mode is ChargingMode.FREE

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "note": "On enables unrestricted charging; off indicates scheduled mode.",
            "off_action_supported": False,
            "off_action_reason": PAUSE_UNSUPPORTED,
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._send(self.coordinator.client.charge_now(self.coordinator.vin))
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        raise ServiceValidationError(PAUSE_UNSUPPORTED)


class GacCommandSwitch(GacEntity, SwitchEntity):
    entity_description: GacSwitch

    def __init__(self, coordinator: GacCoordinator, description: GacSwitch) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description
        self._requested: bool | None = None
        self._requested_until: float = 0.0
        self._op_lock = asyncio.Lock()  # one command at a time per entity
        self._result_events = description.events
        self._init_results()
        self._attr_assumed_state = description.state is None
        self._expire: CALLBACK_TYPE | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._cancel_expiry)

    @callback
    def _cancel_expiry(self) -> None:
        if self._expire is not None:
            self._expire()
            self._expire = None

    @callback
    def _arm_expiry(self) -> None:
        self._cancel_expiry()
        if self._requested is not None and self.entity_description.state is not None:
            self._expire = async_call_later(self.hass, max(0, self._requested_until - monotonic()), self._expired)

    @callback
    def _expired(self, _now) -> None:
        self._expire = None
        if monotonic() >= self._requested_until:
            self._requested = None
            self.async_write_ha_state()
        else:
            self._arm_expiry()

    @property
    def is_on(self) -> bool | None:
        if self._requested is not None:
            return self._requested
        if self.entity_description.state is None or self.status is None:
            return None
        return self.entity_description.state(self.status)

    def _handle_coordinator_update(self) -> None:
        st = self.entity_description.state
        if self._requested is not None and st is not None:
            confirmed = self.status is not None and st(self.status) == self._requested
            if confirmed or monotonic() > self._requested_until:
                self._requested = None  # the car's report is authoritative again
                self._cancel_expiry()
        super()._handle_coordinator_update()

    def _on_command_result(self, result: PushResult) -> None:
        if self._requested is None:
            return
        if result.ok is False:
            _LOGGER.warning("car did not apply %s (result code %s)", self.entity_id, result.code)
            self._requested = None
            self._cancel_expiry()
            self.async_write_ha_state()

    async def _run(self, on: bool, make_request) -> None:
        """Show the requested state at once; undo it if the request itself fails.

        Commands on one entity run one at a time (the request is only created
        once the lock is held), and cancellation restores the previous state.
        """
        async with self._op_lock:
            prev = (self._requested, self._requested_until)
            previous_session = self._session_id
            self._requested, self._requested_until = on, monotonic() + REQUEST_SECONDS
            self._begin_command()
            self._arm_expiry()
            self.async_write_ha_state()
            resp = None
            try:
                resp = await self._send(make_request())
            finally:
                if resp is None:
                    self._requested, self._requested_until = prev
                    self._end_command(previous_session)
                    self._arm_expiry()
                    self.async_write_ha_state()
                else:
                    self._end_command(command_session_id(resp))
                    self._arm_expiry()
        await self.coordinator.async_request_refresh()

    async def _set(self, on: bool) -> None:
        cmd = self.entity_description.on_cmd if on else self.entity_description.off_cmd
        await self._run(on, lambda: self.coordinator.client.command(self.coordinator.vin, cmd))

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)


class GacClimateSwitch(GacEntity, SwitchEntity):
    """Plain on/off for cabin pre-conditioning; the climate entity sets the temperature."""

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
