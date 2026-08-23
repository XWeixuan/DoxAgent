"""Shared semantic-tool execution core used by Data MCP and direct adapters."""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from jsonschema import Draft202012Validator, ValidationError

from doxagent.codex_runtime.schema import CodexAgentRole, CodexD2AgentRole
from doxagent.data_runtime.contracts import (
    DataAvailability,
    DataDelivery,
    DataExecutionContext,
    DataMcpResult,
    DataProvenance,
    DataToolContract,
    DataToolContractRegistry,
)
from doxagent.models import AgentName, AgentPermissions, ResultStatus
from doxagent.observations.kernel import ObservationKernel
from doxagent.tools.registry import ToolRegistry
from doxagent.tools.schema import ToolError, ToolRequest, ToolResult

_AGENT_BY_ROLE = {
    CodexAgentRole.C1: AgentName.C1_FUNDAMENTAL_RESEARCH,
    CodexAgentRole.C2: AgentName.C2_MACRO_RESEARCH,
    CodexAgentRole.C3: AgentName.C3_INDUSTRY_RESEARCH,
    CodexAgentRole.C4: AgentName.SYSTEM,
    CodexAgentRole.O4: AgentName.O4_MARKET_TRACE,
    CodexAgentRole.C5: AgentName.C5_MARKET_IMPLIED_EXPECTATIONS,
    CodexD2AgentRole.O0: AgentName.O1_EXPECTATION_OWNER,
    CodexD2AgentRole.O1: AgentName.O1_EXPECTATION_OWNER,
}

_UNAVAILABLE_ERROR_CODES = {
    "api_key_missing",
    "credential_missing",
    "credentials_missing",
    "entitlement_required",
    "entitlement_or_permission_denied",
    "premium_endpoint_required",
    "gateway_not_configured",
    "ibkr_gateway_unavailable",
    "provider_not_configured",
    "provider_unavailable",
    "subscription_required",
    "tool_unavailable",
}
_DEGRADED_ERROR_CODES = {"market_data_unavailable"}
_SECRET_MESSAGE = re.compile(
    r"(?i)(api[_-]?key|apikey|authorization|bearer|password|token)=([^&\s]+)"
)


