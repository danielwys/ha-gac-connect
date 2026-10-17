"""Charge now also starts a new charge once one has ended (a one-time window, then the schedule off)."""
import asyncio
from unittest.mock import AsyncMock, patch

from custom_components.gac_connect.api import decode_status
from custom_components.gac_connect.charge_now import ChargeNow, charge_now_request
from custom_components.gac_connect.command_tracking import async_send
from custom_components.gac_connect.settings import ChargeDraft
from test_autodetect import vehicle
from test_regressions import ACCEPTED

WINDOW = {"dailyReservationStartTime": 1792207200000, "dailyReservationStopTime": 1792207320000}


def report(status, mode, plug=1, **charging):
    return decode_status({"chargingAccessors": [{"chargingStatus": status, "chargingMode": mode, **WINDOW, **charging}],
                          "chargerAccessors": [{"plug": plug, "lock": 0}]})


def prepared(entry, c, status):
    c.client.charge_now = AsyncMock(return_value=ACCEPTED)
    c.client.restart_charging = AsyncMock(return_value=ACCEPTED)
    c.async_refresh_now = AsyncMock()
    entry.runtime_data.charge_draft = ChargeDraft()
    helper = entry.runtime_data.charge_now = ChargeNow(c)
    c.async_set_updated_data(status)
    return helper


def test_charge_now_only_switches_the_schedule_off_when_the_car_can_charge(tmp_path):
    async def run():
        async with vehicle(tmp_path) as (hass, entry, c):
            prepared(entry, c, report(27, 2))       # waiting for a repeating schedule
            with patch("custom_components.gac_connect.command_tracking.POLL_DELAYS", ()):
                await async_send(c, "charge-now", charge_now_request(c))
            c.client.charge_now.assert_awaited_once_with("VIN", until_soc=False)
            c.client.restart_charging.assert_not_awaited()
    asyncio.run(run())


def test_an_ended_charge_is_started_again_then_the_schedule_switched_off(tmp_path):
    async def run():
        async with vehicle(tmp_path) as (hass, entry, c):
            helper = prepared(entry, c, report(4, 0))
            entry.async_on_unload(c.async_add_listener(lambda: helper.observe(c.data)))
            with patch("custom_components.gac_connect.command_tracking.POLL_DELAYS", ()), \
                    patch("custom_components.gac_connect.charge_now.RESTART_READS", (0.05, 5)):
                await async_send(c, "charge-now", charge_now_request(c))
                c.client.restart_charging.assert_awaited_once_with("VIN")
                c.client.charge_now.assert_not_awaited()
                assert helper.restarting
                assert await charge_now_request(c) is None      # a second press sends nothing more
                c.async_set_updated_data(report(2, 1))           # charging from the one-time window
                await asyncio.wait_for(helper._task, 1)
            c.client.charge_now.assert_awaited_once_with("VIN", until_soc=False)
            assert c.client.restart_charging.await_count == 1
    asyncio.run(run())


def test_without_a_charging_report_the_schedule_is_switched_off_anyway(tmp_path):
    async def run():
        async with vehicle(tmp_path) as (hass, entry, c):
            helper = prepared(entry, c, report(27, 1, dailyReservationStartTime=0, dailyReservationStopTime=0))
            with patch("custom_components.gac_connect.command_tracking.POLL_DELAYS", ()), \
                    patch("custom_components.gac_connect.charge_now.RESTART_READS", (0.01, 0.02)):
                await async_send(c, "charge-now", charge_now_request(c))   # "not set": restarted
                await asyncio.wait_for(helper._task, 1)
            assert c.async_refresh_now.await_count == 2
            c.client.charge_now.assert_awaited_once()
    asyncio.run(run())


def test_a_later_charging_request_cancels_the_switch_off(tmp_path):
    async def run():
        async with vehicle(tmp_path) as (hass, entry, c):
            helper = prepared(entry, c, report(4, 1))
            c.client.resume_schedule = AsyncMock(return_value=ACCEPTED)
            with patch("custom_components.gac_connect.command_tracking.POLL_DELAYS", ()), \
                    patch("custom_components.gac_connect.charge_now.RESTART_READS", (0.05,)):
                await async_send(c, "charge-now", charge_now_request(c))
                await async_send(c, "charge-resume", c.client.resume_schedule("VIN"))
                await asyncio.wait_for(helper._task, 1)
            c.client.charge_now.assert_not_awaited()
    asyncio.run(run())


