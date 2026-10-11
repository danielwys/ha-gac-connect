"""Binary sensors: plugged, charging, open/closed, locked, online."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ._vendor.gac_connect.models import VehicleStatus

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.const import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import GacConfigEntry
from .const import CONF_MODEL
from .entity import GacEntity
from .entity_filter import add_configured_entities
from .lock_state import doors_unlocked


@dataclass(frozen=True, kw_only=True)
class GacBinary(BinarySensorEntityDescription):
    value: Callable[[VehicleStatus], bool | None]


BINARY_SENSORS: tuple[GacBinary, ...] = (
    GacBinary(key="plugged", translation_key="plugged", device_class=BinarySensorDeviceClass.PLUG,
              value=lambda s: s.plugged_in),
    GacBinary(key="charging", translation_key="charging", device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
              value=lambda s: s.charging),
    # HA lock/opening semantics: on = unlocked / open.
    GacBinary(key="locked", translation_key="locked", device_class=BinarySensorDeviceClass.LOCK,
              value=doors_unlocked),
    GacBinary(key="door_open", translation_key="door_open", device_class=BinarySensorDeviceClass.DOOR,
              value=lambda s: s.door_open),
    GacBinary(key="window_open", translation_key="window_open", device_class=BinarySensorDeviceClass.WINDOW,
              value=lambda s: s.window_open),
    GacBinary(key="hatch_open", translation_key="hatch_open", device_class=BinarySensorDeviceClass.OPENING,
              value=lambda s: s.hatch_open),
    GacBinary(key="online", translation_key="online", device_class=BinarySensorDeviceClass.CONNECTIVITY,
              entity_category=EntityCategory.DIAGNOSTIC, value=lambda s: s.online),
    GacBinary(key="charger_locked", translation_key="charger_locked", device_class=BinarySensorDeviceClass.LOCK,
              entity_category=EntityCategory.DIAGNOSTIC,
              value=lambda s: (not s.charger_locked) if s.charger_locked is not None else None),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: GacConfigEntry, add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data.coordinator
    add_configured_entities(entry, add_entities, "binary_sensor",
                            (GacBinaryEntity(coordinator, d) for d in BINARY_SENSORS))


class GacBinaryEntity(GacEntity, BinarySensorEntity):
    entity_description: GacBinary

    def __init__(self, coordinator, description: GacBinary) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        if self.status is None:
            return None
        if self.entity_description.key == "locked":
            return doors_unlocked(self.status, self.coordinator.config_entry.data.get(CONF_MODEL))
        return self.entity_description.value(self.status)
