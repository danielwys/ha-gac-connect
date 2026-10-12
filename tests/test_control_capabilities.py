"""Read-only troubleshooting response without identifiers or control inference."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import HomeAssistantError

from custom_components.gac_connect._vendor.gac_connect.errors import ResponseError
from custom_components.gac_connect.capability_report import cabin_capability_report
from custom_components.gac_connect.services import async_register_services
from test_regressions import coord, service_registry


def report(remote):
    return {"capabilities": [{"vin": "PRIVATE", "capability": {"remoteCapability": remote}}], "token": "PRIVATE"}


def test_capability_projection_keeps_numeric_metadata_and_excludes_private_unknown_data():
    result = cabin_capability_report(report({
        "airConditionCapabilities": [{"controlCapability": 3, "blowingModeCapability": 1,
            "supportBlowingModes": [1, 2, 3, 4], "bcontrolCapability": "0011",
            "enableFrontDefrosterCapability": 0, "token": "PRIVATE", "vin": "PRIVATE",
            "temperatureCapability": {"token": "PRIVATE"}, "maxTemperature": "PRIVATE"}],
        "seatTemperatureCapabilities": [{"seat": 1, "enableWindCapability": 2, "token": "PRIVATE"}],
        "sensitiveDataCapabilities": {"position": "PRIVATE"},
    }))
    assert result == {"airConditionCapabilities": [{"controlCapability": 3, "blowingModeCapability": 1,
        "supportBlowingModes": [1, 2, 3, 4], "bcontrolCapability": "0011", "enableFrontDefrosterCapability": 0,
        "temperatureCapability": None, "maxTemperature": None}],
        "seatTemperatureCapabilities": [{"seat": 1, "enableWindCapability": 2}]}
    assert "PRIVATE" not in str(result)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), [1, "PRIVATE"], [1, float("nan")], [1] * 65])
def test_capability_response_is_bounded_and_json_safe(value):
    result = cabin_capability_report(report({"airConditionCapabilities": [{"supportBlowingModes": value}]}))
    assert result["airConditionCapabilities"] == [{"supportBlowingModes": None}]
    assert result["seatTemperatureCapabilities"] is None


@pytest.mark.parametrize("data", [{}, {"capabilities": [None]}, {"capability": {}},
                                 {"capabilities": [{"capability": None}]}])
def test_unrecognized_metadata_does_not_become_an_unsupported_control_claim(data):
    with pytest.raises(HomeAssistantError):
        cabin_capability_report(data)


@pytest.mark.parametrize("failure", [False, True])
def test_capability_service_is_response_only_and_never_sends_or_confirms_a_command(failure):
    coordinator = SimpleNamespace(vin="VIN", client=SimpleNamespace(get_control_capabilities=AsyncMock(
        return_value=report({"airConditionCapabilities": [{"supportBlowingModes": [1, 2, 3, 4]}]}))))
    if failure:
        coordinator.client.get_control_capabilities.side_effect = ResponseError("unavailable")
    registry = SimpleNamespace(has_service=Mock(return_value=False), async_register=Mock())
    hass = SimpleNamespace(services=registry, config_entries=SimpleNamespace(async_entries=Mock(return_value=[
        SimpleNamespace(runtime_data=SimpleNamespace(coordinator=coordinator))])))
    async_register_services(hass)
    registration = next(call for call in registry.async_register.call_args_list if call.args[1] == "get_control_capabilities")
    assert registration.kwargs == {"supports_response": SupportsResponse.ONLY}
    handler, schema = registration.args[2:4]

    async def run():
        call = SimpleNamespace(data=schema({}))
        if failure:
            with pytest.raises(HomeAssistantError, match="unavailable"):
                await handler(call)
        else:
            result = await handler(call)
            assert result["airConditionCapabilities"] == [{"supportBlowingModes": [1, 2, 3, 4]}]

    with patch("custom_components.gac_connect.services.async_send") as send:
        asyncio.run(run())
    coordinator.client.get_control_capabilities.assert_awaited_once_with("VIN")
    send.assert_not_called()


@pytest.mark.parametrize("code", [0, "0", "0000", 1000, 2000, 13000])
def test_capability_action_uses_vendored_success_parser_before_scrubbing_response(code):
    coordinator = coord(None)
    coordinator.client._iov_read = AsyncMock(return_value={"code": code, "data": report({
        "airConditionCapabilities": [{"supportBlowingModes": [1, 2, 3, 4], "token": "PRIVATE"}],
    })})
    handler, schema = service_registry(coordinator)["get_control_capabilities"]
    result = asyncio.run(handler(SimpleNamespace(data=schema({}))))
    assert result == {"airConditionCapabilities": [{"supportBlowingModes": [1, 2, 3, 4]}],
                      "seatTemperatureCapabilities": None}
    coordinator.client._iov_read.assert_awaited_once_with(
        "/iov-vehicle-gateway/v1/iov/vehicle/access_gateway/get_capability",
        {"vin": "TESTVIN"}, sensitive={"vin": "TESTVIN"},
    )
    coordinator.client.command.assert_not_awaited()


def test_capability_action_shows_rejection_code_without_copying_private_gateway_message():
    coordinator = coord(None)
    coordinator.client._iov_read = AsyncMock(return_value={"code": 13002, "msg": "PRIVATE", "data": {"vin": "PRIVATE"}})
    handler, schema = service_registry(coordinator)["get_control_capabilities"]
    with pytest.raises(HomeAssistantError, match="result code 13002") as caught:
        asyncio.run(handler(SimpleNamespace(data=schema({}))))
    assert "PRIVATE" not in str(caught.value)
    coordinator.client._iov_read.assert_awaited_once()
    coordinator.client.command.assert_not_awaited()
