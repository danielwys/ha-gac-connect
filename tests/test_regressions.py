import asyncio
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from zoneinfo import ZoneInfo

import pytest
from custom_components.gac_connect._vendor.gac_connect.commands import CATALOG
from custom_components.gac_connect._vendor.gac_connect.models import ChargingMode, VehicleStatus
from custom_components.gac_connect._vendor.gac_connect.push import PushResult
from homeassistant.components.climate import HVACMode

from custom_components.gac_connect.binary_sensor import BINARY_SENSORS, GacBinaryEntity
from custom_components.gac_connect.api import GacClient, decode_status, window_operation
from custom_components.gac_connect.climate import GacClimate
from custom_components.gac_connect.coordinator import GacCoordinator
from custom_components.gac_connect.cover import COVERS, GacCoverEntity
from custom_components.gac_connect.fridge import FridgeController
from custom_components.gac_connect.lock_state import doors_locked
from custom_components.gac_connect.sensor import SENSORS, GacSensorEntity, _tyre_sensors
from custom_components.gac_connect.switch import (
    SWITCHES,
    GacChargeSwitch,
    GacClimateSwitch,
)

ACCEPTED = {"code": 13001, "data": {"identifier": {"sessionId": "s1"}}}


def coord(data):
    client = object.__new__(GacClient)
    client.command = AsyncMock(return_value=ACCEPTED)
    return SimpleNamespace(
        hass=Mock(),
        data=data,
        vin="TESTVIN",
        client=client,
        last_update_success=True,
        config_entry=SimpleNamespace(entry_id="test", data={"model": "Aion V", "vin": "TESTVIN"}, options={}),
        async_request_refresh=AsyncMock(),
        async_schedule_command_refresh=Mock(),
    )


def climate(data):
    c = coord(data)
    e = GacClimate(c)
    e.async_write_ha_state = Mock()
    return c, e


@pytest.fixture(autouse=True)
def scheduled(monkeypatch):
    timers = []

    def later(hass, delay, callback):
        timer = SimpleNamespace(delay=delay, callback=callback, cancel=Mock())
        timers.append(timer)
        return timer.cancel

    monkeypatch.setattr("custom_components.gac_connect.cabin.async_call_later", later)
    monkeypatch.setattr("custom_components.gac_connect.switch.async_call_later", later)
    monkeypatch.setattr("custom_components.gac_connect.transition.async_call_later", later)
    return timers


@pytest.mark.parametrize("code,expected", [(0, False), (2, True), (None, None)])
def test_known_charging_states(code, expected):
    assert decode_status({"chargingAccessors": [{"chargingStatus": code}]}).charging is expected


@pytest.mark.parametrize(
    "model,codes,expected",
    [
        ("Aion V", [0, 0, 0, 0], True),
        ("Aion V", [0, 1, 0, 0], False),
        ("Aion V", [0, 0, -1, -1], None),
        ("Aion UT", [0, 0, -1, -1], True),
        (" AION UT Premium ", [0, 0, -1, -1], True),
        ("Aion Utopia", [0, 0, -1, -1], None),
        (None, [0, 0, -1, -1], None),
        ("Aion UT", [1, 0, -1, -1], False),
        ("Aion V", ["0"] * 4, True),
    ],
)
def test_lock_decoding(model, codes, expected):
    s = decode_status({"doorAccessors": [{"door": i + 1, "lock": v} for i, v in enumerate(codes)]})
    assert doors_locked(s, model) is expected


def test_sensor_values_and_units_are_consistent():
    s = decode_status(
        {
            "drivingOverviewAccessors": [
                {"remainElectricityPercentage": 0.65, "remainVoyageCourse": 301, "totalVoyageCourse": 12345}
            ],
            "chargingAccessors": [{"chargingVoltage": 400, "chargingCurrent": 20, "estimatedChargedDuration": 90}],
            "environmentAccessors": [{"temperatureInsideCar": 28, "pm25": 12}],
            "batteryAccessors": [{"voltage": 12.8}],
        },
        updated_ms=1791504000000,
    )
    expected = {
        "soc": 65,
        "range": 301,
        "odometer": 12345,
        "charge_voltage": 400,
        "charge_current": 20,
        "charge_power": 8,
        "charge_time": 90,
        "cabin_temp": 28,
        "pm25": 12,
        "aux_voltage": 12.8,
    }
    for d in SENSORS:
        if d.key in expected:
            assert d.value(s) == expected[d.key]
        if d.key == "last_report":
            assert d.value(s).tzinfo is not None


