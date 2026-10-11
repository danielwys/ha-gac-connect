"""Config flow: region → mobile → slide puzzle → SMS code → vehicle.

The puzzle is an external step (see captcha_view). Reauth re-runs sign-in;
options tune polling and privacy.
"""
from __future__ import annotations

import re
import time
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from gac_connect.client import GacClient
    from gac_connect.models import Vehicle

import aiohttp
import voluptuous as vol
from gac_connect.errors import GacError, LoginError, RateLimitedError

from homeassistant.config_entries import (
    SOURCE_REAUTH,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_REGION
from homeassistant.core import callback
from homeassistant.data_entry_flow import UnknownFlow
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.network import get_url

from .captcha_view import CAPTCHA_URL, CAPTCHA_SECONDS, GacCaptchaView, flow_state, discard_flow_state
from .helpers import async_build_client
from .session_store import ConfigEntryStore
from .entity_filter import OMIT_OPTIONS, is_aion_ut, model_omissions
from .const import (
    CONF_ALLOW_PLAINTEXT_PUSH,
    CONF_AC_MINUTES,
    CONF_ENABLE_FRIDGE,
    CONF_ENABLE_TRACKER,
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
    DEFAULT_ENABLE_FRIDGE,
    DEFAULT_ENABLE_TRACKER,
    DEFAULT_AC_MINUTES,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MIN_SCAN_INTERVAL,
)

REGIONS = ["AU", "NZ", "GB", "SG", "TH", "AE", "GR", "IL"]
PROFILE_UT = "aion_ut"
PROFILE_V = "aion_v"
PROFILE_CUSTOM = "custom"
PROFILES = {
    PROFILE_UT: "Aion UT (omit fridge, steering wheel heat and sunroof)",
    PROFILE_V: "Aion V (allow fridge)",
    PROFILE_CUSTOM: "Other or custom equipment",
}


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

    def __init__(self) -> None:
        self._client: GacClient | None = None
        self._mobile: str | None = None
        self._region: str = "AU"
        self._vehicle: Vehicle | None = None
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
            vol.Required(CONF_REGION, default=self._region): vol.In(REGIONS),
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
        try:
            vehicles = await self._client.list_vehicles()
        except RateLimitedError:
            return self.async_abort(reason="rate_limited")
        except (GacError, aiohttp.ClientError, TimeoutError):
            return self.async_abort(reason="cannot_connect")
        if not vehicles:
            return self.async_abort(reason="no_vehicles")
        by_vin = {v.vin: v for v in vehicles}

        if self.source == SOURCE_REAUTH:
            # Signing in again: keep the same entry and vehicle, store the new session.
            entry = self._get_reauth_entry()
            if entry.data[CONF_VIN] not in by_vin:
                return self.async_abort(reason="wrong_vehicle")
            try:
                await ConfigEntryStore(self.hass, entry).save(self._client.session)
            except (GacError, aiohttp.ClientError, TimeoutError):
                return self.async_abort(reason="cannot_connect")
            return self.async_update_reload_and_abort(entry, data_updates={CONF_MOBILE: self._mobile})

        if len(vehicles) == 1:
            user_input = {CONF_VIN: vehicles[0].vin}
        if user_input is not None:
            vin = user_input[CONF_VIN]
            await self.async_set_unique_id(vin)
            self._abort_if_unique_id_configured()
            self._vehicle = by_vin[vin]
            return await self.async_step_profile()
        options = {v.vin: f"{v.model or 'Aion'} ({v.plate or v.vin[-4:]})" for v in vehicles}
        return self.async_show_form(
            step_id="vehicle", data_schema=vol.Schema({vol.Required(CONF_VIN): vol.In(options)}),
        )

    # ---- step 5: choose a capability profile ----------------------------
    async def async_step_profile(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            profile = user_input["profile"]
            if profile == PROFILE_CUSTOM:
                return await self.async_step_profile_custom()
            options = {
                CONF_ENABLE_FRIDGE: profile == PROFILE_V,
                CONF_OMIT_ENTITIES: ["switch:steering_heat", "cover:sunroof"] if profile == PROFILE_UT else [],
            }
            return self._create_vehicle_entry(options)

        model = (self._vehicle.model or "").strip().casefold()
        suggested = PROFILE_UT if model.startswith("aion ut") else (
            PROFILE_V if model.startswith("aion v") else PROFILE_CUSTOM
        )
        return self.async_show_form(
            step_id="profile",
            data_schema=vol.Schema({vol.Required("profile", default=suggested): vol.In(PROFILES)}),
            description_placeholders={"model": self._vehicle.model or "your vehicle"},
        )

    async def async_step_profile_custom(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self._create_vehicle_entry(user_input)
        return self.async_show_form(
            step_id="profile_custom",
            data_schema=vol.Schema({
                vol.Required(CONF_ENABLE_FRIDGE, default=DEFAULT_ENABLE_FRIDGE): bool,
                vol.Optional(CONF_OMIT_ENTITIES, default=[]): cv.multi_select(OMIT_OPTIONS),
            }),
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
            options=options,
        )

    # ---- reauth ----------------------------------------------------------
    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        self._region = entry_data[CONF_REGION]
        self._mobile = entry_data.get(CONF_MOBILE)
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
        errors: dict[str, str] = {}
        if user_input is not None:
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
                return self.async_create_entry(data={**user_input, **quiet, CONF_PICTURE: picture})
        opts = {**self.config_entry.options, **(user_input or {})}
        forced = model_omissions(self.config_entry)
        omit_options = {key: label for key, label in OMIT_OPTIONS.items() if key not in forced}
        selected_omissions = [
            key for key in opts.get(CONF_OMIT_ENTITIES, ()) if key in omit_options
        ]
        fields = {
            vol.Optional(CONF_ALLOW_PLAINTEXT_PUSH,
                         default=opts.get(CONF_ALLOW_PLAINTEXT_PUSH, False)): bool,
            vol.Optional(CONF_SCAN_INTERVAL,
                         default=opts.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)):
                vol.All(vol.Coerce(int), vol.Clamp(min=MIN_SCAN_INTERVAL)),
            vol.Optional(CONF_ENABLE_TRACKER,
                         default=opts.get(CONF_ENABLE_TRACKER, DEFAULT_ENABLE_TRACKER)): bool,
            vol.Optional(CONF_EXPERIMENTAL_PINLESS_UNLOCK,
                         default=opts.get(CONF_EXPERIMENTAL_PINLESS_UNLOCK, False)): bool,
            vol.Optional(CONF_OMIT_ENTITIES,
                         default=selected_omissions): cv.multi_select(omit_options),
            vol.Optional(CONF_PICTURE, description={"suggested_value": opts.get(CONF_PICTURE, "")}): str,
            vol.Optional(CONF_QUIET_START, default=opts.get(CONF_QUIET_START, "")): str,
            vol.Optional(CONF_QUIET_END, default=opts.get(CONF_QUIET_END, "")): str,
            vol.Optional(CONF_AC_MINUTES, default=opts.get(CONF_AC_MINUTES, DEFAULT_AC_MINUTES)):
                vol.All(vol.Coerce(int), vol.Range(min=5, max=60)),
        }
        if not is_aion_ut(self.config_entry):
            fields[vol.Optional(CONF_ENABLE_FRIDGE,
                                default=opts.get(CONF_ENABLE_FRIDGE, DEFAULT_ENABLE_FRIDGE))] = bool
        schema = vol.Schema(fields)
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
