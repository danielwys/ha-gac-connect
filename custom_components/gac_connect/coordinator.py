"""Polling coordinator and the config-entry token store."""
from __future__ import annotations

import asyncio
import logging
from datetime import time, timedelta
from time import monotonic

from ._vendor.gac_connect import GacError
from ._vendor.gac_connect.client import GacClient
from ._vendor.gac_connect.errors import AuthExpiredError, RateLimitedError
from ._vendor.gac_connect.models import VehicleStatus
from ._vendor.gac_connect.push import PushResult

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_send
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
from .profiles import profile_for

_LOGGER = logging.getLogger(__name__)

# Command results can arrive in bursts; the status refreshes they trigger are at
# least this far apart. (The client library also caps every request it sends.)
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
        # Command results arrive on the service's push feed; each one triggers
        # an immediate status refresh so entities show what the car actually did.
        self.push = PushClient(client.mqtt_info, self._on_command_result, decrypt=client.decrypt_push,
                               allow_plaintext=entry.options.get(CONF_ALLOW_PLAINTEXT_PUSH, False) is True)
        self.last_result: PushResult | None = None
        self._force_refresh = False       # explicit refreshes may run inside quiet hours
        self._result_refresh_task: asyncio.Task | None = None
        self._result_refresh_again = False
        self._last_result_refresh = 0.0
        self._fetch_lock = asyncio.Lock()   # one status request in flight, whatever triggered it

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
        if (manager := getattr(self, "capabilities", None)) is not None and manager.result(result):
            self.async_update_listeners()
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

    def _schedule_result_refresh(self) -> None:
        """One status refresh per burst of results, without blocking the feed."""
        self._force_refresh = True
        if self._result_refresh_task and not self._result_refresh_task.done():
            self._result_refresh_again = True
            return
        self._result_refresh_task = self.config_entry.async_create_background_task(
            self.hass, self._result_refresh(), f"{DOMAIN} result refresh {self.config_entry.entry_id}"
        )

    async def _result_refresh(self) -> None:
        while True:
            wait = self._last_result_refresh + RESULT_REFRESH_GAP - monotonic()
            if wait > 0:
                await asyncio.sleep(wait)   # results that land meanwhile share this refresh
            self._result_refresh_again = False
            await self.async_refresh()
            self._last_result_refresh = monotonic()   # the gap runs from when the refresh finished
            if not self._result_refresh_again:
                return

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
        async with self._fetch_lock:
            # During quiet hours, keep the last reading instead of polling —
            # unless a manual action or command result asked for a refresh.
            if self._in_quiet_hours() and self.data is not None and not self._force_refresh:
                return self.data
            self._force_refresh = False
            try:
                status = await self.client.get_status(self.vin)
                if manager := getattr(self, "capabilities", None):
                    manager.observe(status)
                return status
            except AuthExpiredError as err:
                raise ConfigEntryAuthFailed(str(err)) from err
            except RateLimitedError as err:
                # Held back by the request limits (or the service asked us to slow
                # down): keep showing the last reading rather than going unavailable.
                if self.data is not None:
                    _LOGGER.debug("status refresh skipped: %s", err)
                    return self.data
                raise UpdateFailed(str(err)) from err
            except GacError as err:
                raise UpdateFailed(str(err)) from err
