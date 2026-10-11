"""Polling coordinator and the config-entry token store."""
from __future__ import annotations

import asyncio
import logging
import math
from datetime import time, timedelta
from time import monotonic

from ._vendor.gac_connect import GacError
from ._vendor.gac_connect.client import GacClient
from ._vendor.gac_connect.errors import AuthExpiredError, RateLimitedError
from ._vendor.gac_connect.models import VehicleStatus
from ._vendor.gac_connect.push import PushResult

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CONF_ALLOW_PLAINTEXT_PUSH,
    CONF_QUIET_END,
    CONF_QUIET_START,
    CONF_SCAN_INTERVAL,
    CONF_VIN,
    PROFILE_AUTO,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MIN_SCAN_INTERVAL,
    EVENT_COMMAND_RESULT,
    SIGNAL_COMMAND_RESULT,
)
from .api import PushClient
from .capabilities import CapabilityManager
from .command_tracking import CommandTracker
from .profiles import profile_for

_LOGGER = logging.getLogger(__name__)

# Acknowledgement often precedes fresh telemetry. Read again after the backend
# has had time to update, sharing one bounded sequence across all controls.
COMMAND_REFRESH_DELAYS = (5.0, 15.0, 30.0)
RESULT_REFRESH_GAP = 10.0


def _parse_hhmm(value: str | None) -> time | None:
    if not value:
        return None
    try:
        h, m = (int(x) for x in value.split(":"))
        return time(hour=h, minute=m)
    except (ValueError, AttributeError):
        return None


