"""One boundary between Home Assistant and the installed library.

The public decode_status export was introduced with the consolidated behavior
API. Select that complete API when available, without subclassing its clients.
Older releases use the isolated b1 backports while the manifest pin stays b1.
Remove the fallback after a tested release with this API is pinned.
"""
from __future__ import annotations

import gac_connect as library

if hasattr(library, "decode_status"):
    from gac_connect import (
        Capabilities,
        Support,
        GacClient,
        PAUSE_UNSUPPORTED,
        PushClient,
        decode_status,
        doors_locked,
        local_mobile,
        validate_clock,
        validate_weekly,
        window_operation,
    )
else:
    from ._compat_b1.capabilities import Capabilities, Support
    from ._compat_b1.client import GacClient, PAUSE_UNSUPPORTED, validate_clock, validate_weekly, window_operation
    from ._compat_b1.lock_state import doors_locked
    from ._compat_b1.phone import local_mobile
    from ._compat_b1.push import PushClient
    from ._compat_b1.status import decode_status

__all__ = [
    "Capabilities", "Support",
    "GacClient", "PushClient", "PAUSE_UNSUPPORTED", "decode_status", "doors_locked",
    "local_mobile", "validate_clock", "validate_weekly", "window_operation",
]