class DataExecutionCore:
    def __init__(
        self,
        *,
        tools: ToolRegistry,
        contracts: DataToolContractRegistry,
        context: DataExecutionContext,
        observations: ObservationKernel,
    ) -> None:
        self._tools = tools
        self._contracts = contracts
        self._context = context
        self._observations = observations

    def execute(self, canonical_tool_id: str, arguments: dict[str, Any]) -> DataMcpResult:
        contract = self._contracts.require(canonical_tool_id)
        if canonical_tool_id not in self._context.enabled_tool_ids:
            return self._denied(contract, "tool_not_allowed", "tool is outside attempt capability")
        if not contract.read_only:
            return self._denied(contract, "tool_not_read_only", "Data MCP exposes read-only tools")
        if contract.availability is DataAvailability.UNAVAILABLE:
            return self._denied(
                contract,
                "tool_unavailable",
                contract.availability_reason or "tool is unavailable",
                availability=DataAvailability.UNAVAILABLE,
            )
        try:
            Draft202012Validator(contract.input_schema).validate(arguments)
        except ValidationError as exc:
            return self._denied(
                contract,
                "invalid_tool_input",
                f"input schema validation failed at {list(exc.absolute_path)}: {exc.message}",
            )
        descriptor = self._tools.describe(canonical_tool_id)
        if descriptor is None:
            return self._denied(contract, "tool_not_registered", "tool is not registered")
        provider_input = dict(arguments)
        if "ticker" in descriptor.input_fields:
            provider_input["ticker"] = self._context.ticker
        if "symbol" in descriptor.input_fields:
            provider_input["symbol"] = self._context.ticker
        request = ToolRequest(
            tool_name=canonical_tool_id,
            ticker=self._context.ticker,
            agent_name=_AGENT_BY_ROLE[self._context.agent_role],
            input=provider_input,
            metadata={
                "workflow_version": self._context.workflow_version,
                "run_id": self._context.run_id,
                "node_id": self._context.node_id.value,
                "node_attempt_id": self._context.node_attempt_id,
                "cutoff_at": self._context.cutoff_at.isoformat(),
            },
        )
        started = time.perf_counter()
        try:
            result = self._tools.call(
                request,
                AgentPermissions(allowed_tools=[canonical_tool_id]),
            )
        except Exception as exc:
            result = ToolResult(
                tool_name=canonical_tool_id,
                status=ResultStatus.FAILED,
                error=ToolError(
                    code="data_tool_exception",
                    message=_safe_message(str(exc)) or type(exc).__name__,
                    retryable=False,
                ),
            )
        latency_ms = round((time.perf_counter() - started) * 1_000)
        availability = _availability(contract, result)
        tool_call_id = f"call_{uuid4().hex}"
        delivery = self._observations.ingest_tool_result(
            tool_call_id=tool_call_id,
            contract=contract,
            input_payload=provider_input,
            result=result,
            availability=availability,
            latency_ms=latency_ms,
        )
        output = result.output
        return DataMcpResult(
            canonical_tool_id=canonical_tool_id,
            tool_call_id=tool_call_id,
            node_attempt_id=self._context.node_attempt_id,
            execution_status=result.status,
            availability=availability,
            summary=_safe_message(result.output_summary or _summary(result)),
            delivery=delivery,
            provenance=DataProvenance(
                provider=contract.source_name,
                retrieved_at=datetime.now(UTC),
                published_at=_string_or_none(output.get("published_at")),
                as_of=_string_or_none(output.get("as_of") or output.get("period")),
                method_version=f"{canonical_tool_id}/{contract.contract_version}",
            ),
            warnings=_warnings(result),
            error=_error(result.error),
        )

    def _denied(
        self,
        contract: DataToolContract,
        code: str,
        message: str,
        *,
        availability: DataAvailability = DataAvailability.AVAILABLE,
    ) -> DataMcpResult:
        return DataMcpResult(
            canonical_tool_id=contract.canonical_tool_id,
            tool_call_id=f"call_{uuid4().hex}",
            node_attempt_id=self._context.node_attempt_id,
            execution_status=ResultStatus.FAILED,
            availability=availability,
            summary=_safe_message(message),
            delivery=DataDelivery(mode="inline", observations=[], total_blocks=0, inline_chars=0),
            provenance=DataProvenance(
                provider=contract.source_name,
                retrieved_at=datetime.now(UTC),
                method_version=f"{contract.canonical_tool_id}/{contract.contract_version}",
            ),
            error={"code": code, "message": _safe_message(message), "retryable": False},
        )


def _availability(contract: DataToolContract, result: ToolResult) -> DataAvailability:
    if contract.availability is DataAvailability.UNAVAILABLE:
        return DataAvailability.UNAVAILABLE
    if result.error and result.error.code.lower() in _UNAVAILABLE_ERROR_CODES:
        return DataAvailability.UNAVAILABLE
    if result.error and result.error.code.lower() in _DEGRADED_ERROR_CODES:
        return DataAvailability.DEGRADED
    if result.status is ResultStatus.PARTIAL:
        return DataAvailability.DEGRADED
    return contract.availability


def _summary(result: ToolResult) -> str:
    if result.error:
        return _safe_message(result.error.message)
    if result.status is ResultStatus.EMPTY:
        return "Provider returned no governed records for this request."
    if result.status is ResultStatus.NOT_APPLICABLE:
        return "The semantic tool is not applicable to this request."
    return f"{result.tool_name} returned {result.status.value}."


def _warnings(result: ToolResult) -> list[str]:
    value = result.output.get("warnings")
    if isinstance(value, list):
        return [_safe_message(str(item)) for item in value[:20]]
    return []


def _error(error: ToolError | None) -> dict[str, Any] | None:
    if error is None:
        return None
    return {
        "code": error.code,
        "message": _safe_message(error.message),
        "retryable": error.retryable,
    }


def _safe_message(value: str) -> str:
    return _SECRET_MESSAGE.sub(lambda match: f"{match.group(1)}=[REDACTED]", value)[:2_000]


def _string_or_none(value: Any) -> str | None:
    return str(value)[:500] if value not in (None, "") else None
