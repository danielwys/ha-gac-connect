"""Persist passive evidence and correlate outcomes of user-requested commands."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import logging

from homeassistant.helpers.storage import Store

from .api import Capabilities, Support, doors_locked
from .const import (
    CONF_ENABLE_ENTITIES, CONF_ENABLE_FRIDGE, CONF_ENABLE_TRACKER, CONF_MODEL,
    CONF_OMIT_ENTITIES, CONF_REGION, CONF_VIN, DEFAULT_ENABLE_TRACKER, DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

# HA entity names map to library evidence. Hardware readings may reveal a
# control, but its individual commands remain unconfirmed until a real result.
ENTITY_FEATURES = {
    "sensor:soc": "soc", "sensor:range": "range_km", "sensor:odometer": "odometer_km",
    "sensor:cabin_temp": "cabin_temp_c", "sensor:aux_voltage": "aux_voltage", "sensor:pm25": "pm25",
    "sensor:charge_time": "estimated_charge_minutes", "sensor:charge_current": "charge_current_a",
    "sensor:charge_voltage": "charge_voltage_v", "sensor:charge_power": "charge_power_kw",
    "sensor:last_report": "updated_ms", "sensor:charge_window_start": "charge_window_start",
    "sensor:charge_window_stop": "charge_window_stop",
    **{f"sensor:tyre_{i}_{kind}": f"tyre_{i}_{field}" for i in range(1, 5)
       for kind, field in (("pressure", "pressure"), ("temp", "temperature"))},
    "sensor:fridge_keep_mode": "fridge_keep_mode", "sensor:fridge_time_left": "fridge_keep_minutes",
    "binary_sensor:plugged": "plugged_in", "binary_sensor:charging": "charging",
    "binary_sensor:locked": "locked", "binary_sensor:door_open": "door_open",
    "binary_sensor:window_open": "window_open", "binary_sensor:hatch_open": "hatch_open",
    "binary_sensor:online": "online", "binary_sensor:charger_locked": "charger_locked",
    "switch:steering_heat": "steering_heat", "switch:lights": "lights", "switch:ventilation": "ventilation",
    "switch:charge_switch": "charge_control", "switch:climate_power": "climate", "switch:fridge": "fridge",
    "cover:windows": "windows", "cover:sunroof": "sunroof", "cover:tailgate": "tailgate",
    "climate:climate": "climate", "lock:doors": "doors",
    "button:horn": "horn", "button:precondition_battery": "battery_precondition",
    "button:charge_now": "charge_control", "select:fridge_mode": "fridge", "number:fridge_temperature": "fridge",
    "device_tracker:location": "location",
}
FRIDGE_ENTITIES = frozenset(key for key in ENTITY_FEATURES if "fridge" in key)


def store_key(entry_id: str) -> str:
    return f"{DOMAIN}.capabilities.{entry_id}"


class CapabilityManager:
    def __init__(self, hass, entry) -> None:
        self.entry = entry
        self.detector = Capabilities(entry.data.get(CONF_MODEL))
        self._store = Store(hass, 1, store_key(entry.entry_id))
        identity = f"{entry.data.get(CONF_REGION)}:{entry.data[CONF_VIN]}"
        self._vehicle_key = hashlib.sha256(identity.encode()).hexdigest()
        self._dirty = False

    async def async_load(self) -> None:
        try:
            data = await self._store.async_load()
        except Exception:
            _LOGGER.warning("Could not read detected equipment; learning again from vehicle reports")
            return
        if isinstance(data, dict) and data.get("vehicle_key") == self._vehicle_key:
            self.detector.restore(data.get("evidence"))

    def _snapshot(self) -> dict:
        return {"vehicle_key": self._vehicle_key, "evidence": self.detector.export_state()}

    def _changed(self) -> None:
        self._dirty = True
        self._store.async_delay_save(self._snapshot, 10)

    def observe(self, status) -> None:
        # Use the same model-aware interpretation as the lock entities (UT
        # rear-door sentinels), without changing the coordinator's report.
        if status.locked is None and (locked := doors_locked(status, self.entry.data.get(CONF_MODEL))) is not None:
            status = replace(status, locked=locked)
        if self.detector.observe(status):
            self._changed()

    def includes(self, entity_key: str) -> bool:
        if entity_key == "device_tracker:location" and not self.entry.options.get(CONF_ENABLE_TRACKER, DEFAULT_ENABLE_TRACKER):
            return False
        if entity_key in self.entry.options.get(CONF_OMIT_ENTITIES, ()):
            return False
        if entity_key in FRIDGE_ENTITIES and self.entry.options.get(CONF_ENABLE_FRIDGE) is False:
            return False
        if entity_key == "button:refresh":
            return True
        if entity_key == "button:charge_pause":
            return False  # no actual continuous-pause operation exists
        if entity_key in self.entry.options.get(CONF_ENABLE_ENTITIES, ()):
            return True
        feature = ENTITY_FEATURES.get(entity_key)
        return feature is not None and self.detector.feature(feature).state is Support.SUPPORTED

    def confirm_command(self, command: str) -> bool:
        """Called only after the shared tracker correlates a successful result."""
        if self.detector.confirm_command(command):
            self._changed()
            return True
        return False

    async def async_close(self) -> None:
        if self._dirty:
            try:
                await self._store.async_save(self._snapshot())
                self._dirty = False
            except Exception:
                _LOGGER.warning("Could not save detected equipment; it will be learned again next time")

    def diagnostics(self) -> dict:
        data = self.detector.diagnostics()
        data["entities"] = {key: {"shown": self.includes(key), "override": (
            "disable" if key in self.entry.options.get(CONF_OMIT_ENTITIES, ()) else
            "enable" if key in self.entry.options.get(CONF_ENABLE_ENTITIES, ()) else "automatic")}
            for key in ENTITY_FEATURES}
        return data

    def explanation(self, entity_key: str) -> str:
        """Explain effective visibility, with overrides before passive evidence."""
        if entity_key == "button:charge_pause":
            return "Hidden: continuous charging pause is not supported."
        if entity_key in self.entry.options.get(CONF_OMIT_ENTITIES, ()):
            return "Hidden by your Omit choice."
        if entity_key == "device_tracker:location" and not self.entry.options.get(CONF_ENABLE_TRACKER, DEFAULT_ENABLE_TRACKER):
            return "Hidden: location tracking is turned off."
        if entity_key in FRIDGE_ENTITIES and self.entry.options.get(CONF_ENABLE_FRIDGE) is False:
            return "Hidden: fridge controls are turned off."
        if entity_key in self.entry.options.get(CONF_ENABLE_ENTITIES, ()):
            return "Shown by your Always show choice; this does not prove support."
        evidence = self.detector.feature(ENTITY_FEATURES.get(entity_key, ""))
        if evidence.source == "observed_telemetry":
            return "Shown: a usable vehicle reading was observed."
        if evidence.source == "confirmed_command":
            return "Shown: a matching successful command result was received."
        if evidence.source == "model_default":
            return "Hidden by the model default; positive vehicle evidence can override this."
        if evidence.source == "vehicle_equipment":
            return "Shown by vehicle equipment evidence." if self.includes(entity_key) else "Hidden by vehicle equipment evidence."
        return "Hidden: no usable evidence yet. Missing data does not prove absence."
