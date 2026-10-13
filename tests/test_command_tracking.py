"""Command acknowledgement, result correlation and telemetry are distinct."""
import asyncio
from dataclasses import replace
import json
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.gac_connect.api import decode_status
from custom_components.gac_connect.command_tracking import async_send
from custom_components.gac_connect.const import CONF_EXPERIMENTAL_PINLESS_UNLOCK
from custom_components.gac_connect._vendor.gac_connect.errors import GacError
from test_autodetect import result, vehicle
from test_regressions import ACCEPTED


def test_early_results_require_exact_vehicle_and_session_then_wait_for_telemetry(tmp_path):
    async def run():
        async with vehicle(tmp_path) as (_, _, c):
            c.data = replace(decode_status({}), steering_heat_on=False, updated_ms=100)
            async def send():
                for message in (result('other'), result(vin='OTHER'), result()):
                    c.commands.result(message)
                return ACCEPTED
            await async_send(c, 'steering-on', send())
            assert c.commands.state == 'awaiting_report'
            c.commands.observe(replace(c.data))
            c.commands.observe(replace(c.data, steering_heat_on=True, updated_ms=99))
            assert c.commands.state == 'awaiting_report'
            c.commands.observe(replace(c.data, steering_heat_on=True))
            assert c.commands.state == 'confirmed'
            assert c.data.steering_heat_on is False  # tracker never invents sensor readings
            assert not c.commands.pending
            assert not c.commands.result(result())  # duplicates do not change the result
            encoded = json.dumps(c.commands.attributes())
            assert 'VIN' not in encoded and 's1' not in encoded
    asyncio.run(run())


@pytest.mark.parametrize('failure,state', [(GacError('secret response'), 'failed'), (asyncio.CancelledError(), 'cancelled')])
def test_failed_replacement_keeps_earlier_pending_request(tmp_path, failure, state):
    async def run():
        async with vehicle(tmp_path, options={CONF_EXPERIMENTAL_PINLESS_UNLOCK: True}) as (_, _, c):
            await async_send(c, 'lock', AsyncMock(return_value=ACCEPTED)())
            with pytest.raises(type(failure)):
                await async_send(c, 'unlock', AsyncMock(side_effect=failure)())
            assert c.commands.state == state
            assert [r.command for r in c.commands.pending] == ['lock']
            assert 'secret' not in json.dumps(c.commands.attributes())
    asyncio.run(run())


def test_concurrent_features_and_supersession_do_not_share_outcomes(tmp_path):
    async def run():
        async with vehicle(tmp_path, options={CONF_EXPERIMENTAL_PINLESS_UNLOCK: True}) as (_, _, c):
            for command, sid in [('lock', 'one'), ('horn-on', 'two'), ('unlock', 'three')]:
                await async_send(c, command, AsyncMock(return_value={'data': {'sessionId': sid}})())
            assert c.commands.records[0].state == 'superseded'
            c.commands.result(result('two'))
            assert c.commands.records[1].state == 'reported_success'
            assert c.commands.state == 'pending'
            c.commands.result(result('three', ok=False))
            assert c.commands.state == 'rejected'
            assert not c.commands.pending
    asyncio.run(run())


def test_timeout_and_cleanup_need_no_poll(tmp_path):
    async def run():
        async with vehicle(tmp_path, options={CONF_EXPERIMENTAL_PINLESS_UNLOCK: True}) as (hass, entry, c):
            with patch('custom_components.gac_connect.command_tracking.REQUEST_SECONDS', 0.02):
                await async_send(c, 'lock', AsyncMock(return_value=ACCEPTED)())
                await asyncio.sleep(0.05)
                assert c.commands.state == 'timed_out'
                assert c.commands._timer is None
            await async_send(c, 'unlock', AsyncMock(return_value=ACCEPTED)())
            await entry._async_process_on_unload(hass)
            assert c.commands.state == 'cancelled'
            assert c.commands._timer is None
            assert not c.commands.result(result())
            c.client.get_status.assert_not_awaited()
    asyncio.run(run())


def test_parameterized_cabin_request_requires_target_temperature(tmp_path):
    async def run():
        from custom_components.gac_connect.cabin import controller
        async with vehicle(tmp_path) as (_, _, c):
            c.client.climate_manual_on = AsyncMock(return_value=ACCEPTED)
            c.data = replace(decode_status({}), ac_on=False, ac_target_temp_c=24)
            cabin = controller(c)
            try:
                await cabin.async_set_temperature(22, on=True)
                c.commands.observe(replace(c.data, ac_on=True))
                assert c.commands.state == 'pending'
                c.commands.observe(replace(c.data, ac_on=True, ac_target_temp_c=22))
                assert c.commands.state == 'confirmed'
            finally:
                cabin._cancel_expiry()
    asyncio.run(run())


def test_front_seat_requests_confirm_independently_from_fresh_seat_reports(tmp_path):
    async def run():
        async with vehicle(tmp_path) as (_, _, c):
            def report(first, second, stamp):
                return decode_status({'seatTemperatureAccessors': [
                    {'seat': 2, 'enableWind': 1, 'windStrength': second},
                    {'seat': 1, 'enableWind': 1, 'windStrength': first},
                ]}, updated_ms=stamp)

            c.data = report(0, 0, 100)
            for seat in (1, 2):
                await async_send(c, f'seat-{seat}-ventilation-on',
                                 AsyncMock(return_value={'data': {'sessionId': f'seat{seat}'}})())
            assert [r.state for r in c.commands.records] == ['pending', 'pending']
            c.commands.result(result('seat2', ok=False))
            assert [r.state for r in c.commands.records] == ['pending', 'rejected']
            c.commands.observe(report(1, 0, 99))
            assert c.commands.records[0].state == 'pending'
            c.commands.observe(report(0, 1, 200))
            assert c.commands.records[0].state == 'pending'
            c.commands.observe(report(1, 0, 300))
            assert c.commands.records[0].state == 'confirmed'
            await async_send(c, 'seat-1-ventilation-off', AsyncMock(return_value=ACCEPTED)())
            c.commands.observe(report(0, 0, 400))
            assert c.commands.state == 'confirmed'
    asyncio.run(run())


@pytest.mark.parametrize('command', ['charge-resume', 'charge-window'])
@pytest.mark.parametrize('mode,confirmed', [(1, True), (2, True), (0, False)])
def test_resume_and_window_requests_confirm_with_once_or_repeating_reports(tmp_path, command, mode, confirmed):
    from custom_components.gac_connect.charging import window_target

    async def run():
        async with vehicle(tmp_path) as (_, _, c):
            c.data = decode_status({'chargingAccessors': [{'chargingMode': 0}]}, updated_ms=100)
            expected = window_target('23:00', '05:00', 127, False) if command == 'charge-window' else None
            await async_send(c, command, AsyncMock(return_value=ACCEPTED)(), expected=expected)
            c.commands.observe(decode_status({'chargingAccessors': [{
                'chargingMode': mode, 'dailyReservationStartTime': 82800000, 'dailyReservationStopTime': 18000000,
                'weeklyReservation': 127, 'chargingEndType': 0}]}, updated_ms=200))
            assert c.commands.state == ('confirmed' if confirmed else 'pending')
    asyncio.run(run())
