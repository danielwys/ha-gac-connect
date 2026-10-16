"""GAC Connect (unofficial) — Home Assistant integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._vendor.gac_connect.client import GacClient


from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from ._vendor.gac_connect.errors import GacError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .account_alerts import AccountAlerts, store_key as account_alerts_store_key
from .cabin import controller as cabin_controller
from .capabilities import FRIDGE_ENTITIES, store_key as capabilities_store_key
from .const import (
    CONF_ENABLE_FRIDGE, CONF_MODEL, CONF_OMIT_ENTITIES, CONF_PROFILE, CONF_REGION, CONF_VIN, DOMAIN, PLATFORMS,
)
from .coordinator import GacCoordinator
from .session_store import ConfigEntryStore
from .entity_filter import apply_registry_choices
from .fridge import FridgeController, store_key
from .helpers import async_build_client
from .statistics import async_setup_cleanup

type GacConfigEntry = ConfigEntry[GacRuntime]


@dataclass
class GacRuntime:
    client: GacClient
    coordinator: GacCoordinator
    options: dict = field(default_factory=dict)  # snapshot: reload only when these change
    fridge: FridgeController | None = None
    alerts: AccountAlerts | None = None


async def async_setup(hass: HomeAssistant, _config: dict) -> bool:
    """Watch permanent sensor removals even while vehicle entries are unloaded."""
    async_setup_cleanup(hass)
    return True


# What the removed Aion UT profile omitted on its own; the car's capability list decides these now.
_UT_PROFILE_OMISSIONS = frozenset({
    "switch:steering_heat", "cover:sunroof", "binary_sensor:seat_3_ventilation", "binary_sensor:seat_4_ventilation",
    *(f"sensor:tyre_{i}_{kind}" for i in range(1, 5) for kind in ("pressure", "temp")),
})


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """1.2: vehicle profiles were removed; detection decides, and the owner's own choices stay.

    1.3: Unrestricted charging became Scheduled charging, with on and off swapped, so its
    default entity ID follows the new meaning. A custom ID is left alone.

    1.4: the Fridge / warmer box controls option was removed (the capability list decides);
    an owner's Off becomes Omit for each fridge entity.

    1.5: Battery preconditioning took the app's name, Smart battery temp, and its default
    entity ID follows. A custom ID is left alone.

    1.6: Pre-conditioning became A/C, as it shows the A/C however it was started; its
    default entity ID follows. A custom ID is left alone.
    """
    if entry.version == 1 and entry.minor_version < 2:
        options = dict(entry.options)
        profile = options.pop(CONF_PROFILE, None)
        model = str(entry.data.get(CONF_MODEL) or "").strip().casefold()
        if profile == "aion_ut" or (profile is None and (model == "aion ut" or model.startswith("aion ut "))):
            options[CONF_OMIT_ENTITIES] = sorted(set(options.get(CONF_OMIT_ENTITIES, ())) - _UT_PROFILE_OMISSIONS)
            options[CONF_ENABLE_FRIDGE] = True
        hass.config_entries.async_update_entry(entry, options=options, minor_version=2)
    if entry.version == 1 and entry.minor_version < 3:
        _rename_default_id(hass, entry, "charge_switch", "_unrestricted_charging", "_scheduled_charging")
        hass.config_entries.async_update_entry(entry, minor_version=3)
    if entry.version == 1 and entry.minor_version < 4:
        options = dict(entry.options)
        if options.pop(CONF_ENABLE_FRIDGE, None) is False:
            options[CONF_OMIT_ENTITIES] = sorted(set(options.get(CONF_OMIT_ENTITIES, ())) | FRIDGE_ENTITIES)
        hass.config_entries.async_update_entry(entry, options=options, minor_version=4)
    if entry.version == 1 and entry.minor_version < 5:
        _rename_default_id(hass, entry, "precondition_battery", "_battery_preconditioning", "_smart_battery_temp")
        hass.config_entries.async_update_entry(entry, minor_version=5)
    if entry.version == 1 and entry.minor_version < 6:
        _rename_default_id(hass, entry, "climate_power", "_pre_conditioning", "_a_c")
        hass.config_entries.async_update_entry(entry, minor_version=6)
    return True


def _rename_default_id(hass: HomeAssistant, entry: ConfigEntry, key: str, old: str, new: str) -> None:
    """Give a switch whose default entity ID ends in ``old`` the ending ``new``, unless that ID is taken."""
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("switch", DOMAIN, f"{entry.data[CONF_VIN]}_{key}")
    if entity_id and entity_id.endswith(old):
        renamed = entity_id.removesuffix(old) + new
        if registry.async_get(renamed) is None:
            registry.async_update_entity(entity_id, new_entity_id=renamed)


async def async_setup_entry(hass: HomeAssistant, entry: GacConfigEntry) -> bool:
    # Isolated session: this login's cookies must not mix with other integrations.
    http = async_create_clientsession(hass)
    client = await async_build_client(hass, entry.data[CONF_REGION], http, ConfigEntryStore(hass, entry))
    try:
        await client.load()
    except GacError as err:
        raise ConfigEntryNotReady(str(err)) from err

    _remove_retired_entities(hass, entry)
    coordinator = GacCoordinator(hass, entry, client)
    await coordinator.capabilities.async_load()
    await coordinator.async_config_entry_first_refresh()
    await coordinator.capabilities.async_read_capability_list_once(client, coordinator.vin)

    entry.runtime_data = GacRuntime(client=client, coordinator=coordinator, options=dict(entry.options))
    apply_registry_choices(hass, entry)
    cabin_controller(coordinator).async_setup(entry)
    entry.runtime_data.fridge = FridgeController(hass, entry, coordinator)
    await entry.runtime_data.fridge.async_setup(entry)
    entry.runtime_data.alerts = AccountAlerts(hass, entry, coordinator)
    await entry.runtime_data.alerts.async_setup()
    coordinator.start_push()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_reload_on_update))

    _register_services(hass)
    return True


# unique-id suffixes of entities earlier versions created and later versions replaced
_RETIRED: tuple[tuple[str, str], ...] = (
    ("button", "flash_lights"),
    ("button", "precondition_battery"),        # now a switch
    ("button", "charge_pause"),                # the car has no pause; Scheduled charging on is one
    ("switch", "seat_1_ventilation"), ("switch", "seat_2_ventilation"),   # now level selects
)


def _remove_retired_entities(hass: HomeAssistant, entry: GacConfigEntry) -> None:
    registry = er.async_get(hass)
    for domain, suffix in _RETIRED:
        entity_id = registry.async_get_entity_id(domain, DOMAIN, f"{entry.data[CONF_VIN]}_{suffix}")
        if entity_id:
            registry.async_remove(entity_id)


async def async_unload_entry(hass: HomeAssistant, entry: GacConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.coordinator.capabilities.async_close()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: GacConfigEntry) -> None:
    """Remove this entry's statistics, credentials, preferences and equipment."""
    from homeassistant.helpers.storage import Store

    async_setup_cleanup(hass).async_remove_entry(entry)
    await Store(hass, 1, store_key(entry.entry_id)).async_remove()
    await Store(hass, 1, capabilities_store_key(entry.entry_id)).async_remove()
    await Store(hass, 1, account_alerts_store_key(entry.entry_id)).async_remove()
    await ConfigEntryStore(hass, entry).remove()


async def _reload_on_update(hass: HomeAssistant, entry: GacConfigEntry) -> None:
    # The token store writes refreshed tokens into entry.data; that must not
    # bounce the integration (it would close the session under a running poll).
    runtime = getattr(entry, "runtime_data", None)
    if runtime is not None and dict(entry.options) == runtime.options:
        return
    await hass.config_entries.async_reload(entry.entry_id)


def _register_services(hass: HomeAssistant) -> None:
    # Registered lazily on first entry; imported here to keep module load light.
    from .services import async_register_services

    async_register_services(hass)
