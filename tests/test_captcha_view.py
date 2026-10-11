import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import UnknownFlow

from custom_components.gac_connect.captcha_view import GacCaptchaView, flow_state
from custom_components.gac_connect.config_flow import GacConfigFlow


def pending(hass):
    hass.config_entries = SimpleNamespace(flow=SimpleNamespace(async_get=Mock(return_value={"step_id": "captcha"}),
                                                              async_abort=Mock(), async_configure=AsyncMock()))
    client = SimpleNamespace(request_sms=AsyncMock())
    flow_state(hass)["flow"] = {"client": client, "mobile": "synthetic", "expires": time.monotonic()+600,
                               "attempt": 1, "status": "pending", "captcha": SimpleNamespace(background="", piece="")}
    request = SimpleNamespace(app={"hass": hass}, query={"flow_id": "flow", "state": "1"},
                              json=AsyncMock(return_value={"x": 10}))
    return request, client


def test_removed_flow_and_expired_flow_cannot_request_sms(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        for expired in (False, True):
            request, client = pending(hass)
            if expired:
                flow_state(hass)["flow"]["expires"] = 0
            else:
                hass.config_entries.flow.async_get.side_effect = UnknownFlow
            assert (await GacCaptchaView().post(request)).status == 404
            assert "flow" not in flow_state(hass)
            client.request_sms.assert_not_called()
    asyncio.run(run())


def test_flow_removal_cancels_inflight_verification_and_closes_session(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        request, client = pending(hass)
        started = asyncio.Event()
        async def waiting(*args):
            started.set()
            await asyncio.Event().wait()
        client.request_sms.side_effect = waiting
        task = asyncio.create_task(GacCaptchaView().post(request))
        await started.wait()
        flow = GacConfigFlow()
        flow.hass, flow.flow_id = hass, "flow"
        flow._http = SimpleNamespace(close=AsyncMock())
        http = flow._http
        cancel = flow._captcha_expiry = Mock()
        flow.async_remove()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)
        assert not flow_state(hass)
        http.close.assert_awaited_once()
        cancel.assert_called_once()
        hass.config_entries.flow.async_configure.assert_not_awaited()
    asyncio.run(run())


@pytest.mark.parametrize("body", [[], {}, {"x": -1}, {"x": 264}, {"x": True}, {"x": "10"}])
def test_invalid_offset_cannot_request_sms(tmp_path, body):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        request, client = pending(hass)
        request.json.return_value = body
        response = await GacCaptchaView().post(request)
        assert response.status == 400
        client.request_sms.assert_not_called()
    asyncio.run(run())


def test_expiry_aborts_the_flow(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        pending(hass)
        flow = GacConfigFlow()
        flow.hass, flow.flow_id = hass, "flow"
        flow._expire_puzzle(None)
        hass.config_entries.flow.async_abort.assert_called_once_with("flow")
        assert not flow_state(hass)
    asyncio.run(run())
