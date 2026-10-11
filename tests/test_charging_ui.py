"""Exercise the schedule view and editor against the bundled command builder."""
import asyncio
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from custom_components.gac_connect.api import GacClient, decode_status
from custom_components.gac_connect.config_flow import GacOptionsFlow
from custom_components.gac_connect.charging import schedule_attributes, schedule_mode
from custom_components.gac_connect._vendor.gac_connect.errors import CommandError
from test_autodetect import vehicle
from test_regressions import ACCEPTED


def setup_flow(hass, entry, coordinator):
    flow = GacOptionsFlow()
    flow.hass, flow.handler = hass, entry.entry_id
    client = coordinator.client = object.__new__(GacClient)
    client.timezone = 'Asia/Bangkok'
    client._tz = ZoneInfo(client.timezone)
    client._reservation = AsyncMock(return_value=ACCEPTED)
    return flow, client


def test_view_reports_partial_schedule_and_never_sends_or_prefills_unverified_times(tmp_path):
    async def run():
        async with vehicle(tmp_path) as (hass, entry, c):
            flow, client = setup_flow(hass, entry, c)
            c.data = decode_status({'chargingAccessors': [{'chargingMode': 0, 'dailyReservationStartTime': 82800000,
                'dailyReservationStopTime': 18000000, 'weeklyReservation': 3}]})
            page = await flow.async_step_charging()
            summary = page['description_placeholders']['schedule']
            for value in ('Unrestricted charging', '23:00', '05:00', 'Monday, Tuesday', 'Asia/Bangkok'):
                assert value in summary
            assert schedule_mode(c.data) == 'unrestricted'  # a stored window is not proof scheduled mode is active
            assert schedule_attributes(c.data)['reported_clock_verified'] is False
            form = await flow.async_step_charge_window()
            assert all(key.description.get('suggested_value') == '' for key in form['data_schema'].schema
                       if key.schema in ('start', 'stop'))
            await flow.async_step_charge_now()
            client._reservation.assert_not_awaited()
            assert c.commands.state == 'idle'
    asyncio.run(run())


@pytest.mark.parametrize('until_soc', [False, True])
def test_editor_sends_validated_overnight_window_with_selected_days(tmp_path, until_soc):
    async def run():
        async with vehicle(tmp_path) as (hass, entry, c):
            flow, client = setup_flow(hass, entry, c)
            before = dict(entry.options)
            form = await flow.async_step_charge_window()
            submitted = form['data_schema']({'start': '23:00', 'stop': '05:00', 'days': ['mon', 'fri'], 'until_soc': until_soc})
            response = await flow.async_step_charge_window(submitted)
            assert response['type'] == 'abort' and response['reason'] == 'command_sent'
            vin, operation = client._reservation.await_args.args
            assert vin == 'VIN'
            assert operation['weeklyReservation'] == 17
            assert operation['dailyReservationStartTime'] == 82800000
            assert operation['dailyReservationStopTime'] == 18000000
            assert operation['chargingMode'] == 1 and operation['chargingEndType'] == int(until_soc)
            assert c.commands.state == 'pending'  # acceptance did not fabricate a schedule
            assert c.data is None
            assert dict(entry.options) == before
    asyncio.run(run())


@pytest.mark.parametrize('start,stop', [('23:00', '23:00'), ('25:00', '05:00'), ('12:30:00', '05:00')])
def test_invalid_window_never_sends(tmp_path, start, stop):
    async def run():
        async with vehicle(tmp_path) as (hass, entry, c):
            flow, client = setup_flow(hass, entry, c)
            response = await flow.async_step_charge_window({'start': start, 'stop': stop})
            assert response['errors']['base'] == 'invalid_charge_window'
            client._reservation.assert_not_awaited()
    asyncio.run(run())


def test_rejected_window_keeps_input_and_charge_now_requires_submit(tmp_path):
    async def run():
        async with vehicle(tmp_path) as (hass, entry, c):
            flow, client = setup_flow(hass, entry, c)
            client._reservation.side_effect = CommandError('rejected')
            response = await flow.async_step_charge_window({'start': '22:00', 'stop': '06:00', 'days': ['sun']})
            assert response['errors']['base'] == 'command_failed'
            assert response['data_schema']({'start': '22:00', 'stop': '06:00'})['days'] == ['sun']
            assert c.commands.state == 'failed'
            client._reservation.reset_mock(side_effect=True)
            await flow.async_step_charge_now()
            client._reservation.assert_not_awaited()
            await flow.async_step_charge_now({})
            assert client._reservation.await_args.args[1]['chargingMode'] == 0
    asyncio.run(run())


def test_missing_schedule_stays_unknown_and_refresh_is_explicit(tmp_path):
    async def run():
        async with vehicle(tmp_path) as (hass, entry, c):
            flow = GacOptionsFlow()
            flow.hass, flow.handler = hass, entry.entry_id
            c.client.timezone = 'Asia/Bangkok'
            assert 'Not reported' in (await flow.async_step_charging())['description_placeholders']['schedule']
            c.client.get_status.assert_not_awaited()
            await flow.async_step_refresh_charging()
            c.client.get_status.assert_awaited_once()
            assert schedule_attributes(c.data)['reported_start'] is None
            del entry.runtime_data
            for step in (flow.async_step_charging, flow.async_step_charge_window, flow.async_step_charge_now):
                assert (await step())['reason'] == 'vehicle_not_loaded'
    asyncio.run(run())
