"""Exercise authorization through real HA dispatch; only GAC transport is mocked."""
import asyncio
from unittest.mock import AsyncMock

import pytest
from homeassistant.auth import auth_manager_from_config
from homeassistant.auth.const import GROUP_ID_ADMIN, GROUP_ID_READ_ONLY, GROUP_ID_USER
from homeassistant.auth.models import Group
from homeassistant.core import Context
from homeassistant.exceptions import Unauthorized, UnknownUser
from homeassistant.helpers import entity_registry as er

from test_full_setup import entry, fake_client, home_assistant, offline, stop


async def users(hass):
    hass.auth = await auth_manager_from_config(hass, [], [])
    owner = await hass.auth.async_create_user("Owner", group_ids=[GROUP_ID_ADMIN])
    member = await hass.auth.async_create_user("Member", group_ids=[GROUP_ID_USER])
    viewer = await hass.auth.async_create_user("Viewer", group_ids=[GROUP_ID_READ_ONLY])
    return owner, member, viewer


async def call(hass, user, domain, service, data, **kwargs):
    return await hass.services.async_call(domain, service, data, blocking=True,
                                         context=Context(user_id=user.id) if user else Context(), **kwargs)


def test_read_only_users_cannot_send_commands_or_change_configuration(tmp_path):
    async def run():
        hass = await home_assistant(tmp_path)
        owner, member, viewer = await users(hass)
        car, client = entry(), fake_client()
        client.command = AsyncMock(return_value={"code": 13001})
        client.unlock = AsyncMock(return_value={"code": 13001})
        try:
            with offline(client):
                await hass.config_entries.async_add(car)
                await hass.async_block_till_done()
                registry = er.async_get(hass)
                def entity(domain, key):
                    return registry.async_get_entity_id(domain, "gac_connect", f"VIN_{key}")

                # These are ordinary entity services, whose user context must reach
                # the integration's admin guard even though CONFIG is not an ACL.
                for user in (member, viewer):
                    for domain, service, data in (
                        ("switch", "turn_on", {"entity_id": entity("switch", "allow_remote_unlock")}),
                        ("number", "set_value", {"entity_id": entity("number", "polling_interval"), "value": 600}),
                        ("time", "set_value", {"entity_id": entity("time", "quiet_hours_start"), "time": "22:00:00"}),
                        ("button", "press", {"entity_id": entity("button", "refresh_capabilities")}),
                    ):
                        assert data["entity_id"] is not None, (domain, service, data)
                        with pytest.raises(Unauthorized):
                            await call(hass, user, domain, service, data)
                    with pytest.raises(Unauthorized):
                        await call(hass, user, "gac_connect", "set_entity_visibility",
                                   {"entity": "switch:steering_heat", "visibility": "always"})
                assert car.options.get("experimental_pinless_unlock", False) is False
                assert car.options["scan_interval"] == 300

                # An admin can opt in. That still does not authorize a read-only
                # caller to bypass lock.unlock via the advanced command service.
                await call(hass, owner, "switch", "turn_on",
                           {"entity_id": entity("switch", "allow_remote_unlock")})
                await hass.async_block_till_done()
                assert car.options["experimental_pinless_unlock"] is True
                for command in ("unlock", "tailgate-open", "horn-on"):
                    with pytest.raises(Unauthorized):
                        await call(hass, viewer, "gac_connect", "send_command", {"command": command})
                for service, data in (("charge_now", {}), ("set_charge_window", {"start": "23:00", "stop": "06:00"})):
                    with pytest.raises(Unauthorized):
                        await call(hass, viewer, "gac_connect", service, data)
                client.command.assert_not_awaited()
                client.unlock.assert_not_awaited()
                await call(hass, member, "gac_connect", "send_command", {"command": "unlock"})
                client.command.assert_awaited_once_with("VIN", "unlock")

                # Trusted HA automation calls remain supported, as in HA's own
                # admin services; an invalid authenticated user is never trusted.
                await call(hass, None, "switch", "turn_off",
                           {"entity_id": entity("switch", "allow_remote_unlock")})
                assert car.options["experimental_pinless_unlock"] is False
                with pytest.raises(UnknownUser):
                    await hass.services.async_call("gac_connect", "send_command", {"command": "unlock"},
                                                   blocking=True, context=Context(user_id="deleted-user"))
                await hass.async_block_till_done()
        finally:
            await stop(hass)
    asyncio.run(run())


def test_raw_services_respect_vehicle_feature_and_disabled_entity_scope(tmp_path):
    async def run():
        hass = await home_assistant(tmp_path)
        _, member, _ = await users(hass)
        car, client = entry(), fake_client()
        client.command = AsyncMock(return_value={"code": 13001})
        try:
            with offline(client):
                await hass.config_entries.async_add(car)
                await hass.async_block_till_done()
                hass.config_entries.async_update_entry(car, options={**car.options, "experimental_pinless_unlock": True})
                await hass.async_block_till_done()
                registry = er.async_get(hass)
                lock_id = registry.async_get_entity_id("lock", "gac_connect", "VIN_doors")
                battery_id = registry.async_get_entity_id("sensor", "gac_connect", "VIN_soc")
                assert lock_id and battery_id
                def policy(ids):
                    member.groups = [Group("Scoped", {"entities": {"entity_ids": ids}})]
                policy({battery_id: {"read": True}, "lock.another_car": {"control": True}})
                with pytest.raises(Unauthorized):
                    await call(hass, member, "gac_connect", "send_command", {"command": "unlock"})
                await call(hass, member, "gac_connect", "get_control_capabilities", {}, return_response=True)
                policy({lock_id: {"read": True, "control": True}})
                with pytest.raises(Unauthorized):
                    await call(hass, member, "gac_connect", "send_command", {"command": "tailgate-open"})
                await call(hass, member, "gac_connect", "send_command", {"command": "unlock"})
                client.command.assert_awaited_once_with("VIN", "unlock")
                registry.async_update_entity(lock_id, disabled_by=er.RegistryEntryDisabler.USER)
                with pytest.raises(Unauthorized):
                    await call(hass, member, "gac_connect", "send_command", {"command": "unlock"})
                policy({"lock.another_car": {"read": True, "control": True}})
                with pytest.raises(Unauthorized):
                    await call(hass, member, "gac_connect", "get_control_capabilities", {}, return_response=True)
                assert client.command.await_count == 1
                await hass.async_block_till_done()
        finally:
            await stop(hass)
    asyncio.run(run())
