"""Charge now, which also starts a new charge once one has ended.

GAC has no start command. Charge now switches the schedule off and the car charges
whenever a charge begins (plugging in, the supply coming on), but once a charge has ended
(a one-time window finished, the connector was unlocked) that alone does not start another.
Then a one-time window to the charge limit is sent, starting at the next minute, and the
schedule is switched off once charging has begun, which keeps it going past that window
(Aion UT, 2026-10-17). A repeating schedule is never replaced this way.
"""
from __future__ import annotations

import asyncio
import logging
from time import monotonic

from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError

from ._vendor.gac_connect.errors import GacError
from ._vendor.gac_connect.models import restart_needed
from .command_tracking import async_send, charging_requests

_LOGGER = logging.getLogger(__name__)
# Reads after the restart window is sent, in seconds. It starts 30-90 s later and the car
# reported charging about two and a half minutes after it was sent.
RESTART_READS = (60, 90, 120, 150, 180, 240, 300)


def charge_now_request(coordinator, *, until_soc: bool = False):
    """The Charge now request to send through the command ledger."""
    helper = getattr(getattr(coordinator.config_entry, "runtime_data", None), "charge_now", None)
    if helper is None:
        return coordinator.client.charge_now(coordinator.vin, until_soc=until_soc)
    return helper.request(until_soc=until_soc)


class ChargeNow:
    def __init__(self, coordinator) -> None:
        self._coordinator = coordinator
        self._charging = asyncio.Event()
        self._claimed = False             # set before the restart is sent, so presses cannot both send it
        self._generation: int | None = None
        self._task: asyncio.Task | None = None

    @property
    def restarting(self) -> bool:
        return self._claimed

    @callback
    def observe(self, status) -> None:
        if self.restarting and getattr(status, "charging", None) is True:
            self._charging.set()

    def _still_wanted(self) -> bool:
        return charging_requests(self._coordinator).generation == self._generation

    async def request(self, *, until_soc: bool = False):
        coordinator = self._coordinator
        client, vin = coordinator.client, coordinator.vin
        if self._claimed:
            # The same choice again: the follow-up stays wanted, and nothing more is sent.
            self._generation = charging_requests(coordinator).generation
            _LOGGER.info("Charge now: already starting a new charge; nothing more sent")
            return None
        if not restart_needed(coordinator.data):
            return await client.charge_now(vin, until_soc=until_soc)
        self._claimed = True
        self._charging.clear()
        try:
            started = monotonic()
            response = await client.restart_charging(vin)
        except BaseException:
            self._claimed = False
            raise
        self._generation = charging_requests(coordinator).generation
        _LOGGER.info("Charge now: the car's charge had ended, so a one-time window to the charge limit "
                     "starts a new one; the schedule switches off once charging begins")
        runtime = coordinator.config_entry.runtime_data
        if (draft := getattr(runtime, "charge_draft", None)) is not None:
            draft.mark_sent(None, None, 0, True)     # the dashboard's draft is not this window
        self._task = coordinator.config_entry.async_create_background_task(
            coordinator.hass, self._follow(started, until_soc), "gac_connect charge now")
        return response

    async def _follow(self, started: float, until_soc: bool) -> None:
        coordinator = self._coordinator
        try:
            for at in RESTART_READS:
                if not self._still_wanted():
                    break
                try:
                    async with asyncio.timeout(max(0.0, started + at - monotonic())):
                        await self._charging.wait()
                except TimeoutError:
                    await coordinator.async_refresh_now()
                if self._charging.is_set():
                    break
            if not self._still_wanted():
                _LOGGER.info("Charge now: another charging request followed, so the schedule is left as it is")
                return
            if not self._charging.is_set():
                _LOGGER.info("Charge now: the car did not report charging; switching the schedule off anyway")
            try:
                sent = await async_send(coordinator, "charge-now",
                                        coordinator.client.charge_now(coordinator.vin, until_soc=until_soc),
                                        still_wanted=self._still_wanted)
            except (GacError, HomeAssistantError) as err:
                _LOGGER.warning("Charge now could not switch the schedule off after starting the charge: %s", err)
                return
            if sent is None:
                _LOGGER.info("Charge now: another charging request followed, so the schedule is left as it is")
        finally:
            self._claimed = False
