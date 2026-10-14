"""The car's capability list decides which entities Auto detect shows."""
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
import voluptuous as vol
from homeassistant.components.climate import ClimateEntityFeature
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.gac_connect import fridge
from custom_components.gac_connect.api import decode_status
from custom_components.gac_connect.capabilities import CapabilityManager
from custom_components.gac_connect.climate import GacClimate
from custom_components.gac_connect.config_flow import GacOptionsFlow
from custom_components.gac_connect.equipment import equipment_summary
from custom_components.gac_connect.number import GacFridgeTemperature
from custom_components.gac_connect.select import GacFridgeModeSelect, GacSeatVentilationSelect
from custom_components.gac_connect._vendor.gac_connect.errors import ResponseError
from test_autodetect import vehicle

UT_LIST = json.loads((Path(__file__).parent / "fixtures" / "ut_capabilities.json").read_text())
SHOWN = ["switch:steering_heat", "cover:sunroof", "switch:fridge", "sensor:pm25", "sensor:tyre_1_pressure",
         "binary_sensor:seat_3_ventilation", "button:horn", "switch:ventilation", "select:seat_1_ventilation",
         "switch:precondition_battery", "cover:tailgate", "cover:windows", "lock:doors", "climate:climate",
         "sensor:soc", "binary_sensor:seat_1_ventilation"]


def shown(manager):
    return {key: manager.includes(key) for key in SHOWN}


def test_an_aion_ut_list_shows_its_controls_and_hides_what_it_lacks(tmp_path):
    async def run():
        async with vehicle(tmp_path, model="Aion UT") as (hass, entry, c):
            c.client.get_control_capabilities = AsyncMock(return_value=UT_LIST)
            manager = c.capabilities
            # placeholders the UT sends for a console-only sunroof and an absent PM2.5 sensor
            manager.observe(decode_status({"sunroofAccessors": [{"openMode": 0}],
                                           "environmentAccessors": [{"pm25": 0.0}]}))
            assert manager.includes("cover:sunroof") and manager.includes("sensor:pm25")
            assert await manager.async_read_capability_list(c.client, "VIN") > 40
            assert shown(manager) == {
                "switch:steering_heat": False, "cover:sunroof": False, "switch:fridge": False, "sensor:pm25": False,
                "sensor:tyre_1_pressure": False, "binary_sensor:seat_3_ventilation": False,
                "button:horn": True, "switch:ventilation": True, "select:seat_1_ventilation": True,
                "switch:precondition_battery": True, "cover:tailgate": True, "cover:windows": True,
                "lock:doors": True, "climate:climate": True, "sensor:soc": True,
                "binary_sensor:seat_1_ventilation": True,
            }
            assert manager.explanation("cover:sunroof") == "Hidden: the car's capability list does not offer it remotely."
            assert manager.explanation("button:horn") == "Shown: the car's capability list offers it."
            restored = CapabilityManager(hass, entry)
            await restored.async_load()
            assert restored.capability_list_read and shown(restored) == shown(manager)
    asyncio.run(run())


def test_omit_and_always_show_still_win(tmp_path):
    async def run():
        options = {"omit_entities": ["button:horn"], "enable_entities": ["cover:sunroof"]}
        async with vehicle(tmp_path, options=options) as (_, _, c):
            c.client.get_control_capabilities = AsyncMock(return_value=UT_LIST)
            await c.capabilities.async_read_capability_list(c.client, "VIN")
            assert not c.capabilities.includes("button:horn")
            assert c.capabilities.includes("cover:sunroof")
    asyncio.run(run())


def test_an_unusable_list_changes_nothing(tmp_path):
    async def run():
        async with vehicle(tmp_path) as (_, _, c):
            c.client.get_control_capabilities = AsyncMock(return_value={"capabilities": []})
            c.capabilities.observe(decode_status({"sunroofAccessors": [{"openMode": 0}]}))
            try:
                await c.capabilities.async_read_capability_list(c.client, "VIN")
            except HomeAssistantError:
                pass
            else:
                raise AssertionError("an empty list must not be accepted")
            assert not c.capabilities.capability_list_read and c.capabilities.includes("cover:sunroof")
    asyncio.run(run())


def setup_flow(hass, entry, coordinator):
    flow = GacOptionsFlow()
    flow.hass, flow.handler = hass, entry.entry_id
    hass.config_entries.async_schedule_reload = Mock()
    entry.runtime_data = type("Runtime", (), {"coordinator": coordinator})()
    coordinator.client.timezone = "Asia/Bangkok"
    return flow


