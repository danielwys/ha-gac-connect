"""A bounded cabin/seat projection of the app's raw capability response."""
from __future__ import annotations

import math

from homeassistant.exceptions import HomeAssistantError

COMMON = {"controlCapability", "optionCapability", "optionCapabilityExt", "alarmsCapability",
          "bcontrolCapability", "boptionCapability", "balarmCapability"}
AIR_FIELDS = COMMON | {
    "airConditionMode", "blowingModeCapability", "supportBlowingModes",
    "cyclingModeCapability", "supportCyclingModes", "windStrengthCapability", "minWindStrength", "maxWindStrength",
    "enableAutoModeCapability", "enableAirCompressorCapability",
    "enableFrontDefrosterCapability", "enableRearDefrosterCapability",
    "temperatureCapability", "minTemperature", "maxTemperature", "supportTemperatureUnits",
    "airConditionDurationCapability", "minAirConditionCurrentDuration", "maxAirConditionCurrentDuration",
    "enableAirCleanerCapability", "fragranceCapability", "airConditionAntivirusModeCapability",
    "antivirusModeDurationCapability", "minAntivirusModeCurrentDuration", "maxAntivirusModeCurrentDuration",
}
CHARGING_FIELDS = COMMON | {
    "chargingModeCapability", "supportChargingModes", "chargingEndWithSocCapability",
    "weeklyReservationCapability", "dailyReservationTimeSettingCapability", "chargingStatusCapability",
    "chargingVoltageCapability", "chargingCurrentCapability", "estimatedChargedDurationCapability",
    "dischargeVoltageCapability", "dischargeCurrentCapability",
}
SEAT_FIELDS = COMMON | {
    "seat", "enableWindCapability", "windStrengthCapability", "minWindStrength", "maxWindStrength",
    "temperatureModeCapability", "supportTemperatureModes", "temperatureStrengthCapability",
    "minTemperatureStrength", "maxTemperatureStrength", "supportTemperatureUnits",
    "temperatureDurationCapability", "minTemperatureCurrentDuration", "maxTemperatureCurrentDuration",
}


def _value(value):
    # These DTO fields are numeric flags/ranges, bit strings, or numeric lists.
    # Do not include unknown nested data, text, identifiers or credentials.
    if value is None or isinstance(value, bool) or type(value) is int:
        return value
    if type(value) is float and math.isfinite(value):
        return value
    if isinstance(value, str) and len(value) <= 256 and value and all(c in "0123456789.-" for c in value):
        return value
    if isinstance(value, list) and len(value) <= 64 and all(type(v) in (int, float) for v in value):
        if all(type(v) is int or math.isfinite(v) for v in value):
            return list(value)
    return None


def cabin_capability_report(data: dict) -> dict:
    """Follow the app's first capability record, without interpreting its flags."""
    records = data.get("capabilities")
    record = records[0] if isinstance(records, list) and records else data
    if not isinstance(record, dict):
        raise HomeAssistantError("The capability response has no usable vehicle record")
    capability = record.get("capability", record)
    remote = capability.get("remoteCapability") if isinstance(capability, dict) else None
    if not isinstance(remote, dict):
        raise HomeAssistantError("The capability response has no remote-capability section")
    result = {}
    for name, fields in (("airConditionCapabilities", AIR_FIELDS), ("seatTemperatureCapabilities", SEAT_FIELDS),
                         ("chargingCapabilities", CHARGING_FIELDS)):
        items = remote.get(name)
        result[name] = None if items is None else [
            {key: _value(value) for key, value in item.items() if key in fields}
            for item in items[:16] if isinstance(item, dict)
        ] if isinstance(items, list) else None
    return result
