"""Diagnostics with personal data redacted."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import GacConfigEntry
from ._vendor import LIBRARY_COMMIT, LIBRARY_SOURCE, LIBRARY_VERSION
from ._vendor.gac_connect.const import REGIONS
from .const import (
    CONF_AC_MINUTES, CONF_ALLOW_PLAINTEXT_PUSH, CONF_ENABLE_FRIDGE, CONF_ENABLE_TRACKER,
    CONF_EXPERIMENTAL_PINLESS_UNLOCK, CONF_OMIT_ENTITIES, CONF_ENABLE_ENTITIES, CONF_PROFILE,
    CONF_QUIET_END, CONF_QUIET_START, CONF_SCAN_INTERVAL,
    CONF_REGION, CONF_TRACK_MOVING, CONF_TRACK_PARKED,
)

# URLs can contain passwords, signed query strings or private locations. New
# options must be reviewed before being included in a shareable diagnostic.
SAFE_OPTIONS = {
    CONF_AC_MINUTES, CONF_ALLOW_PLAINTEXT_PUSH, CONF_ENABLE_FRIDGE, CONF_ENABLE_TRACKER,
    CONF_TRACK_PARKED, CONF_TRACK_MOVING,
    CONF_EXPERIMENTAL_PINLESS_UNLOCK, CONF_OMIT_ENTITIES, CONF_ENABLE_ENTITIES, CONF_PROFILE,
    CONF_QUIET_END, CONF_QUIET_START, CONF_SCAN_INTERVAL,
}

TO_REDACT = {
    "vin", "mobile", "session", "token", "refreshToken", "refresh_token",
    "main_token", "main_refresh_token", "latitude", "longitude", "traceId",
    "plateNo", "plate",
}
# Report groups whose every value may locate the car or identify people (position
# details, emergency phone numbers, location-authorisation dates). Field names are
# kept so unmodelled fields can still be discovered; values are masked.
MASKED_GROUPS = {"positionAccessors", "dialNumberAccessors", "gpsAuthorAccessors"}


def _mask(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _mask(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_mask(item) for item in value]
    return None if value is None else "**REDACTED**"


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: GacConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data.coordinator
    status = coordinator.data
    raw = {key: _mask(value) if key in MASKED_GROUPS else value
           for key, value in (status.raw if status else {}).items()}
    region = REGIONS.get(entry.data.get(CONF_REGION), {})
    polling = {"state": getattr(coordinator, "polling_state", None),
               "last_error_reason": getattr(coordinator, "last_refresh_error", None)}
    for name, attr in (("last_attempt", "last_refresh_attempt"), ("last_success", "last_refresh_success"),
                       ("last_error_time", "last_refresh_error_at"), ("retry_not_before", "retry_not_before")):
        value = getattr(coordinator, attr, None)
        polling[name] = value.isoformat() if value is not None else None
    return {
        "library": {
            "source": LIBRARY_SOURCE,
            "version": LIBRARY_VERSION,
            "commit": LIBRARY_COMMIT,
            "bundled": True,
        },
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "options": {key: value for key, value in entry.options.items() if key in SAFE_OPTIONS},
        "endpoints": {"account_host": region.get("main"), "vehicle_host": region.get("iov")},
        "polling": polling,
        "status_updated_ms": status.updated_ms if status else None,
        "status_online": status.online if status else None,
        "commands": coordinator.commands.attributes() if getattr(coordinator, "commands", None) else None,
        "status_raw": async_redact_data(raw, TO_REDACT),
        "capabilities": coordinator.capabilities.diagnostics() if getattr(coordinator, "capabilities", None) else None,
    }
