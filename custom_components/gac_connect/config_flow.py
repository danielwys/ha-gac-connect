"""Config flow: region → mobile → slide puzzle → SMS code → vehicle.

The puzzle is an external step (see captcha_view). Reauth re-runs sign-in;
options tune polling and privacy.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ._vendor.gac_connect.client import GacClient
    from ._vendor.gac_connect.models import Vehicle

import aiohttp
import voluptuous as vol
from ._vendor.gac_connect.const import LISTED_REGIONS
from ._vendor.gac_connect.errors import CommandError, GacError, LoginError, RateLimitedError

from homeassistant.config_entries import (
    SOURCE_REAUTH,
    SOURCE_RECONFIGURE,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_REGION
from homeassistant.core import callback
from homeassistant.data_entry_flow import UnknownFlow
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.network import get_url
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .captcha_view import CAPTCHA_URL, CAPTCHA_SECONDS, GacCaptchaView, flow_state, discard_flow_state
from .helpers import async_build_client
from .location import location_enabled, tracking_choices
from .session_store import ConfigEntryStore
from .entity_filter import OMIT_OPTIONS
from .const import (
    CONF_ALLOW_ACCOUNT_ALERTS,
    CONF_ALLOW_CHARGER_UNLOCK,
    CONF_ALLOW_PLAINTEXT_PUSH,
    CONF_ENABLE_ENTITIES,
    CONF_ENABLE_TRACKER,
    CONF_TRACK_MOVING,
    CONF_TRACK_PARKED,
    CONF_ALLOW_VEHICLE_SHUTDOWN,
    CONF_EXPERIMENTAL_PINLESS_UNLOCK,
    CONF_MOBILE,
    CONF_MODEL,
    CONF_OMIT_ENTITIES,
    CONF_PICTURE,
    CONF_QUIET_END,
    CONF_QUIET_START,
    CONF_SCAN_INTERVAL,
    CONF_SESSION,
    CONF_VIN,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MIN_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

# Markets where the GAC app has launched, plus the UK's separate My AION app.
REGION_NAMES = {
    "AE": "United Arab Emirates", "AU": "Australia", "BR": "Brazil", "ES": "Spain", "FI": "Finland",
    "GB": "United Kingdom", "GR": "Greece", "HK": "Hong Kong", "ID": "Indonesia", "IL": "Israel",
    "KH": "Cambodia", "KW": "Kuwait", "MO": "Macau", "MX": "Mexico", "NZ": "New Zealand",
    "PH": "Philippines", "PL": "Poland", "PT": "Portugal", "RU": "Russia", "SA": "Saudi Arabia",
    "SG": "Singapore", "TH": "Thailand",
}
REGIONS = sorted(LISTED_REGIONS | {"GB"}, key=lambda code: REGION_NAMES[code])
REGION_SELECTOR = SelectSelector(SelectSelectorConfig(
    options=[SelectOptionDict(value=code, label=REGION_NAMES[code]) for code in REGIONS],
    mode=SelectSelectorMode.DROPDOWN,
))
def _thai_local_mobile(mobile: str) -> str:
    """Return a Thai national number for the library's +66 SMS requests."""
    compact = re.sub(r"[\s()-]", "", mobile)
    match = re.fullmatch(r"(?:\+66|0066|66|0)?([689][0-9]{8})", compact)
    if match is None:
        raise ValueError("expected a Thai mobile number")
    # Return national format after this HA form's Thai mobile validation;
    # the client owns regional normalization for SMS and login requests.
    return f"0{match.group(1)}"


class GacConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1
    MINOR_VERSION = 6   # 1.2: profiles removed; 1.3: Scheduled charging entity ID; 1.4: fridge option removed;
                        # 1.5: Smart battery temp entity ID; 1.6: A/C entity ID

    def __init__(self) -> None:
        self._client: GacClient | None = None
        self._mobile: str | None = None
        self._region: str = "AU"
        self._vehicle: Vehicle | None = None
        self._vehicles: list[Vehicle] | None = None
        self._allow_unlock = False
        self._allow_vehicle_shutdown = False
        self._allow_charger_unlock = False
        self._view_registered = False
        self._http: aiohttp.ClientSession | None = None
        self._captcha_expiry = None

    async def _new_http(self):
        if self._http is not None:
            self._http.detach()  # HA owns the shared connector
        self._http = async_create_clientsession(self.hass, auto_cleanup=False)
        return self._http

    @callback
    def _clear_puzzle(self) -> None:
        if self._captcha_expiry is not None:
            self._captcha_expiry()
            self._captcha_expiry = None
        discard_flow_state(self.hass, self.flow_id)

    @callback
    def _expire_puzzle(self, _now) -> None:
        self._clear_puzzle()
        try:
            self.hass.config_entries.flow.async_abort(self.flow_id)
        except UnknownFlow:
            pass

    @callback
    def async_remove(self) -> None:
        self._clear_puzzle()
        if self._http is not None:
            self._http.detach()
            self._http = None
        super().async_remove()

    # ---- step 1: region + mobile ----------------------------------------
    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._region = user_input[CONF_REGION]
            self._mobile = user_input[CONF_MOBILE]
            if self._region == "TH":
                try:
                    self._mobile = _thai_local_mobile(self._mobile)
                except ValueError:
                    errors[CONF_MOBILE] = "invalid_thai_mobile"
            if not errors:
                try:
                    http = await self._new_http()
                    self._client = await async_build_client(self.hass, self._region, http)
                    await self._client.start_captcha()
                except RateLimitedError:
                    errors["base"] = "rate_limited"
                except (GacError, aiohttp.ClientError, TimeoutError):
                    errors["base"] = "cannot_connect"
                else:
                    return await self.async_step_captcha()
        schema = vol.Schema({
            vol.Required(CONF_REGION, default=self._region): REGION_SELECTOR,
            vol.Required(CONF_MOBILE, default=self._mobile or vol.UNDEFINED): str,
        })
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    # ---- step 2: slide puzzle (external) --------------------------------
    async def async_step_captcha(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        # Resumed by the captcha view once the puzzle is solved and SMS is sent.
        if user_input is not None:
            # An external step may only transition to an external-step-done, which
            # then hands off to the SMS step.
            self._clear_puzzle()
            return self.async_external_step_done(next_step_id="sms")

        if not self._view_registered:
            self.hass.http.register_view(GacCaptchaView())
            self._view_registered = True
        self._clear_puzzle()
        self._captcha_expiry = async_call_later(self.hass, CAPTCHA_SECONDS, self._expire_puzzle)
        flow_state(self.hass)[self.flow_id] = {
            "client": self._client,
            "mobile": self._mobile,
            "captcha": self._client._captcha,  # already fetched in step 1
            "attempt": 1,
            "status": "pending",
            "expires": time.monotonic() + CAPTCHA_SECONDS,
        }
        # This URL is opened in the user's own browser, so it must be one the
        # browser can resolve. Prefer the external URL and fall back to internal;
        # the previous internal-only choice handed cluster-internal DNS to browsers
        # on Kubernetes / reverse-proxy setups, which they cannot resolve.
        base = get_url(self.hass, prefer_external=True, allow_internal=True, allow_ip=True)
        return self.async_external_step(step_id="captcha", url=f"{base}{CAPTCHA_URL}?flow_id={self.flow_id}")

    # ---- step 3: SMS code ------------------------------------------------
    async def async_step_sms(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await self._client.login_sms(self._mobile, user_input["code"])
            except LoginError:
                errors["base"] = "invalid_code"
            except RateLimitedError:
                errors["base"] = "rate_limited"
            except (GacError, aiohttp.ClientError, TimeoutError):
                errors["base"] = "cannot_connect"
            if not errors:
                return await self.async_step_vehicle()
        return self.async_show_form(
            step_id="sms", data_schema=vol.Schema({vol.Required("code"): str}), errors=errors,
        )

    # ---- step 4: pick the vehicle ---------------------------------------
    async def async_step_vehicle(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is None or self._vehicles is None:   # submitting the form reuses the list just shown
            try:
                self._vehicles = await self._client.list_vehicles()
            except RateLimitedError:
                return self.async_abort(reason="rate_limited")
            except (GacError, aiohttp.ClientError, TimeoutError):
                return self.async_abort(reason="cannot_connect")
        vehicles = self._vehicles
        if not vehicles:
            return self.async_abort(reason="no_vehicles")
        by_vin = {v.vin: v for v in vehicles}

        if self.source in (SOURCE_REAUTH, SOURCE_RECONFIGURE):
            # Signing in again: keep the same entry and vehicle, store the new session.
            entry = self._get_reauth_entry() if self.source == SOURCE_REAUTH else self._get_reconfigure_entry()
            if entry.data[CONF_VIN] not in by_vin:
                return self.async_abort(reason="wrong_vehicle")
            try:
                await ConfigEntryStore(self.hass, entry).save(self._client.session)
            except (GacError, aiohttp.ClientError, TimeoutError):
                return self.async_abort(reason="cannot_connect")
            return self.async_update_reload_and_abort(entry, data_updates={CONF_MOBILE: self._mobile},
                                                      reason="reauth_successful")

        if len(vehicles) == 1:
            user_input = {CONF_VIN: vehicles[0].vin}
        if user_input is not None:
            vin = user_input[CONF_VIN]
            await self.async_set_unique_id(vin)
            self._abort_if_unique_id_configured()
            self._vehicle = by_vin[vin]
            return await self.async_step_remote_controls()
        options = {v.vin: f"{v.model or 'Aion'} ({v.plate or v.vin[-4:]})" for v in vehicles}
        return self.async_show_form(
            step_id="vehicle", data_schema=vol.Schema({vol.Required(CONF_VIN): vol.In(options)}),
        )

    # ---- step 5: remote controls this integration may use ---------------
    async def async_step_remote_controls(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._allow_unlock = user_input.get(CONF_EXPERIMENTAL_PINLESS_UNLOCK, False)
            self._allow_vehicle_shutdown = user_input.get(CONF_ALLOW_VEHICLE_SHUTDOWN, False)
            self._allow_charger_unlock = user_input.get(CONF_ALLOW_CHARGER_UNLOCK, False)
            return self._create_vehicle_entry({CONF_OMIT_ENTITIES: [], CONF_ENABLE_ENTITIES: []})
        return self.async_show_form(
            step_id="remote_controls",
            data_schema=vol.Schema({
                vol.Required(CONF_EXPERIMENTAL_PINLESS_UNLOCK, default=self._allow_unlock): bool,
                vol.Required(CONF_ALLOW_CHARGER_UNLOCK, default=self._allow_charger_unlock): bool,
                vol.Required(CONF_ALLOW_VEHICLE_SHUTDOWN, default=self._allow_vehicle_shutdown): bool,
            }),
            description_placeholders={"model": self._vehicle.model or "your vehicle"},
        )

    def _create_vehicle_entry(self, options: dict[str, Any]) -> ConfigFlowResult:
        veh = self._vehicle
        return self.async_create_entry(
            title=veh.model or f"Aion {veh.vin[-4:]}",
            data={
                CONF_REGION: self._region,
                CONF_MOBILE: self._mobile,
                CONF_VIN: veh.vin,
                CONF_MODEL: veh.model,
                CONF_SESSION: self._client.session.to_dict(),
            },
            options={**options, CONF_EXPERIMENTAL_PINLESS_UNLOCK: self._allow_unlock,
                     CONF_ALLOW_CHARGER_UNLOCK: self._allow_charger_unlock,
                     CONF_ALLOW_VEHICLE_SHUTDOWN: self._allow_vehicle_shutdown},
        )

    # ---- reauth ----------------------------------------------------------
    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        self._region = entry_data[CONF_REGION]
        self._mobile = entry_data.get(CONF_MOBILE)
        return await self.async_step_reauth_confirm()

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Sign in again on request: account features (the alert list) need a current account session,
        which ends when the GAC app signs in elsewhere, while the vehicle session keeps working."""
        entry = self._get_reconfigure_entry()
        self._region = entry.data[CONF_REGION]
        self._mobile = entry.data.get(CONF_MOBILE)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is None:
            return self.async_show_form(step_id="reauth_confirm")
        errors: dict[str, str] = {}
        try:
            http = await self._new_http()
            self._client = await async_build_client(self.hass, self._region, http)
            await self._client.start_captcha()
        except RateLimitedError:
            errors["base"] = "rate_limited"
        except (GacError, aiohttp.ClientError, TimeoutError):
            errors["base"] = "cannot_connect"
        if errors:
            return self.async_show_form(step_id="reauth_confirm", errors=errors)
        return await self.async_step_captcha()

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> GacOptionsFlow:
        return GacOptionsFlow()


class GacOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="init", menu_options=["settings", "equipment", "capabilities", "charging"])

    def _loaded_coordinator(self):
        runtime = getattr(self.config_entry, "runtime_data", None)
        return getattr(runtime, "coordinator", None)

    async def async_step_charging(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        from .charging import schedule_summary

        if (coordinator := self._loaded_coordinator()) is None:
            return self.async_abort(reason="vehicle_not_loaded")
        return self.async_show_menu(step_id="charging", menu_options=["refresh_charging", "charge_window", "charge_now", "init"],
                                    description_placeholders={"schedule": schedule_summary(coordinator)})

    async def async_step_refresh_charging(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if (coordinator := self._loaded_coordinator()) is None:
            return self.async_abort(reason="vehicle_not_loaded")
        await coordinator.async_refresh_now()
        return await self.async_step_charging()

    async def async_step_charge_now(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        from .command_tracking import async_send

        if (coordinator := self._loaded_coordinator()) is None:
            return self.async_abort(reason="vehicle_not_loaded")
        errors, refusal = {}, {"reason": ""}
        if user_input is not None:
            try:
                await async_send(coordinator, "charge-now", coordinator.client.charge_now(coordinator.vin))
            except CommandError as err:   # the gateway refused it; its reason says why
                _LOGGER.warning("Charge now request failed: %s", err)
                errors["base"], refusal["reason"] = "command_refused", str(err)
            except GacError as err:
                _LOGGER.warning("Charge now request failed: %s", err)
                errors["base"] = "command_failed"
            except HomeAssistantError as err:   # the car refused it
                errors["base"], refusal["reason"] = "command_refused", str(err)
            else:
                await coordinator.async_refresh_now()     # show the car's answer, not the old report
                return await self.async_step_charging()
        return self.async_show_form(step_id="charge_now", data_schema=vol.Schema({}), errors=errors,
                                    description_placeholders=refusal)

    async def async_step_charge_window(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        from .api import validate_clock
        from .charging import DAYS, window_target
        from .command_tracking import async_send

        if (coordinator := self._loaded_coordinator()) is None:
            return self.async_abort(reason="vehicle_not_loaded")
        errors, refusal = {}, {"reason": ""}
        if user_input is not None:
            until_soc = user_input.get("until_soc", False)
            try:
                start = validate_clock(user_input["start"])
                # Stopping at the charge limit sends no stop time.
                stop = validate_clock(user_input.get("stop")) if user_input.get("stop") or not until_soc else None
                if start == stop and not until_soc:
                    raise ValueError("start and stop must differ")
                days = user_input.get("days", [])
                if not isinstance(days, list) or any(day not in DAYS for day in days):
                    raise ValueError("unknown day")
                weekly = sum(1 << index for index, day in enumerate(DAYS) if day in days)
                await async_send(coordinator, "charge-window", coordinator.client.set_charge_window(
                    coordinator.vin, start, stop, weekly=weekly, until_soc=until_soc),
                    expected=window_target(start, stop, weekly, until_soc))
            except ValueError:
                errors["base"] = "invalid_charge_window"
            except CommandError as err:   # the gateway refused it; its reason says why
                _LOGGER.warning("Charging window request failed: %s", err)
                errors["base"], refusal["reason"] = "command_refused", str(err)
            except GacError as err:
                _LOGGER.warning("Charging window request failed: %s", err)
                errors["base"] = "command_failed"
            except HomeAssistantError as err:   # the car refused it
                errors["base"], refusal["reason"] = "command_refused", str(err)
            else:
                await coordinator.async_refresh_now()     # show the car's answer, not the old report
                return await self.async_step_charging()
        values = dict(user_input) if user_input is not None else {}
        if user_input is None and (status := coordinator.data) is not None:
            for key in ("start", "stop"):
                try:
                    values[key] = validate_clock(getattr(status, f"charge_window_{key}", None))
                except ValueError:
                    pass
            weekly = getattr(status, "charge_weekly", None)
            if type(weekly) is int and 0 <= weekly <= 127:
                values["days"] = [day for index, day in enumerate(DAYS) if weekly & (1 << index)]
            values["until_soc"] = getattr(status, "charge_end_type", None) == 1
        return self.async_show_form(step_id="charge_window", errors=errors, data_schema=vol.Schema({
            vol.Required("start", description={"suggested_value": values.get("start", "")}): str,
            vol.Optional("stop", description={"suggested_value": values.get("stop", "")}): str,
            vol.Optional("days", default=values.get("days", [])): cv.multi_select(DAYS),
            vol.Optional("until_soc", default=values.get("until_soc", False)): bool,
        }), description_placeholders={"timezone": coordinator.client.timezone, **refusal})

    async def async_step_equipment(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        from .equipment import equipment_summary

        if user_input is not None:
            return await self.async_step_init()
        return self.async_show_form(step_id="equipment", data_schema=vol.Schema({}),
                                    description_placeholders={"equipment": equipment_summary(self.config_entry)})

    async def async_step_capabilities(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Read the car's capability list again, for example after a software update."""
        if (coordinator := self._loaded_coordinator()) is None:
            return self.async_abort(reason="vehicle_not_loaded")
        manager = coordinator.capabilities
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await manager.async_read_capability_list(coordinator.client, coordinator.vin)
            except (GacError, HomeAssistantError) as err:
                _LOGGER.warning("Capability list read failed: %s", err)
                errors["base"] = "capability_read_failed"
            else:
                self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)
                return await self.async_step_equipment()
        return self.async_show_form(step_id="capabilities", data_schema=vol.Schema({}), errors=errors)

    async def async_step_settings(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input = {**self.config_entry.options, **user_input}
            if set(user_input.get(CONF_ENABLE_ENTITIES, ())) & set(user_input.get(CONF_OMIT_ENTITIES, ())):
                errors[CONF_ENABLE_ENTITIES] = "conflicting_entity_choices"
            picture = (user_input.get(CONF_PICTURE) or "").strip()
            if picture and not (picture.startswith("/") or picture.lower().startswith(("http://", "https://"))):
                errors[CONF_PICTURE] = "invalid_picture"
            quiet = {}
            for key in (CONF_QUIET_START, CONF_QUIET_END):
                value = (user_input.get(key) or "").strip()
                quiet[key] = value
                if value and re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value) is None:
                    errors[key] = "invalid_quiet_time"
            start, end = quiet[CONF_QUIET_START], quiet[CONF_QUIET_END]
            if bool(start) != bool(end):
                errors[CONF_QUIET_END if start else CONF_QUIET_START] = "quiet_pair_required"
            elif start and start == end and not errors:
                errors[CONF_QUIET_END] = "quiet_times_equal"
            if not errors:
                # Persist through HA's normal update API (including its reload
                # listener), without finishing and closing this options flow.
                self.hass.config_entries.async_update_entry(
                    self.config_entry, options={**user_input, **quiet, CONF_PICTURE: picture,
                                                CONF_ENABLE_TRACKER: location_enabled(user_input)}
                )
                return await self.async_step_init()
        opts = {**self.config_entry.options, **(user_input or {})}
        track_parked, track_moving = tracking_choices(opts)
        omit_options = dict(OMIT_OPTIONS)
        selected_omissions = [
            key for key in opts.get(CONF_OMIT_ENTITIES, ()) if key in omit_options
        ]
        enable_options = dict(OMIT_OPTIONS)
        selected_enables = [key for key in opts.get(CONF_ENABLE_ENTITIES, ()) if key in enable_options]
        fields = {
            vol.Optional(CONF_ENABLE_ENTITIES,
                         default=selected_enables): cv.multi_select(enable_options),
            vol.Optional(CONF_ALLOW_PLAINTEXT_PUSH,
                         default=opts.get(CONF_ALLOW_PLAINTEXT_PUSH, False)): bool,
            vol.Optional(CONF_SCAN_INTERVAL,
                         default=opts.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)):
                vol.All(vol.Coerce(int), vol.Clamp(min=MIN_SCAN_INTERVAL)),
            vol.Optional(CONF_TRACK_PARKED, default=track_parked): bool,
            vol.Optional(CONF_TRACK_MOVING, default=track_moving): bool,
            vol.Optional(CONF_EXPERIMENTAL_PINLESS_UNLOCK,
                         default=opts.get(CONF_EXPERIMENTAL_PINLESS_UNLOCK, False)): bool,
            vol.Optional(CONF_ALLOW_CHARGER_UNLOCK,
                         default=opts.get(CONF_ALLOW_CHARGER_UNLOCK, False)): bool,
            vol.Optional(CONF_ALLOW_ACCOUNT_ALERTS,
                         default=opts.get(CONF_ALLOW_ACCOUNT_ALERTS, False)): bool,
            vol.Optional(CONF_ALLOW_VEHICLE_SHUTDOWN,
                         default=opts.get(CONF_ALLOW_VEHICLE_SHUTDOWN, False)): bool,
            vol.Optional(CONF_OMIT_ENTITIES,
                         default=selected_omissions): cv.multi_select(omit_options),
            vol.Optional(CONF_PICTURE, description={"suggested_value": opts.get(CONF_PICTURE, "")}): str,
            vol.Optional(CONF_QUIET_START, default=opts.get(CONF_QUIET_START, "")): str,
            vol.Optional(CONF_QUIET_END, default=opts.get(CONF_QUIET_END, "")): str,
        }
        schema = vol.Schema(fields)
        return self.async_show_form(step_id="settings", data_schema=schema, errors=errors)
