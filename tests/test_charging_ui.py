"""Exercise the schedule view and editor against the bundled command builder."""
import asyncio
from datetime import datetime
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
    client._charge_time_formats = {}
    client._charge_windows = {}
    client._reservation = AsyncMock(return_value=ACCEPTED)
    return flow, client


def test_view_prefills_the_reported_window_without_sending_a_command(tmp_path):
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
            suggestions = {key.schema: key.description.get('suggested_value')
                           for key in form['data_schema'].schema if key.schema in ('start', 'stop')}
            assert suggestions == {'start': '23:00', 'stop': '05:00'}
            assert form['data_schema'](suggestions)['days'] == ['mon', 'tue']
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
            assert response['type'] == 'menu' and response['step_id'] == 'charging'
            vin, operation = client._reservation.await_args.args
            assert vin == 'VIN'
            assert operation['weeklyReservation'] == 17
            assert operation['dailyReservationStartTime'] == 82800000
            assert operation['dailyReservationStopTime'] == 18000000
            assert operation['chargingMode'] == 2 and operation['chargingEndType'] == int(until_soc)
            start = datetime(*(operation['startTime'][k] for k in ('year', 'month', 'day', 'hour', 'minute')))
            assert start.weekday() in (0, 4) and (start.hour, start.minute) == (23, 0)  # dated, never undated
            assert c.commands.state == 'pending'  # acceptance did not fabricate a schedule
            assert c.data is None
            assert dict(entry.options) == before
            await flow.async_step_charging()
            await flow.async_step_charge_window()
            client._reservation.assert_awaited_once()  # navigating back must not replay the command
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
            c.data = decode_status({'chargingAccessors': [{'weeklyReservation': 3, 'chargingEndType': 1,
                'dailyReservationStartTime': 82800000, 'dailyReservationStopTime': 18000000}]})
            client._reservation.side_effect = CommandError('rejected')
            response = await flow.async_step_charge_window({'start': '22:00', 'stop': '06:00', 'days': [], 'until_soc': False})
            assert response['errors']['base'] == 'command_failed'
            values = response['data_schema']({'start': '22:00', 'stop': '06:00'})
            assert values['days'] == [] and values['until_soc'] is False
            assert {key.schema: key.description.get('suggested_value') for key in response['data_schema'].schema
                    if key.schema in ('start', 'stop')} == {'start': '22:00', 'stop': '06:00'}
            assert c.commands.state == 'failed'
            client._reservation.reset_mock(side_effect=True)
            await flow.async_step_charge_now()
            client._reservation.assert_not_awaited()
            response = await flow.async_step_charge_now({})
            assert response['type'] == 'menu' and response['step_id'] == 'charging'
            assert client._reservation.await_args.args[1]['chargingMode'] == 0
            client._reservation.assert_awaited_once()
    asyncio.run(run())


@pytest.mark.parametrize('weekly,days', [(0, []), (127, ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']), (64, ['sun'])])
def test_prefill_includes_reported_days_and_stopping_rule(tmp_path, weekly, days):
    async def run():
        async with vehicle(tmp_path) as (hass, entry, c):
            flow, client = setup_flow(hass, entry, c)
            c.data = decode_status({'chargingAccessors': [{'weeklyReservation': weekly, 'chargingEndType': 1,
                'startTime': {'year': 2026, 'month': 10, 'day': 11, 'hour': 22, 'minute': 8},
                'stopTime': {'year': 2026, 'month': 10, 'day': 12, 'hour': 6, 'minute': 10}}]})
            form = await flow.async_step_charge_window()
            suggestions = {key.schema: key.description.get('suggested_value') for key in form['data_schema'].schema
                           if key.schema in ('start', 'stop')}
            assert suggestions == {'start': '22:08', 'stop': '06:10'}
            values = form['data_schema'](suggestions)
            assert values['days'] == days and values['until_soc'] is True
            client._reservation.assert_not_awaited()
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


def test_real_ut_schedule_reads_calendar_fields_and_confirms_after_new_report(tmp_path):
    async def run():
        async with vehicle(tmp_path) as (hass, entry, c):
            flow, client = setup_flow(hass, entry, c)
            client._charge_time_formats['VIN'] = 'timestamp'
            raw = {'timezoneAccessor': {'timezone': 'Asia/Bangkok'}, 'chargingAccessors': [{
                'chargingMode': 1, 'weeklyReservation': 0, 'chargingEndType': 0,
                'dailyReservationStartTime': 1791727200000, 'dailyReservationStopTime': 1791756000000,
                'startTime': {'year': 2026, 'month': 10, 'day': 11, 'hour': 22, 'minute': 0},
                'stopTime': {'year': 2026, 'month': 10, 'day': 12, 'hour': 6, 'minute': 0},
            }]}
            c.data = decode_status(raw, updated_ms=1000)
            summary = (await flow.async_step_charging())['description_placeholders']['schedule']
            for value in ('22:00', '06:00', 'Stop time', 'disagree'):
                assert value in summary
            response = await flow.async_step_charge_window({'start': '23:00', 'stop': '07:00'})
            assert response['type'] == 'menu'
            operation = client._reservation.await_args.args[1]
            assert operation['dailyReservationStartTime'] > 86_400_000
            assert operation['dailyReservationStopTime'] > operation['dailyReservationStartTime']
            assert c.commands.state == 'pending'
            c.commands.observe(decode_status({'chargingAccessors': [operation]}, updated_ms=2000))
            assert c.commands.state == 'confirmed'
    asyncio.run(run())


def test_editor_one_time_window_and_prefill(tmp_path):
    async def run():
        async with vehicle(tmp_path) as (hass, entry, c):
            flow, client = setup_flow(hass, entry, c)
            c.data = decode_status({'chargingAccessors': [{'chargingMode': 1, 'dailyReservationStartTime': 82800000,
                                                           'dailyReservationStopTime': 18000000}]})
            form = await flow.async_step_charge_window()
            assert form['data_schema']({'start': '23:00', 'stop': '05:00'})['once'] is True
            rejected = await flow.async_step_charge_window(
                {'start': '23:00', 'stop': '05:00', 'days': ['mon'], 'until_soc': False, 'once': True})
            assert rejected['errors']['base'] == 'invalid_charge_window'
            client._reservation.assert_not_awaited()
            await flow.async_step_charge_window({'start': '23:00', 'stop': '05:00', 'days': [], 'once': True})
            operation = client._reservation.await_args.args[1]
            assert (operation['chargingMode'], operation['weeklyReservation']) == (1, 0)
    asyncio.run(run())
