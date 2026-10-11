"""Bounded post-command reads shared by every control and its reported sensors."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.gac_connect.api import decode_status
from custom_components.gac_connect.binary_sensor import BINARY_SENSORS, GacBinaryEntity
from custom_components.gac_connect.capabilities import async_send
from custom_components.gac_connect._vendor.gac_connect.errors import GacError, RateLimitedError
from test_autodetect import vehicle
from test_regressions import ACCEPTED


class Clock:
    now = 0.0

    def monotonic(self):
        return self.now

    async def sleep(self, seconds):
        self.now += seconds


def clock_patch(clock):
    # Replace only this module's asyncio reference; HA keeps its real event loop.
    return patch("custom_components.gac_connect.coordinator.asyncio",
                 SimpleNamespace(Lock=asyncio.Lock, gather=asyncio.gather, sleep=clock.sleep))


def test_accepted_commands_and_results_share_three_delayed_reads(tmp_path):
    async def run():
        async with vehicle(tmp_path) as (_, _, coordinator):
            clock = Clock()
            times = []

            async def fetch(_vin):
                times.append(clock.now)
                return decode_status({})

            coordinator.client.get_status.side_effect = fetch
            with patch.object(coordinator, "_start_result_refresh") as start, clock_patch(clock), \
                    patch("custom_components.gac_connect.coordinator.monotonic", clock.monotonic):
                for command in ("lock", "sunroof-close", "aircon-on", "fridge-on"):
                    await async_send(coordinator, command, AsyncMock(return_value=ACCEPTED)())
                    coordinator._schedule_result_refresh()
                coordinator.client.get_status.assert_not_awaited()  # Never immediately after acceptance.
                assert start.called
                await coordinator._result_refresh()
                assert times == [5, 15, 30]
                assert coordinator._refresh_deadlines == []
    asyncio.run(run())


def test_a_new_command_during_a_read_retains_its_followups(tmp_path):
    async def run():
        async with vehicle(tmp_path) as (_, _, coordinator):
            clock = Clock()
            times = []

            async def fetch(_vin):
                times.append(clock.now)
                if len(times) == 1:
                    coordinator.async_schedule_command_refresh()
                return decode_status({})

            coordinator.client.get_status.side_effect = fetch
            with patch.object(coordinator, "_start_result_refresh"), clock_patch(clock), \
                    patch("custom_components.gac_connect.coordinator.monotonic", clock.monotonic):
                coordinator.async_schedule_command_refresh()
                await coordinator._result_refresh()
            assert times == [5, 15, 25, 35]
            assert all(later - earlier >= 10 for earlier, later in zip(times, times[1:]))
    asyncio.run(run())


def test_scheduled_and_manual_reads_also_space_followup_requests(tmp_path):
    async def run():
        async with vehicle(tmp_path) as (_, _, coordinator):
            clock = Clock()
            times = []

            async def fetch(_vin):
                times.append(clock.now)
                return decode_status({})

            coordinator.client.get_status.side_effect = fetch
            with patch.object(coordinator, "_start_result_refresh"), clock_patch(clock), \
                    patch("custom_components.gac_connect.coordinator.monotonic", clock.monotonic):
                coordinator.async_schedule_command_refresh()
                clock.now = 4
                await coordinator.async_refresh()
                await coordinator._result_refresh()
            assert times == [4, 14, 24, 34]
    asyncio.run(run())


def test_fresh_reports_update_related_sensors_even_during_quiet_hours(tmp_path):
    async def run():
        async with vehicle(tmp_path, model="Aion UT") as (_, _, coordinator):
            clock = Clock()
            locked = decode_status({"doorAccessors": [
                {"door": i, "lock": 0 if i < 3 else -1} for i in range(1, 5)
            ]})
            unlocked = decode_status({"doorAccessors": [{"door": 1, "lock": 1}]})
            coordinator.data = locked
            sensor = GacBinaryEntity(coordinator, next(d for d in BINARY_SENSORS if d.key == "locked"))
            coordinator.client.get_status.side_effect = [locked, locked, unlocked]
            with patch.object(coordinator, "_start_result_refresh"), \
                    patch.object(coordinator, "_in_quiet_hours", return_value=True), clock_patch(clock), \
                    patch("custom_components.gac_connect.coordinator.monotonic", clock.monotonic):
                coordinator.async_schedule_command_refresh()
                assert sensor.is_on is False  # Acceptance never fabricates a sensor reading.
                await coordinator._result_refresh()
                assert sensor.is_on is True
                assert coordinator.client.get_status.await_count == 3
    asyncio.run(run())


def test_a_rate_limit_stops_followups_and_preserves_the_last_report(tmp_path):
    async def run():
        async with vehicle(tmp_path) as (_, _, coordinator):
            clock = Clock()
            previous = coordinator.data = decode_status({})
            coordinator.client.get_status.side_effect = RateLimitedError("wait")
            with patch.object(coordinator, "_start_result_refresh"), clock_patch(clock), \
                    patch("custom_components.gac_connect.coordinator.monotonic", clock.monotonic):
                coordinator.async_schedule_command_refresh()
                await coordinator._result_refresh()
                assert coordinator.data is previous
                assert coordinator.client.get_status.await_count == 1
                assert coordinator._refresh_deadlines == []
    asyncio.run(run())


@pytest.mark.parametrize("failure", [GacError("offline"), asyncio.CancelledError()])
def test_failed_or_cancelled_commands_do_not_schedule_reads(failure):
    coordinator = SimpleNamespace(capabilities=None, async_schedule_command_refresh=Mock())
    with pytest.raises(type(failure)):
        asyncio.run(async_send(coordinator, "lock", AsyncMock(side_effect=failure)()))
    coordinator.async_schedule_command_refresh.assert_not_called()


def test_unload_cancels_followups_and_prevents_late_scheduling(tmp_path):
    async def run():
        async with vehicle(tmp_path) as (hass, entry, coordinator):
            coordinator.async_schedule_command_refresh()
            first = coordinator._result_refresh_task
            coordinator.async_schedule_command_refresh()
            coordinator._schedule_result_refresh()
            assert coordinator._result_refresh_task is first
            await entry._async_process_on_unload(hass)
            await asyncio.sleep(0)
            assert first.done()
            assert coordinator._refresh_deadlines == []
            coordinator.async_schedule_command_refresh()
            assert coordinator._refresh_deadlines == []
            coordinator.client.get_status.assert_not_awaited()
    asyncio.run(run())
