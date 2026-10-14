import asyncio
import json
from types import SimpleNamespace

from custom_components.gac_connect.diagnostics import async_get_config_entry_diagnostics
from custom_components.gac_connect._vendor import LIBRARY_COMMIT, LIBRARY_SOURCE, LIBRARY_VERSION


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
    assert result["library"] == {
        "source": LIBRARY_SOURCE, "version": LIBRARY_VERSION, "commit": LIBRARY_COMMIT, "bundled": True,
    }
    assert entry.options["future_secret_option"] == "PRIVATE-FUTURE"


def test_location_and_contact_groups_keep_field_names_but_mask_every_value():
    status = SimpleNamespace(updated_ms=1, online=True, raw={
        "positionAccessors": [{"latitude": 13.7, "longitude": 100.5, "positionMode": 1, "dateTime": "PRIVATE-TIME",
                               "address": "PRIVATE-STREET", "altitude": None}],
        "dialNumberAccessors": [{"phone": "PRIVATE-PHONE", "nation": 66, "call": 1}],
        "gpsAuthorAccessors": [{"authorMode": 2, "authorStartYear": 2026}],
        "drivingOverviewAccessors": [{"remainVoyageCourse": 300}],
    })
    entry = SimpleNamespace(data={}, options={}, runtime_data=SimpleNamespace(coordinator=SimpleNamespace(data=status)))
    raw = asyncio.run(async_get_config_entry_diagnostics(None, entry))["status_raw"]
    assert "PRIVATE-" not in json.dumps(raw) and "13.7" not in json.dumps(raw)
    assert set(raw["positionAccessors"][0]) == {"latitude", "longitude", "positionMode", "dateTime", "address",
                                                "altitude"}
    assert raw["positionAccessors"][0]["altitude"] is None
    assert raw["dialNumberAccessors"][0] == {"phone": "**REDACTED**", "nation": "**REDACTED**", "call": "**REDACTED**"}
    assert raw["gpsAuthorAccessors"][0]["authorMode"] == "**REDACTED**"
    assert raw["drivingOverviewAccessors"][0]["remainVoyageCourse"] == 300
    assert status.raw["positionAccessors"][0]["latitude"] == 13.7   # the live report is not modified


def test_push_identifiers_show_their_shape_but_no_values():
    from custom_components.gac_connect._vendor.gac_connect.push import BrokerInfo

    broker = BrokerInfo(host="broker.invalid", port=2883, client_id="app_1234567890abcdef",
                        username="66812345678", password="PRIVATE-PASSWORD",
                        topics=("iov/result/LGXCE4CB0SA000001", "iov/notice/987654321/all", "plain/topic"))
    status = SimpleNamespace(updated_ms=1, online=True, raw={})
    coordinator = SimpleNamespace(data=status, vin="LGXCE4CB0SA000001",
                                  push=SimpleNamespace(connected=True, broker=broker))
    entry = SimpleNamespace(data={"vin": "LGXCE4CB0SA000001", "mobile": "0812345678"}, options={},
                            runtime_data=SimpleNamespace(coordinator=coordinator))
    push = asyncio.run(async_get_config_entry_diagnostics(None, entry))["push"]
    assert push == {"connected": True, "tls": False, "host": "broker.invalid", "port": 2883,
                    "topics": ["iov/result/<VIN>", "iov/notice/<9 digits>/all", "plain/topic"],
                    "client_id": "app_<16 chars>", "username": "66<mobile>"}
    for private in ("PRIVATE", "LGXCE", "812345678", "987654321", "1234567890"):
        assert private not in json.dumps(push)


def test_no_push_section_before_the_channel_connects():
    status = SimpleNamespace(updated_ms=1, online=True, raw={})
    coordinator = SimpleNamespace(data=status, push=SimpleNamespace(connected=False, broker=None))
    entry = SimpleNamespace(data={}, options={}, runtime_data=SimpleNamespace(coordinator=coordinator))
    assert asyncio.run(async_get_config_entry_diagnostics(None, entry))["push"] is None
