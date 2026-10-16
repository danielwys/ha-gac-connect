"""Alerts worked out from the car's own report; no account sign-in needed.

Changes fire at once (charging finished, sterilisation or Smart battery temp finished).
Conditions while the car is off (left unlocked, something left open) fire once they have
lasted HOLD_SECONDS, so locking up a minute after switching off does not raise them.
"""
from __future__ import annotations

from typing import Any

from .api import doors_locked

HOLD_SECONDS = 300.0
CAR_ALERT_TYPES = (
    "charging_finished", "left_unlocked", "left_open", "window_left_open",
    "sterilisation_finished", "battery_temp_finished",
)


def _openings(status) -> list[str]:
    return [name for name, value in (("door", status.door_open), ("boot", status.hatch_open),
                                     ("bonnet", getattr(status, "hood_open", None))) if value is True]


class CarAlertWatcher:
    """Compares each new report with the last one and with how long a condition has lasted."""

    def __init__(self, model: str | None) -> None:
        self._model = model
        self._previous = None
        self._since: dict[str, float] = {}
        self._raised: set[str] = set()

    def observe(self, status, now: float) -> list[tuple[str, dict[str, Any]]]:
        if status is None or status is self._previous:
            return []
        previous, self._previous = self._previous, status
        alerts: list[tuple[str, dict[str, Any]]] = []
        if previous is not None:
            if previous.charging is True and status.charging is False and status.plugged_in is True:
                alerts.append(("charging_finished", {"soc": status.soc}))
            if getattr(previous, "sterilisation_on", None) is True and getattr(status, "sterilisation_on", None) is False:
                alerts.append(("sterilisation_finished", {}))
            if (getattr(previous, "battery_precondition_on", None) is True
                    and getattr(status, "battery_precondition_on", None) is False):
                alerts.append(("battery_temp_finished", {}))
        off = status.power_on is False
        openings = _openings(status)
        lasting = {
            "left_unlocked": (off and doors_locked(status, self._model) is False, {}),
            "left_open": (off and bool(openings), {"open": openings}),
            "window_left_open": (off and status.window_open is True, {}),
        }
        for name, (active, attributes) in lasting.items():
            if not active:
                self._since.pop(name, None)
                self._raised.discard(name)
                continue
            start = self._since.setdefault(name, now)
            if name not in self._raised and now - start >= HOLD_SECONDS:
                self._raised.add(name)
                alerts.append((name, attributes))
        return alerts
