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
    AgentName,
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
from doxagent.prompts.assembler import (
    CHINESE_OUTPUT_RULES,
    agent_visible_context_snapshot,
    agent_visible_input_context,
)
from doxagent.prompts.schema import AssembledPrompt
from doxagent.skills import UnknownSkillError
from doxagent.skills.registry import SkillRegistry, default_skill_registry
from doxagent.skills.schema import SkillDefinition
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
    loaded_skills: dict[str, JsonDict] = field(default_factory=dict)
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
                "skill_calls": _public_skill_calls(action.get("skill_calls")),
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

    def record_skill_result(
        self,
        *,
        step: int,
        skill_id: str,
        status: str,
        reason: str,
        message: str,
        skill: SkillDefinition | None = None,
    ) -> None:
        entry: JsonDict = {
            "kind": "skill_result",
            "step": step,
            "skill_id": skill_id,
            "status": status,
            "reason": reason,
            "message": message,
        }
        if skill is not None and status == "loaded":
            self.loaded_skills[skill.skill_id] = _loaded_skill_payload(skill)
            entry["version"] = skill.version
            entry["source_project"] = skill.source_project
            entry["source_kind"] = skill.source_kind.value
        if status != "loaded":
            self.warnings.append(message)
        self.entries.append(entry)

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
            "loaded_skill_ids": sorted(self.loaded_skills),
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
        skill_registry: SkillRegistry | None = None,
        config: ReActHarnessConfig | None = None,
    ) -> None:
        self.model_gateway = model_gateway
        self.tool_registry = tool_registry
        self.skill_registry = skill_registry or default_skill_registry()
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
        try:
            _available_skill_definitions(task, definition, self.skill_registry)
        except UnknownSkillError as exc:
            return _failed(
                task,
                "invalid_skill_catalog",
                str(exc),
                scratchpad=scratchpad,
            )

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
            skill_call_inputs = _skill_call_inputs(action.get("skill_calls"))
            delegation_inputs = _dicts(action.get("delegations"))
            final_payload = action.get("final_payload")
            is_complete = bool(action.get("is_complete", False))
            if (
                is_complete
                and isinstance(final_payload, dict)
                and not tool_call_inputs
                and not skill_call_inputs
            ):
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

            if skill_call_inputs:
                self._load_skill_calls(
                    step=step,
                    task=task,
                    definition=definition,
                    calls=skill_call_inputs,
                    scratchpad=scratchpad,
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

            if not tool_call_inputs and not skill_call_inputs and not delegation_inputs:
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

    def _load_skill_calls(
        self,
        *,
        step: int,
        task: AgentTask,
        definition: AgentDefinition,
        calls: list[JsonDict],
        scratchpad: Scratchpad,
    ) -> None:
        available = {
            skill.skill_id: skill
            for skill in _available_skill_definitions(task, definition, self.skill_registry)
        }
        for call in calls:
            skill_id = str(call.get("skill_id") or call.get("name") or "").strip()
            reason = str(call.get("reason") or "")
            if not skill_id:
                scratchpad.record_skill_result(
                    step=step,
                    skill_id="",
                    status="failed",
                    reason=reason,
                    message="Skill call is missing skill_id.",
                )
                continue
            if skill_id in scratchpad.loaded_skills:
                scratchpad.record_skill_result(
                    step=step,
                    skill_id=skill_id,
                    status="duplicate",
                    reason=reason,
                    message=f"Skill {skill_id} was already loaded in this task.",
                )
                continue
            skill = available.get(skill_id)
            if skill is None:
                scratchpad.record_skill_result(
                    step=step,
                    skill_id=skill_id,
                    status="rejected",
                    reason=reason,
                    message=f"Skill {skill_id} is not exposed for this agent task.",
                )
                continue
            scratchpad.record_skill_result(
                step=step,
                skill_id=skill_id,
                status="loaded",
                reason=reason,
                message=f"Skill {skill_id} loaded.",
                skill=skill,
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
                            skill_registry=self.skill_registry,
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
                            "Do not call tools. Do not include hidden chain-of-thought. "
                            "Use Simplified Chinese for human-readable text while preserving "
                            "JSON keys, schema names, enum values, tool names, agent ids, "
                            "and document types in English."
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
                                "language_rules": CHINESE_OUTPUT_RULES,
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
                "skill_ids": sorted(scratchpad.loaded_skills),
                "skill_versions": {
                    skill_id: str(skill["version"])
                    for skill_id, skill in scratchpad.loaded_skills.items()
                },
                "prompt_block_ids": (
                    task.prompt_bundle.prompt_block_ids if task.prompt_bundle else []
                ),
                "internal_task_skill_ids": (
                    task.prompt_bundle.internal_task_skill_ids if task.prompt_bundle else []
                ),
                "external_skill_package_ids": (
                    sorted(scratchpad.loaded_skills)
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
    skill_registry: SkillRegistry,
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
    tool_call_policy = {
        "required_tool_names": _strings(task.input_context.get("required_tool_names")),
        "tool_requirements": task.input_context.get("tool_requirements", []),
        "available_tools_are_authoritative": True,
        "required_tool_gap_policy": (
            "If a required tool cannot be satisfied, return final_payload with explicit unknowns."
        ),
    }
    available_skills = [
        _available_skill_catalog_item(skill)
        for skill in _available_skill_definitions(task, definition, skill_registry)
    ]
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
                    "skill_calls": [
                        {"skill_id": "available skill id", "reason": "why this step needs it"}
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
            "task": {
                "task_id": task.task_id,
                "ticker": task.ticker,
                "agent_name": task.agent_name.value,
                "task_type": task.task_type.value,
                "workflow_node": task.run_metadata.workflow_node,
                "required_output_schema": task.required_output_schema,
                "permissions": task.permissions.model_dump(mode="json"),
                "input_context": agent_visible_input_context(task.input_context),
            },
            "tool_call_policy": tool_call_policy,
            "output_contract": _output_contract(task.required_output_schema),
            "context_snapshot": agent_visible_context_snapshot(context_snapshot),
            "available_tools": available_tools,
            "available_skills": available_skills,
            "loaded_skills": list(scratchpad.loaded_skills.values()),
            "plan": scratchpad.plan,
            "task_ledger": scratchpad.task_ledger,
            "compacted_evidence_summary": scratchpad.compacted_summaries,
            "recent_trajectory": scratchpad.recent_entries(config.recent_step_window),
            "scratchpad_warnings": scratchpad.warnings[-5:],
        },
        ensure_ascii=True,
        default=str,
    )


def _available_skill_definitions(
    task: AgentTask,
    definition: AgentDefinition,
    registry: SkillRegistry,
) -> list[SkillDefinition]:
    selected: dict[str, SkillDefinition] = {}
    for skill_id in definition.runtime.default_external_skill_package_ids:
        skill = registry.get(skill_id)
        if not _skill_matches_task(skill, task):
            continue
        selected[skill.skill_id] = skill
    return [selected[skill_id] for skill_id in sorted(selected)]


def _skill_matches_task(skill: SkillDefinition, task: AgentTask) -> bool:
    if skill.applicable_agents and task.agent_name not in skill.applicable_agents:
        return False
    if skill.applicable_task_types and task.task_type not in skill.applicable_task_types:
        return False
    return True


def _available_skill_catalog_item(skill: SkillDefinition) -> JsonDict:
    return {
        "skill_id": skill.skill_id,
        "name": skill.name,
        "version": skill.version,
        "source_project": skill.source_project,
        "source_kind": skill.source_kind.value,
        "call_format": {"skill_id": skill.skill_id, "reason": "why this step needs it"},
    }


def _loaded_skill_payload(skill: SkillDefinition) -> JsonDict:
    return {
        "skill_id": skill.skill_id,
        "name": skill.name,
        "version": skill.version,
        "source_project": skill.source_project,
        "source_kind": skill.source_kind.value,
        "instructions": skill.content.prompt_fragment,
    }


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
        "skill_calls",
        "delegations",
        "final_payload",
    }
    if not any(key in payload for key in action_keys):
        return {
            "is_complete": True,
            "completion_reason": "model returned direct structured payload",
            "final_payload": payload,
            "tool_calls": [],
            "skill_calls": [],
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
        "skill_calls",
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


def _output_contract(required_output_schema: str) -> JsonDict:
    contracts: JsonDict = {}
    for schema_name in _schema_names(required_output_schema):
        if schema_name == "ExpectationConstructionResult":
            contracts[schema_name] = {
                "final_payload": {
                    "proposed_patches": [
                        {
                            "patch_id": "patch_<id>",
                            "target": {
                                "document_type": "expectation_unit",
                                "ticker": "<ticker>",
                                "expectation_id": "expectation_<id>",
                                "field_path": "document",
                            },
                            "operation": "create",
                            "before": None,
                            "after": {
                                "document_id": "doc_<id>",
                                "document_type": "expectation_unit",
                                "ticker": "<ticker>",
                                "created_at": "ISO-8601 timestamp",
                                "expectation_id": "same as target.expectation_id",
                                "expectation_name": "short expectation name",
                                "direction": "bullish | bearish | neutral",
                                "why_it_matters": "why this expectation matters",
                                "market_view": {
                                    "text": "market narrative and thesis",
                                    "summary": "one sentence summary",
                                    "evidence_refs": [],
                                    "author_agent": "O1",
                                    "reviewer_agents": [],
                                },
                                "realized_facts": [],
                                "realized_facts_summary": "known facts or explicit unknowns",
                                "key_variables": [],
                                "event_monitoring_direction": {
                                    "known_event_notice": "what is already known",
                                    "positive_events": [],
                                    "negative_events": [],
                                },
                            },
                            "rationale": "why this patch is proposed",
                            "evidence_refs": [],
                            "author_agent": "O1",
                            "validation_status": "pending",
                        }
                    ],
                    "evidence_refs": [],
                    "delegations": [],
                    "unknowns": [],
                    "rationale": "construction rationale",
                    "resolved_objection_ids": [],
                    "accepted_objection_ids": [],
                    "partially_accepted_objection_ids": [],
                    "rejected_objection_ids": [],
                },
                "rules": [
                    "Use proposed_patches, not expectations or expectation_units.",
                    "Generate 1 to 3 expectation_unit create patches for GenerateExpectationUnits.",
                    "Each patch.after must be a complete ExpectationUnitDocument.",
                    "target.expectation_id must exactly equal after.expectation_id.",
                    "If evidence is partial, still produce the patch and list gaps in unknowns.",
                ],
            }
        elif schema_name == "ResearchSection":
            contracts[schema_name] = {
                "final_payload": {
                    "text": "section body",
                    "summary": "short summary",
                    "evidence_refs": [],
                    "author_agent": "<current agent enum>",
                    "reviewer_agents": [],
                }
            }
    return contracts


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
    evidence_refs = _valid_evidence_ref_payloads(payload.get("evidence_refs"))
    if not evidence_refs:
        evidence_refs = [
            item.model_dump(mode="json")
            for item in _evidence_refs(tool_results, delegation_results)
        ]
    return {
        "text": text or summary,
        "summary": summary,
        "evidence_refs": evidence_refs,
        "author_agent": task.agent_name.value,
        "reviewer_agents": _valid_agent_names(payload.get("reviewer_agents")),
    }


def _normalize_expectation_construction_payload(
    payload: JsonDict,
    *,
    task: AgentTask,
    tool_results: list[ToolResult],
    delegation_results: list[AgentResult],
) -> JsonDict:
    evidence_refs = _valid_evidence_ref_payloads(payload.get("evidence_refs"))
    if not evidence_refs:
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
        proposed_patches = payload.get("expectation_patches")
    if not isinstance(proposed_patches, list):
        proposed_patches = payload.get("expectation_unit_patches")
    if not isinstance(proposed_patches, list):
        proposed_patches = []
    normalized_patches = [
        _normalize_blackboard_patch_payload(item, task=task, fallback_evidence=evidence_refs)
        for item in proposed_patches
        if isinstance(item, dict)
    ]
    expectation_items = payload.get("expectations")
    if not isinstance(expectation_items, list):
        expectation_items = payload.get("expectation_units")
    if not isinstance(expectation_items, list):
        singular = payload.get("expectation_unit") or payload.get("expectation")
        expectation_items = [singular] if isinstance(singular, dict) else []
    if not normalized_patches and isinstance(expectation_items, list):
        normalized_patches = [
            _patch_from_expectation_payload(item, task=task, fallback_evidence=evidence_refs)
            for item in expectation_items
            if isinstance(item, dict)
        ]
    if not normalized_patches:
        fallback = _fallback_expectation_from_global_research(task, payload)
        if fallback is not None:
            normalized_patches = [
                _patch_from_expectation_payload(
                    fallback,
                    task=task,
                    fallback_evidence=evidence_refs,
                )
            ]

    return {
        "proposed_patches": normalized_patches,
        "evidence_refs": evidence_refs,
        "delegations": _normalize_output_delegations(payload.get("delegations"), task=task),
        "unknowns": _strings(payload.get("unknowns")),
        "rationale": str(payload.get("rationale") or payload.get("summary") or "O1 construction."),
        "resolved_objection_ids": _strings(payload.get("resolved_objection_ids")),
        "accepted_objection_ids": _strings(payload.get("accepted_objection_ids")),
        "partially_accepted_objection_ids": _strings(
            payload.get("partially_accepted_objection_ids")
        ),
        "rejected_objection_ids": _strings(payload.get("rejected_objection_ids")),
    }


def _fallback_expectation_from_global_research(
    task: AgentTask,
    payload: JsonDict,
) -> JsonDict | None:
    context = task.input_context.get("global_research_context")
    if not isinstance(context, dict):
        return None
    sections = context.get("sections")
    if not isinstance(sections, dict) or not sections:
        return None
    summary = _global_research_summary_text(sections)
    if not summary:
        return None
    return {
        "expectation_id": new_id("expectation"),
        "expectation_name": f"{task.ticker} commercialization milestone execution",
        "direction": "neutral",
        "why_it_matters": (
            str(payload.get("rationale") or payload.get("summary") or "")
            or "Global research identifies milestone execution as the primary expectation axis."
        ),
        "description": summary,
        "realized_facts_summary": "Realized facts require downstream review.",
        "key_variables": [],
        "positive_events": ["Confirmed deployment, partner, or commercialization milestones."],
        "negative_events": [
            "Deployment delays, financing pressure, or weak commercialization evidence."
        ],
    }


def _global_research_summary_text(sections: dict[str, Any]) -> str:
    chunks: list[str] = []
    for key in (
        "market_narrative_report",
        "fundamental_report",
        "industry_report",
        "market_trace_report",
        "macro_report",
    ):
        section = sections.get(key)
        if not isinstance(section, dict):
            continue
        summary = section.get("summary")
        text = section.get("text")
        if isinstance(summary, str) and summary.strip():
            chunks.append(f"{key}: {summary.strip()}")
        elif isinstance(text, str) and text.strip():
            chunks.append(f"{key}: {text.strip()[:800]}")
    return "\n".join(chunks)


def _normalize_output_delegations(value: Any, *, task: AgentTask) -> list[JsonDict]:
    delegations: list[JsonDict] = []
    for item in _dicts(value):
        question = str(item.get("question") or item.get("task") or "").strip()
        if not question:
            continue
        target_agent = _normalize_agent_name(
            item.get("target_agent"),
            default=AgentName.A2_FACT_CHECK,
        )
        delegations.append(
            {
                "delegation_id": str(item.get("delegation_id") or new_id("delegation")),
                "requester_agent": str(item.get("requester_agent") or task.agent_name.value),
                "target_agent": target_agent,
                "question": question,
                "required_evidence": _normalize_required_evidence(
                    item.get("required_evidence"),
                    question=question,
                ),
                "blocking_scope": _normalize_delegation_scope(item.get("blocking_scope"), task),
                "status": str(item.get("status") or "open"),
                "result_summary": item.get("result_summary"),
            }
        )
    return delegations


def _normalize_agent_name(value: Any, *, default: AgentName) -> str:
    raw = str(value or default.value)
    try:
        return AgentName(raw).value
    except ValueError:
        return default.value


def _normalize_required_evidence(value: Any, *, question: str) -> list[str]:
    allowed = {item.value for item in EvidenceSourceType}
    if isinstance(value, list):
        normalized = [str(item) for item in value if str(item) in allowed]
        if normalized:
            return normalized
    lowered = question.lower()
    if any(token in lowered for token in ("ohlcv", "price", "market", "volume")):
        return [EvidenceSourceType.MARKET_DATA.value]
    return [EvidenceSourceType.EXTERNAL_REPORT.value]


def _normalize_delegation_scope(value: Any, task: AgentTask) -> JsonDict:
    raw = _json_dict(value)
    return {
        "document_type": str(raw.get("document_type") or DocumentType.EXPECTATION_UNIT.value),
        "field_path": str(raw.get("field_path") or "document"),
        "ticker": str(raw.get("ticker") or task.ticker),
        "document_id": raw.get("document_id"),
        "expectation_id": raw.get("expectation_id"),
    }


def _normalize_blackboard_patch_payload(
    payload: JsonDict,
    *,
    task: AgentTask,
    fallback_evidence: list[JsonDict],
) -> JsonDict:
    evidence_refs = payload.get("evidence_refs")
    evidence_refs = _valid_evidence_ref_payloads(evidence_refs)
    if not evidence_refs:
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
        target["ticker"] = after["ticker"]
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
            "evidence_refs": _valid_evidence_ref_payloads(market_view.get("evidence_refs"))
            or fallback_evidence,
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
        "realized_facts": _normalize_realized_facts(payload.get("realized_facts")),
        "realized_facts_summary": realized_summary,
        "key_variables": _normalize_variable_statuses(payload.get("key_variables")),
        "event_monitoring_direction": _normalize_event_monitoring_direction(payload),
    }


def _normalize_realized_facts(value: Any) -> list[JsonDict]:
    facts: list[JsonDict] = []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, dict):
            facts.append(
                {
                    "event_id": str(item.get("event_id") or item.get("id") or new_id("event")),
                    "description": str(item.get("description") or item.get("text") or item),
                    "price_reaction": _normalize_price_reaction(item.get("price_reaction")),
                    "evidence_refs": _valid_evidence_ref_payloads(item.get("evidence_refs")),
                }
            )
        elif str(item).strip():
            facts.append(
                {
                    "event_id": new_id("event"),
                    "description": str(item),
                    "price_reaction": _normalize_price_reaction(None),
                    "evidence_refs": [],
                }
            )
    return facts