def test_control_catalog_names_exist():
    for d in (*SWITCHES, *COVERS):
        for attr in ("on_cmd", "off_cmd", "open_cmd", "close_cmd"):
            if hasattr(d, attr):
                assert getattr(d, attr) in CATALOG


def test_climate_valid_start_and_rounding():
    c, e = climate(VehicleStatus(ac_on=False, ac_target_temp_c=24))

    async def run():
        await e.async_set_temperature(temperature=22.3)
        await e.async_turn_on()

    asyncio.run(run())
    c.client.command.assert_awaited_once_with("TESTVIN", "aircon-on", temperature=22.5, currentDuration=30)


def test_combined_temperature_service_respects_requested_mode():
    c, e = climate(VehicleStatus(ac_on=False, ac_target_temp_c=24))
    asyncio.run(e.async_set_temperature(temperature=22, hvac_mode=HVACMode.HEAT_COOL))
    assert c.client.command.await_count == 1, "Combined mode/temperature call silently leaves climate off"
    assert e.hvac_mode == HVACMode.HEAT_COOL


def test_climate_unknown_setpoint_uses_valid_default():
    c, e = climate(VehicleStatus(ac_on=False, ac_target_temp_c=-1))
    asyncio.run(e.async_turn_on())
    c.client.command.assert_awaited_once_with("TESTVIN", "aircon-on", temperature=24, currentDuration=30)


def test_offline_target_survives_status_poll():
    c, e = climate(VehicleStatus(ac_on=False, ac_target_temp_c=24))
    with patch("custom_components.gac_connect.cabin.monotonic", return_value=100):
        asyncio.run(e.async_set_temperature(temperature=22))
    with patch("custom_components.gac_connect.cabin.monotonic", return_value=401):
        e._handle_coordinator_update()
    assert e.target_temperature == 22, "Unsent target is erased at next default-interval poll"


def test_temperature_rejection_clears_requested_target():
    c, e = climate(VehicleStatus(ac_on=True, ac_target_temp_c=24))
    asyncio.run(e.async_set_temperature(temperature=22))
    e._handle_coordinator_update()  # an old report confirms only the on/off portion
    e._cabin._handle_result(PushResult(False, "13101", None, {}, session_id="s1", event="control_air_condition"))
    assert e.target_temperature == 24, "Refusal ignored after ac_on clears pending flag"


def test_climate_and_power_switch_share_selected_target():
    c, e = climate(VehicleStatus(ac_on=False, ac_target_temp_c=24))
    sw = GacClimateSwitch(c)
    sw.async_write_ha_state = Mock()

    async def run():
        await e.async_set_temperature(temperature=22)
        await sw.async_turn_on()

    asyncio.run(run())
    assert c.client.command.await_args.kwargs["temperature"] == 22, "Power switch sends old vehicle setpoint"


def test_pending_climate_state_expires_without_poll(scheduled):
    c, e = climate(VehicleStatus(ac_on=False, ac_target_temp_c=24))
    with patch("custom_components.gac_connect.cabin.monotonic", return_value=100):
        asyncio.run(e.async_turn_on())
    assert e.hvac_mode == HVACMode.HEAT_COOL
    timer = scheduled[-1]
    assert timer.delay == 180
    with patch("custom_components.gac_connect.cabin.monotonic", return_value=301):
        timer.callback(None)
    assert e.hvac_mode == HVACMode.OFF


@pytest.mark.parametrize(
    "key,group,raw",
    [
        ("charging", "chargingAccessors", {"chargingStatus": -1}),
        ("plugged", "chargerAccessors", {"plug": -1}),
        ("charger_locked", "chargerAccessors", {"lock": -1}),
    ],
)
def test_unknown_binary_states_remain_unknown(key, group, raw):
    s = decode_status({group: [raw]})
    d = next(d for d in BINARY_SENSORS if d.key == key)
    assert GacBinaryEntity(coord(s), d).is_on is None, f"{key} converts -1 to a definite state"


