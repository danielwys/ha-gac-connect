"""Offline discovery through real HA coordinators, registries and option flows."""
import asyncio
import importlib
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from zoneinfo import ZoneInfo

import pytest
from custom_components.gac_connect._vendor.gac_connect.push import PushResult
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er

from custom_components.gac_connect import async_remove_entry, async_unload_entry
from custom_components.gac_connect.api import GacClient, Support, decode_status
from custom_components.gac_connect.capabilities import CapabilityManager, ENTITY_FEATURES, store_key
from custom_components.gac_connect.command_tracking import async_send
from custom_components.gac_connect.config_flow import GacConfigFlow, GacOptionsFlow, PROFILES
from custom_components.gac_connect.const import PLATFORMS
from custom_components.gac_connect.coordinator import GacCoordinator
from custom_components.gac_connect.diagnostics import async_get_config_entry_diagnostics
from custom_components.gac_connect.entity_filter import OMIT_OPTIONS, UT_OMISSIONS, apply_registry_choices
from custom_components.gac_connect.lock import GacLock
from test_regressions import ACCEPTED, service_registry
from test_session_store import make_entry


@asynccontextmanager
async def vehicle(tmp_path, *, model="Unknown trim", options=None):
    hass = HomeAssistant(str(tmp_path))
    entry = make_entry(hass, {"region": "AU", "vin": "VIN", "model": model})
    hass.config_entries._store = Mock()
    hass.config_entries.async_update_entry(entry, options={
        "profile": "auto_detect", "enable_fridge": True, **(options or {}),
    })
    if hasattr(dr, "async_setup"):
        dr.async_setup(hass)
    await dr.async_load(hass)
    await er.async_load(hass)
    client = SimpleNamespace(
        get_status=AsyncMock(return_value=decode_status({})), mqtt_info=AsyncMock(), decrypt_push=Mock(),
        command=AsyncMock(return_value=ACCEPTED), unlock=AsyncMock(return_value=ACCEPTED),
    )
    coordinator = GacCoordinator(hass, entry, client)
    entry.runtime_data = SimpleNamespace(coordinator=coordinator, client=client)
    try:
        yield hass, entry, coordinator
    finally:
        if coordinator.capabilities is not None:
            await coordinator.capabilities.async_close()
        await entry._async_process_on_unload(hass)
        await coordinator.async_shutdown()


def result(sid="s1", *, ok=True, vin="VIN"):
    return PushResult(ok, "0000" if ok else "13101", None, {}, session_id=sid, vin=vin)


def test_auto_profile_is_available_at_setup_and_keeps_unknown_features_automatic():
    assert PROFILES["auto_detect"] == "Auto detect"
    flow = GacConfigFlow()
    flow._create_vehicle_entry = Mock()
    asyncio.run(flow.async_step_profile({"profile": "auto_detect"}))
    assert flow._create_vehicle_entry.call_args.args[0] == {
        "profile": "auto_detect", "enable_fridge": True, "omit_entities": [], "enable_entities": [],
    }


@pytest.mark.parametrize("stored_profile", [None, "aion_ut"])
def test_existing_ut_can_switch_to_auto_without_inheriting_preset_exclusions(tmp_path, stored_profile):
    async def run():
        options = {"omit_entities": sorted(UT_OMISSIONS | {"sensor:pm25"}),
                   "enable_fridge": False, "enable_tracker": False, "experimental_pinless_unlock": True}
        if stored_profile:
            options["profile"] = stored_profile
        async with vehicle(tmp_path, model="Aion UT") as (hass, entry, _):
            hass.config_entries.async_update_entry(entry, options=options)
            flow = GacOptionsFlow()
            flow.hass, flow.handler = hass, entry.entry_id
            form = await flow.async_step_settings()
            assert "profile" in form["data_schema"].schema
            response = await flow.async_step_settings({"profile": "auto_detect"})
            assert response["type"] == "menu" and response["step_id"] == "init"
            assert entry.options["omit_entities"] == ["sensor:pm25"]
            assert entry.options["enable_fridge"] is True
            assert entry.options["enable_tracker"] is False
            assert entry.options["experimental_pinless_unlock"] is True
    asyncio.run(run())


