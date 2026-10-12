"""Additional readings must remain read-only and respect location privacy."""
import asyncio
import importlib
import json
from unittest.mock import AsyncMock, Mock
from types import SimpleNamespace

import pytest
from homeassistant.components.device_tracker import SourceType

from custom_components.gac_connect.api import decode_status
from custom_components.gac_connect.binary_sensor import BINARY_SENSORS, GacBinaryEntity
from custom_components.gac_connect.device_tracker import GacDeviceTracker
from custom_components.gac_connect.climate import GacClimate
from custom_components.gac_connect.sensor import SENSORS, GacSensorEntity
from custom_components.gac_connect.switch import SWITCHES, GacCommandSwitch
from custom_components.gac_connect._vendor.gac_connect.errors import CommandError, RateLimitedError
from custom_components.gac_connect.command_tracking import async_send
from custom_components.gac_connect.diagnostics import async_get_config_entry_diagnostics
from custom_components.gac_connect.charging import schedule_attributes
from custom_components.gac_connect.config_flow import GacOptionsFlow
from test_autodetect import vehicle
from test_regressions import coord


def test_gps_sensor_keeps_the_tracker_and_exposes_coordinate_attributes():
    status = decode_status({'positionAccessors': [{'latitude': 12.3456789, 'longitude': 98.7654321}]})
    c = coord(status)
    tracker = GacDeviceTracker(c)
    gps = GacSensorEntity(c, next(d for d in SENSORS if d.key == 'gps_coordinates'))
    assert tracker.source_type is SourceType.GPS
    assert tracker.entity_category is gps.entity_category is None  # both appear in Sensors
    assert tracker.unique_id == 'TESTVIN_location' and gps.unique_id == 'TESTVIN_gps_coordinates'
    assert tracker.latitude == 12.3456789 and tracker.longitude == 98.7654321
    assert gps.native_value == '12.345679, 98.765432'
    assert gps.extra_state_attributes == {'latitude': 12.3456789, 'longitude': 98.7654321}
    c.data = decode_status({'positionAccessors': [{'latitude': 12.3456789}]})
    assert gps.native_value is None


@pytest.mark.parametrize('profile', ['auto_detect', 'aion_ut'])
@pytest.mark.parametrize('enabled', [False, True])
def test_coordinate_and_tracker_entities_share_privacy_setting(tmp_path, profile, enabled):
    async def run():
        async with vehicle(tmp_path, options={'profile': profile, 'enable_tracker': enabled}) as (hass, entry, c):
            added = []
            c.data = decode_status({'positionAccessors': [{'latitude': 12.3, 'longitude': 98.7}]})
            if c.capabilities:
                c.capabilities.observe(c.data)
            for platform in ('sensor', 'device_tracker'):
                await importlib.import_module(f'custom_components.gac_connect.{platform}').async_setup_entry(
                    hass, entry, lambda entities: added.extend(e.unique_id for e in entities))
            assert ('VIN_gps_coordinates' in added) is enabled
            assert ('VIN_location' in added) is enabled
            c.client.command.assert_not_awaited()
            diagnostics = await async_get_config_entry_diagnostics(hass, entry)
            assert '12.3' not in json.dumps(diagnostics)
            assert diagnostics['status_raw']['positionAccessors'][0]['latitude'] == '**REDACTED**'
    asyncio.run(run())


def test_seat_ventilation_and_anti_theft_expose_status_without_writable_entities():
    c = coord(decode_status({'seatTemperatureAccessors': [
        {'seat': 2, 'enableWind': 1, 'windStrength': 3, 'temperatureStrength': 0, 'temperatureMode': 0},
        {'seat': 1, 'enableWind': 0, 'windStrength': 0},
    ], 'antiTheftAccessors': [{'enable': 1}]}))
    seat = GacBinaryEntity(c, next(d for d in BINARY_SENSORS if d.key == 'seat_2_ventilation'))
    theft = GacBinaryEntity(c, next(d for d in BINARY_SENSORS if d.key == 'anti_theft'))
    assert seat.is_on is True and seat.extra_state_attributes['seat_id'] == '2'
    assert seat.extra_state_attributes['reported_wind_strength'] == 3
    assert seat.extra_state_attributes['reported_enable_wind'] == 1
    assert theft.is_on is True
    c.client.command.assert_not_awaited()


def test_unknown_seat_enable_code_is_exposed_without_inventing_smart_state():
    c = coord(decode_status({'seatTemperatureAccessors': [
        {'seat': 1, 'enableWind': 2, 'windStrength': 1, 'temperatureMode': 0},
    ]}))
    seat = GacBinaryEntity(c, next(d for d in BINARY_SENSORS if d.key == 'seat_1_ventilation'))
    assert seat.is_on is None
    assert seat.extra_state_attributes['reported_enable_wind'] == 2
    assert seat.extra_state_attributes['reported_wind_strength'] == 1
    assert seat.extra_state_attributes['reported_temperature_mode'] == 0
    c.client.command.assert_not_awaited()