def test_unknown_charge_mode_remains_unknown():
    assert GacChargeSwitch(coord(VehicleStatus(charging_mode=ChargingMode.UNKNOWN))).is_on is None


@pytest.mark.parametrize(
    "group,attr",
    [
        ("doorAccessors", "door_open"),
        ("windowAccessors", "window_open"),
        ("hatchAccessors", "hatch_open"),
        ("sunroofAccessors", "sunroof_open"),
    ],
)
def test_partial_opening_reports_are_unknown(group, attr):
    s = decode_status({group: [{"openMode": 0}, {"openMode": -1}]})
    assert getattr(s, attr) is None, "One closed plus one unknown incorrectly means all closed"


def test_tyre_entity_uses_tyre_id_instead_of_list_position():
    s = decode_status({"tyreAccessors": [{"tyre": 2, "pressure": 220}, {"tyre": 1, "pressure": 250}]})
    d = next(d for d in _tyre_sensors() if d.key == "tyre_1_pressure")
    assert d.value(s) == 250, "Tyre 1 entity shows tyre 2 reading on reordered/sparse report"


@pytest.mark.parametrize("raw", ["unknown", float("inf")])
def test_bad_charge_mode_does_not_abort_all_telemetry(raw):
    s = decode_status(
        {
            "drivingOverviewAccessors": [{"remainElectricityPercentage": 0.65}],
            "chargingAccessors": [{"chargingMode": raw}],
        }
    )
    assert s.soc == 65


def test_pause_does_not_create_recurring_charging_window():
    from custom_components.gac_connect._vendor.gac_connect.errors import CommandError

    c = coord(VehicleStatus())
    with pytest.raises(CommandError, match="03:00 to 03:30"):
        asyncio.run(c.client.charge_pause(c.vin))
    c.client.command.assert_not_awaited()


@pytest.mark.parametrize("hour", [0, 2, 5, 12, 23])
def test_overnight_window_struct_dates_are_ordered(hour):
    op = window_operation(
        "23:00", "05:00", ZoneInfo("Asia/Bangkok"), now=datetime(2026, 10, 9, hour, 30, tzinfo=ZoneInfo("Asia/Bangkok"))
    )

    def dt(v):
        return datetime(**{k: v[k] for k in ("year", "month", "day", "hour", "minute", "second")})

    assert dt(op["stopTime"]) > dt(op["startTime"])
    assert op["dailyReservationStartTime"] == 82800000
    assert op["dailyReservationStopTime"] == 18000000


@pytest.mark.parametrize("mask", [-1, 128, 1.5, "1", True])
def test_service_weekly_mask_out_of_range_is_rejected(mask):
    with pytest.raises(ValueError):
        window_operation("23:00", "05:00", ZoneInfo("Asia/Bangkok"), weekly=mask)


def test_fridge_state_sync_and_temperature_bounds():
    c = coord(VehicleStatus(fridge_mode="heat", fridge_temp_c=42))
    with patch("custom_components.gac_connect.fridge.Store"):
        fc = FridgeController(Mock(), c.config_entry, c)
    fc._save = Mock()
    fc.sync()
    assert fc.option == "heat" and fc.running is True and fc.temperature == 42
    assert fc.temp_mode == "heat" and fc.mode == "heat"
    assert fc._checked("freeze", -11.7) == (4, -12)


def test_session_matching_ignores_other_command():
    _, e = climate(VehicleStatus(ac_on=False))
    asyncio.run(e.async_turn_on())
    e._cabin._handle_result(PushResult(False, "13101", None, {}, session_id="other", event="control_air_condition"))
    assert e.hvac_mode == HVACMode.HEAT_COOL


def test_climate_send_failure_restores_previous_state():
    c, e = climate(VehicleStatus(ac_on=False, ac_target_temp_c=24))
    c.client.command.side_effect = RuntimeError("transport failed")
    with pytest.raises(RuntimeError):
        asyncio.run(e.async_turn_on())
    assert e.hvac_mode == HVACMode.OFF and e._cabin._req is None


