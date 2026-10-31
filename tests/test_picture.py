"""Keep existing car pictures through upgrades and unrelated option changes."""
import asyncio
from unittest.mock import Mock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.gac_connect.config_flow import GacOptionsFlow
from custom_components.gac_connect.dashboard import VehicleDashboard
from custom_components.gac_connect.device_tracker import GacDeviceTracker
from custom_components.gac_connect.settings import async_set_options, picture_url
from test_autodetect import vehicle
from test_dashboard import lovelace
from test_session_store import make_entry

PICTURE = "/api/image/serve/example-image/512x512"


@pytest.mark.parametrize("url", [PICTURE, "/local/car.png", "https://example.com/car.webp", "http://example.com/car.png", ""])
def test_picture_option_survives_options_form_and_entity_settings(tmp_path, url):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        car = make_entry(hass, {"vin": "VIN", "model": "Aion V"})
        hass.config_entries._store = Mock()
        hass.config_entries.async_update_entry(car, options={"picture": url})
        flow = GacOptionsFlow()
        flow.hass, flow.handler = hass, car.entry_id
        form = await flow.async_step_settings()
        result = await flow.async_step_settings(form["data_schema"]({"scan_interval": 600}))
        assert result["type"] == "menu" and car.options["picture"] == url
        async_set_options(hass, car, {"scan_interval": 900})
        assert car.options["picture"] == url
    asyncio.run(run())


@pytest.mark.parametrize("url", ["javascript:alert(1)", "data:text/html,example", "//example.com/car.png",
                                 "file:///secret", "https://user:pass@example.com/car.png", "/\\example.com/car.png",
                                 "https://example.com/\ncar.png"])
def test_invalid_picture_urls_are_rejected_without_changing_options(tmp_path, url):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        car = make_entry(hass, {"vin": "VIN"})
        hass.config_entries.async_update_entry(car, options={"picture": PICTURE})
        flow = GacOptionsFlow()
        flow.hass, flow.handler = hass, car.entry_id
        with pytest.raises(ValueError):
            picture_url(url)
        result = await flow.async_step_settings({"picture": url})
        assert result["errors"]["picture"] == "invalid_picture"
        assert car.options["picture"] == PICTURE
    asyncio.run(run())


def test_picture_updates_tracker_and_generated_dashboard_without_overwriting_edits(tmp_path):
    async def run():
        async with vehicle(tmp_path, options={"picture": PICTURE}) as (hass, car, coordinator):
            tracker = GacDeviceTracker(coordinator)
            assert tracker.entity_picture == PICTURE
            boards, _, cleanup = lovelace()
            try:
                dashboard = VehicleDashboard(hass, car.entry_id)
                await dashboard.async_apply(("vehicle", "charging"), "device", "AION V", tracker.entity_picture)
                board = boards["gac-aion-v"]
                cards = board.saved["views"][0]["cards"]
                assert cards[0]["image"] == PICTURE and "image" not in cards[1]
                async_set_options(hass, car, {"picture": "/local/updated.png"})
                assert tracker.entity_picture == "/local/updated.png"
                await dashboard.async_apply(("vehicle", "charging"), "device", "AION V", tracker.entity_picture)
                assert board.saved["views"][0]["cards"][0]["image"] == "/local/updated.png"
                board.saved["views"][0]["cards"][0]["image"] = "/local/owner-edited.png"
                await dashboard.async_apply(("vehicle", "charging"), "device", "AION V", PICTURE)
                assert board.saved["views"][0]["cards"][0]["image"] == "/local/owner-edited.png"
            finally:
                cleanup()
    asyncio.run(run())
