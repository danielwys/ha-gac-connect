"""Integration-local fixes for the pinned client's read and schedule paths.

The library still handles authentication, signing, request limits and transport.
These overrides intentionally follow its 0.4.0b1 request shapes; regression
tests must be checked when updating the pinned dependency.
"""

from __future__ import annotations

import re
import logging
import math
import json
from contextlib import AsyncExitStack
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp
from gac_connect.client import GacClient as LibraryClient
from gac_connect.commands import CATALOG, build_body
from gac_connect.const import CODE_COMMAND_ACCEPTED, ERR_REFRESH_SPENT, ERR_TOKEN_INVALID, HTTP_TIMEOUT
from gac_connect.crypto import decrypt_envelope
from gac_connect.errors import CommandError, GacError, RateLimitedError, TokenInvalidError
from gac_connect.limits import parse_retry_after
from gac_connect.models import VehicleStatus

from .status import decode_status, enum_code
from .phone import local_mobile

PAUSE_UNSUPPORTED = (
    "Continuous charging pause is unavailable. The previous pause command allowed "
    "charging daily from 03:00 to 03:30, so Pause and Off are disabled. "
    "Use Set charge window to choose an explicit charging schedule."
)
_LOGGER = logging.getLogger(__name__)
_CLOCK = re.compile(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]\Z")


def command_result(response: Any) -> dict:
    """Require an explicit acceptance result from the pinned transport."""
    if not isinstance(response, dict):
        raise CommandError("command response must be an object with an acceptance result")
    code = response.get("code")
    if code == ERR_TOKEN_INVALID:
        raise TokenInvalidError("token invalid; refresh and reissue the command")
    if response.get("success") is True or code in (CODE_COMMAND_ACCEPTED, str(CODE_COMMAND_ACCEPTED), "0000"):
        return response
    raise CommandError(f"command rejected: {str(response.get('msg') or code)[:200]}")


def validate_clock(value: Any) -> str:
    if not isinstance(value, str) or _CLOCK.fullmatch(value) is None:
        raise ValueError("charge times must use HH:MM, from 00:00 to 23:59")
    return value


def validate_weekly(value: Any) -> int:
    code = enum_code(value)
    if not isinstance(value, (int, float)) or isinstance(value, bool) or code is None or not 0 <= code <= 127:
        raise ValueError("weekly must be an integer from 0 to 127")
    return code


def window_operation(
    start: str, stop: str, zone: ZoneInfo, *, weekly: int = 0, until_soc: bool = False, now: datetime | None = None
) -> dict:
    """Build a daily window with its stop strictly after its next start."""
    validate_clock(start)
    validate_clock(stop)
    weekly = validate_weekly(weekly)
    if start == stop:
        raise ValueError("start and stop must differ; use Charge now for unrestricted charging")
    if not isinstance(until_soc, bool):
        raise ValueError("until_soc must be a boolean")
    now = datetime.now(zone) if now is None else now.astimezone(zone)
    sh, sm = map(int, start.split(":"))
    eh, em = map(int, stop.split(":"))
    start_at = now.replace(hour=sh, minute=sm, second=0, microsecond=0)
    if start_at <= now:
        start_at += timedelta(days=1)
    while weekly and not weekly & (1 << start_at.weekday()):
        start_at += timedelta(days=1)
    stop_at = start_at.replace(hour=eh, minute=em)
    if stop_at <= start_at:
        stop_at += timedelta(days=1)

    def struct(value: datetime) -> dict:
        return {
            "year": value.year,
            "month": value.month,
            "day": value.day,
            "hour": value.hour,
            "minute": value.minute,
            "second": 0,
            "millisecond": 0,
        }

    return {
        "dailyReservationStartTime": (sh * 60 + sm) * 60000,
        "dailyReservationStopTime": (eh * 60 + em) * 60000,
        "weeklyReservation": weekly,
        "chargingMode": 1,
        "chargingEndType": 1 if until_soc else 0,
        "startTime": struct(start_at),
        "stopTime": struct(stop_at),
    }


def _complete_iov_tokens(data: Any) -> bool:
    """A partial rotation must never overwrite the last complete credential pair."""
    if not isinstance(data, dict):
        return False
    if not all(isinstance(data.get(key), str) and data[key].strip() for key in ("token", "refreshToken")):
        return False
    for key in ("expireTime", "rexpireTime"):
        value = data.get(key)
        if type(value) is int and value > 0:
            continue
        if isinstance(value, float) and math.isfinite(value) and value > 0:
            continue
        return False
    return True


