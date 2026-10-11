"""Run these contracts with both the pinned package and an installed candidate."""
import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import gac_connect
import pytest
from gac_connect.client import GacClient as LibraryClient
from gac_connect.const import PROFILE_MATERIAL, REGIONS
from gac_connect.session import MemoryStore

from custom_components.gac_connect import api, helpers


def test_expected_library_backend_is_actually_exercised(pytestconfig):
    native = api.GacClient is LibraryClient
    expected = pytestconfig.getoption("--library-backend")
    if expected is not None:
        assert native is (expected == "native"), "Wrong library installed or compatibility overrides mask the candidate"
    if native:
        for name in api.__all__:
            assert getattr(api, name) is getattr(gac_connect, name), name
        assert not any(name.startswith("custom_components.gac_connect._compat_b1") for name in sys.modules)
    else:
        assert not hasattr(gac_connect, "decode_status")
        assert api.GacClient.__module__ == "custom_components.gac_connect._compat_b1.client"
        assert api.PushClient.__module__ == "custom_components.gac_connect._compat_b1.push"


@pytest.mark.parametrize("region", list(REGIONS))
def test_factory_uses_selected_client_and_loads_correct_profile_off_loop(region):
    material = Mock()
    hass = SimpleNamespace(async_add_executor_job=AsyncMock(return_value=material))
    http, store = Mock(), MemoryStore()
    with patch.object(helpers, "async_setup_limits", new=AsyncMock()) as limits:
        client = asyncio.run(helpers.async_build_client(hass, region, http, store))
    assert type(client) is api.GacClient
    assert client._http is http and client._store is store and client._m is material
    limits.assert_awaited_once_with(hass)
    hass.async_add_executor_job.assert_awaited_once_with(helpers.load_material, PROFILE_MATERIAL[REGIONS[region]["profile"]])
