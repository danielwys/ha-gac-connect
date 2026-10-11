import asyncio
import json
from types import SimpleNamespace

from custom_components.gac_connect.diagnostics import async_get_config_entry_diagnostics


def test_diagnostics_omit_private_urls_and_unreviewed_options():
    status = SimpleNamespace(
        updated_ms=123, online=True,
        raw={"nested": [{"vin": "PRIVATE-VIN", "latitude": 12.34,
                         "token": "PRIVATE-TOKEN"}], "soc": 0.5},
    )
    entry = SimpleNamespace(
        data={"vin": "PRIVATE-VIN", "session": {"token": "PRIVATE-TOKEN"}},
        options={"picture": "https://user:PRIVATE-PASSWORD@photos.invalid/a?token=PRIVATE-QUERY#PRIVATE-FRAGMENT",
                 "future_secret_option": "PRIVATE-FUTURE", "scan_interval": 300},
        runtime_data=SimpleNamespace(coordinator=SimpleNamespace(data=status)),
    )
    result = asyncio.run(async_get_config_entry_diagnostics(None, entry))
    assert "PRIVATE-" not in json.dumps(result)
    assert result["options"] == {"scan_interval": 300}
    assert result["status_raw"]["soc"] == 0.5
    assert result["status_updated_ms"] == 123
    assert entry.options["future_secret_option"] == "PRIVATE-FUTURE"
