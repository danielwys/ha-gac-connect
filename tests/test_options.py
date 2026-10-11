import asyncio
from datetime import datetime
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.gac_connect.config_flow import GacOptionsFlow
from custom_components.gac_connect.coordinator import GacCoordinator
from test_session_store import make_entry


@pytest.mark.parametrize("start,end,error", [
    ("25:70", "06:00", "invalid_quiet_time"), ("1:00", "06:00", "invalid_quiet_time"),
    ("22:00", "", "quiet_pair_required"), ("", "06:00", "quiet_pair_required"),
    ("22:00", "22:00", "quiet_times_equal"), ("words", "06:00", "invalid_quiet_time"),
    ("", "", None), ("22:00", "06:00", None), ("08:00", "18:00", None),
])
def test_quiet_hours_validation(tmp_path, start, end, error):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        entry = make_entry(hass, {"vin": "VIN"})
        flow = GacOptionsFlow()
        flow.hass = hass
        flow.handler = entry.entry_id
        result = await flow.async_step_init({"quiet_start": start, "quiet_end": end})
        if error:
            assert result["type"] == "form"
            assert error in result["errors"].values()
        else:
            assert result["type"] == "create_entry"
            assert result["data"]["quiet_start"] == start
            assert result["data"]["quiet_end"] == end
    asyncio.run(run())


@pytest.mark.parametrize("start,end,clock,quiet", [
    ("22:00", "06:00", "23:00", True), ("22:00", "06:00", "05:59", True),
    ("22:00", "06:00", "06:00", False), ("08:00", "18:00", "08:00", True),
    ("08:00", "18:00", "18:00", False), ("", "", "23:00", False),
])
def test_quiet_hours_boundaries_use_home_assistant_clock(start, end, clock, quiet):
    from types import SimpleNamespace
    coordinator = object.__new__(GacCoordinator)
    coordinator.config_entry = SimpleNamespace(options={"quiet_start": start, "quiet_end": end})
    now = datetime.fromisoformat(f"2026-10-11T{clock}:00+07:00")
    with patch("custom_components.gac_connect.coordinator.dt_util.now", return_value=now):
        assert coordinator._in_quiet_hours() is quiet
