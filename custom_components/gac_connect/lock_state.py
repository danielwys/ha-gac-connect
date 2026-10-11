"""Map library lock state to Home Assistant's binary sensor polarity."""
from __future__ import annotations

from gac_connect.models import VehicleStatus

from .api import doors_locked as doors_locked


def doors_unlocked(status: VehicleStatus, model: str | None = None) -> bool | None:
    """Home Assistant lock binary sensors use on for unlocked."""
    locked = doors_locked(status, model)
    return None if locked is None else not locked
