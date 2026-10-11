"""Expected telemetry shared by pinned and native library test runs."""
import pytest

from custom_components.gac_connect.api import decode_status


@pytest.mark.parametrize("value,expected", [
    (-1, None), (-2, None), (128, None), (1638, None), ("-1", None), ("128", None),
    (True, None), (1.5, None), (float("nan"), None), (float("inf"), None), (10**400, None),
    (None, None), (0, 0), (127, 127), ("127", 127), ("64", 64),
])
def test_reported_weekly_mask_has_only_seven_day_bits(value, expected):
    raw = {"chargingAccessors": [{"weeklyReservation": value}]}
    assert decode_status(raw).charge_weekly == expected
