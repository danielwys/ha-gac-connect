"""Diagnostics with personal data redacted."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import GacConfigEntry
from ._vendor import LIBRARY_COMMIT, LIBRARY_SOURCE, LIBRARY_VERSION
from .const import (
    CONF_AC_MINUTES, CONF_ALLOW_PLAINTEXT_PUSH, CONF_ENABLE_FRIDGE, CONF_ENABLE_TRACKER,
    CONF_EXPERIMENTAL_PINLESS_UNLOCK, CONF_OMIT_ENTITIES, CONF_ENABLE_ENTITIES, CONF_PROFILE,
    CONF_QUIET_END, CONF_QUIET_START, CONF_SCAN_INTERVAL,
)

# URLs can contain passwords, signed query strings or private locations. New
# options must be reviewed before being included in a shareable diagnostic.
SAFE_OPTIONS = {
    CONF_AC_MINUTES, CONF_ALLOW_PLAINTEXT_PUSH, CONF_ENABLE_FRIDGE, CONF_ENABLE_TRACKER,
    CONF_EXPERIMENTAL_PINLESS_UNLOCK, CONF_OMIT_ENTITIES, CONF_ENABLE_ENTITIES, CONF_PROFILE,
    CONF_QUIET_END, CONF_QUIET_START, CONF_SCAN_INTERVAL,
}

TO_REDACT = {
    "vin", "mobile", "session", "token", "refreshToken", "refresh_token",
    "main_token", "main_refresh_token", "latitude", "longitude", "traceId",
    "plateNo", "plate",
}

async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: GacConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data.coordinator
    status = coordinator.data
    raw = dict(status.raw) if status else {}
    return {
        "library": {
            "source": LIBRARY_SOURCE,
            "version": LIBRARY_VERSION,
            "commit": LIBRARY_COMMIT,
            "bundled": True,
        },
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "options": {key: value for key, value in entry.options.items() if key in SAFE_OPTIONS},
        "status_updated_ms": status.updated_ms if status else None,
        "status_online": status.online if status else None,
        "commands": coordinator.commands.attributes() if getattr(coordinator, "commands", None) else None,
        "status_raw": async_redact_data(raw, TO_REDACT),
        "capabilities": coordinator.capabilities.diagnostics() if getattr(coordinator, "capabilities", None) else None,
    }
