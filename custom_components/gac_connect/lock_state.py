"""Map library lock state to Home Assistant's binary sensor polarity."""
from __future__ import annotations

from gac_connect.models import VehicleStatus

from .api import doors_locked as _library_doors_locked


def doors_locked(status: VehicleStatus | None, model: str | None = None) -> bool | None:
    """Read door locks, including the Aion V's central locking pattern."""
    locked = _library_doors_locked(status, model)
    if locked is not None or status is None or (model or "").strip().casefold() != "aion v":
        return locked

    # The Aion V's central lock can be inferred from the two front lock readings
    # when the body alarm is armed and each door reports closed.
    if status.anti_theft_on is not True or not all(value is False for value in (
        status.front_left_door_open,
        status.front_right_door_open,
        status.rear_left_door_open,
        status.rear_right_door_open,
    )):
        return None

    doors = status.raw.get("doorAccessors")
    if not isinstance(doors, list) or len(doors) != 4:
        return None
    locks = {
        door["door"]: door["lock"]
        for door in doors
        if isinstance(door, dict) and type(door.get("door")) is int and type(door.get("lock")) is int
    }
    return True if locks == {1: 0, 2: 0, 3: -1, 4: -1} else None


def car_locked(status: VehicleStatus | None, model: str | None = None) -> bool | None:
    """Locked means all four doors locked and the boot shut: with the boot open the car is not locked."""
    if status is not None and status.hatch_open is True:
        return False
    return doors_locked(status, model)


def doors_unlocked(status: VehicleStatus, model: str | None = None) -> bool | None:
    """Home Assistant lock binary sensors use on for unlocked; an open boot counts as unlocked."""
    locked = car_locked(status, model)
    return None if locked is None else not locked
