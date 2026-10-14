"""The car's capability list decides which entities Auto detect shows."""
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.components.climate import ClimateEntityFeature
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.gac_connect.api import decode_status
from custom_components.gac_connect.capabilities import CapabilityManager
from custom_components.gac_connect.climate import GacClimate
from custom_components.gac_connect.config_flow import GacOptionsFlow
from custom_components.gac_connect.equipment import equipment_summary
from custom_components.gac_connect.select import GacSeatVentilationSelect
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
            assert restored.ranges["seat_3_ventilation_levels"] == ()
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


def test_a_list_saved_before_ranges_were_kept_is_read_once_more(tmp_path):
    async def run():
        async with vehicle(tmp_path) as (hass, entry, c):
            c.client.get_control_capabilities = AsyncMock(return_value=UT_LIST)
            await c.capabilities.async_read_capability_list(c.client, "VIN")
            snapshot = c.capabilities._snapshot()
            del snapshot["ranges"]
            await c.capabilities._store.async_save(snapshot)
            older = CapabilityManager(hass, entry)
            await older.async_load()
            assert not older.capability_list_read and older.ranges == {}
            assert not older.includes("cover:sunroof")          # its equipment flags still apply meanwhile
            await older.async_read_capability_list_once(c.client, "VIN")
            assert older.capability_list_read and older.ranges["blowing_modes"] == (1, 2, 3, 4)
    asyncio.run(run())