def test_fridge_pending_request_rejection_restores_preferences():
    from time import monotonic

    from custom_components.gac_connect.fridge import _Request

    c = coord(VehicleStatus(fridge_mode="off"))
    with patch("custom_components.gac_connect.fridge.Store"):
        fc = FridgeController(Mock(), c.config_entry, c)
    fc._save = Mock()
    fc.mode = "heat"
    fc.temps["heat"] = 45
    fc._req = _Request("heat", 45, monotonic() + 180, session_id="s1", undo=("refrigerate", 42))
    fc._handle_result(PushResult(False, "13101", None, {}, session_id="s1", event="control_refrigerator"))
    assert fc.option == "off" and fc.mode == "refrigerate" and fc.temps["heat"] == 42


def test_manual_refresh_fetches_during_quiet_hours():
    from custom_components.gac_connect.button import GacRefreshButton

    c = object.__new__(GacCoordinator)
    c._fetch_lock = asyncio.Lock()
    c._fetch_generation = 0
    c._rate_limit_until = float("-inf")
    c._quiet_recovery_remaining = 0
    c._recovery_retry_at = None
    c.commands = Mock()
    c.data = VehicleStatus(soc=50)
    c.vin = "TESTVIN"
    c._force_refresh = False
    c._in_quiet_hours = lambda: True
    c.client = SimpleNamespace(get_status=AsyncMock(return_value=VehicleStatus(soc=60)))
    c.config_entry = SimpleNamespace(data={"model": "Aion V"})
    c.async_request_refresh = c._async_update_data
    asyncio.run(GacRefreshButton(c).async_press())
    assert c.client.get_status.await_count == 1, "Refresh button silently returns cached status during quiet hours"


def test_status_error_and_unknown_finite_reading_handling():
    # Sentinel charge current must not become a huge charge-power reading.
    s = decode_status({"chargingAccessors": [{"chargingCurrent": 1638, "chargingVoltage": 400}]})
    assert s.charge_current_a is None and s.charge_power_kw is None


@pytest.mark.parametrize("raw", [None, -1, "-1", "unknown", float("nan"), float("inf"), True, 1.5])
def test_invalid_charge_modes_preserve_other_readings(raw):
    s = decode_status(
        {
            "chargingAccessors": [{"chargingMode": raw}],
            "drivingOverviewAccessors": [{"remainElectricityPercentage": 0.5}],
        }
    )
    assert s.soc == 50
    assert s.charging_mode is None


@pytest.mark.parametrize("code,plugged,locked", [("0", False, True), ("1", True, False)])
def test_charger_numeric_strings_decode_correctly(code, plugged, locked):
    s = decode_status({"chargerAccessors": [{"plug": code, "lock": code}]})
    assert s.plugged_in is plugged
    assert s.charger_locked is locked
    d = next(d for d in BINARY_SENSORS if d.key == "charger_locked")
    assert GacBinaryEntity(coord(s), d).is_on is (not locked)


@pytest.mark.parametrize(
    "items,expected",
    [
        ([], None),
        ([{"openMode": "0"}], False),
        ([{"openMode": 0}, None], None),
        ([{"openMode": 0}, {}], None),
        ([{"openMode": -1}, {"openMode": "1"}], True),
        ([{"openMode": float("nan")}], None),
    ],
)
def test_opening_truth_table(items, expected):
    s = decode_status({"windowAccessors": items})
    assert s.window_open is expected
    d = next(d for d in COVERS if d.key == "windows")
    assert GacCoverEntity(coord(s), d).is_closed is (None if expected is None else not expected)


@pytest.mark.parametrize("flag", [0, "0", -1, "unknown", False])
def test_invalid_reading_flags_suppress_charge_power_and_tyres(flag):
    s = decode_status(
        {
            "chargingAccessors": [{"chargingVoltage": 400, "chargingCurrent": 10, "chargingVoltageFlag": flag}],
            "tyreAccessors": [
                {"tyre": "2", "pressure": 250, "temperature": 20, "pressureFlag": flag, "temperatureFlag": flag}
            ],
        }
    )
    assert s.charge_voltage_v is None and s.charge_power_kw is None
    assert s.tyres[1].pressure_kpa is None and s.tyres[1].temperature_c is None


