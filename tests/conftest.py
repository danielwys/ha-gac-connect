import pytest


@pytest.fixture(autouse=True)
def _no_result_poll_waits(monkeypatch):
    """Commands sent without a push channel poll for the car's answer; tests do not wait for it."""
    monkeypatch.setattr("custom_components.gac_connect.command_tracking.POLL_DELAYS", (0, 0, 0))
