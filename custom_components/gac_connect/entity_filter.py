"""Apply equipment profiles, passive discovery and explicit entity choices."""
from __future__ import annotations

from collections.abc import Iterable

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_OMIT_ENTITIES, CONF_VIN, DOMAIN, PROFILE_AUTO, PROFILE_UT
from .profiles import profile_for

# Option IDs use the platform and existing unique-id suffix so an option can be
# applied to an entity that was already registered before this setting existed.
ENTITY_CHOICES: dict[str, dict[str, str]] = {
    "sensor": {
        "soc": "Battery", "range": "Range", "odometer": "Odometer",
        "cabin_temp": "Cabin temperature", "aux_voltage": "12V battery",
        "charge_time": "Estimated charge time", "charge_current": "Charge current",
        "charge_power": "Charge power", "charge_voltage": "Charge voltage",
        "pm25": "Cabin PM2.5", "last_report": "Last report",
        "charge_window_start": "Reported charge window start", "charge_window_stop": "Reported charge window stop",
        "fridge_keep_mode": "Fridge after leaving", "fridge_time_left": "Fridge time left",
        **{f"tyre_{i}_{kind}": f"Tyre {i} {label}"
           for i in range(1, 5)
           for kind, label in (("pressure", "pressure"), ("temp", "temperature"))},
    },
    "binary_sensor": {
        "plugged": "Plugged in", "charging": "Charging", "locked": "Lock state",
        "door_open": "Door state", "window_open": "Window state",
        "hatch_open": "Boot state", "online": "Online", "charger_locked": "Charger lock",
    },
    "button": {
        "charge_now": "Charge now", "charge_pause": "Pause charging",
        "horn": "Sound horn", "precondition_battery": "Precondition battery",
    },
    "switch": {
        "charge_switch": "Scheduled charging", "climate_power": "Pre-conditioning",
        "steering_heat": "Steering wheel heat", "lights": "Flash lights",
        "ventilation": "Ventilation",
        "fridge": "Fridge",
    },
    "cover": {"windows": "Windows", "sunroof": "Sunroof", "tailgate": "Tailgate"},
    "climate": {"climate": "Climate"},
    "lock": {"doors": "Doors"},
    "select": {"fridge_mode": "Fridge mode"},
    "number": {"fridge_temperature": "Fridge temperature"},
    "device_tracker": {"location": "Location"},
}

OMIT_OPTIONS: dict[str, str] = {
    f"{domain}:{key}": f"{domain.replace('_', ' ').title()} · {label}"
    for domain in ENTITY_CHOICES
    for key, label in ENTITY_CHOICES[domain].items()
}

UT_OMISSIONS = frozenset({
    "switch:steering_heat", "cover:sunroof",
    *(f"sensor:tyre_{i}_{kind}" for i in range(1, 5) for kind in ("pressure", "temp")),
})


def is_aion_ut(entry: ConfigEntry) -> bool:
    """Apply the UT preset, retaining defaults for entries without a stored profile."""
    return profile_for(entry) == PROFILE_UT


def model_omissions(entry: ConfigEntry) -> set[str]:
    """Entities known to be unsupported on this vehicle model."""
    return set(UT_OMISSIONS) if is_aion_ut(entry) else set()


def effective_omissions(entry: ConfigEntry) -> set[str]:
    return set(entry.options.get(CONF_OMIT_ENTITIES, ())) | model_omissions(entry)


def add_configured_entities(
    entry: ConfigEntry, add_entities: AddEntitiesCallback, domain: str, entities: Iterable
) -> None:
    """Never add entities explicitly omitted for this config entry."""
    if profile_for(entry) == PROFILE_AUTO:
        coordinator = entry.runtime_data.coordinator
        manager = coordinator.capabilities
        candidates = list(entities)
        added: set[str] = set()
        prefix = f"{entry.data[CONF_VIN]}_"

        def add_detected() -> None:
            ready = []
            for entity in candidates:
                key = f"{domain}:{entity.unique_id.removeprefix(prefix)}"
                if key not in added and manager.includes(key):
                    added.add(key)
                    ready.append(entity)
            if ready:
                apply_registry_choices(coordinator.hass, entry)
                add_entities(ready)

        entry.async_on_unload(coordinator.async_add_listener(add_detected))
        add_detected()
        return
    omitted = effective_omissions(entry)
    prefix = f"{entry.data[CONF_VIN]}_"
    add_entities(
        entity for entity in entities
        if f"{domain}:{entity.unique_id.removeprefix(prefix)}" not in omitted
    )


def apply_registry_choices(hass, entry: ConfigEntry) -> None:
    """Remove model-incompatible entities and disable optional omissions."""
    registry = er.async_get(hass)
    forced = model_omissions(entry)
    omitted = effective_omissions(entry)
    runtime = getattr(entry, "runtime_data", None)
    manager = getattr(getattr(runtime, "coordinator", None), "capabilities", None)
    for option in OMIT_OPTIONS:
        domain, key = option.split(":", 1)
        entity_id = registry.async_get_entity_id(domain, DOMAIN, f"{entry.data[CONF_VIN]}_{key}")
        if entity_id is None:
            continue
        if option in forced:
            # This model cannot use the entity; remove registrations left by
            # older integration versions rather than leaving a disabled row.
            registry.async_remove(entity_id)
            continue
        current = registry.async_get(entity_id)
        excluded = not manager.includes(option) if manager is not None else option in omitted
        if excluded and current.disabled_by is None:
            registry.async_update_entity(entity_id, disabled_by=er.RegistryEntryDisabler.INTEGRATION)
        elif not excluded and current.disabled_by is er.RegistryEntryDisabler.INTEGRATION:
            registry.async_update_entity(entity_id, disabled_by=None)