def _normalize_price_reaction(value: Any) -> JsonDict:
    if isinstance(value, dict):
        return {
            "price_change": str(value.get("price_change") or "unknown"),
            "price_pattern": str(value.get("price_pattern") or "unknown"),
            "interpretation": str(value.get("interpretation") or "Price reaction not established."),
            "evidence_refs": _valid_evidence_ref_payloads(value.get("evidence_refs")),
        }
    return {
        "price_change": "unknown",
        "price_pattern": "unknown",
        "interpretation": "Price reaction not established.",
        "evidence_refs": [],
    }


def _normalize_variable_statuses(value: Any) -> list[JsonDict]:
    variables: list[JsonDict] = []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, dict):
            name = str(item.get("name") or item.get("variable") or item.get("id") or "variable")
            variables.append(
                {
                    "variable_id": str(item.get("variable_id") or item.get("id") or new_id("var")),
                    "name": name,
                    "current_status": str(
                        item.get("current_status")
                        or item.get("status")
                        or item.get("description")
                        or "unknown"
                    ),
                    "certainty": str(item.get("certainty") or item.get("confidence") or "unknown"),
                    "evidence_refs": _valid_evidence_ref_payloads(item.get("evidence_refs")),
                }
            )
        elif str(item).strip():
            variables.append(
                {
                    "variable_id": new_id("var"),
                    "name": str(item),
                    "current_status": "unknown",
                    "certainty": "unknown",
                    "evidence_refs": [],
                }
            )
    return variables


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