def test_conflicting_manual_overrides_are_rejected(tmp_path):
    async def run():
        async with vehicle(tmp_path) as (hass, entry, _):
            flow = GacOptionsFlow()
            flow.hass, flow.handler = hass, entry.entry_id
            response = await flow.async_step_settings({"enable_entities": ["cover:sunroof"],
                                                   "omit_entities": ["cover:sunroof"]})
            assert response["errors"] == {"enable_entities": "conflicting_entity_choices"}
    asyncio.run(run())


def test_live_platform_discovery_adds_only_observed_entities_once_without_commands(tmp_path):
    async def run():
        async with vehicle(tmp_path) as (hass, entry, coordinator):
            added = []
            for platform in PLATFORMS:
                module = importlib.import_module(f"custom_components.gac_connect.{platform}")
                await module.async_setup_entry(hass, entry, lambda entities, p=platform: added.extend(
                    (p, entity.unique_id) for entity in entities))
            health = {("sensor", f"VIN_{key}") for key in ("data_freshness", "last_refresh", "polling_status", "last_command")}
            assert set(added) == health | {("button", "VIN_refresh")}

            coordinator.client.get_status.return_value = decode_status({
                "drivingOverviewAccessors": [{"remainElectricityPercentage": 0}],
                "sunroofAccessors": [{"openMode": 0}],
                "steeringAccessors": [{"steering": 0}],
                "tyreAccessors": [{"tyre": 3, "pressure": 240, "temperatureFlag": 0, "temperature": 22},
                                  {"tyre": 1, "pressure": -1, "temperature": -1}],
                "refrigeratorAccessors": [{"workingMode": 3}],
            })
            await coordinator.async_refresh()
            assert set(added) == health | {
                ("button", "VIN_refresh"), ("sensor", "VIN_soc"), ("cover", "VIN_sunroof"),
                ("switch", "VIN_steering_heat"), ("sensor", "VIN_tyre_3_pressure"),
                ("switch", "VIN_fridge"), ("select", "VIN_fridge_mode"), ("number", "VIN_fridge_temperature"),
            }
            coordinator.client.get_status.return_value = decode_status({})
            await coordinator.async_refresh()
            assert len(added) == 8 + len(health)  # a sleeping/missing report neither removes nor duplicates entities
            coordinator.client.command.assert_not_called()
            coordinator.client.unlock.assert_not_called()
            coordinator.client.mqtt_info.assert_not_called()
            assert coordinator.capabilities.detector.command("sunroof-open").state is Support.UNKNOWN
            await entry._async_process_on_unload(hass)
            assert not coordinator._listeners
    asyncio.run(run())


def test_manual_overrides_respect_privacy_and_unlock_opt_in(tmp_path):
    async def run():
        async with vehicle(tmp_path, options={
            "enable_entities": ["switch:fridge", "button:horn", "cover:sunroof", "device_tracker:location", "lock:doors"],
            "omit_entities": ["cover:sunroof"], "enable_tracker": False, "enable_fridge": False,
        }) as (hass, entry, coordinator):
            manager = coordinator.capabilities
            assert manager.includes("button:horn")
            assert not manager.includes("cover:sunroof")
            assert not manager.includes("switch:fridge")
            assert not manager.includes("device_tracker:location")
            assert not manager.includes("button:charge_pause")
            tracker = importlib.import_module("custom_components.gac_connect.device_tracker")
            add = Mock()
            await tracker.async_setup_entry(hass, entry, add)
            add.assert_not_called()
            entity = GacLock(coordinator)
            with pytest.raises(HomeAssistantError, match="disabled"):
                await entity.async_unlock()
            coordinator.client.unlock.assert_not_called()
    asyncio.run(run())


def test_ut_auto_uses_model_lock_decoding_and_accepts_positive_variant_evidence(tmp_path):
    async def run():
        async with vehicle(tmp_path, model="Aion UT") as (_, _, coordinator):
            manager = coordinator.capabilities
            assert not manager.includes("sensor:tyre_1_pressure")
            assert not manager.includes("cover:sunroof")
            status = decode_status({
                "doorAccessors": [{"door": i + 1, "lock": value} for i, value in enumerate([0, 0, -1, -1])],
                "sunroofAccessors": [{"openMode": 0}],
                "tyreAccessors": [{"tyre": 1, "pressure": 230}],
            })
            assert status.locked is None
            manager.observe(status)
            assert manager.includes("binary_sensor:locked")
            assert manager.includes("cover:sunroof")
            assert manager.includes("sensor:tyre_1_pressure")
            assert not manager.includes("sensor:tyre_1_temp")
            assert status.locked is None  # discovery leaves the report unchanged
    asyncio.run(run())


