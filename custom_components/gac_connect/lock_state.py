"""Decode door lock state without treating missing readings as unlocked.

The pinned client considers every door whose raw ``lock`` value is not integer
zero to be unlocked. Some cars report numeric strings or an unknown sentinel,
so use the raw accessors until the client library distinguishes those cases.
"""
from __future__ import annotations

from gac_connect.models import VehicleStatus


def doors_locked(status: VehicleStatus | None, model: str | None = None) -> bool | None:
    """Decode locks, accounting for the UT's unreported rear lock readings."""
    if status is None:
        return None
    doors = status.raw.get("doorAccessors")
    if not isinstance(doors, list) or not doors:
        return None

    unknown = False
    known_locked = False
    codes_by_door: dict[int, float] = {}
    for door in doors:
        value = door.get("lock") if isinstance(door, dict) else None
        if isinstance(value, bool):
            unknown = True
            continue
        try:
            code = float(value)
        except (TypeError, ValueError, OverflowError):
            unknown = True
            continue
        if isinstance(door, dict):
            index = door.get("door")
            if isinstance(index, int) and not isinstance(index, bool):
                codes_by_door[index] = code
        if code == 1:
            return False
        if code == 0:
            known_locked = True
        else:
            unknown = True
    if not known_locked:
        return None
    if not unknown:
        return True

    # The UT reports door 3/4 as -1 even when its app confirms the car is
    # locked. Paired live captures show door 1/2 flip 0 <-> 1 with the remote.
    if (model or "").strip().casefold().startswith("aion ut"):
        if (len(doors) == 4
                and codes_by_door == {1: 0, 2: 0, 3: -1, 4: -1}):
            return True
    return None


def doors_unlocked(status: VehicleStatus, model: str | None = None) -> bool | None:
    """Home Assistant lock binary sensors use on for unlocked."""
    locked = doors_locked(status, model)
    return None if locked is None else not locked