class GacCoordinator(DataUpdateCoordinator[VehicleStatus]):
    """Polls one vehicle's status."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: GacClient) -> None:
        try:   # the options form clamps this too; stored values are checked again here
            interval = max(MIN_SCAN_INTERVAL, int(entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)))
        except (TypeError, ValueError):
            interval = DEFAULT_SCAN_INTERVAL
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} {entry.data[CONF_VIN][-4:]}",
            update_interval=timedelta(seconds=interval),
            config_entry=entry,
        )
        self.client = client
        self.vin = entry.data[CONF_VIN]
        self.capabilities = CapabilityManager(hass, entry) if profile_for(entry) == PROFILE_AUTO else None
        self.commands = CommandTracker(self)
        # Command results arrive on the service's push feed; each one triggers
        # an immediate status refresh so entities show what the car actually did.
        self.push = PushClient(client.mqtt_info, self._on_command_result, decrypt=client.decrypt_push,
                               allow_plaintext=entry.options.get(CONF_ALLOW_PLAINTEXT_PUSH, False) is True)
        self.last_result: PushResult | None = None
        self._force_refresh = False       # explicit refreshes may run inside quiet hours
        self._result_refresh_task: asyncio.Task | None = None
        self._refresh_deadlines: list[float] = []
        self._last_status_refresh = float("-inf")
        self._followup_blocked = False
        self._followup_closed = False
        self._fetch_lock = asyncio.Lock()   # one status request in flight, whatever triggered it
        self._fetch_generation = 0
        self._fetched_status = None
        self._rate_limit_until = float("-inf")
        self.retry_not_before = None
        self.last_refresh_success = None
        self.last_refresh_attempt = None
        self._refresh_outcome = "waiting"
        self._freshness_timer = async_track_time_interval(hass, self._freshness_tick, timedelta(minutes=1))
        entry.async_on_unload(self._cancel_result_refresh)

    @callback
    def _freshness_tick(self, _now) -> None:
        """Age the displayed report without making a request to the car."""
        self.async_update_listeners()

    @property
    def report_age(self) -> float | None:
        if self.data is None or self.data.updated_ms is None:
            return None
        return (dt_util.utcnow().timestamp() - self.data.updated_ms / 1000)

    @property
    def stale_after(self) -> int:
        return max(600, int(self.update_interval.total_seconds()) * 2)

    @property
    def freshness(self) -> str:
        age = self.report_age
        if age is None:
            return "not_reported"
        if age < -60:
            return "clock_mismatch"
        return "stale" if age > self.stale_after else "recent"

    @property
    def polling_state(self) -> str:
        if self._refresh_outcome in ("error", "auth_required", "rate_limited"):
            return self._refresh_outcome
        return "quiet_hours" if self._in_quiet_hours() else self._refresh_outcome

    def start_push(self) -> None:
        self.config_entry.async_create_background_task(
            self.hass, self.push.run(), f"{DOMAIN} push {self.config_entry.entry_id}"
        )
        self.config_entry.async_on_unload(self.push.stop)

    async def _on_command_result(self, topic: str, payload: bytes, result: PushResult) -> None:
        if result.ok is None:
            _LOGGER.debug("ignoring unrecognised push message (%d bytes)", len(payload))
            return
        if result.vin != self.vin:
            return   # another vehicle on the account, or no vehicle named: not ours to act on
        _LOGGER.debug("command result: event=%s ok=%s code=%s", result.event, result.ok, result.code)
        self.last_result = result
        self.commands.result(result)
        registry = dr.async_get(self.hass)
        if lookup := getattr(registry, "async_get_device_by_identifier", None):
            device = lookup((DOMAIN, self.vin), self.config_entry.entry_id)
        else:  # HA versions before device identifiers became entry-scoped
            device = registry.async_get_device(identifiers={(DOMAIN, self.vin)})
        self.hass.bus.async_fire(EVENT_COMMAND_RESULT, {
            "device_id": device.id if device else None,
            "ok": result.ok, "code": result.code,
            "event": result.event, "session_id": result.session_id,
        })
        async_dispatcher_send(self.hass, SIGNAL_COMMAND_RESULT.format(vin=self.vin), result)
        self._schedule_result_refresh()

    @callback
    def async_schedule_command_refresh(self) -> None:
        """Debounce accepted commands into at most three follow-up reads per burst."""
        if self._followup_closed or monotonic() < self._rate_limit_until:
            return
        now = monotonic()
        self._refresh_deadlines = [now + delay for delay in COMMAND_REFRESH_DELAYS]
        self._start_result_refresh()

    @callback
    def _schedule_result_refresh(self) -> None:
        """A push result shares pending reads, or requests one delayed read."""
        if self._followup_closed or monotonic() < self._rate_limit_until:
            return
        if not self._refresh_deadlines:
            self._refresh_deadlines = [monotonic() + COMMAND_REFRESH_DELAYS[0]]
        self._start_result_refresh()

    @callback
    def _start_result_refresh(self) -> None:
        if self._result_refresh_task and not self._result_refresh_task.done():
            return
        self._result_refresh_task = self.config_entry.async_create_background_task(
            self.hass, self._result_refresh(), f"{DOMAIN} result refresh {self.config_entry.entry_id}"
        )

    async def _result_refresh(self) -> None:
        while self._refresh_deadlines:
            wait = max(self._refresh_deadlines[0], self._last_status_refresh + RESULT_REFRESH_GAP) - monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
                continue  # commands or another status read may have moved the deadline
            self._refresh_deadlines.pop(0)
            self._force_refresh = True  # explicit commands also refresh during quiet hours
            await self.async_refresh()
            if self._followup_blocked:
                self._refresh_deadlines.clear()  # do not retry through a rate limit or expired login
            elif self.commands.records and not self.commands.pending:
                # One read after a result is useful; further reads cannot confirm
                # an already settled command. Unrelated pending actions still wait.
                self._refresh_deadlines.clear()

    @callback
    def async_rate_limited(self, error: RateLimitedError) -> None:
        """Share a command/status backoff without spending further request budget."""
        delay = error.retry_after
        if not isinstance(delay, (int, float)) or not math.isfinite(delay) or delay <= 0:
            delay = 60
        self._rate_limit_until = max(self._rate_limit_until, monotonic() + min(delay, 86400))
        self.retry_not_before = dt_util.utcnow() + timedelta(seconds=self._rate_limit_until - monotonic())
        self._refresh_outcome = "rate_limited"
        self._followup_blocked = True
        self._refresh_deadlines.clear()
        self.async_update_listeners()

    @callback
    def _cancel_result_refresh(self) -> None:
        self._followup_closed = True
        self.commands.close()
        if self._freshness_timer is not None:
            self._freshness_timer()
            self._freshness_timer = None
        self._refresh_deadlines.clear()
        if self._result_refresh_task is not None:
            self._result_refresh_task.cancel()

    async def async_shutdown(self) -> None:
        self._cancel_result_refresh()
        if self._result_refresh_task is not None:
            await asyncio.gather(self._result_refresh_task, return_exceptions=True)
        await super().async_shutdown()

    def _in_quiet_hours(self) -> bool:
        start = _parse_hhmm(self.config_entry.options.get(CONF_QUIET_START))
        end = _parse_hhmm(self.config_entry.options.get(CONF_QUIET_END))
        if not start or not end:
            return False
        now = dt_util.now().time()   # Home Assistant's time zone, not the host's
        if start <= end:
            return start <= now < end
        return now >= start or now < end  # window crosses midnight

    async def async_manual_refresh(self) -> None:
        """Honor an explicit refresh even when scheduled polling is suppressed."""
        self._force_refresh = True
        await self.async_request_refresh()

    async def _async_update_data(self) -> VehicleStatus:
        generation = self._fetch_generation
        async with self._fetch_lock:
            if generation != self._fetch_generation and self._refresh_outcome == "ok":
                # A caller that waited for this same in-flight read shares its
                # result, even before DataUpdateCoordinator publishes it.
                self._force_refresh = False
                return self._fetched_status
            # During quiet hours, keep the last reading instead of polling —
            # unless a manual action or command result asked for a refresh.
            if self._in_quiet_hours() and self.data is not None and not self._force_refresh:
                return self.data
            self._force_refresh = False
            if monotonic() < self._rate_limit_until:
                self._followup_blocked = True
                if self.data is not None:
                    return self.data
                raise UpdateFailed("Waiting for GAC's request limit before refreshing")
            self._followup_blocked = False
            self.last_refresh_attempt = dt_util.utcnow()
            try:
                status = await self.client.get_status(self.vin)
                self.last_refresh_success = dt_util.utcnow()
                self._refresh_outcome = "ok"
                self.retry_not_before = None
                self._fetched_status = status
                if manager := getattr(self, "capabilities", None):
                    manager.observe(status)
                self.commands.observe(status)
                return status
            except AuthExpiredError as err:
                self._refresh_outcome = "auth_required"
                self._followup_blocked = True
                raise ConfigEntryAuthFailed(str(err)) from err
            except RateLimitedError as err:
                self.async_rate_limited(err)
                # Held back by the request limits (or the service asked us to slow
                # down): keep showing the last reading rather than going unavailable.
                if self.data is not None:
                    _LOGGER.debug("status refresh skipped: %s", err)
                    return self.data
                raise UpdateFailed(str(err)) from err
            except GacError as err:
                self._refresh_outcome = "error"
                raise UpdateFailed(str(err)) from err
            except Exception:
                self._refresh_outcome = "error"
                raise
            finally:
                self._last_status_refresh = monotonic()
                self._fetch_generation += 1