def test_refresh_reads_the_list_reloads_and_shows_the_result(tmp_path):
    async def run():
        async with vehicle(tmp_path) as (hass, entry, c):
            c.client.get_control_capabilities = AsyncMock(return_value=UT_LIST)
            flow = setup_flow(hass, entry, c)
            form = await flow.async_step_capabilities()
            assert form["type"] == "form" and form["errors"] == {}
            result = await flow.async_step_capabilities({})
            assert result["step_id"] == "equipment"
            assert "capability list" in result["description_placeholders"]["equipment"]
            hass.config_entries.async_schedule_reload.assert_called_once_with(entry.entry_id)
            c.client.get_control_capabilities.assert_awaited_once_with("VIN")
    asyncio.run(run())


def test_a_failed_refresh_keeps_the_previous_list(tmp_path):
    async def run():
        async with vehicle(tmp_path) as (hass, entry, c):
            c.client.get_control_capabilities = AsyncMock(return_value=UT_LIST)
            await c.capabilities.async_read_capability_list(c.client, "VIN")
            c.client.get_control_capabilities = AsyncMock(side_effect=ResponseError("Could not reach GAC"))
            flow = setup_flow(hass, entry, c)
            result = await flow.async_step_capabilities({})
            assert result["errors"] == {"base": "capability_read_failed"}
            assert not c.capabilities.includes("cover:sunroof")
            hass.config_entries.async_schedule_reload.assert_not_called()
    asyncio.run(run())


def test_the_list_is_read_once_and_a_failure_only_falls_back(tmp_path):
    async def run():
        async with vehicle(tmp_path) as (_, _, c):
            c.client.get_control_capabilities = AsyncMock(side_effect=ResponseError("Could not reach GAC"))
            await c.capabilities.async_read_capability_list_once(c.client, "VIN")      # must not raise
            assert not c.capabilities.capability_list_read
            c.client.get_control_capabilities = AsyncMock(return_value=UT_LIST)
            await c.capabilities.async_read_capability_list_once(c.client, "VIN")
            await c.capabilities.async_read_capability_list_once(c.client, "VIN")
            c.client.get_control_capabilities.assert_awaited_once_with("VIN")
            assert c.capabilities.capability_list_read
    asyncio.run(run())


def remote(data):
    return data["capabilities"][0]["capability"]["remoteCapability"]


def other_car():
    """A list unlike the UT's: five remote fan speeds, outlets 1, 2, 5 and an unnamed 6, driver seat two levels."""
    data = json.loads(json.dumps(UT_LIST))
    air, seats = remote(data)["airConditionCapabilities"][0], remote(data)["seatTemperatureCapabilities"]
    air.update(maxWindStrength=5, supportBlowingModes=[0, 1, 2, 5, 6])
    seats[0]["maxWindStrength"] = 2
    return data


def test_the_ut_list_keeps_its_verified_choices_and_they_survive_a_restart(tmp_path):
    async def run():
        async with vehicle(tmp_path) as (hass, entry, c):
            c.client.get_control_capabilities = AsyncMock(return_value=UT_LIST)
            await c.capabilities.async_read_capability_list(c.client, "VIN")
            climate, seat = GacClimate(c), GacSeatVentilationSelect(c, 1)
            assert climate.fan_modes == ["off", "1", "2", "3", "4", "5", "6", "7"]
            assert climate.swing_modes == ["cabin", "cabin_feet", "feet", "feet_windscreen"]
            assert seat.options == ["off", "low", "medium", "high"]
            assert c.capabilities.diagnostics()["ranges"]["fan_speeds"] == [1, 2, 3, 4, 5, 6, 7]
            await c.capabilities.async_close()
            restored = CapabilityManager(hass, entry)
            await restored.async_load()
            assert restored.ranges == c.capabilities.ranges
            assert "seat_3_ventilation_levels" not in restored.ranges     # a switched-off group states nothing
    asyncio.run(run())


def test_another_cars_list_sets_its_fan_airflow_and_seat_choices(tmp_path):
    async def run():
        async with vehicle(tmp_path) as (hass, entry, c):
            c.client.get_control_capabilities = AsyncMock(return_value=other_car())
            await c.capabilities.async_read_capability_list(c.client, "VIN")
            c.data = decode_status({"airConditionAccessors": [{"blowingMode": 3}]})
            climate, driver, passenger = GacClimate(c), GacSeatVentilationSelect(c, 1), GacSeatVentilationSelect(c, 2)
            assert climate.fan_modes == ["off", "1", "2", "3", "4", "5"]
            assert climate.swing_modes == ["cabin", "cabin_feet", "windscreen"]
            assert climate.swing_mode is None          # feet: named, but not a remote choice here
            assert driver.options == ["off", "low", "medium"]
            assert passenger.options == ["off", "low", "medium", "high"]
            for call in (climate.async_set_fan_mode("6"), climate._cabin.async_set_fan_speed(6),
                         climate.async_set_swing_mode("feet"), climate._cabin.async_set_blowing_mode(3),
                         driver.async_select_option("high")):
                with pytest.raises(ServiceValidationError):
                    await call
            c.client.command.assert_not_awaited()
            entry.runtime_data = type("Runtime", (), {"coordinator": c})()
            summary = equipment_summary(entry)
            assert "- Climate (Off, On, Fan only; 18–32 °C; fan 1–5; 3 airflow choices; Heating and Cooling presets)" in summary
            assert "- Driver seat ventilation (Off, Low, Medium)" in summary
    asyncio.run(run())


