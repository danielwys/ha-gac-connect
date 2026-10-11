"""Push validation backport for the pinned b1 library only."""
from __future__ import annotations

import json

from gac_connect.push import PushClient as LibraryPushClient


class PushClient(LibraryPushClient):
    def _unwrap(self, payload: bytes) -> bytes:
        """Reject plaintext and failed envelopes when decryption is configured."""
        if self._decrypt is None:
            return payload
        try:
            obj = json.loads(payload)
            if isinstance(obj, dict) and obj.get("encryptData"):
                plain = self._decrypt(obj)
                if plain is not None:
                    return json.dumps(plain).encode()
        except Exception:  # malformed or undecryptable input is not a command result
            pass
        return b""
