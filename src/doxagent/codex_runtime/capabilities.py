"""Short-lived, scope-bound capability tokens for worker operations."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass

from doxagent.codex_runtime.errors import CapabilityDenied


@dataclass(frozen=True)
class CapabilityClaims:
    run_id: str
    operations: tuple[str, ...]
    expires_at: int


class CapabilityTokenCodec:
    def __init__(self, secret: str) -> None:
        if len(secret.encode("utf-8")) < 32:
            raise ValueError("capability token secret must contain at least 32 bytes")
        self._secret = secret.encode("utf-8")

    def issue(self, *, run_id: str, operations: set[str], ttl_seconds: int = 300) -> str:
        payload = {
            "run_id": run_id,
            "operations": sorted(operations),
            "expires_at": int(time.time()) + ttl_seconds,
        }
        raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        encoded = base64.urlsafe_b64encode(raw).rstrip(b"=")
        signature = hmac.new(self._secret, encoded, hashlib.sha256).digest()
        return f"{encoded.decode()}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode()}"

    def verify(self, token: str, *, run_id: str, operation: str) -> CapabilityClaims:
        try:
            encoded, signature = token.split(".", maxsplit=1)
            encoded_bytes = encoded.encode("ascii")
            expected = hmac.new(self._secret, encoded_bytes, hashlib.sha256).digest()
            supplied = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
            if not hmac.compare_digest(expected, supplied):
                raise CapabilityDenied("invalid capability token signature")
            raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
            payload = json.loads(raw)
            claims = CapabilityClaims(
                run_id=str(payload["run_id"]),
                operations=tuple(str(value) for value in payload["operations"]),
                expires_at=int(payload["expires_at"]),
            )
        except CapabilityDenied:
            raise
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise CapabilityDenied("malformed capability token") from exc
        if claims.expires_at < int(time.time()):
            raise CapabilityDenied("expired capability token")
        if claims.run_id != run_id or operation not in claims.operations:
            raise CapabilityDenied("capability scope does not allow this operation")
        return claims
