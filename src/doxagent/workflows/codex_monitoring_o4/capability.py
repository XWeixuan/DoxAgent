"""Signed node-scoped capability for O4 operational MCP mutations."""

from __future__ import annotations

import base64
import hashlib
import json
import time
from collections.abc import Iterable
from uuid import uuid4

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from pydantic import Field

from doxagent.codex_runtime.errors import CapabilityDenied
from doxagent.codex_runtime.schema import CodexMonitoringO4Node

from .schema import O4Model

CONFIGURE_TOOLS = frozenset(
    {
        "monitoring.list_sources",
        "monitoring.get_source",
        "monitoring.get_ticker_config",
        "monitoring.update_ticker_config",
        "monitoring.list_status",
        "monitoring.register_source",
        "monitoring.update_source",
        "monitoring.hard_delete_source",
        "monitoring.get_default_profile",
        "monitoring.update_default_profile",
        "crawler_plane.list",
        "crawler_plane.get",
        "crawler_plane.register_source",
    }
)
DELIVER_TOOLS = frozenset(
    {
        "monitoring.list_sources",
        "monitoring.get_source",
        "monitoring.get_ticker_config",
        "monitoring.update_ticker_config",
        "monitoring.list_status",
        "monitoring.register_source",
        "monitoring.update_source",
        "crawler_plane.list",
        "crawler_plane.get",
        "crawler_plane.create_version",
        "crawler_plane.execute",
        "crawler_plane.live_probe",
        "crawler_plane.get_execution",
        "crawler_plane.get_cassette",
        "crawler_plane.add_regression",
        "crawler_plane.certify",
        "crawler_plane.promote",
        "crawler_plane.register_source",
    }
)
REPAIR_TOOLS = frozenset(
    {
        "monitoring.list_sources",
        "monitoring.get_source",
        "monitoring.get_ticker_config",
        "monitoring.update_ticker_config",
        "monitoring.list_status",
        "monitoring.recent_events",
        "monitoring.list_failures",
        "monitoring.update_source",
        "crawler_plane.list",
        "crawler_plane.get",
        "crawler_plane.create_version",
        "crawler_plane.execute",
        "crawler_plane.live_probe",
        "crawler_plane.get_execution",
        "crawler_plane.get_cassette",
        "crawler_plane.list_alerts",
        "crawler_plane.update_alert_policy",
        "crawler_plane.resolve_alert",
        "crawler_plane.list_retries",
        "crawler_plane.resolve_retry",
        "crawler_plane.reactivate_retry",
        "crawler_plane.add_regression",
        "crawler_plane.certify",
        "crawler_plane.promote",
    }
)
TOOLS_BY_NODE = {
    CodexMonitoringO4Node.CONFIGURE: CONFIGURE_TOOLS,
    CodexMonitoringO4Node.DELIVER: DELIVER_TOOLS,
    CodexMonitoringO4Node.REPAIR: REPAIR_TOOLS,
}
# The Codex client-side allow-list must remain stable while one persistent ticker
# thread moves between nodes.  This is only a discovery/filtering superset; the
# signed node capability and the server-side intersection remain the authority.
ALL_O4_TOOLS = frozenset().union(*TOOLS_BY_NODE.values())


class O4OperationClaims(O4Model):
    run_id: str
    request_id: str
    ticker: str
    node: CodexMonitoringO4Node
    enabled_tool_ids: list[str]
    expires_at: int
    nonce: str
    audience: str = "doxagent-o4-operations-mcp"
    issued_at: int = Field(default_factory=lambda: int(time.time()))


class O4OperationCapabilityCodec:
    def __init__(self, secret: str) -> None:
        if len(secret.encode("utf-8")) < 32:
            raise ValueError("O4 operation capability secret must contain at least 32 bytes")
        seed = hashlib.sha256(b"doxagent-o4-operations-v1\0" + secret.encode()).digest()
        self._private_key = Ed25519PrivateKey.from_private_bytes(seed)

    @property
    def public_key(self) -> str:
        raw = self._private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        return _b64encode(raw)

    def issue(
        self,
        *,
        run_id: str,
        request_id: str,
        ticker: str,
        node: CodexMonitoringO4Node,
        enabled_tool_ids: Iterable[str] | None = None,
        ttl_seconds: int = 7_500,
    ) -> str:
        maximum = TOOLS_BY_NODE[node]
        requested = maximum if enabled_tool_ids is None else set(enabled_tool_ids)
        if not set(requested).issubset(maximum):
            raise CapabilityDenied("requested O4 tools exceed node policy")
        claims = O4OperationClaims(
            run_id=run_id,
            request_id=request_id,
            ticker=ticker.upper(),
            node=node,
            enabled_tool_ids=sorted(requested),
            expires_at=int(time.time()) + ttl_seconds,
            nonce=uuid4().hex,
        )
        encoded = _b64encode(
            json.dumps(
                claims.model_dump(mode="json"),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        )
        signature = _b64encode(self._private_key.sign(encoded.encode("ascii")))
        return f"{encoded}.{signature}"

    @staticmethod
    def verify(token: str, *, public_key: str) -> O4OperationClaims:
        try:
            encoded, signature = token.split(".", maxsplit=1)
            Ed25519PublicKey.from_public_bytes(_b64decode(public_key)).verify(
                _b64decode(signature), encoded.encode("ascii")
            )
            claims = O4OperationClaims.model_validate_json(_b64decode(encoded))
        except InvalidSignature as exc:
            raise CapabilityDenied("invalid O4 operation capability signature") from exc
        except (TypeError, ValueError) as exc:
            raise CapabilityDenied("malformed O4 operation capability") from exc
        if claims.audience != "doxagent-o4-operations-mcp":
            raise CapabilityDenied("invalid O4 operation capability audience")
        if claims.expires_at < int(time.time()):
            raise CapabilityDenied("expired O4 operation capability")
        if not set(claims.enabled_tool_ids).issubset(TOOLS_BY_NODE[claims.node]):
            raise CapabilityDenied("O4 operation capability exceeds node policy")
        return claims


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    if _b64encode(decoded) != value:
        raise ValueError("non-canonical base64url value")
    return decoded


__all__ = [
    "ALL_O4_TOOLS",
    "CONFIGURE_TOOLS",
    "DELIVER_TOOLS",
    "O4OperationCapabilityCodec",
    "O4OperationClaims",
    "REPAIR_TOOLS",
    "TOOLS_BY_NODE",
]
