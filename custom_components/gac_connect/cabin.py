"""One command queue and setpoint shared by both cabin A/C controls."""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from time import monotonic

from homeassistant.core import CALLBACK_TYPE, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import async_call_later

from ._vendor.gac_connect import GacError, command_session_id
from ._vendor.gac_connect.commands import validate_blowing_mode, validate_climate
from ._vendor.gac_connect.push import PushResult

from .const import CONF_AC_MINUTES, DEFAULT_AC_MINUTES, SIGNAL_COMMAND_RESULT
from .command_tracking import async_send

_LOGGER = logging.getLogger(__name__)
DEFAULT_TARGET_C = 24.0
REQUEST_SECONDS = 180
# The UT cloud advertises 7; the user's trials confirmed 7 and rejected 8–10.
# Console telemetry still has ten speeds and must not be clipped to this limit.
REMOTE_FAN_MAX = 7


def _remote_temperature(target: float) -> float:
    """Match the app's whole-degree commands without rounding vehicle reports."""
    try:
        validate_climate(target, DEFAULT_AC_MINUTES)
    except ValueError as err:
        raise ServiceValidationError(str(err)) from err
    return float(math.floor(float(target) + 0.5))


@dataclass
class _Request:
    on: bool
    target: float | None
    deadline: float
    session_id: str | None = None
    selection_before: float | None = None
    selection_after: float | None = None
    expected: dict = field(default_factory=dict)
    baseline: object = None


