"""ReAct harness for autonomous, audited tool use inside one agent task."""

from __future__ import annotations

import asyncio
import json
import re
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

from pydantic import BaseModel, ValidationError

from doxagent.agents.config import AgentDefinition
from doxagent.agents.runtime.tools import ToolRegistryFunctionAdapter, tool_result_to_summary
from doxagent.gateway import (
    GatewayError,
    MessageRole,
    ModelGateway,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ProviderName,
    ResponseFormat,
)
from doxagent.models import (
    AgentError,
    AgentResult,
    AgentTask,
    DelegatedRetrievalResult,
    DocumentType,
    DoxAtlasAuditResult,
    EvidenceRef,
    EvidenceSourceType,
    ExpectationConstructionResult,
    ExpectationFieldReviewResult,
    PatchOperation,
    ResearchSection,
    ResultStatus,
    ValidationStatus,
    new_id,
)
from doxagent.prompts.schema import AssembledPrompt
from doxagent.tools import ToolError, ToolRegistry, ToolRequest, ToolResult

JsonDict = dict[str, Any]
DelegationHandler = Callable[[JsonDict], Awaitable[AgentResult]]

MAX_TOOL_CALLS_PER_NAME = 3
SIMILARITY_WARNING_THRESHOLD = 0.72
MICROCOMPACT_MARKER = "[old observation compacted]"
_FINAL_PAYLOAD_SCHEMAS: dict[str, type[BaseModel]] = {
    "DelegatedRetrievalResult": DelegatedRetrievalResult,
    "DoxAtlasAuditResult": DoxAtlasAuditResult,
    "ExpectationConstructionResult": ExpectationConstructionResult,
    "ExpectationFieldReviewResult": ExpectationFieldReviewResult,
    "ResearchSection": ResearchSection,
}


@dataclass(frozen=True)
class ReActHarnessConfig:
    max_steps: int = 5
    max_tool_calls_per_name: int = MAX_TOOL_CALLS_PER_NAME
    recent_step_window: int = 2
    compaction_token_threshold: int = 12_000
    max_consecutive_compaction_failures: int = 3