@pytest.mark.parametrize(
    "items",
    [
        [{"tyre": 1, "pressure": 250}, {"tyre": 1, "pressure": 200}],
        [{"tyre": [], "pressure": 250}],
        [None],
        [{"tyre": True, "pressure": 250}],
    ],
)
def test_invalid_or_duplicate_tyre_identity_is_unknown(items):
    s = decode_status({"tyreAccessors": items})
    assert len(s.tyres) == 4
    assert all(t.pressure_kpa is None for t in s.tyres)


def test_normalization_does_not_change_raw_diagnostics():
    import copy

    raw = {
        "chargingAccessors": [{"chargingMode": "unknown", "chargingCurrent": "nan"}],
        "tyreAccessors": [{"tyre": "3", "pressure": "250", "temperature": "-5"}],
    }
    before = copy.deepcopy(raw)
    s = decode_status(raw, online="1", updated_ms="1791504000000")
    assert raw == before and s.raw is raw
    assert s.online is True and s.updated_ms == 1791504000000
    assert s.tyres[2].pressure_kpa == 250 and s.tyres[2].temperature_c == -5


def test_live_ut_unreported_tyres_are_unknown_through_client_and_entities():
    raw = json.loads((Path(__file__).parent / "fixtures" / "ut_unreported_tyres.json").read_text())
    c = coord(VehicleStatus())
    c.client._iov_read = AsyncMock(return_value={"data": {"results": [raw], "online": True}})
    c.data = asyncio.run(c.client.get_status(c.vin))
    entities = [GacSensorEntity(c, description) for description in _tyre_sensors()]
    assert len(entities) == 8
    assert all(entity.available and entity.native_value is None for entity in entities)
    assert c.data.raw is raw
    assert all(item["temperature"] == -1 and item["temperatureFlag"] == 1 for item in raw["tyreAccessors"])


@pytest.mark.parametrize("temperature", [-1, "-1.0", -5, 0, 28])
def test_reported_tyres_keep_legitimate_cold_temperatures(temperature):
    status = decode_status(
        {
            "tyreAccessors": [
                {"tyre": 1, "pressure": 250, "temperature": temperature, "pressureFlag": 1, "temperatureFlag": 1}
            ]
        }
    )
    assert status.tyres[0].pressure_kpa == 250
    assert status.tyres[0].temperature_c == float(temperature)


def test_string_ut_placeholder_pair_is_unknown():
    status = decode_status({"tyreAccessors": [{"tyre": "1", "pressure": "-1", "temperature": "-1.0"}]})
    assert status.tyres[0].pressure_kpa is None and status.tyres[0].temperature_c is None


def test_client_read_uses_corrected_decoder_and_existing_transport():
    c = coord(VehicleStatus())
    raw = {
        "chargingAccessors": [{"chargingMode": "bad"}],
        "drivingOverviewAccessors": [{"remainElectricityPercentage": 0.8}],
    }
    c.client._iov_read = AsyncMock(return_value={"data": {"results": [raw], "online": True}})
    s = asyncio.run(c.client.get_status(c.vin))
    assert s.soc == 80 and s.charging_mode is None and s.raw is raw
    c.client._iov_read.assert_awaited_once_with(CATALOG["status"].path, {"vin": c.vin}, sensitive={"vin": c.vin})


@pytest.mark.parametrize("data", [[], "invalid", {"results": []}, {"results": [None]}])
def test_malformed_report_envelope_fails_clearly(data):
    from custom_components.gac_connect._vendor.gac_connect.errors import AuthExpiredError, GacError

    c = coord(VehicleStatus())
    c.client._iov_read = AsyncMock(return_value={"data": data})
    with pytest.raises(GacError, match="no valid") as caught:
        asyncio.run(c.client.get_status(c.vin))
    assert not isinstance(caught.value, AuthExpiredError)


