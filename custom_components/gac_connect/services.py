"""Services: charge_now, set_charge_window, send_command, get_control_capabilities."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv

from ._vendor.gac_connect.errors import GacError

from .api import validate_clock, validate_weekly
from .command_tracking import async_send
from .charging import window_target
from .capability_report import cabin_capability_report, full_capability_report
from .const import DOMAIN

ATTR_VIN = "vin"
ATTR_START = "start"
ATTR_STOP = "stop"
ATTR_WEEKLY = "weekly"
ATTR_UNTIL_SOC = "until_soc"
ATTR_COMMAND = "command"
ATTR_PARAMS = "params"


def _coordinators(hass: HomeAssistant) -> dict[str, object]:
    """Map VIN -> coordinator across all loaded entries."""
    out: dict[str, object] = {}
    for entry in hass.config_entries.async_entries(DOMAIN):
        runtime = getattr(entry, "runtime_data", None)
        if runtime is not None:
            out[runtime.coordinator.vin] = runtime.coordinator
    return out


def _resolve(hass: HomeAssistant, call: ServiceCall):
    coordinators = _coordinators(hass)
    vin = call.data.get(ATTR_VIN) or (next(iter(coordinators)) if len(coordinators) == 1 else None)
    if vin not in coordinators:
        raise HomeAssistantError(f"unknown or unspecified vin; known: {', '.join(coordinators) or 'none'}")
    return coordinators[vin], vin


def async_register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, "charge_now"):
        return

    async def charge_now(call: ServiceCall) -> None:
        coordinator, vin = _resolve(hass, call)
        try:
            await async_send(coordinator, "charge-now",
                             coordinator.client.charge_now(vin, until_soc=call.data.get(ATTR_UNTIL_SOC, False)))
        except GacError as err:
            raise HomeAssistantError(str(err)) from err

    async def set_charge_window(call: ServiceCall) -> None:
        coordinator, vin = _resolve(hass, call)
        start, stop = call.data[ATTR_START], call.data.get(ATTR_STOP)
        weekly, until_soc = call.data.get(ATTR_WEEKLY, 0), call.data.get(ATTR_UNTIL_SOC, False)
        if stop is None and not until_soc:
            raise ServiceValidationError("stop is required unless until_soc is set")
        try:
            await async_send(coordinator, "charge-window", coordinator.client.set_charge_window(
                vin, start, stop, weekly=weekly, until_soc=until_soc,
            ), expected=window_target(start, stop, weekly, until_soc))
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err
        except GacError as err:
            raise HomeAssistantError(str(err)) from err

    async def send_command(call: ServiceCall) -> None:
        coordinator, vin = _resolve(hass, call)
        try:
            command = call.data[ATTR_COMMAND]
            params = call.data.get(ATTR_PARAMS) or {}
            await async_send(coordinator, command, coordinator.client.command(vin, command, **params),
                             expected={} if params else None)
        except (ValueError, TypeError) as err:
            # The library validates raw parameters (e.g. A/C duration or fan speed) before sending.
            raise ServiceValidationError(f"Invalid parameters for {call.data[ATTR_COMMAND]}: {err}") from err
        except GacError as err:
            raise HomeAssistantError(str(err)) from err

    async def get_control_capabilities(call: ServiceCall) -> dict:
        coordinator, vin = _resolve(hass, call)
        try:
            data = await coordinator.client.get_control_capabilities(vin)
            return {**cabin_capability_report(data), "all": full_capability_report(data)}
        except GacError as err:
            raise HomeAssistantError(str(err)) from err

    base = vol.Schema({vol.Optional(ATTR_VIN): cv.string})
    hass.services.async_register(
        DOMAIN, "charge_now", charge_now, base.extend({vol.Optional(ATTR_UNTIL_SOC): cv.boolean})
    )
    hass.services.async_register(
        DOMAIN,
        "set_charge_window",
        set_charge_window,
        base.extend(
            {
                vol.Required(ATTR_START): validate_clock,
                vol.Optional(ATTR_STOP): validate_clock,
                vol.Optional(ATTR_WEEKLY): validate_weekly,
                vol.Optional(ATTR_UNTIL_SOC): cv.boolean,
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        "send_command",
        send_command,
        base.extend({vol.Required(ATTR_COMMAND): cv.string, vol.Optional(ATTR_PARAMS): dict}),
    )
    hass.services.async_register(
        DOMAIN, "get_control_capabilities", get_control_capabilities, base,
        supports_response=SupportsResponse.ONLY,
    )