class GacClient(LibraryClient):
    """Retain the library transport with corrected telemetry and reservations."""

    async def _post(self, host: str, path: str, wrapper: dict, headers: dict, kind: str = "request") -> Any:
        """Backport HTTP error classification without changing the b1 wire protocol."""
        async with AsyncExitStack() as held:
            for limiter in self._limiters:
                await held.enter_async_context(limiter.slot(kind))
            async with self._http.post(
                f"https://{host}{path}", data=json.dumps(wrapper, separators=(",", ":")).encode(),
                headers=headers, timeout=aiohttp.ClientTimeout(total=HTTP_TIMEOUT),
                allow_redirects=False, raise_for_status=False,
            ) as response:
                if response.status == 429:
                    seconds = parse_retry_after(response.headers.get("Retry-After"))
                    pause = max(limiter.block(seconds) for limiter in self._limiters)
                    raise RateLimitedError("the service rate-limited the request", retry_after=pause)
                if response.status >= 300:
                    raise GacError(f"HTTP {response.status} from {path}; credentials retained")
                raw = await response.read()
        try:
            result = json.loads(raw)
        except ValueError as err:
            raise GacError(f"non-JSON response from {path}") from err
        if isinstance(result, dict) and result.get("encryptData"):
            plain = decrypt_envelope(result, self._response_key(host))
            if plain is not None:
                return plain
        return result

    async def _iov_call(self, path: str, body: Any, *, sensitive: dict | None = None,
                        kind: str = "request") -> Any:
        used = self._session.token
        response = await super()._iov_call(path, body, sensitive=sensitive, kind=kind)
        if kind == "command":
            if (isinstance(response, dict) and response.get("code") == ERR_TOKEN_INVALID
                    and self._session.token == used):
                self._session.expire_time = 0
            return command_result(response)
        if path == "/refresh/token":
            data = self._data(response)
            spent = isinstance(response, dict) and response.get("code") == ERR_REFRESH_SPENT
            if not spent and not _complete_iov_tokens(data):
                # b1 otherwise treats any error body as permanent authentication loss.
                raise GacError("refresh response has no usable token; credentials retained")
        return response

    async def request_sms(self, mobile: str, x: int) -> None:
        # b1 strips Australian "61" before zeros. Prefix a trunk zero after
        # regional normalization so valid local 61... numbers survive that helper.
        await super().request_sms("0" + local_mobile(mobile, self._cfg["tel"]), x)

    async def login_sms(self, mobile: str, code: str) -> None:
        await super().login_sms("0" + local_mobile(mobile, self._cfg["tel"]), code)

    @staticmethod
    def _ok(response: Any) -> bool:
        return isinstance(response, dict) and response.get("success") is True

    async def _rotate_account_token(self) -> None:
        """A failed optional rotation must not discard a completed sign-in."""
        if not (self._session.main_token and self._session.main_refresh_token):
            return
        try:
            response = await self._main_call(
                "/iam/api/user/token/refresh/sec", {"refreshToken": self._session.main_refresh_token},
                authorization=self._session.main_token,
            )
        except (GacError, aiohttp.ClientError, TimeoutError):
            _LOGGER.warning("Account rotation failed; sign-in is usable but later app sign-in may end it")
            return
        data = self._data(response)
        if self._ok(response) and isinstance(data, dict) and data.get("token"):
            self._session.main_token = data["token"]
            self._session.main_refresh_token = data.get("refreshToken") or self._session.main_refresh_token
            await self._persist()

    async def get_status(self, vin: str) -> VehicleStatus:
        response = await self._iov_read(CATALOG["status"].path, {"vin": vin}, sensitive={"vin": vin})
        data = self._data(response) or {}
        if not isinstance(data, dict):
            raise CommandError("vehicle status response has no valid data")
        results = data.get("results")
        if not isinstance(results, list) or not results or not isinstance(results[0], dict):
            raise CommandError("vehicle status response has no valid report")
        return decode_status(results[0], online=data.get("online"), updated_ms=data.get("updateTime"))

    async def charge_pause(self, vin: str) -> Any:
        raise CommandError(PAUSE_UNSUPPORTED)

    async def unlock(self, vin: str) -> Any:
        """Backport the public unlock API without b1's incorrect PIN guard."""
        command = CATALOG["unlock"]
        await self._ensure_token()
        # This adapter's _iov_call validates acceptance and invalidates only
        # the rejected token, without replaying the action.
        return await self._iov_call(
            command.path, build_body(command, vin), sensitive={"vin": vin}, kind="command",
        )

    async def set_charge_window(
        self, vin: str, start: str, stop: str, *, weekly: int = 0, until_soc: bool = False
    ) -> Any:
        operation = window_operation(start, stop, self._zone(), weekly=weekly, until_soc=until_soc)
        return await self._reservation(vin, operation)