def test_client_factory_installs_selected_client():
    from custom_components.gac_connect import helpers

    hass = SimpleNamespace(async_add_executor_job=AsyncMock(return_value=Mock()))
    with patch.object(helpers, "async_setup_limits", new=AsyncMock()):
        client = asyncio.run(helpers.async_build_client(hass, "TH", Mock()))
    assert type(client) is GacClient
    assert client.timezone == "Asia/Bangkok"


@pytest.mark.parametrize("value", [17, 33, float("nan"), float("inf"), "bad", True])
def test_invalid_temperature_never_sends_a_command(value):
    from homeassistant.exceptions import ServiceValidationError

    c, e = climate(VehicleStatus(ac_on=True))
    with pytest.raises(ServiceValidationError):
        asyncio.run(e.async_set_temperature(temperature=value))
    c.client.command.assert_not_awaited()


@pytest.mark.parametrize(
    "initial,mode,command",
    [
        (False, HVACMode.HEAT_COOL, "aircon-on"),
        (True, HVACMode.OFF, "aircon-off"),
        (True, None, "aircon-on"),
        (False, None, None),
        (None, None, None),
    ],
)
def test_combined_climate_action_truth_table(initial, mode, command):
    c, e = climate(VehicleStatus(ac_on=initial, ac_target_temp_c=24))
    asyncio.run(e.async_set_temperature(temperature=22, hvac_mode=mode))
    if command is None:
        c.client.command.assert_not_awaited()
    else:
        assert c.client.command.await_args.args == (c.vin, command)
    assert e.target_temperature == 22


def test_confirmation_requires_temperature_as_well_as_power(scheduled):
    c, e = climate(VehicleStatus(ac_on=True, ac_target_temp_c=24))
    asyncio.run(e.async_set_temperature(temperature=22))
    timer = scheduled[-1]
    c.data = VehicleStatus(ac_on=True, ac_target_temp_c=24)
    e._handle_coordinator_update()
    timer.cancel.assert_not_called()
    c.data = VehicleStatus(ac_on=True, ac_target_temp_c=22)
    e._handle_coordinator_update()
    timer.cancel.assert_called_once()
    c.data = VehicleStatus(ac_on=True, ac_target_temp_c=25)
    e._handle_coordinator_update()
    assert e.target_temperature == 25


def test_result_before_acceptance_is_correlated_after_response():
    c, e = climate(VehicleStatus(ac_on=False, ac_target_temp_c=24))

    async def send(*args, **kwargs):
        e._cabin._handle_result(PushResult(False, "13101", None, {}, session_id="s1"))
        return ACCEPTED

    c.client.command.side_effect = send
    asyncio.run(e.async_turn_on())
    assert e.hvac_mode == HVACMode.OFF and e.target_temperature == 24


def test_shared_command_queue_serializes_both_entities():
    c, e = climate(VehicleStatus(ac_on=False, ac_target_temp_c=24))
    sw = GacClimateSwitch(c)
    sw.async_write_ha_state = Mock()

    async def run():
        started, release = asyncio.Event(), asyncio.Event()

        async def send(vin, command, **kwargs):
            if command == "aircon-on":
                started.set()
                await release.wait()
            return ACCEPTED

        c.client.command.side_effect = send
        first = asyncio.create_task(e.async_turn_on())
        await started.wait()
        second = asyncio.create_task(sw.async_turn_off())
        await asyncio.sleep(0)
        assert c.client.command.await_count == 1
        release.set()
        await asyncio.gather(first, second)

    asyncio.run(run())
    assert [call.args[1] for call in c.client.command.await_args_list] == ["aircon-on", "aircon-off"]
    assert e.hvac_mode == HVACMode.OFF and sw.is_on is False


def test_cancellation_restores_previous_pending_request(scheduled):
    c, e = climate(VehicleStatus(ac_on=False, ac_target_temp_c=24))

    async def run():
        await e.async_turn_on()
        original = e._cabin._req
        entered = asyncio.Event()

        async def send(*args, **kwargs):
            entered.set()
            await asyncio.Event().wait()

        c.client.command.side_effect = send
        task = asyncio.create_task(e.async_turn_off())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert e._cabin._req is original

    asyncio.run(run())
    assert e.hvac_mode == HVACMode.HEAT_COOL
    assert e._cabin._req.session_id == "s1"
    assert scheduled[-1].delay <= 180


