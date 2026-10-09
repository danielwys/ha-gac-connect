"""Integration-local fixes for the pinned client's read and schedule paths.

The library still handles authentication, signing, request limits and transport.
These overrides intentionally follow its 0.4.0b1 request shapes; regression
tests must be checked when updating the pinned dependency.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from gac_connect.client import GacClient as LibraryClient
from gac_connect.commands import CATALOG
from gac_connect.errors import CommandError
from gac_connect.models import VehicleStatus

from .status import decode_status, enum_code

PAUSE_UNSUPPORTED = (
    "Continuous charging pause is unavailable. The previous pause command allowed "
    "charging daily from 03:00 to 03:30, so Pause and Off are disabled. "
    "Use Set charge window to choose an explicit charging schedule."
)
_CLOCK = re.compile(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]\Z")


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


class GacClient(LibraryClient):
    """Retain the library transport with corrected telemetry and reservations."""

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

    async def set_charge_window(
        self, vin: str, start: str, stop: str, *, weekly: int = 0, until_soc: bool = False
    ) -> Any:
        operation = window_operation(start, stop, self._zone(), weekly=weekly, until_soc=until_soc)
        return await self._reservation(vin, operation)