def test_registry_preserves_ids_and_user_disabled_rows_through_discovery_and_profiles(tmp_path):
    async def run():
        async with vehicle(tmp_path) as (hass, entry, coordinator):
            registry = er.async_get(hass)
            sunroof = registry.async_get_or_create("cover", "gac_connect", "VIN_sunroof")
            steering = registry.async_get_or_create("switch", "gac_connect", "VIN_steering_heat",
                                                     disabled_by=er.RegistryEntryDisabler.USER)
            other = registry.async_get_or_create("sensor", "gac_connect", "OTHER_soc")
            apply_registry_choices(hass, entry)
            assert registry.async_get(sunroof.entity_id).disabled_by is er.RegistryEntryDisabler.INTEGRATION
            coordinator.capabilities.observe(decode_status({"sunroofAccessors": [{"openMode": 0}],
                                                           "steeringAccessors": [{"steering": 0}]}))
            apply_registry_choices(hass, entry)
            assert registry.async_get(sunroof.entity_id).disabled_by is None
            assert registry.async_get(steering.entity_id).disabled_by is er.RegistryEntryDisabler.USER
            assert registry.async_get(other.entity_id).disabled_by is None
            # A later manual profile restores previously automatic-disabled rows.
            unknown = registry.async_get_or_create("button", "gac_connect", "VIN_horn")
            apply_registry_choices(hass, entry)
            assert registry.async_get(unknown.entity_id).disabled_by is er.RegistryEntryDisabler.INTEGRATION
            await coordinator.capabilities.async_close()
            coordinator.capabilities = None
            hass.config_entries.async_update_entry(entry, options={"profile": "custom"})
            apply_registry_choices(hass, entry)
            assert registry.async_get(unknown.entity_id).disabled_by is None
            assert registry.async_get(steering.entity_id).disabled_by is er.RegistryEntryDisabler.USER
    asyncio.run(run())


def test_evidence_survives_reload_but_does_not_leak_between_vehicles(tmp_path):
    async def run():
        async with vehicle(tmp_path) as (hass, entry, coordinator):
            manager = coordinator.capabilities
            manager.observe(decode_status({"tyreAccessors": [{"tyre": 4, "temperature": -1, "pressure": 230}]}))
            await coordinator.commands.send("sunroof-close", coordinator.client.command("VIN", "sunroof-close"))
            assert coordinator.commands.result(result())
            await manager.async_close()
            text = (tmp_path / ".storage" / store_key(entry.entry_id)).read_text()
            assert "VIN" not in text and "230" not in text
            restored = CapabilityManager(hass, entry)
            await restored.async_load()
            restored.observe(decode_status({}))
            assert restored.includes("sensor:tyre_4_temp")
            assert restored.detector.command("sunroof-close").state is Support.SUPPORTED
            assert restored.detector.command("sunroof-open").state is Support.UNKNOWN
            for key, value in (("vin", "OTHER"), ("region", "GB")):
                different = SimpleNamespace(entry_id=entry.entry_id, options=entry.options, data={**entry.data, key: value})
                foreign = CapabilityManager(hass, different)
                await foreign.async_load()
                assert not foreign.includes("sensor:tyre_4_temp")
            await async_remove_entry(hass, entry)
            assert not (tmp_path / ".storage" / store_key(entry.entry_id)).exists()
    asyncio.run(run())


def test_unload_flushes_evidence_only_after_platforms_unload(tmp_path):
    async def run():
        async with vehicle(tmp_path) as (hass, entry, coordinator):
            manager = coordinator.capabilities
            manager.observe(decode_status({"drivingOverviewAccessors": [{"remainElectricityPercentage": 0.5}]}))
            with patch.object(hass.config_entries, "async_unload_platforms", new=AsyncMock(return_value=False)):
                assert not await async_unload_entry(hass, entry)
                assert not (tmp_path / ".storage" / store_key(entry.entry_id)).exists()
            with patch.object(hass.config_entries, "async_unload_platforms", new=AsyncMock(return_value=True)):
                assert await async_unload_entry(hass, entry)
                assert (tmp_path / ".storage" / store_key(entry.entry_id)).exists()
    asyncio.run(run())