def test_unsent_selection_after_off_request_survives_rejection():
    c, e = climate(VehicleStatus(ac_on=True, ac_target_temp_c=24))

    async def run():
        await e.async_turn_off()
        await e.async_set_temperature(temperature=22)

    asyncio.run(run())
    e._cabin._handle_result(PushResult(False, "13101", None, {}, session_id="s1"))
    assert e.hvac_mode == HVACMode.HEAT_COOL and e.target_temperature == 22


def test_reported_switch_expires_without_poll(scheduled):
    from custom_components.gac_connect.switch import GacCommandSwitch

    c = coord(VehicleStatus(lights_on=False))
    sw = GacCommandSwitch(c, next(d for d in SWITCHES if d.key == "lights"))
    sw.hass = Mock()
    sw.async_write_ha_state = Mock()
    with patch("custom_components.gac_connect.switch.monotonic", return_value=100):
        asyncio.run(sw.async_turn_on())
    assert sw.is_on is True
    with patch("custom_components.gac_connect.switch.monotonic", return_value=281):
        scheduled[-1].callback(None)
    assert sw.is_on is False
    assert sw.async_write_ha_state.call_count >= 2


def test_generic_switch_restores_previous_result_correlation_after_failure():
    from custom_components.gac_connect._vendor.gac_connect.errors import CommandError
    from homeassistant.exceptions import HomeAssistantError

    from custom_components.gac_connect.switch import GacCommandSwitch

    c = coord(VehicleStatus(lights_on=False))
    sw = GacCommandSwitch(c, next(d for d in SWITCHES if d.key == "lights"))
    sw.hass = Mock()
    sw.async_write_ha_state = Mock()

    async def run():
        await sw.async_turn_on()
        c.client.command.side_effect = CommandError("refused")
        with pytest.raises(HomeAssistantError):
            await sw.async_turn_off()

    asyncio.run(run())
    sw._handle_result(PushResult(False, "13101", None, {}, session_id="s1"))
    assert sw.is_on is False


def service_registry(c):
    from custom_components.gac_connect.services import async_register_services

    registered = {}

    def register(domain, name, handler, schema):
        registered[name] = (handler, schema)

    runtime = SimpleNamespace(coordinator=c, client=c.client)
    hass = SimpleNamespace(
        services=SimpleNamespace(has_service=Mock(return_value=False), async_register=register),
        config_entries=SimpleNamespace(async_entries=Mock(return_value=[SimpleNamespace(runtime_data=runtime)])),
    )
    async_register_services(hass)
    return registered


@pytest.mark.parametrize("surface", ["button", "switch", "service", "client"])
def test_pause_is_blocked_at_every_entry_point(surface):
    from custom_components.gac_connect._vendor.gac_connect.errors import CommandError
    from homeassistant.exceptions import ServiceValidationError

    from custom_components.gac_connect.button import BUTTONS, GacButtonEntity

    c = coord(VehicleStatus())
    if surface == "button":
        d = next(d for d in BUTTONS if d.key == "charge_pause")
        action = GacButtonEntity(c, d).async_press()
    elif surface == "switch":
        action = GacChargeSwitch(c).async_turn_off()
    elif surface == "service":
        action = service_registry(c)["charge_pause"][0](SimpleNamespace(data={}))
    else:
        action = c.client.charge_pause(c.vin)
    with pytest.raises((ServiceValidationError, CommandError), match="Continuous charging pause"):
        asyncio.run(action)
    c.client.command.assert_not_awaited()


