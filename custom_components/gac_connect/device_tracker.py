"""Device tracker: the vehicle's GPS location. Off by default for privacy.

The tracker carries the car's picture so the map shows the car, not the entity's
initials: the one set in the options, or else a bundled render for the model.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import GacConfigEntry
from .const import CONF_MODEL, DOMAIN
from .coordinator import GacCoordinator
from .entity import GacEntity
from .entity_filter import add_configured_entities
from .location import location_enabled, shown_position

_LOGGER = logging.getLogger(__name__)

PICTURES_URL = f"/{DOMAIN}/pictures"
_PICTURES_DIR = Path(__file__).parent / "pictures"
# model name as the service reports it (lower case) -> bundled picture
_BUNDLED: dict[str, str] = {"aion v": "aion-v.png"}
_REGISTERED = f"{DOMAIN}_pictures_registered"
_REGISTER_LOCK = f"{DOMAIN}_pictures_lock"


async def async_setup_entry(
    hass: HomeAssistant, entry: GacConfigEntry, add_entities: AddEntitiesCallback
) -> None:
    if not location_enabled(entry.options):
        return
    picture = await _async_picture(hass, entry)
    add_configured_entities(entry, add_entities, "device_tracker", [GacDeviceTracker(entry.runtime_data.coordinator, picture)])


async def _async_picture(hass: HomeAssistant, entry: GacConfigEntry) -> str | None:
    """A bundled picture for the model, else none (initials)."""
    name = _BUNDLED.get(str(entry.data.get(CONF_MODEL) or "").strip().lower())
    http = getattr(hass, "http", None)
    if name is None or http is None:
        return None
    if not await _ensure_pictures_served(hass, http):
        return None      # could not serve the file; fall back to the entity initials
    return f"{PICTURES_URL}/{name}"


async def _ensure_pictures_served(hass: HomeAssistant, http) -> bool:
    """Register the bundled-pictures path once, retrying on a later call if it fails."""
    if hass.data.get(_REGISTERED):
        return True
    lock = hass.data.setdefault(_REGISTER_LOCK, asyncio.Lock())
    async with lock:
        if hass.data.get(_REGISTERED):
            return True
        try:
            await http.async_register_static_paths(
                [StaticPathConfig(PICTURES_URL, str(_PICTURES_DIR), cache_headers=False)])
        except Exception:  # noqa: BLE001 — a picture is cosmetic; never block the tracker
            _LOGGER.debug("could not register the car-picture path; using entity initials", exc_info=True)
            return False
        hass.data[_REGISTERED] = True      # only after it actually succeeded
        return True


class GacDeviceTracker(GacEntity, TrackerEntity):
    _attr_translation_key = "location"
    _attr_source_type = SourceType.GPS
    # TrackerEntity defaults to Diagnostic; keep both location readings in Sensors.
    _attr_entity_category = None

    def __init__(self, coordinator: GacCoordinator, picture: str | None = None) -> None:
        super().__init__(coordinator, "location")
        self._attr_entity_picture = picture

    @property
    def latitude(self) -> float | None:
        return shown_position(self.status, self.coordinator.config_entry.options)[0]

    @property
    def longitude(self) -> float | None:
        return shown_position(self.status, self.coordinator.config_entry.options)[1]
