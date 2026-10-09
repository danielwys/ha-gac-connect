"""Per-vehicle opt-out for entities that a car does not support.

The status response is not a dependable feature list: an absent reading can
also mean the car is asleep or the feature is idle. Keep this choice explicit.
"""
from __future__ import annotations

from collections.abc import Iterable

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_MODEL, CONF_OMIT_ENTITIES, CONF_VIN, DOMAIN

# Option IDs use the platform and existing unique-id suffix so an option can be
# applied to an entity that was already registered before this setting existed.
ENTITY_CHOICES: dict[str, dict[str, str]] = {
    "sensor": {
        "soc": "Battery", "range": "Range", "odometer": "Odometer",
        "cabin_temp": "Cabin temperature", "aux_voltage": "12V battery",
        "charge_time": "Estimated charge time", "charge_current": "Charge current",
        "charge_power": "Charge power", "charge_voltage": "Charge voltage",
        "pm25": "Cabin PM2.5", "last_report": "Last report",
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
    },
    "cover": {"windows": "Windows", "sunroof": "Sunroof", "tailgate": "Tailgate"},
    "climate": {"climate": "Climate"},
    "lock": {"doors": "Doors"},
}

OMIT_OPTIONS: dict[str, str] = {
    f"{domain}:{key}": f"{domain.replace('_', ' ').title()} · {label}"
    for domain in ("switch", "cover", "climate", "lock", "button", "sensor", "binary_sensor")
    for key, label in ENTITY_CHOICES[domain].items()
}


def is_aion_ut(entry: ConfigEntry) -> bool:
    """Recognise UT entries, including ones created before profiles existed."""
    model = entry.data.get(CONF_MODEL) or ""
    return isinstance(model, str) and model.strip().casefold().startswith("aion ut")


def model_omissions(entry: ConfigEntry) -> set[str]:
    """Entities known to be unsupported on this vehicle model."""
    return {"switch:steering_heat", "cover:sunroof"} if is_aion_ut(entry) else set()


def effective_omissions(entry: ConfigEntry) -> set[str]:
    return set(entry.options.get(CONF_OMIT_ENTITIES, ())) | model_omissions(entry)


def add_configured_entities(
    entry: ConfigEntry, add_entities: AddEntitiesCallback, domain: str, entities: Iterable
) -> None:
    """Never add entities explicitly omitted for this config entry."""
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
        if option in omitted and current.disabled_by is None:
            registry.async_update_entity(entity_id, disabled_by=er.RegistryEntryDisabler.INTEGRATION)
        elif option not in omitted and current.disabled_by is er.RegistryEntryDisabler.INTEGRATION:
            registry.async_update_entity(entity_id, disabled_by=None)