@pytest.mark.parametrize("start,stop", [("23:00", "05:00"), ("01:00", "02:00")])
def test_charge_window_service_uses_corrected_client(start, stop):
    c = coord(VehicleStatus())
    c.client._tz = ZoneInfo("Asia/Bangkok")
    c.client._reservation = AsyncMock(return_value=ACCEPTED)
    handler, schema = service_registry(c)["set_charge_window"]
    data = schema({"start": start, "stop": stop, "weekly": 3.0, "until_soc": True})
    asyncio.run(handler(SimpleNamespace(data=data)))
    vin, op = c.client._reservation.await_args.args
    assert vin == c.vin and op["weeklyReservation"] == 3 and op["chargingEndType"] == 1
    assert isinstance(op["weeklyReservation"], int)

    def dt(v):
        return datetime(**{k: v[k] for k in ("year", "month", "day", "hour", "minute", "second")})

    assert dt(op["stopTime"]) > dt(op["startTime"])


@pytest.mark.parametrize(
    "data",
    [
        {"start": "25:00", "stop": "05:00"},
        {"start": "3:00", "stop": "05:00"},
        {"start": "23:60", "stop": "05:00"},
        {"start": "23:00", "stop": "05:00", "weekly": 128},
    ],
)
def test_invalid_charge_window_service_input_is_rejected(data):
    import voluptuous as vol

    c = coord(VehicleStatus())
    schema = service_registry(c)["set_charge_window"][1]
    with pytest.raises(vol.Invalid):
        schema(data)
    c.client.command.assert_not_awaited()


def test_equal_charge_window_times_give_a_validation_error_without_sending():
    from homeassistant.exceptions import ServiceValidationError

    c = coord(VehicleStatus())
    c.client._tz = ZoneInfo("Asia/Bangkok")
    c.client._reservation = AsyncMock()
    handler = service_registry(c)["set_charge_window"][0]
    with pytest.raises(ServiceValidationError, match="must differ"):
        asyncio.run(handler(SimpleNamespace(data={"start": "05:00", "stop": "05:00"})))
    c.client._reservation.assert_not_awaited()


def test_weekday_mask_selects_next_matching_start_date():
    op = window_operation(
        "23:00",
        "05:00",
        ZoneInfo("Asia/Bangkok"),
        weekly=1,
        now=datetime(2026, 10, 9, 2, 0, tzinfo=ZoneInfo("Asia/Bangkok")),
    )
    assert op["startTime"]["day"] == 12  # Monday
    assert op["stopTime"]["day"] == 13


def test_cabin_setup_cancels_timer_and_subscriptions_on_unload():
    from custom_components.gac_connect.cabin import CabinController

    c = coord(VehicleStatus())
    unload = []
    c.config_entry.async_on_unload = unload.append
    unsub_dispatch, unsub_status = Mock(), Mock()
    c.async_add_listener = Mock(return_value=unsub_status)
    fc = CabinController(c)
    timer_cancel = Mock()
    fc._expire = timer_cancel
    with patch("custom_components.gac_connect.cabin.async_dispatcher_connect", return_value=unsub_dispatch):
        fc.async_setup(c.config_entry)
    for remove in unload:
        remove()
    unsub_dispatch.assert_called_once()
    unsub_status.assert_called_once()
    timer_cancel.assert_called_once()


def test_expiry_with_real_home_assistant_timer(monkeypatch, tmp_path):
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.event import async_call_later

    monkeypatch.setattr("custom_components.gac_connect.cabin.async_call_later", async_call_later)
    monkeypatch.setattr("custom_components.gac_connect.cabin.REQUEST_SECONDS", 0.02)

    async def run():
        c, e = climate(VehicleStatus(ac_on=False, ac_target_temp_c=24))
        c.hass = HomeAssistant(str(tmp_path))
        await e.async_turn_on()
        assert e.hvac_mode == HVACMode.HEAT_COOL
        await asyncio.sleep(0.05)
        assert e.hvac_mode == HVACMode.OFF

    asyncio.run(run())


def test_malformed_first_accessor_does_not_shift_other_readings():
    status = decode_status({"drivingOverviewAccessors": [None, {"remainElectricityPercentage": 0.8}]})
    assert status.soc is None


def test_decoded_lock_state_preserves_unknown_doors():
    status = decode_status({"doorAccessors": [{"door": 1, "lock": "0"}, {"door": 2, "lock": -1}]})
    assert status.locked is None
