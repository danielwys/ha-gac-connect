import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import aiohttp
import pytest
from homeassistant.core import HomeAssistant

from custom_components.gac_connect.config_flow import GacConfigFlow


@pytest.mark.parametrize("error", [aiohttp.ClientConnectionError(), TimeoutError()])
@pytest.mark.parametrize("step", ["user", "sms", "vehicle", "reauth_confirm"])
def test_network_failure_is_a_flow_error(tmp_path, error, step):
    async def run():
        flow = GacConfigFlow()
        flow.hass = HomeAssistant(str(tmp_path))
        flow._client = SimpleNamespace(start_captcha=AsyncMock(side_effect=error),
                                       login_sms=AsyncMock(side_effect=error), list_vehicles=AsyncMock(side_effect=error))
        data = {"region": "AU", "mobile": "0412345678", "code": "123456"}
        with patch("custom_components.gac_connect.config_flow.async_create_clientsession", return_value=Mock()), \
             patch("custom_components.gac_connect.config_flow.async_build_client", AsyncMock(return_value=flow._client)):
            result = await getattr(flow, f"async_step_{step}")(data)
        if step == "vehicle":
            assert result["type"] == "abort" and result["reason"] == "cannot_connect"
        else:
            assert result["type"] == "form" and result["errors"] == {"base": "cannot_connect"}
    asyncio.run(run())
