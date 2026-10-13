"""Fixed-level seat fans are stopped once the car stays locked and set back at the next unlock."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.gac_connect.api import decode_status
from custom_components.gac_connect.seat_fans import LOCKED_SECONDS, SeatFanGuard
from custom_components.gac_connect._vendor.gac_connect.errors import CommandError
from test_autodetect import vehicle
from test_regressions import ACCEPTED


def report(locked, seat1=0, seat2=0, *, stamp=1):
    """A report with each front seat's level (0 off) and all four doors locked or unlocked."""
    return decode_status({
        "doorAccessors": [{"door": door, "lock": 0 if locked else 1} for door in (1, 2, 3, 4)],
        "seatTemperatureAccessors": [{"seat": seat, "enableWind": int(level > 0), "windStrength": level}
                                     for seat, level in ((1, seat1), (2, seat2))],
    }, updated_ms=stamp)


class Harness:
    def __init__(self, hass, entry, coordinator):
        self.hass, self.c = hass, coordinator
        self.now, self.timers = 1000.0, []
        coordinator.client.set_seat_ventilation_level = AsyncMock(return_value=ACCEPTED)
        coordinator.async_manual_refresh = AsyncMock()
        coordinator.async_schedule_command_refresh = Mock()   # no delayed follow-up reads
        self.guard = SeatFanGuard(hass, entry, coordinator)
        self.guard.async_setup()

    def later(self, hass, delay, callback):
        timer = SimpleNamespace(delay=delay, callback=callback, cancelled=False)
        self.timers.append(timer)
        return lambda: setattr(timer, "cancelled", True)

    async def at(self, seconds, status):
        self.now = 1000.0 + seconds
        self.c.async_set_updated_data(status)
        await self.hass.async_block_till_done(wait_background_tasks=True)

    @property
    def sent(self):
        return [call.args[1:] for call in self.c.client.set_seat_ventilation_level.await_args_list]


def run(tmp_path, scenario):
    async def main():
        async with vehicle(tmp_path) as (hass, entry, c):
            h = Harness(hass, entry, c)
            with patch("custom_components.gac_connect.seat_fans.monotonic", lambda: h.now), \
                    patch("custom_components.gac_connect.seat_fans.async_call_later", h.later):
                await scenario(h)
    asyncio.run(main())


def test_fixed_levels_are_stopped_after_the_wait_and_restored_on_unlock(tmp_path):
    async def scenario(h):
        await h.at(0, report(False, 2, 0))
        await h.at(10, report(True, 2, 1))             # just locked; seat 2 is on Auto
        await h.at(70, report(True, 2, 1))
        assert h.sent == [] and [t.delay for t in h.timers] == [LOCKED_SECONDS]
        await h.at(LOCKED_SECONDS + 5, report(True, 2, 0))   # Auto stopped by itself
        assert h.sent == []                            # not yet locked for the full wait
        await h.at(LOCKED_SECONDS + 15, report(True, 2, 0))
        assert h.sent == [(1, 0)] and h.timers[0].cancelled
        await h.at(LOCKED_SECONDS + 75, report(True, 0, 0))
        assert h.sent == [(1, 0)]                      # checked once per locked period
        await h.at(900, report(False, 0, 0))
        assert h.sent == [(1, 0), (1, 2)]              # only the stopped seat is set back
        await h.at(960, report(False, 0, 0))
        assert len(h.sent) == 2
    run(tmp_path, scenario)


def test_wait_ends_with_one_refresh_even_without_polling(tmp_path):
    async def scenario(h):
        await h.at(0, report(True, 3, 0))
        await h.at(30, report(True, 3, 0))
        assert len(h.timers) == 1 and h.sent == []     # no second timer while one is armed
        h.now = 1000.0 + LOCKED_SECONDS
        h.timers[0].callback(None)
        await h.hass.async_block_till_done(wait_background_tasks=True)
        h.c.async_manual_refresh.assert_awaited_once()
    run(tmp_path, scenario)


def test_fans_chosen_while_locked_or_since_unlocking_are_left_alone(tmp_path):
    async def scenario(h):
        await h.at(0, report(True, 0, 0))
        await h.at(LOCKED_SECONDS, report(True, 0, 0))
        await h.at(LOCKED_SECONDS + 60, report(True, 3, 3))   # turned on from HA while parked
        assert h.sent == [] and h.timers == []
        await h.at(600, report(False, 3, 3))
        await h.at(660, report(True, 1, 0))
        await h.at(660 + LOCKED_SECONDS, report(True, 1, 0))
        assert h.sent == [(1, 0)]
        await h.at(1200, report(False, 2, 0))           # already running again at unlock
        assert h.sent == [(1, 0)]
    run(tmp_path, scenario)


@pytest.mark.parametrize("failure", [CommandError("refused"), None])
def test_a_seat_that_was_not_stopped_is_not_restored(tmp_path, failure):
    async def scenario(h):
        if failure is not None:
            h.c.client.set_seat_ventilation_level.side_effect = failure
        await h.at(0, report(True, 1, 0))
        await h.at(LOCKED_SECONDS, report(True, 1, 0))
        await h.at(600, report(False, 0, 0))
        assert h.sent == ([(1, 0)] if failure else [(1, 0), (1, 1)])
    run(tmp_path, scenario)


def test_unknown_lock_state_changes_nothing(tmp_path):
    async def scenario(h):
        unknown = decode_status({"seatTemperatureAccessors": [{"seat": 1, "enableWind": 1, "windStrength": 2}]})
        await h.at(0, unknown)
        await h.at(LOCKED_SECONDS * 2, unknown)
        assert h.sent == [] and h.timers == []
    run(tmp_path, scenario)