def test_only_matched_success_confirms_a_command_and_directions_are_independent(tmp_path):
    async def run():
        async with vehicle(tmp_path) as (_, _, coordinator):
            manager = coordinator.capabilities
            await async_send(coordinator, "sunroof-close", coordinator.client.command("VIN", "sunroof-close"))
            assert manager.detector.command("sunroof-close").state is Support.UNKNOWN  # accepted, not applied
            assert not coordinator.commands.result(result("other"))
            assert not coordinator.commands.result(result(vin="OTHER"))
            assert not coordinator.commands.result(result(ok=None))  # a progress/unknown message must not consume the request
            assert coordinator.commands.result(result())
            assert manager.detector.command("sunroof-close").state is Support.SUPPORTED
            assert manager.detector.command("sunroof-open").state is Support.UNKNOWN
            assert not coordinator.commands.result(result())  # duplicate result
            await coordinator.commands.send("sunroof-open", coordinator.client.command("VIN", "sunroof-open"))
            assert coordinator.commands.result(result(ok=False))
            assert manager.detector.command("sunroof-open").state is Support.UNKNOWN
            assert manager.includes("cover:sunroof")  # generic failure does not erase equipment evidence
    asyncio.run(run())


def test_early_command_result_is_correlated_after_acceptance(tmp_path):
    async def run():
        async with vehicle(tmp_path) as (_, _, coordinator):
            manager = coordinator.capabilities

            async def request():
                coordinator.commands.result(result("unrelated"))
                coordinator.commands.result(result())
                return ACCEPTED

            await async_send(coordinator, "horn-on", request())
            assert manager.includes("button:horn")
            assert manager.detector.command("horn-on").state is Support.SUPPORTED
            assert not coordinator.commands._early
    asyncio.run(run())


@pytest.mark.parametrize("surface,command", [
    ("cover", "sunroof-close"), ("lock", "lock"), ("button", "horn-on"),
    ("switch", "steering-on"), ("cabin", "aircon-on"), ("fridge", "fridge-off"),
    ("charge_button", "charge-now"), ("charge_service", "charge-now"), ("window_service", "charge-window"),
])
def test_entity_and_service_actions_feed_the_same_discovery_evidence(tmp_path, surface, command):
    async def run():
        from custom_components.gac_connect import cabin, fridge
        from custom_components.gac_connect.button import BUTTONS, GacButtonEntity
        from custom_components.gac_connect.cover import COVERS, GacCoverEntity
        from custom_components.gac_connect.switch import SWITCHES, GacCommandSwitch

        async with vehicle(tmp_path) as (hass, entry, coordinator):
            client = object.__new__(GacClient)
            client.command = AsyncMock(return_value=ACCEPTED)
            client._reservation = AsyncMock(return_value=ACCEPTED)
            client._tz = ZoneInfo("UTC")
            coordinator.client = client
            coordinator.async_request_refresh = AsyncMock()
            cabin.controller(coordinator).async_setup(entry)
            entry.runtime_data.fridge = fridge.FridgeController(hass, entry, coordinator)
            await entry.runtime_data.fridge.async_setup(entry)
            if surface == "cabin":
                await cabin.controller(coordinator).async_set_power(True)
            elif surface == "fridge":
                await entry.runtime_data.fridge.async_turn_off()
            elif surface.endswith("service"):
                if surface == "charge_service":
                    name, data = "charge_now", {}
                else:
                    name, data = "set_charge_window", {"start": "22:00", "stop": "06:00"}
                await service_registry(coordinator)[name][0](SimpleNamespace(data=data))
            else:
                if surface == "cover":
                    entity = GacCoverEntity(coordinator, next(d for d in COVERS if d.key == "sunroof"))
                    action = entity.async_close_cover
                elif surface == "lock":
                    entity = GacLock(coordinator)
                    action = entity.async_lock
                elif surface == "switch":
                    entity = GacCommandSwitch(coordinator, next(d for d in SWITCHES if d.key == "steering_heat"))
                    action = entity.async_turn_on
                    entry.async_on_unload(entity._cancel_expiry)
                else:
                    key = "charge_now" if surface == "charge_button" else "horn"
                    entity = GacButtonEntity(coordinator, next(d for d in BUTTONS if d.key == key))
                    action = entity.async_press
                entity.async_write_ha_state = Mock()
                entity.hass = hass
                if surface in {"lock", "cover"}:
                    entry.async_on_unload(entity._clear_pending)
                await action()
            assert client.command.await_count + client._reservation.await_count == 1
            assert len(coordinator._refresh_deadlines) == 3
            coordinator.async_request_refresh.assert_not_awaited()
            assert coordinator.capabilities.detector.command(command).state is Support.UNKNOWN
            assert coordinator.commands.result(result())
            assert coordinator.capabilities.detector.command(command).state is Support.SUPPORTED
    asyncio.run(run())


