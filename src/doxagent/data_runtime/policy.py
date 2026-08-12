"""Attempt-scoped Data MCP capability and static role/node policy."""

from __future__ import annotations

import base64
import hashlib
import json
import time
from collections.abc import Iterable
from datetime import datetime
from uuid import uuid4

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from pydantic import Field

from doxagent.agents.config import default_agent_registry
from doxagent.codex_runtime.errors import CapabilityDenied
from doxagent.codex_runtime.schema import (
    CODEX_D1_WORKFLOW_VERSION,
    CodexAgentRole,
    CodexD1Node,
)
from doxagent.data_runtime.contracts import DATA_MCP_EXCLUDED_TOOL_IDS, DataRuntimeModel
from doxagent.models import AgentName


class DataCapabilityClaims(DataRuntimeModel):
    workflow_version: str = CODEX_D1_WORKFLOW_VERSION
    run_id: str
    node_id: CodexD1Node
    node_attempt_id: str
    agent_role: CodexAgentRole
    ticker: str
    cutoff_at: datetime
    enabled_tool_ids: list[str]
    read_only: bool = True
    expires_at: int
    nonce: str
    pilot_case_id: str | None = None
    audience: str = "doxagent-data-mcp"
    issued_at: int = Field(default_factory=lambda: int(time.time()))


_ROLE_BY_NODE = {
    CodexD1Node.C1: CodexAgentRole.C1,
    CodexD1Node.C2: CodexAgentRole.C2,
    CodexD1Node.C3: CodexAgentRole.C3,
    CodexD1Node.C4_PRE_SCAN: CodexAgentRole.C4,
    CodexD1Node.C4_ENRICHMENT: CodexAgentRole.C4,
    CodexD1Node.C4_FINALIZATION: CodexAgentRole.C4,
    CodexD1Node.O4_B: CodexAgentRole.O4,
    CodexD1Node.O4_A: CodexAgentRole.O4,
}

_LEGACY_AGENT_BY_ROLE = {
    CodexAgentRole.C1: AgentName.C1_FUNDAMENTAL_RESEARCH,
    CodexAgentRole.C2: AgentName.C2_MACRO_RESEARCH,
    CodexAgentRole.C3: AgentName.C3_INDUSTRY_RESEARCH,
    CodexAgentRole.O4: AgentName.O4_MARKET_TRACE,
}

_C4_TOOLS = {
    "congress.legislative_actions",
    "federal_register.documents",
    "finnhub.company_news_events",
    "ir.official_feed_discovery",
    "ir.official_updates",
    "openfda.approval_milestones",
    "openfda.safety_actions",
    "regulations.rulemaking_records",
    "sam.contract_opportunities",
    "sec.filing_content",
    "sec.issuer_filings",
    "sec.management_disclosures",
    "sec.material_contracts_projects",
    "tavily.extract",
    "usaspending.award_detail",
    "usaspending.award_search",
}


class DataToolPolicyRegistry:
    """Server-side maximum allowlist keyed by workflow node and role."""

    def __init__(self) -> None:
        agents = default_agent_registry()
        self._by_role: dict[CodexAgentRole, frozenset[str]] = {
            role: frozenset(agents.get(agent_name).runtime.allowed_tools).difference(
                DATA_MCP_EXCLUDED_TOOL_IDS
            )
            for role, agent_name in _LEGACY_AGENT_BY_ROLE.items()
        }
        self._by_role[CodexAgentRole.C4] = frozenset(_C4_TOOLS)

    def allowed_tools(self, node: CodexD1Node, role: CodexAgentRole) -> frozenset[str]:
        expected = _ROLE_BY_NODE.get(node)
        if expected is None or expected is not role:
            return frozenset()
        return self._by_role.get(role, frozenset())

    def effective_tools(self, claims: DataCapabilityClaims) -> frozenset[str]:
        maximum = self.allowed_tools(claims.node_id, claims.agent_role)
        return maximum.intersection(claims.enabled_tool_ids)


class DataCapabilityCodec:
    """Ed25519 attempt capability; the MCP process only receives the public key."""

    def __init__(self, secret: str) -> None:
        if len(secret.encode("utf-8")) < 32:
            raise ValueError("data capability secret must contain at least 32 bytes")
        seed = hashlib.sha256(b"doxagent-data-mcp-v1\0" + secret.encode("utf-8")).digest()
        self._private_key = Ed25519PrivateKey.from_private_bytes(seed)

    @property
    def public_key(self) -> str:
        raw = self._private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        return _b64encode(raw)

    def issue(
        self,
        *,
        run_id: str,
        node_id: CodexD1Node,
        node_attempt_id: str,
        agent_role: CodexAgentRole,
        ticker: str,
        cutoff_at: datetime,
        enabled_tool_ids: Iterable[str],
        ttl_seconds: int = 7_200,
        pilot_case_id: str | None = None,
    ) -> str:
        claims = DataCapabilityClaims(
            run_id=run_id,
            node_id=node_id,
            node_attempt_id=node_attempt_id,
            agent_role=agent_role,
            ticker=ticker.upper(),
            cutoff_at=cutoff_at,
            enabled_tool_ids=sorted(set(enabled_tool_ids)),
            expires_at=int(time.time()) + ttl_seconds,
            nonce=uuid4().hex,
            pilot_case_id=pilot_case_id,
        )
        raw = json.dumps(
            claims.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        encoded = _b64encode(raw)
        signature = _b64encode(self._private_key.sign(encoded.encode("ascii")))
        return f"{encoded}.{signature}"

    @staticmethod
    def verify(token: str, *, public_key: str) -> DataCapabilityClaims:
        try:
            encoded, signature = token.split(".", maxsplit=1)
            key = Ed25519PublicKey.from_public_bytes(_b64decode(public_key))
            key.verify(_b64decode(signature), encoded.encode("ascii"))
            claims = DataCapabilityClaims.model_validate_json(_b64decode(encoded))
        except InvalidSignature as exc:
            raise CapabilityDenied("invalid Data MCP capability signature") from exc
        except (ValueError, TypeError) as exc:
            raise CapabilityDenied("malformed Data MCP capability") from exc
        if claims.audience != "doxagent-data-mcp":
            raise CapabilityDenied("invalid Data MCP capability audience")
        if claims.expires_at < int(time.time()):
            raise CapabilityDenied("expired Data MCP capability")
        if not claims.read_only:
            raise CapabilityDenied("Data MCP capability must be read-only")
        expected_role = _ROLE_BY_NODE.get(claims.node_id)
        if expected_role is None or expected_role is not claims.agent_role:
            raise CapabilityDenied("Data MCP node and role scope do not match")
        return claims


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    if _b64encode(decoded) != value:
        raise ValueError("non-canonical base64url value")
    return decoded
