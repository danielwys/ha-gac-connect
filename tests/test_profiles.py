import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import device_registry as dr

from custom_components.gac_connect.config_flow import GacConfigFlow
from custom_components.gac_connect.entity_filter import UT_OMISSIONS, apply_registry_choices, effective_omissions
from test_session_store import make_entry


def test_ut_preset_omits_all_eight_tyre_readings():
    flow = GacConfigFlow()
    flow._create_vehicle_entry = Mock()
    asyncio.run(flow.async_step_profile({"profile": "aion_ut"}))
    options = flow._create_vehicle_entry.call_args.args[0]
    assert options["enable_fridge"] is False
    assert set(options["omit_entities"]) == UT_OMISSIONS
    assert len([key for key in UT_OMISSIONS if key.startswith("sensor:tyre_")]) == 8


def test_old_ut_entries_get_the_same_exclusions_but_other_models_do_not():
    gated = {"button:vehicle_power_off"}
    assert effective_omissions(SimpleNamespace(data={"model": "Aion UT"}, options={"enable_tracker": True})) == UT_OMISSIONS | gated
    assert effective_omissions(SimpleNamespace(data={"model": "Aion V"}, options={"enable_tracker": True})) == gated


def test_ut_cleanup_removes_only_its_unsupported_registrations(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        entry = make_entry(hass, {"vin": "VIN", "model": "Aion UT"})
        if hasattr(dr, "async_setup"):
            dr.async_setup(hass)
        await dr.async_load(hass)
        await er.async_load(hass)
        registry = er.async_get(hass)
        created = []
        for key in UT_OMISSIONS:
            domain, suffix = key.split(":")
            created.append(registry.async_get_or_create(domain, "gac_connect", f"VIN_{suffix}"))
        battery = registry.async_get_or_create("sensor", "gac_connect", "VIN_soc")
        other = registry.async_get_or_create("sensor", "gac_connect", "OTHER_tyre_1_pressure")
        apply_registry_choices(hass, entry)
        assert all(registry.async_get(item.entity_id) is None for item in created)
        assert registry.async_get(battery.entity_id) is not None
        assert registry.async_get(other.entity_id) is not None

    asyncio.run(run())
