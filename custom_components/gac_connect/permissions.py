"""Authorize vehicle services and administrator-owned configuration changes."""
from __future__ import annotations

from homeassistant.auth.permissions.const import POLICY_CONTROL, POLICY_READ
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import Unauthorized, UnknownUser
from homeassistant.helpers import entity_registry as er

from .capabilities import ENTITY_FEATURES
from .const import CONF_VIN, DOMAIN

_CONTROL_DOMAINS = frozenset({"button", "climate", "cover", "lock", "number", "select", "switch", "time"})


async def _user(hass: HomeAssistant, context: Context | None):
    # HA-created automations/internal calls have no user. Authenticated API and
    # entity-service calls carry their user's context and must be checked.
    if context is None or context.user_id is None:
        return None
    user = await hass.auth.async_get_user(context.user_id)
    if user is None:
        raise UnknownUser(context=context)
    return user


async def async_require_admin(hass: HomeAssistant, context: Context | None) -> None:
    """Keep permission/privacy settings under administrator control."""
    user = await _user(hass, context)
    if user is not None and not user.is_admin:
        raise Unauthorized(context=context)


async def async_require_vehicle_permission(
    hass: HomeAssistant, context: Context | None, coordinator, *,
    permission: str = POLICY_READ, feature: str | None = None,
) -> None:
    """Check this vehicle and, for commands, an entity controlling the requested feature.

    A right to read the battery or control another car does not authorize a
    command. Unknown command families require an administrator at the caller.
    Disabled/omitted controls do not grant permission through the raw service.
    """
    user = await _user(hass, context)
    if user is None or user.is_admin:
        return
    entry = coordinator.config_entry
    prefix = f"{entry.data[CONF_VIN]}_"
    registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity.platform != DOMAIN or entity.disabled_by is not None:
            continue
        if permission == POLICY_CONTROL:
            key = f"{entity.domain}:{entity.unique_id.removeprefix(prefix)}"
            if entity.domain not in _CONTROL_DOMAINS or ENTITY_FEATURES.get(key) != feature:
                continue
        if user.permissions.check_entity(entity.entity_id, permission):
            return
    raise Unauthorized(context=context, permission=permission)
