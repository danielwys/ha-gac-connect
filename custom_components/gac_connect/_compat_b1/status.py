"""Legacy compatibility fixes for telemetry decoded by gac-connect 0.4.0b1.

Keep the original report for diagnostics and lock decoding. Normalize a copy
before using the library so one malformed enum cannot discard other readings.
"""

from __future__ import annotations

import math
from typing import Any

from gac_connect.models import ChargingMode, Tyre, VehicleStatus

from .lock_state import doors_locked


def finite_number(value: Any) -> float | None:
    """Return a finite reading, accepting numeric strings but not booleans."""
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def enum_code(value: Any) -> int | None:
    number = finite_number(value)
    return int(number) if number is not None and number.is_integer() else None


def _first(results: dict, key: str) -> dict:
    items = results.get(key)
    return items[0] if isinstance(items, list) and items and isinstance(items[0], dict) else {}


def _enabled(item: dict, flag: str) -> bool:
    if flag not in item:
        return True
    value = item[flag]
    return value if isinstance(value, bool) else enum_code(value) == 1


def _open_state(results: dict, key: str) -> bool | None:
    items = results.get(key)
    if not isinstance(items, list) or not items:
        return None
    codes = [enum_code(item.get("openMode")) if isinstance(item, dict) else None for item in items]
    if any(code is not None and code > 0 for code in codes):
        return True
    if all(code == 0 for code in codes):
        return False
    return None


_NUMERIC_FIELDS = {
    "drivingOverviewAccessors": ("remainElectricityPercentage", "remainVoyageCourse", "totalVoyageCourse"),
    "chargingAccessors": (
        "chargingCurrent",
        "chargingVoltage",
        "estimatedChargedDuration",
        "weeklyReservation",
        "dailyReservationStartTime",
        "dailyReservationStopTime",
    ),
    "environmentAccessors": ("temperatureInsideCar", "pm25"),
    "batteryAccessors": ("voltage",),
    "positionAccessors": ("latitude", "longitude"),
    "airConditionAccessors": ("enableAirCompressor", "windStrength", "temperature"),
    "steeringAccessors": ("steering",),
    "lightAccessors": ("open",),
}


def decode_status(results: dict, *, online: Any = None, updated_ms: Any = None) -> VehicleStatus:
    """Decode known states conservatively and keep tyres in stable ID slots."""
    normalized = dict(results)
    for key, items in results.items():
        if key.endswith("Accessors"):
            normalized[key] = (
                [dict(item) if isinstance(item, dict) else {} for item in items] if isinstance(items, list) else []
            )
    for key, fields in _NUMERIC_FIELDS.items():
        for item in normalized.get(key, []):
            for field in fields:
                if field in item:
                    item[field] = finite_number(item[field])

    charging = _first(normalized, "chargingAccessors")
    mode = enum_code(_first(results, "chargingAccessors").get("chargingMode"))
    charging["chargingMode"] = mode if mode in (0, 1) else None
    if "chargingVoltageFlag" in charging:
        charging["chargingVoltageFlag"] = _enabled(charging, "chargingVoltageFlag")
    # The pinned decoder looks up tyre IDs in a dictionary before normalization.
    for item in normalized.get("tyreAccessors", []):
        item["tyre"] = enum_code(item.get("tyre"))
    stamp = enum_code(updated_ms)
    if stamp is not None and not 0 < stamp < 253402300800000:
        stamp = None
    status = VehicleStatus.from_results(normalized, online=None, updated_ms=stamp)
    status.raw = results
    status.locked = doors_locked(status)
    status.online = online if isinstance(online, bool) else {0: False, 1: True}.get(enum_code(online))
    status.charging_mode = ChargingMode(mode) if mode in (0, 1) else None
    status.charging = {0: False, 2: True}.get(enum_code(charging.get("chargingStatus")))
    charger = _first(results, "chargerAccessors")
    status.plugged_in = {0: False, 1: True}.get(enum_code(charger.get("plug")))
    status.charger_locked = {0: True, 1: False}.get(enum_code(charger.get("lock")))
    if status.soc is not None and not 0 <= status.soc <= 100:
        status.soc = None
    for attr in ("range_km", "odometer_km", "charge_current_a", "charge_voltage_v", "pm25", "aux_voltage"):
        value = getattr(status, attr)
        if value is not None and value < 0:
            setattr(status, attr, None)
    if status.latitude is not None and not -90 <= status.latitude <= 90:
        status.latitude = None
    if status.longitude is not None and not -180 <= status.longitude <= 180:
        status.longitude = None
    for key, attr in (
        ("doorAccessors", "door_open"),
        ("windowAccessors", "window_open"),
        ("hatchAccessors", "hatch_open"),
        ("sunroofAccessors", "sunroof_open"),
    ):
        setattr(status, attr, _open_state(results, key))

    # The API's tyre IDs are 1..4; their physical corners remain unverified.
    status.tyres = [Tyre(str(index), None, None) for index in range(1, 5)]
    seen: set[int] = set()
    for item in normalized.get("tyreAccessors", []):
        index = enum_code(item.get("tyre"))
        if index not in (1, 2, 3, 4):
            continue
        if index in seen:
            status.tyres[index - 1] = Tyre(str(index), None, None)
            continue  # conflicting duplicate IDs are not a trustworthy reading
        seen.add(index)
        pressure = finite_number(item.get("pressure"))
        temperature = finite_number(item.get("temperature"))
        # A Thailand UT live report uses this pair for unreported tyre data,
        # even with both reading flags set to 1. Do not reject -1 °C alone:
        # a direct TPMS can legitimately report a sub-zero temperature.
        if pressure == -1 and temperature == -1:
            temperature = None
        if not _enabled(item, "pressureFlag") or (pressure is not None and pressure < 0):
            pressure = None
        if not _enabled(item, "temperatureFlag"):
            temperature = None
        status.tyres[index - 1] = Tyre(str(index), pressure, temperature)
    return status