def _valid_evidence_ref_payloads(value: Any) -> list[JsonDict]:
    if not isinstance(value, list):
        return []
    refs: list[JsonDict] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        try:
            refs.append(EvidenceRef.model_validate(item).model_dump(mode="json"))
        except ValidationError:
            continue
    return refs


def _tool_call_inputs(value: Any) -> list[JsonDict]:
    return [
        item
        for item in _dicts(value)
        if str(item.get("tool_name") or item.get("name") or "").strip()
    ]


def _skill_call_inputs(value: Any) -> list[JsonDict]:
    return [
        item
        for item in _dicts(value)
        if str(item.get("skill_id") or item.get("name") or "").strip()
    ]


def _public_tool_calls(value: Any) -> list[JsonDict]:
    return [
        {
            "tool_name": str(item.get("tool_name") or item.get("name") or ""),
            "input": item.get("input", {}),
        }
        for item in _dicts(value)
    ]


def _public_skill_calls(value: Any) -> list[JsonDict]:
    return [
        {
            "skill_id": str(item.get("skill_id") or item.get("name") or ""),
            "reason": str(item.get("reason") or ""),
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


def _valid_agent_names(value: Any) -> list[str]:
    valid = {item.value for item in AgentName}
    return [item for item in _strings(value) if item in valid]


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
