"""Optionally stop front seat fans left on a fixed level in a locked car, and restore them on unlock."""
from __future__ import annotations

import logging
from time import monotonic

from homeassistant.core import CALLBACK_TYPE, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_call_later

from ._vendor.gac_connect import GacError
from .api import doors_locked
from .command_tracking import async_send
from .seats import reported_seat

_LOGGER = logging.getLogger(__name__)

# An Aion UT stops seats on Auto by itself once the driver leaves, but keeps a fixed
# level (Low, Medium or High) running in the locked car. A fan still running this
# long after locking is therefore on a fixed level.
LOCKED_SECONDS = 180
FRONT_SEATS = (1, 2)


def fixed_levels(status) -> dict[int, int]:
    """Front seats whose fans report running, with their level 1–3."""
    levels = {}
    for seat_id in FRONT_SEATS:
        seat = reported_seat(status, seat_id)
        strength = getattr(seat, "ventilation_strength", None)
        if getattr(seat, "ventilation_on", None) is True and strength in (1, 2, 3):
            levels[seat_id] = int(strength)
    return levels


class SeatFanGuard:
    """Once per locked period, turn off fixed-level seat fans; set them back at the next unlock.

    Locking and unlocking are seen in status reports. A running fan seen soon after
    locking schedules one extra refresh at the end of the wait, so the check also
    happens when polling is slow or paused for quiet hours. The levels to restore
    are kept in memory only, so after a restart the fans simply stay off.
    """

    def __init__(self, hass, entry, coordinator) -> None:
        self.hass, self.entry, self.coordinator = hass, entry, coordinator
        self._model = entry.data.get("model")
        self._locked_since: float | None = None
        self._handled = False            # this locked period has been checked
        self._saved: dict[int, int] = {}  # seat -> level to restore at the next unlock
        self._timer: CALLBACK_TYPE | None = None

    def async_setup(self) -> None:
        self.entry.async_on_unload(self.coordinator.async_add_listener(self._update))
        self.entry.async_on_unload(self._cancel_timer)

    @callback
    def _cancel_timer(self) -> None:
        if self._timer is not None:
            self._timer()
            self._timer = None

    @callback
    def _update(self) -> None:
        status = self.coordinator.data
        locked = doors_locked(status, self._model)
        if locked is None:
            return
        if not locked:
            self._locked_since, self._handled = None, False
            self._cancel_timer()
            if self._saved:
                saved, self._saved = self._saved, {}
                self._run(self._restore(saved), "restore")
            return
        if self._locked_since is None:
            self._locked_since = monotonic()
        if self._handled:
            return
        waited = monotonic() - self._locked_since
        if waited >= LOCKED_SECONDS:
            self._handled = True
            self._cancel_timer()
            if levels := fixed_levels(status):
                self._run(self._stop(levels), "stop")
        elif self._timer is None and fixed_levels(status):
            self._timer = async_call_later(self.hass, LOCKED_SECONDS - waited, self._recheck)

    @callback
    def _recheck(self, _now) -> None:
        self._timer = None
        self._run(self.coordinator.async_manual_refresh(), "refresh")

    def _run(self, coro, purpose: str) -> None:
        self.entry.async_create_background_task(self.hass, coro, f"gac_connect seat fans {purpose}")

    async def _stop(self, levels: dict[int, int]) -> None:
        for seat, level in levels.items():
            if await self._set(seat, 0) and self._locked_since is not None:
                self._saved[seat] = level

    async def _restore(self, saved: dict[int, int]) -> None:
        for seat, level in saved.items():
            if getattr(reported_seat(self.coordinator.data, seat), "ventilation_on", None) is True:
                continue   # already running: someone chose a setting since unlocking
            await self._set(seat, level)

    async def _set(self, seat: int, level: int) -> bool:
        client, vin = self.coordinator.client, self.coordinator.vin
        try:
            await async_send(self.coordinator, f"seat-{seat}-ventilation-{'on' if level else 'off'}",
                             client.set_seat_ventilation_level(vin, seat, level))
        except (GacError, HomeAssistantError) as err:
            _LOGGER.warning("could not %s seat %s ventilation: %s", "restore" if level else "stop", seat, err)
            return False
        return True
