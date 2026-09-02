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
    CodexD2AgentRole,
    CodexD2Node,
    CodexD3AgentRole,
    CodexD3Node,
    CodexEventLibraryAgentRole,
    CodexEventLibraryNode,
    CodexPersistentRuntimeAgentRole,
    CodexPersistentRuntimeNode,
    CodexResearchAgentRole,
    CodexResearchNode,
    CodexWorkflowVersion,
    ResearchLane,
    lane_for_workflow,
)
from doxagent.data_runtime.contracts import DataRuntimeModel, is_data_mcp_excluded_tool
from doxagent.models import AgentName


class DataCapabilityClaims(DataRuntimeModel):
    workflow_version: CodexWorkflowVersion = CODEX_D1_WORKFLOW_VERSION
    research_lane: ResearchLane = ResearchLane.LEGACY_DOCUMENT1
    run_id: str
    node_id: CodexResearchNode
    node_attempt_id: str
    agent_role: CodexResearchAgentRole
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
    CodexD1Node.C5: CodexAgentRole.C5,
    CodexD1Node.O4: CodexAgentRole.O4,
    CodexD2Node.O0_CANDIDATE_C1: CodexD2AgentRole.O0,
    CodexD2Node.O0_CANDIDATE_C3: CodexD2AgentRole.O0,
    CodexD2Node.O0_CANDIDATE_C5: CodexD2AgentRole.O0,
    CodexD2Node.O0_CANDIDATE_NARRATIVE: CodexD2AgentRole.O0,
    CodexD2Node.O0_SYNTHESIS: CodexD2AgentRole.O0,
    CodexD2Node.O0_REVIEW_C1: CodexAgentRole.C1,
    CodexD2Node.O0_REVIEW_C3: CodexAgentRole.C3,
    CodexD2Node.O0_REVIEW_C5: CodexAgentRole.C5,
    CodexD2Node.O0_FINALIZATION: CodexD2AgentRole.O0,
    CodexD2Node.O1_STATE: CodexD2AgentRole.O1,
    CodexD2Node.O1_REALIZATION: CodexD2AgentRole.O1,
    CodexD2Node.O1_GAPS: CodexD2AgentRole.O1,
    CodexD2Node.O1_FINALIZATION: CodexD2AgentRole.O1,
    CodexEventLibraryNode.O2_MAINTAIN: CodexEventLibraryAgentRole.O2,
    CodexD3Node.O3_TRIGGER_CALIBRATION: CodexD3AgentRole.O3,
    CodexD3Node.O3_POLICY_COMPILE: CodexD3AgentRole.O3,
    CodexD3Node.O3_FINAL_REVIEW: CodexD3AgentRole.O3,
    CodexD3Node.O3_MAINTAIN: CodexD3AgentRole.O3,
    CodexPersistentRuntimeNode.W3: CodexPersistentRuntimeAgentRole.W3,
}