def test_unreadable_cache_is_relearned_from_reports(tmp_path):
    async def run():
        async with vehicle(tmp_path) as (_, _, coordinator):
            manager = coordinator.capabilities
            with patch.object(manager._store, "async_load", new=AsyncMock(side_effect=OSError("unreadable"))):
                await manager.async_load()
            assert not manager.includes("cover:sunroof")
            manager.observe(decode_status({"sunroofAccessors": [{"openMode": 0}]}))
            assert manager.includes("cover:sunroof")
    asyncio.run(run())


def test_failed_cancelled_and_expired_requests_never_confirm_equipment(tmp_path):
    async def run():
        async with vehicle(tmp_path) as (_, _, coordinator):
            manager = coordinator.capabilities
            for failure in (HomeAssistantError("offline"), asyncio.CancelledError()):
                request = AsyncMock(side_effect=failure)
                with pytest.raises(type(failure)):
                    await coordinator.commands.send("horn-on", request())
                assert not coordinator.commands.pending
                assert not coordinator.commands.result(result())
            with patch("custom_components.gac_connect.command_tracking.monotonic", return_value=0):
                await coordinator.commands.send("horn-on", coordinator.client.command("VIN", "horn-on"))
            with patch("custom_components.gac_connect.command_tracking.monotonic", return_value=181):
                assert not coordinator.commands.result(result())
            assert not manager.includes("button:horn")
    asyncio.run(run())


def test_explicit_service_action_and_push_result_can_discover_a_control(tmp_path):
    async def run():
        async with vehicle(tmp_path) as (hass, entry, coordinator):
            handler, _ = service_registry(coordinator)["send_command"]
            await handler(SimpleNamespace(data={"command": "ventilate-on"}))
            coordinator.client.command.assert_awaited_once_with("VIN", "ventilate-on")
            coordinator._schedule_result_refresh = Mock()
            device = dr.async_get(hass).async_get_or_create(
                config_entry_id=entry.entry_id, identifiers={("gac_connect", "VIN")})
            events = []

            @callback
            def capture(event):
                events.append(event.data)

            entry.async_on_unload(hass.bus.async_listen("gac_connect_command_result", capture))
            await coordinator._on_command_result("test", b"", result())
            await hass.async_block_till_done()
            assert events[0]["device_id"] == device.id
            assert coordinator.capabilities.includes("switch:ventilation")
            assert coordinator.capabilities.detector.command("ventilate-off").state is Support.UNKNOWN
    asyncio.run(run())


def test_diagnostics_explain_unknown_observed_and_forced_features_without_raw_values(tmp_path):
    async def run():
        async with vehicle(tmp_path, options={"enable_entities": ["button:horn"]}) as (hass, entry, coordinator):
            coordinator.capabilities.observe(decode_status({"steeringAccessors": [{"steering": 0}]}))
            data = (await async_get_config_entry_diagnostics(hass, entry))["capabilities"]
            assert data["features"]["steering_heat"] == {"state": "supported", "source": "observed_telemetry"}
            assert data["commands"]["steering-on"]["state"] == "unknown"
            assert data["entities"]["button:horn"] == {"shown": True, "override": "enable"}
            assert "VIN" not in json.dumps(data)
    asyncio.run(run())


def test_every_configurable_entity_has_a_detection_rule_or_is_explicitly_unsupported():
    assert set(OMIT_OPTIONS) - set(ENTITY_FEATURES) == {"button:charge_pause"}