def test_a_list_without_remote_fan_or_airflow_drops_those_controls(tmp_path):
    async def run():
        data = json.loads(json.dumps(UT_LIST))
        air = remote(data)["airConditionCapabilities"][0]
        air.update(windStrengthCapability=1, blowingModeCapability=1)
        async with vehicle(tmp_path) as (_, _, c):
            c.client.get_control_capabilities = AsyncMock(return_value=data)
            await c.capabilities.async_read_capability_list(c.client, "VIN")
            climate = GacClimate(c)
            assert not climate.supported_features & (ClimateEntityFeature.FAN_MODE | ClimateEntityFeature.SWING_MODE)
            assert climate.supported_features & ClimateEntityFeature.TARGET_TEMPERATURE
            with pytest.raises(ServiceValidationError, match="offers no remote fan speeds"):
                await climate.async_set_fan_mode("3")
            c.client.command.assert_not_awaited()
    asyncio.run(run())


@pytest.mark.parametrize("saved", [{"ranges": None, "ranges_version": None},       # before ranges were kept
                                   {"ranges": {"fan_speeds": [1, 2, 3, 4, 5, 6, 7]}, "ranges_version": None}])
def test_a_list_saved_by_an_earlier_version_is_read_once_more(tmp_path, saved):
    async def run():
        async with vehicle(tmp_path) as (hass, entry, c):
            c.client.get_control_capabilities = AsyncMock(return_value=UT_LIST)
            await c.capabilities.async_read_capability_list(c.client, "VIN")
            snapshot = {**c.capabilities._snapshot(), **saved}
            await c.capabilities._store.async_save({key: value for key, value in snapshot.items() if value is not None})
            older = CapabilityManager(hass, entry)
            await older.async_load()
            assert not older.capability_list_read and "ac_temperature_range" not in older.ranges
            assert not older.includes("cover:sunroof")          # its equipment flags still apply meanwhile
            await older.async_read_capability_list_once(c.client, "VIN")
            assert older.capability_list_read and older.ranges["blowing_modes"] == (1, 2, 3, 4)
    asyncio.run(run())


def a_car_with_its_own_spans():
    """Unlike the UT: 16–30 °C, 10–30 minute runs, fresh air only, no remote preconditioning start,
    and a fridge that refrigerates (from 2 °C) and freezes."""
    data = json.loads(json.dumps(UT_LIST))
    groups = remote(data)
    groups["airConditionCapabilities"][0].update(
        minTemperature=16, maxTemperature=30, minAirConditionCurrentDuration=600,
        maxAirConditionCurrentDuration=1800, supportCyclingModes=[0, 2])
    groups["batteryPretreatmentCapabilities"][0]["supportBatteryPretreatmentModes"] = [0, 3]
    groups["refrigeratorCapabilities"][0].update(controlCapability=1, workingMode=[1, 3, 4],
                                                 minRefrigerationTemperature=2)
    return data


def settings_validator(form, name):
    return next(value for key, value in form["data_schema"].schema.items() if key == name)


def test_the_ut_list_keeps_its_temperature_run_time_and_switches(tmp_path):
    async def run():
        async with vehicle(tmp_path, options={"ac_minutes": 15}) as (hass, entry, c):
            c.client.get_control_capabilities = AsyncMock(return_value=UT_LIST)
            await c.capabilities.async_read_capability_list(c.client, "VIN")
            climate = GacClimate(c)
            assert (climate.min_temp, climate.max_temp) == (18, 32) and climate._cabin.run_minutes == 15
            assert c.capabilities.includes("switch:recirculation")
            assert c.capabilities.includes("switch:precondition_battery")
            assert fridge.options(c) == ("off", "refrigerate", "heat", "freeze")   # its fridge group is off
            assert fridge.temperature_range(c, "refrigerate") == (0.0, 20.0)
    asyncio.run(run())


