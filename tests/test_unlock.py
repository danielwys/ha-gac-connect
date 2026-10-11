import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from custom_components.gac_connect._vendor.gac_connect.errors import CommandError
from homeassistant.exceptions import HomeAssistantError

from custom_components.gac_connect.const import CONF_EXPERIMENTAL_PINLESS_UNLOCK
from custom_components.gac_connect.lock import GacLock
from test_client_failures import Response, make_client
from test_regressions import coord


@pytest.mark.parametrize("enabled", [False, True])
def test_unlock_calls_public_client_method_only_when_enabled(enabled):
    coordinator = coord(None)
    coordinator.config_entry.options[CONF_EXPERIMENTAL_PINLESS_UNLOCK] = enabled
    coordinator.client.unlock = AsyncMock(return_value={"code": 13001})
    entity = GacLock(coordinator)
    entity.async_write_ha_state = Mock()
    if enabled:
        asyncio.run(entity.async_unlock())
        coordinator.client.unlock.assert_awaited_once_with("TESTVIN")
        coordinator.async_schedule_command_refresh.assert_called_once()
        assert not entity.is_unlocking
    else:
        with pytest.raises(HomeAssistantError, match="disabled"):
            asyncio.run(entity.async_unlock())
        coordinator.client.unlock.assert_not_called()


def test_unlock_failure_clears_pending_state_and_surfaces_error():
    coordinator = coord(None)
    coordinator.config_entry.options[CONF_EXPERIMENTAL_PINLESS_UNLOCK] = True
    coordinator.client.unlock = AsyncMock(side_effect=CommandError("rejected"))
    entity = GacLock(coordinator)
    entity.async_write_ha_state = Mock()
    with pytest.raises(HomeAssistantError, match="rejected"):
        asyncio.run(entity.async_unlock())
    assert not entity.is_unlocking
    coordinator.client.unlock.assert_awaited_once()


@pytest.mark.parametrize("body,accepted", [({"code": 13001}, True), ({"success": "false"}, False), ({}, False)])
@pytest.mark.parametrize("generic", [False, True])
def test_unlock_uses_normal_transport_and_command_acceptance(body, accepted, generic):
    from custom_components.gac_connect._vendor.gac_connect.limits import Limiter
    from unittest.mock import patch

    with patch("custom_components.gac_connect._vendor.gac_connect.client.DEFAULT_LIMITER", Limiter(min_gap=0, state_path=None)):
        client, http, _ = make_client(Response(body=body), valid=True)

    async def run():
        await client.load()
        action = client.command("VIN", "unlock") if generic else client.unlock("VIN")
        if accepted:
            assert await action == body
        else:
            with pytest.raises(CommandError):
                await action

    asyncio.run(run())
    assert http.post.call_count == 1
    assert http.post.call_args.args[0].endswith("/door/unlock")
