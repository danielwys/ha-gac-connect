"""Opt-in test of the gateway's unlock command without a PIN.

The pinned client blocks this command locally before contacting the gateway.
Use its existing command transport and result decoder for this single command;
do not weaken the guard for other PIN-marked commands or retry an action.
"""
from __future__ import annotations

from typing import Any

from gac_connect.client import GacClient, _command_result
from gac_connect.commands import CATALOG, build_body


async def async_try_pinless_unlock(client: GacClient, vin: str) -> Any:
    """Send one PIN-free door/unlock request and return the gateway result."""
    command = CATALOG["unlock"]
    await client._ensure_token()
    response = await client._iov_call(
        command.path,
        build_body(command, vin),
        sensitive={"vin": vin},
        kind="command",
    )
    return _command_result(response)
