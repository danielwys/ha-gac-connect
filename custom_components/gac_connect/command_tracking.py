"""One bounded, in-memory command ledger for controls and service calls."""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from inspect import iscoroutine
from time import monotonic

from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from ._vendor.gac_connect import command_session_id
from ._vendor.gac_connect.capabilities import COMMAND_FEATURES
from ._vendor.gac_connect.commands import refusal_hint
from ._vendor.gac_connect.models import ChargingMode
from ._vendor.gac_connect.errors import AuthError, CommandError, RateLimitedError, TokenInvalidError
from .api import doors_locked
from .const import (
    CONF_ALLOW_CHARGER_UNLOCK, CONF_ALLOW_VEHICLE_SHUTDOWN, CONF_EXPERIMENTAL_PINLESS_UNLOCK,
    VEHICLE_SHUTDOWN_COMMANDS,
)
from .seats import ventilation_state

REQUEST_SECONDS = 180
# How long an accepted command waits for the car's answer so a refusal can fail the action.
# An Aion UT refused a remote A/C start about four seconds after the request.
RESULT_WAIT_SECONDS = 10
ACTIVE = {"sending", "pending", "awaiting_report"}
STATES = ["idle", "sending", "pending", "awaiting_report", "confirmed", "reported_success",
          "rejected", "failed", "timed_out", "cancelled", "superseded"]
TARGETS = {
    "vehicle-power-off": {"power_on": False},
    "lock": {"locked": True}, "unlock": {"locked": False},
    "charge-now": {"charging_mode": ChargingMode.FREE},
    "charge-resume": {"charge_scheduled": True},
    "battery-precondition": {"battery_precondition_on": True},
    "battery-precondition-off": {"battery_precondition_on": False},
    **{f"{command}-{direction}": {field: value}
       for command, field in (("window", "window_open"), ("sunroof", "sunroof_open"), ("tailgate", "hatch_open"))
       for direction, value in (("open", True), ("close", False))},
    **{f"{command}-{direction}": {field: value}
       for command, field in (("steering", "steering_heat_on"), ("flash", "lights_on"), ("aircon", "ac_on"))
       for direction, value in (("on", True), ("off", False))},
    "fridge-off": {"fridge_mode": "off"},
    **{f"seat-{seat}-ventilation-{direction}": {f"seat_{seat}_ventilation_on": value}
       for seat in range(1, 5) for direction, value in (("on", True), ("off", False))},
}


@dataclass
class CommandRecord:
    command: str
    started: float
    baseline: object
    expected: dict
    state: str = "sending"
    session_id: str | None = None
    requested_at: object = field(default_factory=dt_util.utcnow)
    result_received: bool = False
    failure_reason: str | None = None
    response_code: str | None = None
    refusal_reason: str | None = None
    answered: asyncio.Event = field(default_factory=asyncio.Event, repr=False, compare=False)


def refusal_text(command: str, code: object, reason: str | None) -> str:
    """One wording for a refusal, whether the gateway answered it or the car pushed it."""
    detail = f"{reason} (code {code})" if reason else f"result code {code}"
    hint = refusal_hint(code, command)
    return f"The car refused the request: {detail}." + (f" {hint}" if hint else "")


def refusal_message(record: CommandRecord) -> str:
    """Explain a refusal reported by the car."""
    return refusal_text(record.command, record.response_code, record.refusal_reason)


