"""The bundled cards use native dashboard resources without altering unrelated modules."""
import asyncio
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.components.lovelace.const import LOVELACE_DATA
from homeassistant.components.lovelace.resources import ResourceStorageCollection, ResourceYAMLCollection
from homeassistant.core import HomeAssistant

from custom_components.gac_connect import dashboard


@contextmanager
def frontend(hass):
    hass.http = Mock(async_register_static_paths=AsyncMock())
    with patch.object(dashboard, "_available", return_value=True), \
         patch("homeassistant.components.frontend.add_extra_js_url") as added, \
         patch("homeassistant.components.frontend.remove_extra_js_url") as removed:
        yield added, removed


def resources(hass, *, mapping=False):
    collection = ResourceStorageCollection(hass, SimpleNamespace(async_load=AsyncMock(return_value={"views": []})))
    hass.data[LOVELACE_DATA] = {"resources": collection} if mapping else SimpleNamespace(resources=collection)
    return collection


@pytest.mark.parametrize("mapping", [False, True])
def test_one_module_is_shared_by_all_opted_in_cars_and_removed_only_after_the_last(tmp_path, mapping):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        collection = resources(hass, mapping=mapping)
        try:
            with frontend(hass) as (added, removed):
                await dashboard.async_want_cards(hass, "one", False)
                assert not collection.async_items()
                await dashboard.async_want_cards(hass, "one", True)
                item, = collection.async_items()
                assert item["type"] == "module"
                assert item["url"].startswith("/gac_connect/frontend/gac-connect-cards-main.js?v=")
                assert len(item["url"].rsplit("=", 1)[1]) == 12
                await dashboard.async_want_cards(hass, "two", True)
                assert collection.async_items() == [item]
                await dashboard.async_want_cards(hass, "one", False)
                assert collection.async_items() == [item]
                await dashboard.async_want_cards(hass, "two", False)
                assert collection.async_items() == []
                added.assert_not_called()
                removed.assert_not_called()
                assert hass.http.async_register_static_paths.await_count == 1
        finally:
            await hass.async_stop(force=True)
    asyncio.run(run())


def test_reload_updates_existing_version_and_retires_only_exact_local_duplicates(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        collection = resources(hass)
        path = "/gac_connect/frontend/gac-connect-cards-main.js"
        try:
            await collection.async_get_info()
            old = await collection.async_create_item({"url": f"{path}?v=old", "res_type": "module"})
            await collection.async_create_item({"url": f"{path}?v=duplicate", "res_type": "module"})
            unrelated = await collection.async_create_item({"url": "/local/another-card.js", "res_type": "module"})
            external = await collection.async_create_item({"url": f"https://example.com{path}", "res_type": "module"})
            with frontend(hass) as (added, _):
                await dashboard.async_want_cards(hass, "one", True)
                items = collection.async_items()
                updated, = [item for item in items if item["url"].startswith(path)]
                assert updated["id"] == old["id"] and updated["url"] != old["url"]
                assert unrelated in items and external in items and len(items) == 3
                await dashboard.async_want_cards(hass, "one", False)
                assert collection.async_items() == [unrelated, external]
                added.assert_not_called()
        finally:
            await hass.async_stop(force=True)
    asyncio.run(run())


def test_yaml_resources_are_not_modified_and_keep_the_existing_loader(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        existing = [{"url": "/local/user-card.js", "type": "module"}]
        collection = ResourceYAMLCollection(existing)
        hass.data[LOVELACE_DATA] = SimpleNamespace(resources=collection)
        try:
            with frontend(hass) as (added, removed):
                await dashboard.async_want_cards(hass, "one", True)
                url = added.call_args.args[1]
                assert url.startswith("/gac_connect/frontend/gac-connect-cards.js?v=")
                await dashboard.async_want_cards(hass, "one", False)
                removed.assert_called_once_with(hass, url)
                assert collection.async_items() == existing
        finally:
            await hass.async_stop(force=True)
    asyncio.run(run())


def test_opt_out_cleans_stale_resource_even_if_the_bundle_was_removed(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        collection = resources(hass)
        try:
            await collection.async_get_info()
            await collection.async_create_item({"url": "/gac_connect/frontend/gac-connect-cards-main.js?v=old",
                                                "res_type": "module"})
            unrelated = await collection.async_create_item({"url": "/local/user-card.js", "res_type": "module"})
            with frontend(hass) as (added, removed), \
                 patch.object(dashboard, "FRONTEND_DIR", tmp_path / "missing-assets"):
                await dashboard.async_want_cards(hass, "one", False)
                assert collection.async_items() == [unrelated]
                added.assert_not_called()
                removed.assert_not_called()
        finally:
            await hass.async_stop(force=True)
    asyncio.run(run())


def test_resource_storage_error_falls_back_without_preventing_the_car_from_loading(tmp_path, caplog):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        collection = resources(hass)
        try:
            with frontend(hass) as (added, removed), \
                 patch.object(collection, "async_get_info", side_effect=OSError("resource store unavailable")):
                await dashboard.async_want_cards(hass, "one", True)
                assert added.call_count == 1
                assert "using the page loader" in caplog.text
                await dashboard.async_want_cards(hass, "one", False)
                removed.assert_called_once()
        finally:
            await hass.async_stop(force=True)
    asyncio.run(run())