@dataclass
class Scratchpad:
    task: AgentTask
    tool_counts: Counter[str] = field(default_factory=Counter)
    query_history: list[tuple[str, str]] = field(default_factory=list)
    plan: list[str] = field(default_factory=list)
    task_ledger: list[JsonDict] = field(default_factory=list)
    entries: list[JsonDict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    compacted_summaries: list[str] = field(default_factory=list)
    compaction_failures: int = 0

    def record_action(self, step: int, action: JsonDict) -> None:
        self.plan.extend(_strings(action.get("plan_update")))
        self.task_ledger.extend(_dicts(action.get("task_ledger_updates")))
        self.entries.append(
            {
                "kind": "action",
                "step": step,
                "is_complete": bool(action.get("is_complete", False)),
                "completion_reason": str(action.get("completion_reason") or ""),
                "reasoning_summary": str(action.get("reasoning_summary") or ""),
                "tool_calls": _public_tool_calls(action.get("tool_calls")),
                "delegations": _public_delegations(action.get("delegations")),
            }
        )

    def can_call_tool(self, tool_name: str, limit: int) -> bool:
        return self.tool_counts[tool_name] < limit

    def record_tool_attempt(self, tool_name: str, input_payload: JsonDict) -> list[str]:
        self.tool_counts[tool_name] += 1
        warnings = self._similar_query_warnings(tool_name, input_payload)
        self.warnings.extend(warnings)
        self.query_history.append((tool_name, _query_text(input_payload)))
        return warnings

    def record_tool_result(
        self,
        *,
        step: int,
        result: ToolResult,
        input_payload: JsonDict,
        warnings: list[str],
    ) -> None:
        self.entries.append(
            {
                "kind": "tool_result",
                "step": step,
                "tool_name": result.tool_name,
                "status": result.status.value,
                "input": input_payload,
                "output_summary": result.output_summary,
                "error": result.error.model_dump(mode="json") if result.error else None,
                "warnings": warnings,
                "evidence_count": len(result.evidence_refs),
                "output": result.output,
            }
        )

    def record_delegation(
        self,
        *,
        step: int,
        request: JsonDict,
        result: AgentResult,
    ) -> None:
        self.entries.append(
            {
                "kind": "delegation_result",
                "step": step,
                "target_agent": request.get("target_agent"),
                "status": result.status.value,
                "payload": result.payload,
                "error": result.error.model_dump(mode="json") if result.error else None,
                "evidence_count": len(result.evidence_refs),
            }
        )

    def microcompact(self, recent_step_window: int) -> int:
        latest_step = max(
            (int(entry.get("step") or 0) for entry in self.entries),
            default=0,
        )
        min_step_to_keep = max(0, latest_step - recent_step_window + 1)
        cleared = 0
        for entry in self.entries:
            if int(entry.get("step") or 0) >= min_step_to_keep:
                continue
            if entry.get("kind") not in {"tool_result", "delegation_result"}:
                continue
            if entry.get("output") == MICROCOMPACT_MARKER or entry.get("payload") == {
                "compacted": True
            }:
                continue
            if "output" in entry:
                entry["output"] = MICROCOMPACT_MARKER
            if "payload" in entry:
                entry["payload"] = {"compacted": True}
            cleared += 1
        if cleared:
            self.entries.append({"kind": "microcompact", "cleared": cleared})
        return cleared

    def append_compaction_summary(self, summary: str) -> None:
        self.compacted_summaries.append(summary)
        self.entries = [
            entry
            for entry in self.entries
            if entry.get("kind") not in {"tool_result", "delegation_result"}
        ]
        self.entries.append({"kind": "full_compaction", "summary": summary})
        self.compaction_failures = 0

    def record_compaction_failure(self, message: str) -> None:
        self.compaction_failures += 1
        self.entries.append(
            {
                "kind": "compaction_failure",
                "message": message,
                "consecutive_failures": self.compaction_failures,
            }
        )

    def record_model_format_error(self, *, step: int, error: GatewayError) -> None:
        warning = (
            "Model returned non-JSON text for a JSON ReAct action; retrying with "
            "the same task context."
        )
        self.warnings.append(warning)
        self.entries.append(
            {
                "kind": "model_format_error",
                "step": step,
                "status": "warning",
                "error": error.model_dump(mode="json"),
            }
        )

    def record_no_progress(self, *, step: int) -> None:
        warning = (
            "Model returned no final payload, tool calls, or delegations; retrying with "
            "the same task context."
        )
        self.warnings.append(warning)
        self.entries.append(
            {
                "kind": "react_no_progress",
                "step": step,
                "status": "warning",
            }
        )

    def recent_entries(self, recent_step_window: int) -> list[JsonDict]:
        latest_step = max(
            (int(entry.get("step") or 0) for entry in self.entries),
            default=0,
        )
        min_step_to_keep = max(0, latest_step - recent_step_window + 1)
        return [
            entry
            for entry in self.entries
            if int(entry.get("step") or latest_step) >= min_step_to_keep
        ]

    def audit(self) -> JsonDict:
        return {
            "max_tool_calls_per_name": MAX_TOOL_CALLS_PER_NAME,
            "tool_counts": dict(self.tool_counts),
            "plan": list(self.plan),
            "task_ledger": list(self.task_ledger),
            "warnings": list(self.warnings),
            "compacted_summaries": list(self.compacted_summaries),
            "entries": list(self.entries),
        }

    def _similar_query_warnings(self, tool_name: str, input_payload: JsonDict) -> list[str]:
        query_text = _query_text(input_payload)
        if not query_text:
            return []
        warnings: list[str] = []
        for previous_tool_name, previous_query in self.query_history:
            if previous_tool_name != tool_name:
                continue
            similarity = _jaccard_similarity(query_text, previous_query)
            if similarity >= SIMILARITY_WARNING_THRESHOLD:
                warnings.append(
                    f"Similar query detected for {tool_name}; similarity={similarity:.2f}."
                )
        return warnings


class ReActAgentHarness:
    def __init__(
        self,
        *,
        model_gateway: ModelGateway,
        tool_registry: ToolRegistry | None,
        provider: ProviderName,
        model: str,
        tool_mode: str,
        config: ReActHarnessConfig | None = None,
    ) -> None:
        self.model_gateway = model_gateway
        self.tool_registry = tool_registry
        self.provider = provider
        self.model = model
        self.tool_mode = tool_mode
        self.config = config or ReActHarnessConfig()

    async def run(
        self,
        *,
        task: AgentTask,
        definition: AgentDefinition,
        assembled_prompt: AssembledPrompt,
        context_snapshot: Any | None,
        metadata: dict[str, str],
        delegate: DelegationHandler,
    ) -> AgentResult:
        scratchpad = Scratchpad(task)
        tool_results: list[ToolResult] = []
        delegation_results: list[AgentResult] = []
        model_audits: list[JsonDict] = []

        for step in range(1, self.config.max_steps + 1):
            scratchpad.microcompact(self.config.recent_step_window)
            await self._compact_if_needed(task, assembled_prompt, scratchpad, metadata)
            response = await self._complete_step(
                task=task,
                definition=definition,
                assembled_prompt=assembled_prompt,
                context_snapshot=context_snapshot,
                scratchpad=scratchpad,
                metadata={**metadata, "react_step": str(step)},
            )
            model_audits.append(response.audit.model_dump(mode="json"))
            if response.error is not None:
                can_retry_json = (
                    _recoverable_json_response_error(response.error)
                    and step < self.config.max_steps
                )
                if can_retry_json:
                    scratchpad.record_model_format_error(step=step, error=response.error)
                    continue
                return _failed(
                    task,
                    "model_gateway_error",
                    response.error.message,
                    retryable=response.error.retryable,
                    tool_results=tool_results,
                    delegation_results=delegation_results,
                    scratchpad=scratchpad,
                    details={"gateway_error": response.error.model_dump(mode="json")},
                )

            action = _parse_action(response)
            if action is None:
                return _failed(
                    task,
                    "invalid_react_action",
                    "Model response could not be parsed as a ReAct JSON action.",
                    tool_results=tool_results,
                    delegation_results=delegation_results,
                    scratchpad=scratchpad,
                    details={"text": response.text},
                )
            scratchpad.record_action(step, action)

            tool_call_inputs = _tool_call_inputs(action.get("tool_calls"))
            delegation_inputs = _dicts(action.get("delegations"))
            final_payload = action.get("final_payload")
            is_complete = bool(action.get("is_complete", False))
            if is_complete and isinstance(final_payload, dict) and not tool_call_inputs:
                if not delegation_inputs:
                    return self._succeeded(
                        task=task,
                        definition=definition,
                        assembled_prompt=assembled_prompt,
                        context_snapshot=context_snapshot,
                        structured=final_payload,
                        text=response.text or json.dumps(final_payload, ensure_ascii=True),
                        model_audits=model_audits,
                        tool_results=tool_results,
                        delegation_results=delegation_results,
                        scratchpad=scratchpad,
                        completion_reason=str(action.get("completion_reason") or "complete"),
                    )

            if tool_call_inputs:
                step_results = await self._execute_tool_calls(
                    step=step,
                    task=task,
                    calls=tool_call_inputs,
                    scratchpad=scratchpad,
                )
                tool_results.extend(step_results)

            for delegation in delegation_inputs:
                result = await delegate(delegation)
                scratchpad.record_delegation(step=step, request=delegation, result=result)
                delegation_results.append(result)

            if not tool_call_inputs and not delegation_inputs:
                if isinstance(final_payload, dict):
                    return self._succeeded(
                        task=task,
                        definition=definition,
                        assembled_prompt=assembled_prompt,
                        context_snapshot=context_snapshot,
                        structured=final_payload,
                        text=response.text or json.dumps(final_payload, ensure_ascii=True),
                        model_audits=model_audits,
                        tool_results=tool_results,
                        delegation_results=delegation_results,
                        scratchpad=scratchpad,
                        completion_reason=str(action.get("completion_reason") or "final_payload"),
                    )
                if step < self.config.max_steps:
                    scratchpad.record_no_progress(step=step)
                    continue
                return _failed(
                    task,
                    "react_no_progress",
                    "ReAct step returned no final payload, tool calls, or delegations.",
                    tool_results=tool_results,
                    delegation_results=delegation_results,
                    scratchpad=scratchpad,
                )

        return _failed(
            task,
            "react_max_steps_exceeded",
            "ReAct loop reached max_steps without a complete final payload.",
            tool_results=tool_results,
            delegation_results=delegation_results,
            scratchpad=scratchpad,
        )

    async def _execute_tool_calls(
        self,
        *,
        step: int,
        task: AgentTask,
        calls: list[JsonDict],
        scratchpad: Scratchpad,
    ) -> list[ToolResult]:
        if self.tool_registry is None:
            return [
                self._blocked_tool_result(
                    task,
                    call,
                    code="tool_registry_disabled",
                    message="No tool registry is configured for this runner.",
                )
                for call in calls
            ]

        adapter = ToolRegistryFunctionAdapter(self.tool_registry)
        results: list[ToolResult | None] = [None] * len(calls)
        concurrent_work: list[tuple[int, JsonDict, list[str]]] = []

        for index, call in enumerate(calls):
            tool_name = str(call.get("tool_name") or call.get("name") or "")
            input_payload = _json_dict(call.get("input"))
            if not tool_name:
                results[index] = self._blocked_tool_result(
                    task,
                    call,
                    code="invalid_tool_call",
                    message="Tool call is missing tool_name.",
                )
                continue
            if not scratchpad.can_call_tool(tool_name, self.config.max_tool_calls_per_name):
                blocked_result = self._blocked_tool_result(
                    task,
                    call,
                    code="tool_call_limit_exceeded",
                    message=(
                        f"Tool {tool_name} exceeded the per-run limit of "
                        f"{self.config.max_tool_calls_per_name} calls."
                    ),
                )
                results[index] = blocked_result
                scratchpad.record_tool_result(
                    step=step,
                    result=blocked_result,
                    input_payload=input_payload,
                    warnings=[],
                )
                continue

            warnings = scratchpad.record_tool_attempt(tool_name, input_payload)
            descriptor = self.tool_registry.describe(tool_name)
            if descriptor is not None and descriptor.concurrent_safe:
                concurrent_work.append((index, call, warnings))
            else:
                result = await self._call_tool(adapter, tool_name, task, input_payload)
                results[index] = result
                scratchpad.record_tool_result(
                    step=step,
                    result=result,
                    input_payload=input_payload,
                    warnings=warnings,
                )

        if concurrent_work:
            gathered = await asyncio.gather(
                *[
                    self._call_tool(
                        adapter,
                        str(call.get("tool_name") or call.get("name") or ""),
                        task,
                        _json_dict(call.get("input")),
                    )
                    for _, call, _ in concurrent_work
                ]
            )
            for (index, call, warnings), result in zip(concurrent_work, gathered, strict=True):
                results[index] = result
                scratchpad.record_tool_result(
                    step=step,
                    result=result,
                    input_payload=_json_dict(call.get("input")),
                    warnings=warnings,
                )

        return [result for result in results if result is not None]

    async def _call_tool(
        self,
        adapter: ToolRegistryFunctionAdapter,
        tool_name: str,
        task: AgentTask,
        input_payload: JsonDict,
    ) -> ToolResult:
        return await asyncio.to_thread(
            adapter.call_tool,
            tool_name=tool_name,
            task=task,
            input_payload=input_payload,
        )

    def _blocked_tool_result(
        self,
        task: AgentTask,
        call: JsonDict,
        *,
        code: str,
        message: str,
    ) -> ToolResult:
        return ToolResult(
            tool_name=str(call.get("tool_name") or call.get("name") or "unknown_tool"),
            status=ResultStatus.FAILED,
            output_summary=f"{code}: {message}",
            error=ToolError(code=code, message=message, retryable=False),
            output={"ticker": task.ticker, "agent_name": task.agent_name.value},
        )

    async def _complete_step(
        self,
        *,
        task: AgentTask,
        definition: AgentDefinition,
        assembled_prompt: AssembledPrompt,
        context_snapshot: Any | None,
        scratchpad: Scratchpad,
        metadata: dict[str, str],
    ) -> ModelResponse:
        return await self.model_gateway.complete(
            ModelRequest(
                provider=self.provider,
                model=self.model,
                messages=[
                    ModelMessage(
                        role=MessageRole.SYSTEM,
                        content=_react_system_prompt(assembled_prompt.instructions),
                    ),
                    ModelMessage(
                        role=MessageRole.USER,
                        content=_react_user_prompt(
                            task=task,
                            definition=definition,
                            assembled_prompt=assembled_prompt,
                            context_snapshot=context_snapshot,
                            scratchpad=scratchpad,
                            tool_registry=self.tool_registry,
                            config=self.config,
                        ),
                    ),
                ],
                temperature=0.2,
                response_format=ResponseFormat.JSON,
                metadata=metadata,
            )
        )

    async def _compact_if_needed(
        self,
        task: AgentTask,
        assembled_prompt: AssembledPrompt,
        scratchpad: Scratchpad,
        metadata: dict[str, str],
    ) -> None:
        if not any(
            entry.get("kind") in {"tool_result", "delegation_result"}
            for entry in scratchpad.entries
        ):
            return
        if scratchpad.compaction_failures >= self.config.max_consecutive_compaction_failures:
            return
        context_payload = {
            "task": task.model_dump(mode="json"),
            "scratchpad": scratchpad.audit(),
        }
        if _estimated_tokens(context_payload) < self.config.compaction_token_threshold:
            return
        response = await self.model_gateway.complete(
            ModelRequest(
                provider=self.provider,
                model=self.model,
                messages=[
                    ModelMessage(
                        role=MessageRole.SYSTEM,
                        content=(
                            "Summarize tool and delegation observations into JSON. "
                            "Do not call tools. Do not include hidden chain-of-thought."
                        ),
                    ),
                    ModelMessage(
                        role=MessageRole.USER,
                        content=json.dumps(
                            {
                                "task": task.model_dump(mode="json"),
                                "tool_and_delegation_history": scratchpad.entries,
                                "required_summary_fields": [
                                    "data_retrieved",
                                    "errors",
                                    "numbers",
                                    "pending_data_needs",
                                    "current_work_state",
                                    "recommended_next_steps",
                                ],
                            },
                            ensure_ascii=True,
                            default=str,
                        ),
                    ),
                ],
                temperature=0,
                response_format=ResponseFormat.JSON,
                metadata={**metadata, "react_compaction": "true"},
            )
        )
        if response.error is not None:
            scratchpad.record_compaction_failure(response.error.message)
            return
        summary = response.structured if isinstance(response.structured, dict) else response.text
        if summary:
            scratchpad.append_compaction_summary(json.dumps(summary, ensure_ascii=True))
        else:
            scratchpad.record_compaction_failure("Compaction returned an empty summary.")

    def _succeeded(
        self,
        *,
        task: AgentTask,
        definition: AgentDefinition,
        assembled_prompt: AssembledPrompt,
        context_snapshot: Any | None,
        structured: JsonDict,
        text: str,
        model_audits: list[JsonDict],
        tool_results: list[ToolResult],
        delegation_results: list[AgentResult],
        scratchpad: Scratchpad,
        completion_reason: str,
    ) -> AgentResult:
        structured = _normalize_final_payload(
            structured,
            task=task,
            required_output_schema=task.required_output_schema,
            tool_results=tool_results,
            delegation_results=delegation_results,
        )
        schema_error = _final_payload_schema_error(structured, task.required_output_schema)
        if schema_error is not None:
            return _failed(
                task,
                "invalid_final_payload",
                schema_error,
                tool_results=tool_results,
                delegation_results=delegation_results,
                scratchpad=scratchpad,
                details={"required_output_schema": task.required_output_schema},
            )
        required_tool_names = _strings(task.input_context.get("required_tool_names"))
        failed_required = _failed_required_tools(required_tool_names, tool_results)
        if failed_required:
            warning = (
                "Required ReAct tool call was missing or failed; continuing with "
                f"unknowns/data gaps: {', '.join(failed_required)}."
            )
            scratchpad.warnings.append(warning)
            scratchpad.entries.append(
                {
                    "kind": "required_tool_gap",
                    "required_tool_names": required_tool_names,
                    "failed": failed_required,
                    "status": "warning",
                }
            )
        evidence_refs = _evidence_refs(tool_results, delegation_results)
        return AgentResult(
            task_id=task.task_id,
            agent_name=task.agent_name,
            status=ResultStatus.SUCCEEDED,
            payload={
                "runtime": "react",
                "structured": structured,
                "text": text,
                "completion_reason": completion_reason,
                "model_audits": model_audits,
                "react_audit": scratchpad.audit(),
                "skill_ids": task.skill_bundle.skill_ids if task.skill_bundle else [],
                "skill_versions": task.skill_bundle.skill_versions if task.skill_bundle else {},
                "prompt_block_ids": (
                    task.prompt_bundle.prompt_block_ids if task.prompt_bundle else []
                ),
                "internal_task_skill_ids": (
                    task.prompt_bundle.internal_task_skill_ids if task.prompt_bundle else []
                ),
                "external_skill_package_ids": (
                    task.prompt_bundle.external_skill_package_ids if task.prompt_bundle else []
                ),
                "prompt_versions": task.prompt_bundle.versions if task.prompt_bundle else {},
                "assembled_prompt_metadata": assembled_prompt.metadata,
                "tool_mode": self.tool_mode,
                "agent_definition": {
                    "agent_name": definition.agent_name.value,
                    "role": definition.role.value,
                    "output_schema": definition.runtime.output_schema,
                },
                "context_snapshot": _dump_context(context_snapshot),
            },
            evidence_refs=evidence_refs,
            tool_calls=[tool_result_to_summary(result) for result in tool_results],
        )


def _react_system_prompt(base_instructions: str) -> str:
    return "\n\n".join(
        [
            base_instructions or "Follow DoxAgent prompt resources.",
            "## ReAct Harness Rules",
            (
                "You are running inside DoxAgent's audited ReAct harness. "
                "Decide whether tools or delegation are needed before returning final output."
            ),
            "Do not write Blackboard state directly.",
            "Do not expose hidden chain-of-thought; use concise reasoning_summary only.",
            (
                "Return one JSON object matching the ReAct action protocol. "
                "Put plan_update, is_complete, tool_calls, delegations, and final_payload "
                "at the top level; do not wrap them under react_protocol."
            ),
        ]
    )


def _react_user_prompt(
    *,
    task: AgentTask,
    definition: AgentDefinition,
    assembled_prompt: AssembledPrompt,
    context_snapshot: Any | None,
    scratchpad: Scratchpad,
    tool_registry: ToolRegistry | None,
    config: ReActHarnessConfig,
) -> str:
    available_tools = (
        [
            descriptor.model_dump(mode="json")
            for descriptor in tool_registry.describe_allowed(task.permissions)
        ]
        if tool_registry is not None
        else []
    )
    return json.dumps(
        {
            "react_protocol": {
                "max_steps": config.max_steps,
                "max_tool_calls_per_name": config.max_tool_calls_per_name,
                "response_schema": {
                    "plan_update": ["short public plan updates"],
                    "task_ledger_updates": [{"item": "string", "status": "todo|done|blocked"}],
                    "reasoning_summary": "brief public rationale, not hidden chain-of-thought",
                    "is_complete": "boolean",
                    "completion_reason": "string",
                    "tool_calls": [
                        {"tool_name": "registered tool name", "input": {"key": "value"}}
                    ],
                    "delegations": [
                        {
                            "target_agent": "agent enum value",
                            "task_type": "optional task type",
                            "question": "delegated task",
                            "context_summary": "bounded context",
                            "required_output_schema": "optional schema",
                        }
                    ],
                    "final_payload": "AgentResult-compatible structured payload when complete",
                },
            },
            "task_spec": {
                "task_id": task.task_id,
                "ticker": task.ticker,
                "agent_name": task.agent_name.value,
                "task_type": task.task_type.value,
                "workflow_node": task.run_metadata.workflow_node,
                "required_output_schema": task.required_output_schema,
                "runtime_output_schema": definition.runtime.output_schema,
                "permissions": task.permissions.model_dump(mode="json"),
                "input_context": task.input_context,
                "required_tool_names": _strings(task.input_context.get("required_tool_names")),
                "tool_requirements": task.input_context.get("tool_requirements", []),
            },
            "assembled_task_prompt": assembled_prompt.user_prompt,
            "context_snapshot": _dump_context(context_snapshot),
            "available_tools": available_tools,
            "plan": scratchpad.plan,
            "task_ledger": scratchpad.task_ledger,
            "compacted_evidence_summary": scratchpad.compacted_summaries,
            "recent_trajectory": scratchpad.recent_entries(config.recent_step_window),
            "scratchpad_warnings": scratchpad.warnings[-5:],
            "rules": [
                "Call only tools listed in available_tools.",
                "If no tool is needed, return is_complete=true and final_payload.",
                (
                    "If more evidence is needed, return tool_calls or delegations "
                    "and no final_payload."
                ),
                (
                    "If a required tool cannot be satisfied, return a final_payload "
                    "with explicit unknowns."
                ),
            ],
        },
        ensure_ascii=True,
        default=str,
    )


def _parse_action(response: ModelResponse) -> JsonDict | None:
    payload: Any = response.structured
    if payload is None and response.text is not None:
        try:
            payload = json.loads(response.text)
        except json.JSONDecodeError:
            return None
    if not isinstance(payload, dict):
        return None
    payload = _unwrap_action_payload(payload)
    action_keys = {
        "plan_update",
        "task_ledger_updates",
        "is_complete",
        "completion_reason",
        "tool_calls",
        "delegations",
        "final_payload",
    }
    if not any(key in payload for key in action_keys):
        return {
            "is_complete": True,
            "completion_reason": "model returned direct structured payload",
            "final_payload": payload,
            "tool_calls": [],
            "delegations": [],
        }
    return cast(JsonDict, payload)


def _unwrap_action_payload(payload: JsonDict) -> JsonDict:
    action_keys = {
        "plan_update",
        "task_ledger_updates",
        "is_complete",
        "completion_reason",
        "tool_calls",
        "delegations",
        "final_payload",
    }
    if any(key in payload for key in action_keys):
        return payload
    for key in ("react_protocol", "react_action", "action"):
        nested = payload.get(key)
        if isinstance(nested, dict) and any(item in nested for item in action_keys):
            return cast(JsonDict, nested)
    return payload


def _failed(
    task: AgentTask,
    code: str,
    message: str,
    *,
    retryable: bool = False,
    tool_results: list[ToolResult] | None = None,
    delegation_results: list[AgentResult] | None = None,
    scratchpad: Scratchpad | None = None,
    details: JsonDict | None = None,
) -> AgentResult:
    tool_results = tool_results or []
    delegation_results = delegation_results or []
    evidence_refs = _evidence_refs(tool_results, delegation_results)
    return AgentResult(
        task_id=task.task_id,
        agent_name=task.agent_name,
        status=ResultStatus.FAILED,
        payload={"runtime": "react", "react_audit": scratchpad.audit() if scratchpad else {}},
        evidence_refs=evidence_refs,
        tool_calls=[tool_result_to_summary(result) for result in tool_results],
        error=AgentError(code=code, message=message, retryable=retryable, details=details or {}),
    )


def _evidence_refs(
    tool_results: list[ToolResult],
    delegation_results: list[AgentResult],
) -> list[EvidenceRef]:
    evidence_refs: list[EvidenceRef] = []
    for tool_result in tool_results:
        evidence_refs.extend(tool_result.evidence_refs)
    for delegation_result in delegation_results:
        evidence_refs.extend(delegation_result.evidence_refs)
    return evidence_refs


def _failed_required_tools(
    required_tool_names: list[str],
    tool_results: list[ToolResult],
) -> list[str]:
    if not required_tool_names:
        return []
    successful = {
        result.tool_name
        for result in tool_results
        if result.status is ResultStatus.SUCCEEDED and result.error is None
    }
    return [tool_name for tool_name in required_tool_names if tool_name not in successful]


def _final_payload_schema_error(payload: JsonDict, required_output_schema: str) -> str | None:
    if not payload:
        return "ReAct final_payload must be a non-empty JSON object."
    errors: list[str] = []
    schema_checked = False
    for schema_name in _schema_names(required_output_schema):
        model = _FINAL_PAYLOAD_SCHEMAS.get(schema_name)
        if model is None:
            continue
        schema_checked = True
        try:
            model.model_validate(payload)
            return None
        except ValidationError as exc:
            errors.append(f"{schema_name}: {exc}")
    if not schema_checked:
        return None
    return "ReAct final_payload failed schema validation: " + " | ".join(errors)


def _schema_names(required_output_schema: str) -> list[str]:
    return [item.strip() for item in required_output_schema.split("|") if item.strip()]


def _recoverable_json_response_error(error: GatewayError) -> bool:
    return error.code in {"invalid_json", "missing_json_text"}


def _normalize_final_payload(
    payload: JsonDict,
    *,
    task: AgentTask,
    required_output_schema: str,
    tool_results: list[ToolResult],
    delegation_results: list[AgentResult],
) -> JsonDict:
    if "ResearchSection" not in _schema_names(required_output_schema):
        if "ExpectationConstructionResult" in _schema_names(required_output_schema):
            return _normalize_expectation_construction_payload(
                payload,
                task=task,
                tool_results=tool_results,
                delegation_results=delegation_results,
            )
        return payload
    text = _research_section_text(payload)
    summary = str(payload.get("summary") or payload.get("section_summary") or "")
    if not summary:
        summary = text[:500] if text else f"{task.ticker} {task.agent_name.value} research."
    evidence_refs = payload.get("evidence_refs")
    if not isinstance(evidence_refs, list) or not evidence_refs:
        evidence_refs = [
            item.model_dump(mode="json")
            for item in _evidence_refs(tool_results, delegation_results)
        ]
    return {
        "text": text or summary,
        "summary": summary,
        "evidence_refs": evidence_refs,
        "author_agent": task.agent_name.value,
        "reviewer_agents": _strings(payload.get("reviewer_agents")),
    }


def _normalize_expectation_construction_payload(
    payload: JsonDict,
    *,
    task: AgentTask,
    tool_results: list[ToolResult],
    delegation_results: list[AgentResult],
) -> JsonDict:
    evidence_refs = payload.get("evidence_refs")
    if not isinstance(evidence_refs, list) or not evidence_refs:
        evidence_refs = [
            item.model_dump(mode="json")
            for item in _evidence_refs(tool_results, delegation_results)
        ]
    if not evidence_refs:
        evidence_refs = [_agent_output_evidence_ref(task)]

    proposed_patches = payload.get("proposed_patches")
    if not isinstance(proposed_patches, list):
        proposed_patches = payload.get("patches")
    if not isinstance(proposed_patches, list):
        proposed_patches = []
    normalized_patches = [
        _normalize_blackboard_patch_payload(item, task=task, fallback_evidence=evidence_refs)
        for item in proposed_patches
        if isinstance(item, dict)
    ]
    expectation_items = payload.get("expectations")
    if not normalized_patches and isinstance(expectation_items, list):
        normalized_patches = [
            _patch_from_expectation_payload(item, task=task, fallback_evidence=evidence_refs)
            for item in expectation_items
            if isinstance(item, dict)
        ]

    return {
        "proposed_patches": normalized_patches,
        "evidence_refs": evidence_refs,
        "delegations": _dicts(payload.get("delegations")),
        "unknowns": _strings(payload.get("unknowns")),
        "rationale": str(payload.get("rationale") or payload.get("summary") or "O1 construction."),
        "resolved_objection_ids": _strings(payload.get("resolved_objection_ids")),
        "accepted_objection_ids": _strings(payload.get("accepted_objection_ids")),
        "partially_accepted_objection_ids": _strings(
            payload.get("partially_accepted_objection_ids")
        ),
        "rejected_objection_ids": _strings(payload.get("rejected_objection_ids")),
    }


def _normalize_blackboard_patch_payload(
    payload: JsonDict,
    *,
    task: AgentTask,
    fallback_evidence: list[JsonDict],
) -> JsonDict:
    evidence_refs = payload.get("evidence_refs")
    if not isinstance(evidence_refs, list) or not evidence_refs:
        evidence_refs = fallback_evidence
    target = _normalize_blackboard_target_payload(
        _json_dict(payload.get("target")),
        task=task,
        after=_json_dict(payload.get("after")),
    )
    after = payload.get("after")
    if isinstance(after, dict) and target["document_type"] == DocumentType.EXPECTATION_UNIT.value:
        after = _normalize_expectation_document_payload(
            after,
            task=task,
            fallback_evidence=evidence_refs,
            fallback_expectation_id=target.get("expectation_id"),
        )
        target["expectation_id"] = after["expectation_id"]
    return {
        "patch_id": str(payload.get("patch_id") or new_id("patch")),
        "target": target,
        "operation": str(payload.get("operation") or PatchOperation.CREATE.value),
        "before": payload.get("before"),
        "after": after,
        "rationale": str(payload.get("rationale") or "O1 expectation construction."),
        "evidence_refs": evidence_refs,
        "author_agent": str(payload.get("author_agent") or task.agent_name.value),
        "validation_status": str(
            payload.get("validation_status") or ValidationStatus.PENDING.value
        ),
    }


def _patch_from_expectation_payload(
    expectation: JsonDict,
    *,
    task: AgentTask,
    fallback_evidence: list[JsonDict],
) -> JsonDict:
    expectation_id = str(expectation.get("expectation_id") or new_id("expectation"))
    after = _normalize_expectation_document_payload(
        expectation,
        task=task,
        fallback_evidence=fallback_evidence,
        fallback_expectation_id=expectation_id,
    )
    return {
        "patch_id": new_id("patch"),
        "target": {
            "document_type": DocumentType.EXPECTATION_UNIT.value,
            "ticker": task.ticker,
            "expectation_id": expectation_id,
            "field_path": "document",
        },
        "operation": PatchOperation.CREATE.value,
        "before": None,
        "after": after,
        "rationale": str(expectation.get("rationale") or "O1 expectation construction."),
        "evidence_refs": fallback_evidence,
        "author_agent": task.agent_name.value,
        "validation_status": ValidationStatus.PENDING.value,
    }


def _normalize_expectation_document_payload(
    payload: JsonDict,
    *,
    task: AgentTask,
    fallback_evidence: list[JsonDict],
    fallback_expectation_id: str | None,
) -> JsonDict:
    expectation_id = str(
        payload.get("expectation_id")
        or payload.get("id")
        or fallback_expectation_id
        or new_id("expectation")
    )
    name = str(
        payload.get("expectation_name")
        or payload.get("name")
        or payload.get("title")
        or expectation_id
    )
    description = str(
        payload.get("why_it_matters")
        or payload.get("description")
        or payload.get("thesis")
        or name
    )
    realized_summary = str(
        payload.get("realized_facts_summary")
        or payload.get("known_facts_summary")
        or payload.get("source")
        or "No realized facts were available from configured tools."
    )
    market_view = payload.get("market_view")
    if not isinstance(market_view, dict):
        market_view = {
            "text": description,
            "summary": name,
            "evidence_refs": fallback_evidence,
            "author_agent": task.agent_name.value,
            "reviewer_agents": [],
        }
    else:
        market_view = {
            "text": str(market_view.get("text") or market_view.get("description") or description),
            "summary": str(market_view.get("summary") or name),
            "evidence_refs": market_view.get("evidence_refs")
            if isinstance(market_view.get("evidence_refs"), list)
            else fallback_evidence,
            "author_agent": str(market_view.get("author_agent") or task.agent_name.value),
            "reviewer_agents": _strings(market_view.get("reviewer_agents")),
        }
    return {
        "document_id": str(payload.get("document_id") or new_id("doc")),
        "document_type": DocumentType.EXPECTATION_UNIT.value,
        "ticker": str(payload.get("ticker") or task.ticker),
        "created_at": str(payload.get("created_at") or datetime.now(UTC).isoformat()),
        "updated_at": payload.get("updated_at"),
        "expectation_id": expectation_id,
        "expectation_name": name,
        "direction": _normalize_expectation_direction(payload.get("direction") or description),
        "why_it_matters": description,
        "market_view": market_view,
        "realized_facts": payload.get("realized_facts")
        if isinstance(payload.get("realized_facts"), list)
        else [],
        "realized_facts_summary": realized_summary,
        "key_variables": payload.get("key_variables")
        if isinstance(payload.get("key_variables"), list)
        else [],
        "event_monitoring_direction": _normalize_event_monitoring_direction(payload),
    }


def _normalize_expectation_direction(value: Any) -> str:
    text = str(value or "").lower()
    if "bear" in text or "negative" in text or "downside" in text:
        return "bearish"
    if "bull" in text or "positive" in text or "upside" in text:
        return "bullish"
    if text in {"bullish", "bearish", "neutral"}:
        return text
    return "neutral"


def _normalize_event_monitoring_direction(payload: JsonDict) -> JsonDict:
    value = payload.get("event_monitoring_direction")
    if isinstance(value, dict):
        return {
            "known_event_notice": str(
                value.get("known_event_notice") or "Monitor for new confirmed events."
            ),
            "positive_events": _strings(value.get("positive_events")),
            "negative_events": _strings(value.get("negative_events")),
        }
    return {
        "known_event_notice": "Monitor for new confirmed events.",
        "positive_events": _strings(payload.get("positive_events")),
        "negative_events": _strings(payload.get("negative_events")),
    }


def _normalize_blackboard_target_payload(
    payload: JsonDict,
    *,
    task: AgentTask,
    after: JsonDict,
) -> JsonDict:
    expectation_id = payload.get("expectation_id") or after.get("expectation_id")
    return {
        "document_type": str(payload.get("document_type") or DocumentType.EXPECTATION_UNIT.value),
        "field_path": str(payload.get("field_path") or "document"),
        "ticker": str(payload.get("ticker") or task.ticker),
        "document_id": payload.get("document_id"),
        "expectation_id": str(expectation_id) if expectation_id else None,
    }


def _agent_output_evidence_ref(task: AgentTask) -> JsonDict:
    return {
        "evidence_id": new_id("evidence"),
        "source_type": EvidenceSourceType.AGENT_OUTPUT.value,
        "source_id": f"react:{task.task_id}",
        "title": f"{task.agent_name.value} ReAct output provenance",
        "summary": "Provider evidence was unavailable; retained model output provenance.",
        "retrieval_metadata": {
            "agent_name": task.agent_name.value,
            "task_id": task.task_id,
            "ticker": task.ticker,
            "evidence_gap": True,
        },
        "confidence": 0.35,
        "citation_scope": "expectation_unit",
    }


def _research_section_text(payload: JsonDict) -> str:
    preferred_keys = (
        "text",
        "report",
        "analysis",
        "narrative",
        "section_text",
        "fundamental_report",
        "macro_report",
        "industry_report",
        "market_trace_report",
        "market_narrative_report",
    )
    chunks: list[str] = []
    for key in preferred_keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            chunks.append(value.strip())
    for key in ("sections", "findings", "key_points", "unknowns", "risks", "data_gaps"):
        value = payload.get(key)
        rendered = _render_payload_fragment(value)
        if rendered:
            chunks.append(f"{key}:\n{rendered}")
    if chunks:
        return "\n\n".join(chunks)
    return _render_payload_fragment(payload)


def _render_payload_fragment(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        rendered_items = [_render_payload_fragment(item) for item in value]
        return "\n".join(f"- {item}" for item in rendered_items if item)
    if isinstance(value, dict):
        parts: list[str] = []
        for key, item in value.items():
            rendered = _render_payload_fragment(item)
            if rendered:
                parts.append(f"{key}: {rendered}")
        return "\n".join(parts)
    if value is None:
        return ""
    return str(value)


def _tool_call_inputs(value: Any) -> list[JsonDict]:
    return [
        item
        for item in _dicts(value)
        if str(item.get("tool_name") or item.get("name") or "").strip()
    ]


def _public_tool_calls(value: Any) -> list[JsonDict]:
    return [
        {
            "tool_name": str(item.get("tool_name") or item.get("name") or ""),
            "input": item.get("input", {}),
        }
        for item in _dicts(value)
    ]


def _public_delegations(value: Any) -> list[JsonDict]:
    return [
        {
            "target_agent": item.get("target_agent"),
            "task_type": item.get("task_type"),
            "question": item.get("question"),
        }
        for item in _dicts(value)
    ]


def _json_dict(value: Any) -> JsonDict:
    return value if isinstance(value, dict) else {}


def _dicts(value: Any) -> list[JsonDict]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _query_text(input_payload: JsonDict) -> str:
    for key in ("query", "url", "symbol", "series_id"):
        value = input_payload.get(key)
        if value is not None:
            return str(value)
    return json.dumps(input_payload, ensure_ascii=True, sort_keys=True, default=str)


def _jaccard_similarity(left: str, right: str) -> float:
    left_tokens = set(re.findall(r"[a-zA-Z0-9_]+", left.lower()))
    right_tokens = set(re.findall(r"[a-zA-Z0-9_]+", right.lower()))
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _estimated_tokens(value: Any) -> int:
    text = json.dumps(value, ensure_ascii=True, default=str)
    return max(1, len(text) // 4)


def _dump_context(context_snapshot: Any | None) -> JsonDict | None:
    if context_snapshot is None:
        return None
    if hasattr(context_snapshot, "model_dump"):
        return cast(JsonDict, context_snapshot.model_dump(mode="json"))
    if isinstance(context_snapshot, dict):
        return context_snapshot
    return {"value": str(context_snapshot)}


def tool_request_from_call(task: AgentTask, tool_name: str, input_payload: JsonDict) -> ToolRequest:
    return ToolRequest(
        tool_name=tool_name,
        ticker=task.ticker,
        agent_name=task.agent_name,
        input=input_payload,
    )


def gateway_error_to_agent_error(error: GatewayError) -> AgentError:
    return AgentError(
        code="model_gateway_error",
        message=error.message,
        retryable=error.retryable,
        details={"gateway_error": error.model_dump(mode="json")},
    )


__all__ = [
    "ReActAgentHarness",
    "ReActHarnessConfig",
    "Scratchpad",
    "gateway_error_to_agent_error",
    "tool_request_from_call",
]