def test_another_cars_spans_and_codes_shape_its_controls(tmp_path):
    async def run():
        async with vehicle(tmp_path, options={"ac_minutes": 45}) as (hass, entry, c):
            c.client.get_control_capabilities = AsyncMock(return_value=a_car_with_its_own_spans())
            await c.capabilities.async_read_capability_list(c.client, "VIN")
            climate = GacClimate(c)
            assert (climate.min_temp, climate.max_temp) == (18, 30)   # 16–17 are beyond what is sent
            assert climate._presets == {"heating": 30, "cooling": 18}
            assert climate._cabin.run_minutes == 30
            for call in (climate.async_set_temperature(temperature=31), climate._cabin.async_set_temperature(31)):
                with pytest.raises(ServiceValidationError, match="between 18 and 30"):
                    await call
            c.client.command.assert_not_awaited()
            for key in ("switch:recirculation", "switch:precondition_battery"):
                assert not c.capabilities.includes(key)
                assert c.capabilities.explanation(key) == "Hidden: the car's capability list does not offer it remotely."
            flow = setup_flow(hass, entry, c)
            minutes = settings_validator(await flow.async_step_settings(), "ac_minutes")
            assert minutes(10) == 10 and minutes("30") == 30
            for value in (5, 31):
                with pytest.raises(vol.Invalid):
                    minutes(value)

            controller = fridge.FridgeController(hass, entry, c)
            entry.runtime_data.fridge = controller
            assert fridge.options(c) == controller.options == ("off", "refrigerate", "freeze")
            assert GacFridgeModeSelect(c).options == ["off", "refrigerate", "freeze"]
            controller.temps["refrigerate"] = 1.0                   # kept from before: below this car's 2 °C
            assert controller._checked("refrigerate", controller._kept("refrigerate")) == (1, 2.0)
            for mode, value in (("refrigerate", 1), ("heat", 40)):
                with pytest.raises(ServiceValidationError):
                    controller._checked(mode, value)
            with pytest.raises(ServiceValidationError):
                await controller.async_set_option("heat")
            number = GacFridgeTemperature(c)
            assert number._all == (-15.0, 20.0)
            entry.runtime_data.coordinator = c
            summary = equipment_summary(entry)
            assert "- Climate (Off, On, Fan only; 18–30 °C; fan 1–7; 4 airflow choices; Heating and Cooling presets)" in summary
            assert "- Fridge mode (Off, Refrigerate, Freeze)" in summary
            assert "- Fridge temperature (2–20 or −15 to −1 °C by mode)" in summary
    asyncio.run(run())


UT_HATCHES = {   # as an Aion UT reported them (2026-10-14)
    "closed": [{"hatch": 1, "openModeFlag": 1, "openMode": 0}, {"hatch": 3, "openModeFlag": 0, "openMode": 0}],
    "boot": [{"hatch": 1, "openModeFlag": 1, "openMode": 0}, {"hatch": 3, "openModeFlag": 1, "openMode": 1}],
    "bonnet": [{"hatch": 1, "openModeFlag": 1, "openMode": 1}, {"hatch": 3, "openModeFlag": 0, "openMode": 0}],
}


@pytest.mark.parametrize("case,boot,hood", [("closed", False, False), ("boot", True, False), ("bonnet", False, True)])
def test_the_boot_and_hood_sensors_follow_their_own_hatch(case, boot, hood):
    from custom_components.gac_connect.binary_sensor import BINARY_SENSORS, GacBinaryEntity
    from test_regressions import coord

    c = coord(decode_status({"hatchAccessors": UT_HATCHES[case], "brakeAccessors": [{"alarms": []}]}))
    sensor = {d.key: GacBinaryEntity(c, d) for d in BINARY_SENSORS}
    assert (sensor["hatch_open"].is_on, sensor["hood_open"].is_on) == (boot, hood)
    assert sensor["brake_fluid"].is_on is False


def test_the_ut_shows_hood_and_brake_fluid(tmp_path):
    async def run():
        async with vehicle(tmp_path) as (_, _, c):
            c.client.get_control_capabilities = AsyncMock(return_value=UT_LIST)
            await c.capabilities.async_read_capability_list(c.client, "VIN")
            assert c.capabilities.includes("binary_sensor:brake_fluid")     # its brake group lists alarms
            assert not c.capabilities.includes("binary_sensor:hood_open")   # not until the car reports it
            c.capabilities.observe(decode_status({"hatchAccessors": UT_HATCHES["closed"]}))
            assert c.capabilities.includes("binary_sensor:hood_open")
            assert c.capabilities.explanation("binary_sensor:hood_open") == "Shown: seen in the car's reports."
    asyncio.run(run())