def test_the_restart_window_does_not_replace_the_dashboard_draft():
    draft = ChargeDraft()
    draft.set(start="22:00", stop="06:00", days={"sat"})
    draft._reported = ("11:00", "11:11", 0, 0)
    draft.mark_sent(None, None, 0, True)
    draft.observe(decode_status({"chargingAccessors": [{"chargingMode": 1, "chargingEndType": 1, "weeklyReservation": 0,
                                                        "dailyReservationStartTime": 1792209600000,
                                                        "dailyReservationStopTime": 1792209600000}]}))
    assert (draft.start, draft.stop, draft.days) == ("22:00", "06:00", {"sat"})


def test_two_presses_at_once_send_one_restart(tmp_path):
    async def run():
        async with vehicle(tmp_path) as (hass, entry, c):
            helper = prepared(entry, c, report(4, 0))
            gate = asyncio.Event()

            async def slow_restart(vin):
                await gate.wait()
                return ACCEPTED
            c.client.restart_charging.side_effect = slow_restart
            with patch("custom_components.gac_connect.command_tracking.POLL_DELAYS", ()), \
                    patch("custom_components.gac_connect.charge_now.RESTART_READS", (5,)):
                first = asyncio.create_task(async_send(c, "charge-now", charge_now_request(c)))
                second = asyncio.create_task(async_send(c, "charge-now", charge_now_request(c)))
                await asyncio.sleep(0.01)
                gate.set()
                await asyncio.gather(first, second)
                assert c.client.restart_charging.await_count == 1 and helper.restarting
                helper._task.cancel()
    asyncio.run(run())


def test_a_newer_schedule_made_while_the_switch_off_waits_its_turn_is_kept(tmp_path):
    async def run():
        async with vehicle(tmp_path) as (hass, entry, c):
            helper = prepared(entry, c, report(4, 0))
            c.client.set_charge_window = AsyncMock(return_value=ACCEPTED)
            entry.async_on_unload(c.async_add_listener(lambda: helper.observe(c.data)))
            with patch("custom_components.gac_connect.command_tracking.POLL_DELAYS", ()), \
                    patch("custom_components.gac_connect.charge_now.RESTART_READS", (0.05, 5)):
                await async_send(c, "charge-now", charge_now_request(c))
                gate = asyncio.Event()

                async def slow_window(*args, **kwargs):
                    await gate.wait()
                    return ACCEPTED
                c.client.set_charge_window.side_effect = slow_window
                newer = asyncio.create_task(async_send(c, "charge-window", c.client.set_charge_window(
                    "VIN", "23:00", "05:00", weekly=127, until_soc=False)))
                await asyncio.sleep(0.01)                        # the newer window is on its way out
                c.async_set_updated_data(report(2, 1))           # charging: the switch-off comes due and waits
                await asyncio.sleep(0.01)
                gate.set()
                await newer
                await asyncio.wait_for(helper._task, 1)
            c.client.set_charge_window.assert_awaited_once()
            c.client.charge_now.assert_not_awaited()             # the newer schedule is not switched off
            assert not helper.restarting
    asyncio.run(run())


def test_a_newer_schedule_cannot_overtake_a_switch_off_already_on_its_way(tmp_path):
    async def run():
        async with vehicle(tmp_path) as (hass, entry, c):
            helper = prepared(entry, c, report(4, 0))
            order, gate = [], asyncio.Event()

            async def slow_off(vin, until_soc=False):
                await gate.wait()                                 # waiting for its turn with the service
                order.append("schedule off")
                return ACCEPTED

            async def window(*args, **kwargs):
                order.append("newer window")
                return ACCEPTED
            c.client.set_charge_window = AsyncMock(side_effect=window)
            entry.async_on_unload(c.async_add_listener(lambda: helper.observe(c.data)))
            with patch("custom_components.gac_connect.command_tracking.POLL_DELAYS", ()), \
                    patch("custom_components.gac_connect.charge_now.RESTART_READS", (0.05, 5)):
                await async_send(c, "charge-now", charge_now_request(c))
                c.client.charge_now.side_effect = slow_off
                c.async_set_updated_data(report(2, 1))
                await asyncio.sleep(0.01)                        # the switch-off is being sent
                newer = asyncio.create_task(async_send(c, "charge-window", c.client.set_charge_window(
                    "VIN", "23:00", "05:00", weekly=127, until_soc=False)))
                await asyncio.sleep(0.01)
                gate.set()
                await newer
                await asyncio.wait_for(helper._task, 1)
            assert order == ["schedule off", "newer window"]   # the newer choice is the one left in place
    asyncio.run(run())
