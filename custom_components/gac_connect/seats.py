"""Read seats by their app IDs, including during partial library updates."""
from __future__ import annotations


def reported_seat(status, seat_id: int):
    return next((seat for seat in getattr(status, "seats", ()) if seat.position == str(seat_id)), None)


def ventilation_state(status, seat_id: int) -> bool | None:
    return getattr(reported_seat(status, seat_id), "ventilation_on", None)