@pytest.mark.parametrize('seat_id', [1, 2])
def test_seat_switches_use_separate_commands_and_correlate_only_their_session(tmp_path, seat_id):
    from custom_components.gac_connect._vendor.gac_connect.push import PushResult
    from homeassistant.exceptions import HomeAssistantError

    async def run():
        async with vehicle(tmp_path, options={'profile': 'aion_ut'}) as (hass, _, c):
            c.data = decode_status({'seatTemperatureAccessors': [{'seat': seat_id, 'enableWind': 1, 'windStrength': 0}]})
            sw = GacCommandSwitch(c, next(d for d in SWITCHES if d.key == f'seat_{seat_id}_ventilation'))
            sw.hass, sw.async_write_ha_state = hass, Mock()
            try:
                assert sw.is_on is False and sw.assumed_state is False
                await sw.async_turn_on()
                c.client.command.assert_awaited_once_with('VIN', f'seat-{seat_id}-ventilation-on')
                assert sw.is_on is True
                sw._handle_result(PushResult(False, '13101', None, {}, event='control_seat_temperature'))
                sw._handle_result(PushResult(False, '13101', None, {}, session_id='other-seat'))
                assert sw.is_on is True
                sw._handle_result(PushResult(False, '13101', None, {}, session_id='s1'))
                assert sw.is_on is False
                c.client.command.side_effect = CommandError('rejected')
                with pytest.raises(HomeAssistantError):
                    await sw.async_turn_on()
                assert sw.is_on is False
                c.client.command.side_effect = None
                await sw.async_turn_off()
                assert c.client.command.await_args.args == ('VIN', f'seat-{seat_id}-ventilation-off')
            finally:
                sw._cancel_expiry()
    asyncio.run(run())


def test_seat_controls_require_manual_or_successful_command_evidence_in_auto_profile(tmp_path):
    async def run():
        async with vehicle(tmp_path, options={'enable_entities': ['switch:seat_1_ventilation']}) as (_, _, c):
            c.data = decode_status({'seatTemperatureAccessors': [
                {'seat': 1, 'enableWind': 0}, {'seat': 2, 'enableWind': 0},
            ]})
            c.capabilities.observe(c.data)
            assert c.capabilities.includes('switch:seat_1_ventilation')
            assert not c.capabilities.includes('switch:seat_2_ventilation')
            c.capabilities.confirm_command('seat-2-ventilation-on')
            assert c.capabilities.includes('switch:seat_2_ventilation')
            assert all(not d.key.startswith(('seat_3_', 'seat_4_')) for d in SWITCHES)
            c.client.command.assert_not_awaited()
    asyncio.run(run())


@pytest.mark.parametrize('error,reason,code', [
    (CommandError('PRIVATE response', code=13101), 'gateway_rejected', '13101'),
    (CommandError('PRIVATE response', code='PRIVATE-TOKEN'), 'command_error', None),
    (RateLimitedError('PRIVATE response'), 'rate_limited', None),
])
def test_command_failures_keep_safe_reason_and_service_code(tmp_path, error, reason, code):
    async def run():
        async with vehicle(tmp_path) as (_, _, c):
            with pytest.raises(type(error)):
                await async_send(c, 'charge-window', AsyncMock(side_effect=error)())
            attrs = c.commands.attributes()
            assert attrs['failure_reason'] == reason and attrs['response_code'] == code
            assert attrs['recent_commands'][-1]['failure_reason'] == reason
            assert 'PRIVATE' not in json.dumps(attrs)
    asyncio.run(run())


def test_older_status_does_not_remove_climate_or_crash_the_charging_panel(tmp_path):
    """Reproduce the live partial-install failure with the older status schema."""
    async def run():
        async with vehicle(tmp_path, options={'profile': 'aion_ut'}) as (hass, entry, c):
            current = decode_status({'chargingAccessors': [{'chargingMode': 1,
                'dailyReservationStartTime': 79200000, 'dailyReservationStopTime': 21600000}],
                'airConditionAccessors': [{'temperature': 24, 'windStrength': 0, 'enableAirCompressor': 0}]})
            added_fields = {'outside_temp_c', 'ac_fan_strength', 'ac_auto_on', 'ac_compressor_on',
                            'ac_blowing_mode', 'ac_cycling_mode', 'seats', 'anti_theft_on',
                            'charge_end_type', 'charge_clock_consistent', 'charge_time_format'}
            c.data = SimpleNamespace(**{k: v for k, v in vars(current).items() if k not in added_fields})
            c.client.timezone = 'Asia/Bangkok'
            climate = GacClimate(c)
            assert climate.hvac_mode == 'off' and climate.target_temperature == 24
            assert climate.extra_state_attributes['reported_fan_strength'] is None
            for description in BINARY_SENSORS:
                entity = GacBinaryEntity(c, description)
                entity.is_on
                entity.extra_state_attributes
            for description in SENSORS:
                entity = GacSensorEntity(c, description)
                entity.native_value
                entity.extra_state_attributes
            assert schedule_attributes(c.data)['stopping_rule'] == 'Not reported'
            flow = GacOptionsFlow()
            flow.hass, flow.handler = hass, entry.entry_id
            result = await flow.async_step_charging()
            assert result['type'] == 'menu'
            assert '22:00' in result['description_placeholders']['schedule']
    asyncio.run(run())
