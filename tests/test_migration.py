"""Entries from before 1.2 drop their vehicle profile and keep the owner's own choices."""
import asyncio

import pytest

from custom_components.gac_connect import _UT_PROFILE_OMISSIONS, async_migrate_entry
from test_autodetect import vehicle


@pytest.mark.parametrize("stored_profile", [None, "aion_ut"])
def test_aion_ut_entries_lose_only_the_preset_omissions(tmp_path, stored_profile):
    async def run():
        options = {"omit_entities": sorted(_UT_PROFILE_OMISSIONS | {"sensor:pm25"}), "enable_fridge": False,
                   "enable_tracker": False, "experimental_pinless_unlock": True}
        if stored_profile:
            options["profile"] = stored_profile
        async with vehicle(tmp_path, model="Aion UT") as (hass, entry, _):
            hass.config_entries.async_update_entry(entry, options=options, minor_version=1)
            assert await async_migrate_entry(hass, entry)
            assert entry.minor_version == 2 and "profile" not in entry.options
            assert entry.options["omit_entities"] == ["sensor:pm25"]
            assert entry.options["enable_fridge"] is True
            assert entry.options["enable_tracker"] is False and entry.options["experimental_pinless_unlock"] is True
            assert len([key for key in _UT_PROFILE_OMISSIONS if key.startswith("sensor:tyre_")]) == 8
    asyncio.run(run())


@pytest.mark.parametrize("profile", ["custom", "aion_v", "auto_detect"])
def test_other_profiles_keep_every_choice(tmp_path, profile):
    async def run():
        options = {"profile": profile, "omit_entities": ["cover:sunroof", "sensor:tyre_1_temp"], "enable_fridge": False}
        async with vehicle(tmp_path, model="Aion UT") as (hass, entry, _):
            hass.config_entries.async_update_entry(entry, options=options, minor_version=1)
            assert await async_migrate_entry(hass, entry)
            assert entry.options == {"omit_entities": ["cover:sunroof", "sensor:tyre_1_temp"], "enable_fridge": False}
    asyncio.run(run())


def test_migration_runs_once(tmp_path):
    async def run():
        async with vehicle(tmp_path, model="Aion UT") as (hass, entry, _):
            hass.config_entries.async_update_entry(entry, options={"omit_entities": ["cover:sunroof"]}, minor_version=2)
            assert await async_migrate_entry(hass, entry)
            assert entry.options["omit_entities"] == ["cover:sunroof"]   # a later Omit choice stays
    asyncio.run(run())
