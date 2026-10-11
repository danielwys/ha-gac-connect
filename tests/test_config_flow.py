import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import aiohttp
import pytest
from homeassistant.core import HomeAssistant

from custom_components.gac_connect.config_flow import GacConfigFlow
from custom_components.gac_connect.const import CONF_EXPERIMENTAL_PINLESS_UNLOCK
from custom_components.gac_connect.entity_filter import UT_OMISSIONS


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


@pytest.mark.parametrize("profile", ["aion_ut", "aion_v", "auto_detect", "custom"])
@pytest.mark.parametrize("allow_unlock", [None, False, True])
def test_setup_saves_unlock_choice_for_every_profile(tmp_path, profile, allow_unlock):
    async def run():
        flow = GacConfigFlow()
        flow.hass = HomeAssistant(str(tmp_path))
        flow._vehicle = SimpleNamespace(vin="TESTVIN", model="Aion UT")
        flow._client = SimpleNamespace(session=SimpleNamespace(to_dict=Mock(return_value={})))

        form = await flow.async_step_profile()
        submitted = {"profile": profile}
        if allow_unlock is not None:
            submitted[CONF_EXPERIMENTAL_PINLESS_UNLOCK] = allow_unlock
        validated = form["data_schema"](submitted)
        assert validated[CONF_EXPERIMENTAL_PINLESS_UNLOCK] is (allow_unlock is True)

        result = await flow.async_step_profile(validated)
        if profile == "custom":
            assert result["type"] == "form" and result["step_id"] == "profile_custom"
            flow._client.session.to_dict.assert_not_called()
            result = await flow.async_step_profile_custom(result["data_schema"]({
                "enable_fridge": False, "omit_entities": ["cover:sunroof"],
            }))
        assert result["type"] == "create_entry"
        assert result["options"][CONF_EXPERIMENTAL_PINLESS_UNLOCK] is (allow_unlock is True)
        assert result["options"]["profile"] == profile
        if profile == "aion_ut":
            assert result["options"]["enable_fridge"] is False
            assert set(result["options"]["omit_entities"]) == UT_OMISSIONS
        elif profile == "custom":
            assert result["options"]["enable_fridge"] is False
            assert result["options"]["omit_entities"] == ["cover:sunroof"]
        else:
            assert result["options"]["enable_fridge"] is True
            assert result["options"]["omit_entities"] == []

    asyncio.run(run())