class CommandTracker:
    def __init__(self, coordinator) -> None:
        self.coordinator = coordinator
        self.records: deque[CommandRecord] = deque(maxlen=32)
        self._early = deque(maxlen=32)
        self._timer = None
        self._closed = False

    @property
    def pending(self) -> list[CommandRecord]:
        return [record for record in self.records if record.state in ACTIVE]

    @property
    def state(self) -> str:
        return self.records[-1].state if self.records else "idle"

    def attributes(self) -> dict:
        last = self.records[-1] if self.records else None
        return {
            "command": last.command if last else None,
            "requested_at": last.requested_at.isoformat() if last else None,
            "pending_count": len(self.pending),
            "pending_commands": [record.command for record in self.pending],
            "failure_reason": last.failure_reason if last else None,
            "response_code": last.response_code if last else None,
            # No VINs, session identifiers, command parameters or API messages.
            "recent_commands": [{"command": r.command, "state": r.state,
                                 "failure_reason": r.failure_reason, "response_code": r.response_code,
                                 "requested_at": r.requested_at.isoformat()} for r in list(self.records)[-5:]],
        }

    @callback
    def _changed(self) -> None:
        if self._closed:
            return
        if self._timer:
            self._timer()
            self._timer = None
        if pending := self.pending:
            deadline = min(record.started + REQUEST_SECONDS for record in pending)
            self._timer = async_call_later(self.coordinator.hass, max(0, deadline - monotonic()), self._expire)
        self.coordinator.async_update_listeners()

    @callback
    def _expire(self, _now) -> None:
        self._timer = None
        for record in self.pending:
            if monotonic() >= record.started + REQUEST_SECONDS:
                record.state = "timed_out"
        self._changed()

    @callback
    def close(self) -> None:
        self._closed = True
        if self._timer:
            self._timer()
            self._timer = None
        self._early.clear()
        for record in self.pending:
            record.state = "cancelled"
            record.answered.set()

    async def send(self, command, request, *, expected=None):
        if self._closed:
            if asyncio.iscoroutine(request):
                request.close()
            raise RuntimeError("Vehicle integration is unloading")
        record = CommandRecord(command, monotonic(), self.coordinator.data,
                               dict(TARGETS.get(command, {}) if expected is None else expected))
        self.records.append(record)
        self._changed()
        try:
            response = await request
        except asyncio.CancelledError:
            record.state = "cancelled"
            raise
        except RateLimitedError as err:
            record.state = "failed"
            record.failure_reason = "rate_limited"
            self.coordinator.async_rate_limited(err)
            raise
        except Exception as err:
            record.state = "failed"
            if isinstance(err, CommandError):
                record.response_code = getattr(err, "code", None)
                record.failure_reason = "gateway_rejected" if record.response_code else "command_error"
                if record.response_code:
                    raise CommandError(refusal_text(command, record.response_code, err.reason),
                                       code=record.response_code) from err
            elif isinstance(err, (AuthError, TokenInvalidError)):
                record.failure_reason = "authentication"
            else:
                record.failure_reason = "request_error"
            raise
        else:
            if self._closed or record.state == "timed_out":
                return response
            record.session_id = command_session_id(response)
            record.state = "pending"
            feature = COMMAND_FEATURES.get(command, command)
            for previous in self.pending:
                if previous is not record and previous.started <= record.started and previous.state != "sending" \
                        and COMMAND_FEATURES.get(previous.command, previous.command) == feature:
                    previous.state = "superseded"
            for received, result in tuple(self._early):
                if received >= record.started and result.session_id == record.session_id:
                    self.result(result)
                    break
            self.observe(self.coordinator.data)
            self.coordinator.async_schedule_command_refresh()
        finally:
            if not any(r.state == "sending" for r in self.records):
                self._early.clear()
            self._changed()
        await self._await_answer(record)
        return response

    async def _await_answer(self, record: CommandRecord) -> None:
        """Give the car a moment to answer; a refusal fails the action that asked for it.

        Without a push channel, or with no answer in time, the command stays
        pending and resolves from later reports as before.
        """
        push = getattr(self.coordinator, "push", None)
        if not record.result_received and record.session_id and getattr(push, "connected", None) is True:
            try:
                async with asyncio.timeout(RESULT_WAIT_SECONDS):
                    await record.answered.wait()
            except TimeoutError:
                return
        if record.failure_reason == "vehicle_rejected":
            raise HomeAssistantError(refusal_message(record))

    def result(self, result) -> bool:
        if self._closed or result.vin != self.coordinator.vin or not result.session_id or result.ok is None:
            return False
        for record in reversed(self.records):
            if record.session_id != result.session_id:
                continue
            if record.result_received or monotonic() > record.started + REQUEST_SECONDS:
                return False
            record.result_received = True
            if not result.ok:
                record.failure_reason, record.response_code = "vehicle_rejected", result.code
                record.refusal_reason = result.reason
            record.answered.set()
            if record.state in {"pending", "awaiting_report"}:
                record.state = ("awaiting_report" if record.expected else "reported_success") if result.ok else "rejected"
            manager = self.coordinator.capabilities
            if result.ok and manager is not None:
                manager.confirm_command(record.command)
            self._changed()
            return True
        if any(r.state == "sending" for r in self.records):
            self._early.append((monotonic(), result))
        return False

    def observe(self, status) -> None:
        if status is None or self._closed:
            return
        changed = False
        for record in self.pending:
            if monotonic() >= record.started + REQUEST_SECONDS:
                record.state = "timed_out"
                changed = True
                continue
            if record.state == "sending" or not record.expected or status == record.baseline:
                continue
            before = record.baseline.updated_ms if record.baseline is not None else None
            if before is not None and status.updated_ms is not None and status.updated_ms < before:
                continue
            model = self.coordinator.config_entry.data.get("model")
            def reading(key):
                if key == "locked":
                    return doors_locked(status, model)
                if key.startswith("seat_") and key.endswith("_ventilation_on"):
                    return ventilation_state(status, int(key.split("_")[1]))
                if key == "charge_scheduled":   # once and repeating schedules both count
                    mode = getattr(status, "charging_mode", None)
                    return None if mode in (None, ChargingMode.UNKNOWN) else mode.scheduled
                return getattr(status, key, None)

            if all(reading(key) == value for key, value in record.expected.items()):
                record.state = "confirmed"
                changed = True
        if changed:
            self._changed()


async def async_send(coordinator, command: str, request, *, expected=None):
    """Send through the shared ledger; every surface (entities, actions) honors the same opt-ins."""
    refusal = None
    if (command in VEHICLE_SHUTDOWN_COMMANDS
            and coordinator.config_entry.options.get(CONF_ALLOW_VEHICLE_SHUTDOWN) is not True):
        refusal = ("Remote vehicle shutdown is disabled. Turn on 'Allow remote vehicle shutdown' "
                   "in GAC Connect Vehicle settings to use it.")
    elif command == "unlock" and coordinator.config_entry.options.get(CONF_EXPERIMENTAL_PINLESS_UNLOCK) is not True:
        refusal = "Remote unlocking is disabled. Turn on 'Allow remote unlocking' in GAC Connect options to use it."
    elif command == "charger-unlock" and coordinator.config_entry.options.get(CONF_ALLOW_CHARGER_UNLOCK) is not True:
        refusal = ("Remote charging connector unlock is disabled. Turn on 'Allow remote charging connector "
                   "unlock' in GAC Connect options to use it.")
    if refusal is not None:
        if iscoroutine(request):
            request.close()
        raise ServiceValidationError(refusal)
    if (tracker := getattr(coordinator, "commands", None)) is not None:
        return await tracker.send(command, request, expected=expected)
    response = await request
    coordinator.async_schedule_command_refresh()
    return response