_LEGACY_AGENT_BY_ROLE = {
    CodexAgentRole.C1: AgentName.C1_FUNDAMENTAL_RESEARCH,
    CodexAgentRole.C2: AgentName.C2_MACRO_RESEARCH,
    CodexAgentRole.C3: AgentName.C3_INDUSTRY_RESEARCH,
    CodexAgentRole.O4: AgentName.O4_MARKET_TRACE,
    CodexAgentRole.C5: AgentName.C5_MARKET_IMPLIED_EXPECTATIONS,
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

# Keep provider clients available to direct callers and other nodes, but do not
# advertise tools to O4-A when the currently configured provider/account tier is
# known to reject them and there is no usable fallback for that tool contract.
_MARKET_IMPLIED_TOOL_EXCLUSIONS = frozenset(
    {
        "alpha.historical_options",
        "benzinga.analyst_events",
        "benzinga.market_signals",
        "fmp.valuation_snapshot",
        "ibkr.fed_funds_curve",
        "ibkr.historical_ticks",
        "ibkr.option_surface",
        "twelvedata.sell_side_estimates",
    }
)

_NODE_TOOL_EXCLUSIONS: dict[CodexResearchNode, frozenset[str]] = {
    CodexD1Node.O4_A: _MARKET_IMPLIED_TOOL_EXCLUSIONS,
    CodexD1Node.C5: _MARKET_IMPLIED_TOOL_EXCLUSIONS,
    CodexD2Node.O0_CANDIDATE_C5: _MARKET_IMPLIED_TOOL_EXCLUSIONS,
}


class DataToolPolicyRegistry:
    """Server-side maximum allowlist keyed by workflow node and role."""

    def __init__(self) -> None:
        agents = default_agent_registry()
        self._by_role: dict[CodexResearchAgentRole, frozenset[str]] = {
            role: frozenset(
                tool_id
                for tool_id in agents.get(agent_name).runtime.allowed_tools
                if not is_data_mcp_excluded_tool(tool_id)
            )
            for role, agent_name in _LEGACY_AGENT_BY_ROLE.items()
        }
        self._by_role[CodexAgentRole.C4] = frozenset(
            tool_id for tool_id in _C4_TOOLS if not is_data_mcp_excluded_tool(tool_id)
        )
        self._by_role[CodexD2AgentRole.O0] = frozenset()
        self._by_role[CodexD2AgentRole.O1] = frozenset().union(
            self._by_role[CodexAgentRole.C1],
            self._by_role[CodexAgentRole.C3],
            self._by_role[CodexAgentRole.C4],
            self._by_role[CodexAgentRole.C5],
        )
        self._by_role[CodexEventLibraryAgentRole.O2] = frozenset()
        self._by_role[CodexD3AgentRole.O3] = self._by_role[CodexD2AgentRole.O1]
        # W3 uses live Web Search under its own skill contract. It has no Data MCP budget.
        self._by_role[CodexPersistentRuntimeAgentRole.W3] = frozenset()
        legacy_o4_tools = self._by_role[CodexAgentRole.C5]
        self._by_node: dict[CodexResearchNode, frozenset[str]] = {
            CodexD1Node.O4_A: legacy_o4_tools,
            CodexD1Node.O4_B: legacy_o4_tools,
            # Candidate discovery runs under the shared O0 role, but each
            # branch researches one pinned Document1 domain. Give it the same
            # read-only Data MCP ceiling as that source domain instead of the
            # empty O0 synthesis/finalization ceiling.
            CodexD2Node.O0_CANDIDATE_C1: self._by_role[CodexAgentRole.C1],
            CodexD2Node.O0_CANDIDATE_C3: self._by_role[CodexAgentRole.C3],
            CodexD2Node.O0_CANDIDATE_C5: self._by_role[CodexAgentRole.C5],
            # Compile consumes the frozen Stage-A trigger surface. It does not
            # receive a routine Data MCP research budget.
            CodexD3Node.O3_POLICY_COMPILE: frozenset(),
        }

    def allowed_tools(
        self, node: CodexResearchNode, role: CodexResearchAgentRole
    ) -> frozenset[str]:
        expected = _ROLE_BY_NODE.get(node)
        if expected is None or expected is not role:
            return frozenset()
        maximum = self._by_node.get(node, self._by_role.get(role, frozenset()))
        return maximum.difference(_NODE_TOOL_EXCLUSIONS.get(node, frozenset()))

    def allowed_tools_for_ticker(
        self,
        node: CodexResearchNode,
        role: CodexResearchAgentRole,
        ticker: str,
    ) -> frozenset[str]:
        """Return the maximum allowlist after deterministic market scoping."""
        allowed = self.allowed_tools(node, role)
        if not ticker.upper().endswith(".HK"):
            allowed = allowed.difference({"yfinance.hk_basic_snapshot"})
        return allowed

    def effective_tools(self, claims: DataCapabilityClaims) -> frozenset[str]:
        maximum = self.allowed_tools_for_ticker(
            claims.node_id,
            claims.agent_role,
            claims.ticker,
        )
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
        node_id: CodexResearchNode,
        node_attempt_id: str,
        agent_role: CodexResearchAgentRole,
        ticker: str,
        cutoff_at: datetime,
        enabled_tool_ids: Iterable[str],
        ttl_seconds: int = 7_200,
        pilot_case_id: str | None = None,
        workflow_version: CodexWorkflowVersion = CODEX_D1_WORKFLOW_VERSION,
        research_lane: ResearchLane = ResearchLane.LEGACY_DOCUMENT1,
    ) -> str:
        claims = DataCapabilityClaims(
            workflow_version=workflow_version,
            research_lane=research_lane,
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
        if lane_for_workflow(claims.workflow_version) is not claims.research_lane:
            raise CapabilityDenied("Data MCP workflow and research lane do not match")
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