class CabinController:
    """Keep an unsent selection separately from a command awaiting confirmation."""

    def __init__(self, coordinator) -> None:
        self.coordinator = coordinator
        self.lock = asyncio.Lock()
        self._selected_target: float | None = None
        self._req: _Request | None = None
        self._expire: CALLBACK_TYPE | None = None
        self._listeners: set[Callable[[], None]] = set()
        self._awaiting_accept = False
        self._held: list[PushResult] = []
        self._seen = None

    def async_setup(self, entry) -> None:
        entry.async_on_unload(
            async_dispatcher_connect(
                self.coordinator.hass, SIGNAL_COMMAND_RESULT.format(vin=self.coordinator.vin), self._handle_result
            )
        )
        entry.async_on_unload(self.coordinator.async_add_listener(self.sync))
        entry.async_on_unload(self._cancel_expiry)

    @callback
    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.add(listener)
        return lambda: self._listeners.discard(listener)

    @callback
    def _notify(self) -> None:
        for listener in tuple(self._listeners):
            listener()

    @property
    def run_minutes(self) -> int:
        minutes = self.coordinator.config_entry.options.get(CONF_AC_MINUTES, DEFAULT_AC_MINUTES)
        try:
            _, duration = validate_climate(DEFAULT_TARGET_C, minutes)
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err
        return duration

    @property
    def target(self) -> float:
        if self._req is not None and self._req.on and self._req.target is not None:
            return self._req.target
        if self._selected_target is not None:
            return self._selected_target
        data = self.coordinator.data
        try:
            target, _ = validate_climate(data.ac_target_temp_c if data else None, DEFAULT_AC_MINUTES)
        except ValueError:
            return DEFAULT_TARGET_C
        return target

    @property
    def on(self) -> bool | None:
        if self._req is not None:
            return self._req.on
        data = self.coordinator.data
        return data.ac_on if data else None

    @property
    def fan_mode(self) -> str | None:
        if self.on is False:
            return "off"
        if self._req is not None and "ac_fan_strength" in self._req.expected:
            return str(self._req.expected["ac_fan_strength"])
        speed = getattr(self.coordinator.data, "ac_fan_strength", None)
        # enableAutoMode was also true in verified manual-speed reports.
        # Display the numeric blower reading without inferring Auto from it.
        if isinstance(speed, bool) or not isinstance(speed, (int, float)):
            return None
        if not math.isfinite(speed) or speed != int(speed) or not 0 <= speed <= 10:
            return None
        return "off" if speed == 0 else str(int(speed))

    @property
    def blowing_mode(self) -> int | None:
        # Outlet selection remains meaningful when A/C is off.
        if self._req is not None and "ac_blowing_mode" in self._req.expected:
            return self._req.expected["ac_blowing_mode"]
        value = getattr(self.coordinator.data, "ac_blowing_mode", None)
        return value if type(value) is int and value > 0 else None

    @callback
    def sync(self) -> None:
        data = self.coordinator.data
        if data is None or data is self._seen or self._awaiting_accept:
            return
        self._seen = data
        req = self._req
        if req is None or data == req.baseline:
            return
        before = getattr(req.baseline, "updated_ms", None)
        after = getattr(data, "updated_ms", None)
        if before is not None and after is not None and after < before:
            return
        if all(getattr(data, key, None) == value for key, value in req.expected.items()):
            if req.on:
                self._selected_target = None
            self._clear_pending()
            self._notify()

    @callback
    def _cancel_expiry(self) -> None:
        if self._expire is not None:
            self._expire()
            self._expire = None

    @callback
    def _arm_expiry(self) -> None:
        self._cancel_expiry()
        if self._req is not None:
            self._expire = async_call_later(
                self.coordinator.hass, max(0, self._req.deadline - monotonic()), self._expired
            )

    @callback
    def _clear_pending(self) -> None:
        self._req = None
        self._cancel_expiry()

    @callback
    def _expired(self, _now) -> None:
        self._expire = None
        if self._req is not None and monotonic() >= self._req.deadline:
            self._restore_selection(self._req)
            self._clear_pending()
            self._notify()
        elif self._req is not None:
            self._arm_expiry()

    def _matches(self, result: PushResult) -> bool:
        req = self._req
        if req is None:
            return False
        if result.session_id and req.session_id:
            return result.session_id == req.session_id
        return result.event == "control_air_condition"

    @callback
    def _handle_result(self, result: PushResult) -> None:
        if self._awaiting_accept:
            self._held.append(result)
        elif self._matches(result):
            self._on_result(result)

    @callback
    def _on_result(self, result: PushResult) -> None:
        if result.ok is False and self._req is not None:
            _LOGGER.warning("car did not apply the cabin A/C command (result code %s: %s)", result.code,
                            result.reason or "unknown reason")
            self._restore_selection(self._req)
            self._clear_pending()
            self._notify()

    def _restore_selection(self, req: _Request) -> None:
        if self._selected_target == req.selection_after:
            self._selected_target = req.selection_before

    @callback
    def _replay(self, held: list[PushResult]) -> None:
        for result in held:
            if self._matches(result):
                self._on_result(result)

    async def async_set_power(self, on: bool) -> None:
        async with self.lock:
            await self._command(on)

    async def async_set_fan_speed(self, speed: int) -> None:
        if type(speed) is not int or not 1 <= speed <= REMOTE_FAN_MAX:
            raise ServiceValidationError(
                f"Remote fan speed must be an integer from 1 to {REMOTE_FAN_MAX}; "
                "use Off to stop A/C. Speeds 8–10 are available on the vehicle console."
            )
        async with self.lock:
            await self._command(True, fan_speed=speed)

    async def async_set_blowing_mode(self, mode: int) -> None:
        try:
            mode = validate_blowing_mode(mode)
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err
        async with self.lock:
            if getattr(self.coordinator.data, "power_on", None) is not True:
                raise ServiceValidationError(
                    "Airflow outlet changes require the vehicle fully on (ready to drive). "
                    "Refresh vehicle status after switching it on."
                )
            await self._command(True, blowing_mode=mode)

    async def async_set_temperature(
        self, target: float, *, on: bool | None = None, automatic: bool = False
    ) -> None:
        target = _remote_temperature(target)
        async with self.lock:
            if on is None and self.on is not True:
                self._selected_target = target
                self._notify()
                return
            requested_on = self.on is True if on is None else on
            data = self.coordinator.data
            temperature_only = (requested_on and not automatic and self.on is True
                                and data is not None and data.ac_on is True)
            await self._command(requested_on, target, temperature_only=temperature_only, automatic=automatic)

    async def _command(
        self, on: bool, target: float | None = None, *, temperature_only: bool = False,
        automatic: bool = False, fan_speed: int | None = None, blowing_mode: int | None = None,
    ) -> None:
        """Send one command under the shared lock; undo transport failures or cancellation."""
        previous, selection = self._req, self._selected_target
        setpoint = target if target is not None else self.target
        if on:
            setpoint = _remote_temperature(setpoint)
        duration = self.run_minutes if on and not temperature_only else None
        req = _Request(on, setpoint if on else None, monotonic() + REQUEST_SECONDS,
                       selection_before=selection, baseline=self.coordinator.data)
        req.expected = {"ac_on": on, **({"ac_target_temp_c": setpoint} if on else {})}
        if on and not automatic:
            for key, value in (("ac_fan_strength", fan_speed), ("ac_blowing_mode", blowing_mode)):
                if value is not None:
                    req.expected[key] = value
                elif previous is not None and previous.deadline > monotonic() and key in previous.expected:
                    # Omitted fields retain pending selections without replaying cached settings.
                    req.expected[key] = previous.expected[key]
        if not on and target is not None:
            self._selected_target = target
        req.selection_after = self._selected_target
        self._req = req
        self._awaiting_accept, self._held = True, []
        self._arm_expiry()
        self._notify()
        accepted = False
        try:
            client, vin = self.coordinator.client, self.coordinator.vin
            if temperature_only:
                command, request = "aircon-temperature", client.climate_set_temperature(vin, setpoint)
            elif on and automatic:
                command, request = "aircon-on", client.climate_on(vin, temperature=setpoint, minutes=duration)
            elif on:
                command, request = "aircon-manual-on", client.climate_manual_on(
                    vin, temperature=setpoint, minutes=duration, fan_speed=fan_speed, blowing_mode=blowing_mode
                )
            else:
                command, request = "aircon-off", client.climate_off(vin)
            response = await async_send(self.coordinator, command, request,
                expected=req.expected,
            )
            req.session_id = command_session_id(response)
            accepted = True
        except GacError as err:
            raise HomeAssistantError(str(err)) from err
        finally:
            self._awaiting_accept = False
            held, self._held = self._held, []
            if not accepted:
                self._selected_target = selection
                self._req = previous if previous is not None and previous.deadline > monotonic() else None
                self._arm_expiry()
            self._replay(held)
            self.sync()
            self._notify()


def controller(coordinator) -> CabinController:
    """Return the shared controller for one vehicle."""
    if getattr(coordinator, "cabin", None) is None:
        coordinator.cabin = CabinController(coordinator)
    return coordinator.cabin
