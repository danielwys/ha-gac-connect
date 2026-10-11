"""One bounded, in-memory command ledger for controls and service calls."""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from time import monotonic

from homeassistant.core import callback
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from ._vendor.gac_connect import command_session_id
from ._vendor.gac_connect.capabilities import COMMAND_FEATURES
from ._vendor.gac_connect.models import ChargingMode
from ._vendor.gac_connect.errors import RateLimitedError
from .api import doors_locked

REQUEST_SECONDS = 180
ACTIVE = {"sending", "pending", "awaiting_report"}
STATES = ["idle", "sending", "pending", "awaiting_report", "confirmed", "reported_success",
          "rejected", "failed", "timed_out", "cancelled", "superseded"]
TARGETS = {
    "lock": {"locked": True}, "unlock": {"locked": False},
    "charge-now": {"charging_mode": ChargingMode.FREE},
    **{f"{command}-{direction}": {field: value}
       for command, field in (("window", "window_open"), ("sunroof", "sunroof_open"), ("tailgate", "hatch_open"))
       for direction, value in (("open", True), ("close", False))},
    **{f"{command}-{direction}": {field: value}
       for command, field in (("steering", "steering_heat_on"), ("flash", "lights_on"), ("aircon", "ac_on"))
       for direction, value in (("on", True), ("off", False))},
    "fridge-off": {"fridge_mode": "off"},
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
            # No VINs, session identifiers, command parameters or API messages.
            "recent_commands": [{"command": r.command, "state": r.state,
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
            self.coordinator.async_rate_limited(err)
            raise
        except Exception:
            record.state = "failed"
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
            return response
        finally:
            if not any(r.state == "sending" for r in self.records):
                self._early.clear()
            self._changed()

    def result(self, result) -> bool:
        if self._closed or result.vin != self.coordinator.vin or not result.session_id or result.ok is None:
            return False
        for record in reversed(self.records):
            if record.session_id != result.session_id:
                continue
            if record.result_received or monotonic() > record.started + REQUEST_SECONDS:
                return False
            record.result_received = True
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
            if all((doors_locked(status, model) if key == "locked" else getattr(status, key, None)) == value
                   for key, value in record.expected.items()):
                record.state = "confirmed"
                changed = True
        if changed:
            self._changed()


async def async_send(coordinator, command: str, request, *, expected=None):
    if (tracker := getattr(coordinator, "commands", None)) is not None:
        return await tracker.send(command, request, expected=expected)
    response = await request
    coordinator.async_schedule_command_refresh()
    return response
