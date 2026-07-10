"""ReAct harness for autonomous, audited tool use inside one agent task."""

from __future__ import annotations

import asyncio
import importlib
import json
import re
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from pydantic import BaseModel, ValidationError

from doxagent.agents.config import AgentDefinition
from doxagent.agents.runtime.memory import (
    READ_OBSERVATION_TOOL_NAME,
    ContextBudgetConfig,
    TaskMemoryRuntime,
    maintenance_action_schema,
    measure_context_budget,
    memory_action_schema,
    read_observation_descriptor,
)
from doxagent.agents.runtime.tools import ToolRegistryFunctionAdapter, tool_result_to_summary
from doxagent.gateway import (
    GatewayError,
    MessageRole,
    ModelAuditSummary,
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
    DocumentType,
    EvidenceRef,
    EvidenceSourceType,
    ObjectionSeverity,
    ObjectionStatus,
    PatchOperation,
    ResultStatus,
    ValidationStatus,
    new_id,
)
from doxagent.models.output_schemas import REQUIRED_OUTPUT_SCHEMA_MODELS, schema_names
from doxagent.prompts.assembler import (
    CHINESE_OUTPUT_RULES,
    agent_visible_context_snapshot,
    agent_visible_input_context,
)
from doxagent.prompts.schema import AssembledPrompt
from doxagent.skills import UnknownSkillError
from doxagent.skills.registry import SkillRegistry, default_skill_registry
from doxagent.skills.schema import SkillDefinition
from doxagent.tools import ToolDescriptor, ToolError, ToolRegistry, ToolRequest, ToolResult

JsonDict = dict[str, Any]
DelegationHandler = Callable[[JsonDict], Awaitable[AgentResult]]

MAX_TOOL_CALLS_PER_NAME = 3
_FINAL_PAYLOAD_SCHEMAS: dict[str, type[BaseModel]] = REQUIRED_OUTPUT_SCHEMA_MODELS
_REVIEWER_ACCEPTANCE_WARNINGS_KEY = "reviewer_acceptance_warnings"
_REVIEWER_ACCEPTANCE_WARNINGS_INTERNAL_KEY = "_reviewer_acceptance_warnings"
_REQUIRED_EVIDENCE_REF_FIELDS = frozenset(
    {
        "evidence_id",
        "source_type",
        "source_id",
        "title",
        "summary",
        "confidence",
        "citation_scope",
    }
)
_RUNTIME_FINAL_PAYLOAD_SCHEMA_NAMES = frozenset(
    {"W1Result", "W2Result", "A2Result", "O3Result"}
)
_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL | re.IGNORECASE)
_EXPECTATION_UNIT_FLAT_PATCH_FIELDS = {
    "expectation_name",
    "direction",
    "why_it_matters",
    "market_view",
    "realized_facts",
    "realized_facts_summary",
    "key_variables",
    "event_monitoring_direction",
}


@dataclass(frozen=True)
class ReActHarnessConfig:
    max_steps: int = 5
    max_tool_calls_per_name: int = MAX_TOOL_CALLS_PER_NAME
    max_tool_call_batches: int | None = None
    model_context_window: int = 128_000
    reserved_output_tokens: int = 8_000
    safety_reserve_tokens: int = 4_000
    micro_maintenance_ratio: float = 0.75
    full_compaction_ratio: float = 0.85
    max_full_compaction_retries: int = 1
    model_request_timeout_seconds: float | None = None
    tool_call_timeout_seconds: float | None = 180.0

    def budget_config(self) -> ContextBudgetConfig:
        return ContextBudgetConfig(
            model_context_window=self.model_context_window,
            reserved_output_tokens=self.reserved_output_tokens,
            safety_reserve_tokens=self.safety_reserve_tokens,
            micro_maintenance_ratio=self.micro_maintenance_ratio,
            full_compaction_ratio=self.full_compaction_ratio,
            max_full_compaction_retries=self.max_full_compaction_retries,
        )


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
        runtime = TaskMemoryRuntime(task)
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
                runtime=runtime,
            )

        for step in range(1, self.config.max_steps + 1):
            use_micro_context = await self._prepare_active_context(
                task=task,
                definition=definition,
                assembled_prompt=assembled_prompt,
                context_snapshot=context_snapshot,
                runtime=runtime,
                metadata={**metadata, "react_step": str(step)},
                model_audits=model_audits,
            )
            response = await self._complete_step(
                task=task,
                definition=definition,
                assembled_prompt=assembled_prompt,
                context_snapshot=context_snapshot,
                runtime=runtime,
                metadata={**metadata, "react_step": str(step)},
                micro=use_micro_context,
            )
            model_audits.append(response.audit.model_dump(mode="json"))
            if response.error is not None:
                can_retry_json = (
                    _recoverable_json_response_error(response.error)
                    and step < self.config.max_steps
                )
                if can_retry_json:
                    runtime.record_model_format_error(
                        step=step,
                        error=response.error.model_dump(mode="json"),
                    )
                    continue
                return _failed(
                    task,
                    "model_gateway_error",
                    response.error.message,
                    retryable=response.error.retryable,
                    tool_results=tool_results,
                    delegation_results=delegation_results,
                    runtime=runtime,
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
                    runtime=runtime,
                    details={"text": response.text},
                )
            action = _coerce_direct_final_action(action, task.required_output_schema)
            runtime.record_action(step, action)
            action, action_text = await self._maybe_run_pre_final_challenge(
                step=step,
                task=task,
                definition=definition,
                assembled_prompt=assembled_prompt,
                context_snapshot=context_snapshot,
                runtime=runtime,
                action=action,
                response_text=response.text,
                tool_results=tool_results,
                metadata={**metadata, "react_step": str(step)},
                model_audits=model_audits,
            )

            tool_call_inputs = _tool_call_inputs(action.get("tool_calls"))
            runtime.record_tool_call_loop(
                {
                    str(call.get("tool_name") or call.get("name") or "")
                    for call in tool_call_inputs
                    if str(call.get("tool_name") or call.get("name") or "").strip()
                }
            )
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
                        text=action_text or json.dumps(final_payload, ensure_ascii=False),
                        model_audits=model_audits,
                        tool_results=tool_results,
                        delegation_results=delegation_results,
                        runtime=runtime,
                        completion_reason=str(action.get("completion_reason") or "complete"),
                    )

            if skill_call_inputs:
                self._load_skill_calls(
                    step=step,
                    task=task,
                    definition=definition,
                    calls=skill_call_inputs,
                    runtime=runtime,
                )
                if (
                    is_complete
                    and isinstance(final_payload, dict)
                    and not tool_call_inputs
                    and not delegation_inputs
                ):
                    return self._succeeded(
                        task=task,
                        definition=definition,
                        assembled_prompt=assembled_prompt,
                        context_snapshot=context_snapshot,
                        structured=final_payload,
                        text=action_text or json.dumps(final_payload, ensure_ascii=False),
                        model_audits=model_audits,
                        tool_results=tool_results,
                        delegation_results=delegation_results,
                        runtime=runtime,
                        completion_reason=str(action.get("completion_reason") or "complete"),
                    )

            if tool_call_inputs:
                if not runtime.can_start_tool_call_batch(self.config.max_tool_call_batches):
                    return _failed(
                        task,
                        "tool_call_batch_limit_exceeded",
                        (
                            "ReAct task exceeded the configured tool-call batch budget; "
                            "produce a final payload from existing context."
                        ),
                        tool_results=tool_results,
                        delegation_results=delegation_results,
                        runtime=runtime,
                        details={
                            "max_tool_call_batches": self.config.max_tool_call_batches,
                            "attempted_batch_step": step,
                        },
                    )
                runtime.record_tool_call_batch()
                step_results = await self._execute_tool_calls(
                    step=step,
                    task=task,
                    calls=tool_call_inputs,
                    runtime=runtime,
                )
                tool_results.extend(step_results)

            for delegation in delegation_inputs:
                result = await delegate(delegation)
                runtime.record_delegation(step=step, request=delegation, result=result)
                delegation_results.append(result)

            if not tool_call_inputs and not skill_call_inputs and not delegation_inputs:
                if isinstance(final_payload, dict):
                    if is_complete:
                        return self._succeeded(
                            task=task,
                            definition=definition,
                            assembled_prompt=assembled_prompt,
                            context_snapshot=context_snapshot,
                            structured=final_payload,
                            text=action_text or json.dumps(final_payload, ensure_ascii=False),
                            model_audits=model_audits,
                            tool_results=tool_results,
                            delegation_results=delegation_results,
                            runtime=runtime,
                            completion_reason=str(
                                action.get("completion_reason") or "final_payload"
                            ),
                        )
                    if step < self.config.max_steps:
                        runtime.record_no_progress(step=step)
                        continue
                    runtime.record_no_progress(step=step)
                    recovered_review_result = self._succeeded_with_review_max_steps_fallback(
                        task=task,
                        definition=definition,
                        assembled_prompt=assembled_prompt,
                        context_snapshot=context_snapshot,
                        model_audits=model_audits,
                        tool_results=tool_results,
                        delegation_results=delegation_results,
                        runtime=runtime,
                    )
                    if recovered_review_result is not None:
                        return recovered_review_result
                    return _failed(
                        task,
                        "react_incomplete_final_payload",
                        "ReAct step returned final_payload with is_complete=false.",
                        tool_results=tool_results,
                        delegation_results=delegation_results,
                        runtime=runtime,
                    )
                if step < self.config.max_steps:
                    runtime.record_no_progress(step=step)
                    continue
                runtime.record_no_progress(step=step)
                recovered_review_result = self._succeeded_with_review_max_steps_fallback(
                    task=task,
                    definition=definition,
                    assembled_prompt=assembled_prompt,
                    context_snapshot=context_snapshot,
                    model_audits=model_audits,
                    tool_results=tool_results,
                    delegation_results=delegation_results,
                    runtime=runtime,
                )
                if recovered_review_result is not None:
                    return recovered_review_result
                return _failed(
                    task,
                    "react_no_progress",
                    "ReAct step 未返回 final payload、工具调用或委托。",
                    tool_results=tool_results,
                    delegation_results=delegation_results,
                    runtime=runtime,
                )

        recovered_research = _max_steps_research_section_fallback(
            task,
            tool_results=tool_results,
            delegation_results=delegation_results,
            runtime=runtime,
        )
        if recovered_research is not None:
            structured, text = recovered_research
            runtime.add_warning(
                "ResearchSection reached max_steps; recovered from successful tool evidence."
            )
            runtime.event_log.append(
                "max_steps_recovered",
                {
                    "status": "warning",
                    "schema": "ResearchSection",
                    "successful_tool_count": sum(
                        1 for result in tool_results if result.status is ResultStatus.SUCCEEDED
                    ),
                },
            )
            return self._succeeded(
                task=task,
                definition=definition,
                assembled_prompt=assembled_prompt,
                context_snapshot=context_snapshot,
                structured=structured,
                text=text,
                model_audits=model_audits,
                tool_results=tool_results,
                delegation_results=delegation_results,
                runtime=runtime,
                completion_reason=(
                    "达到 ReAct max_steps，已基于成功工具证据生成保守 ResearchSection。"
                ),
            )

        recovered_review_payload = _max_steps_review_result_fallback(
            task,
            tool_results=tool_results,
            delegation_results=delegation_results,
            runtime=runtime,
        )
        if recovered_review_payload is not None:
            recovered_review_result = self._succeeded_with_review_max_steps_fallback(
                task=task,
                definition=definition,
                assembled_prompt=assembled_prompt,
                context_snapshot=context_snapshot,
                model_audits=model_audits,
                tool_results=tool_results,
                delegation_results=delegation_results,
                runtime=runtime,
                recovered_review=recovered_review_payload,
            )
            if recovered_review_result is not None:
                return recovered_review_result

        return _failed(
            task,
            "react_max_steps_exceeded",
            "ReAct loop reached max_steps without a complete final payload.",
            tool_results=tool_results,
            delegation_results=delegation_results,
            runtime=runtime,
        )

    def _succeeded_with_review_max_steps_fallback(
        self,
        *,
        task: AgentTask,
        definition: AgentDefinition,
        assembled_prompt: AssembledPrompt,
        context_snapshot: Any | None,
        model_audits: list[JsonDict],
        tool_results: list[ToolResult],
        delegation_results: list[AgentResult],
        runtime: TaskMemoryRuntime,
        recovered_review: tuple[JsonDict, str, str] | None = None,
    ) -> AgentResult | None:
        recovered_review = recovered_review or _max_steps_review_result_fallback(
            task,
            tool_results=tool_results,
            delegation_results=delegation_results,
            runtime=runtime,
        )
        if recovered_review is None:
            return None
        structured, text, schema_name = recovered_review
        runtime.add_warning(
            f"{schema_name} reached max_steps; recovered as conservative review result."
        )
        runtime.event_log.append(
            "max_steps_recovered",
            {
                "status": "warning",
                "schema": schema_name,
                "successful_tool_count": sum(
                    1 for result in tool_results if result.status is ResultStatus.SUCCEEDED
                ),
            },
        )
        return self._succeeded(
            task=task,
            definition=definition,
            assembled_prompt=assembled_prompt,
            context_snapshot=context_snapshot,
            structured=structured,
            text=text,
            model_audits=model_audits,
            tool_results=tool_results,
            delegation_results=delegation_results,
            runtime=runtime,
            completion_reason=(
                "Reached ReAct max_steps; recovered as conservative review result."
            ),
        )

    def _load_skill_calls(
        self,
        *,
        step: int,
        task: AgentTask,
        definition: AgentDefinition,
        calls: list[JsonDict],
        runtime: TaskMemoryRuntime,
    ) -> None:
        available = {
            skill.skill_id: skill
            for skill in _available_skill_definitions(task, definition, self.skill_registry)
        }
        for call in calls:
            skill_id = str(call.get("skill_id") or call.get("name") or "").strip()
            reason = str(call.get("reason") or "")
            if not skill_id:
                runtime.record_skill_result(
                    step=step,
                    skill_id="",
                    status="failed",
                    reason=reason,
                    message="技能调用缺少 skill_id。",
                )
                continue
            if skill_id in runtime.loaded_skills:
                runtime.record_skill_result(
                    step=step,
                    skill_id=skill_id,
                    status="duplicate",
                    reason=reason,
                    message=f"技能 {skill_id} 在本任务中已加载。",
                )
                continue
            skill = available.get(skill_id)
            if skill is None:
                runtime.record_skill_result(
                    step=step,
                    skill_id=skill_id,
                    status="rejected",
                    reason=reason,
                    message=f"技能 {skill_id} 未暴露给当前 agent task。",
                )
                continue
            runtime.record_skill_result(
                step=step,
                skill_id=skill_id,
                status="loaded",
                reason=reason,
                message=f"技能 {skill_id} 已加载。",
                skill=skill,
            )

    async def _execute_tool_calls(
        self,
        *,
        step: int,
        task: AgentTask,
        calls: list[JsonDict],
        runtime: TaskMemoryRuntime,
    ) -> list[ToolResult]:
        adapter = (
            ToolRegistryFunctionAdapter(self.tool_registry)
            if self.tool_registry is not None
            else None
        )
        results: list[ToolResult | None] = [None] * len(calls)
        concurrent_work: list[
            tuple[int, JsonDict, list[str], str, ToolDescriptor | None]
        ] = []

        for index, call in enumerate(calls):
            tool_name = str(call.get("tool_name") or call.get("name") or "")
            input_payload = _json_dict(call.get("input"))
            if tool_name == READ_OBSERVATION_TOOL_NAME:
                runtime.read_observation(step=step, input_payload=input_payload)
                continue
            tool_call_id = runtime.begin_tool_call(
                step=step,
                tool_name=tool_name or "unknown_tool",
                input_payload=input_payload,
            )
            if not tool_name:
                blocked_result = self._blocked_tool_result(
                    task,
                    call,
                    code="invalid_tool_call",
                    message="工具调用缺少 tool_name。",
                )
                results[index] = blocked_result
                runtime.record_tool_result(
                    step=step,
                    tool_call_id=tool_call_id,
                    result=blocked_result,
                    input_payload=input_payload,
                    warnings=[],
                    descriptor=None,
                )
                continue
            if adapter is None:
                blocked_result = self._blocked_tool_result(
                    task,
                    call,
                    code="tool_registry_disabled",
                    message="当前 runner 未配置 tool registry。",
                )
                results[index] = blocked_result
                runtime.record_tool_result(
                    step=step,
                    tool_call_id=tool_call_id,
                    result=blocked_result,
                    input_payload=input_payload,
                    warnings=[],
                    descriptor=None,
                )
                continue
            descriptor = self.tool_registry.describe(tool_name) if self.tool_registry else None
            if not runtime.can_call_tool(tool_name, self.config.max_tool_calls_per_name):
                blocked_result = self._blocked_tool_result(
                    task,
                    call,
                    code="tool_call_limit_exceeded",
                    message=(
                        f"工具 {tool_name} 已经在同一任务节点内连续 "
                        f"{self.config.max_tool_calls_per_name} 轮 loop 被调用；"
                        "请先产出结论、换用其他证据路径，或在后续非连续 loop 中重试。"
                    ),
                )
                results[index] = blocked_result
                runtime.record_tool_result(
                    step=step,
                    tool_call_id=tool_call_id,
                    result=blocked_result,
                    input_payload=input_payload,
                    warnings=[],
                    descriptor=descriptor,
                )
                continue

            warnings = runtime.record_tool_attempt(tool_name, input_payload)
            if descriptor is not None and descriptor.concurrent_safe:
                concurrent_work.append((index, call, warnings, tool_call_id, descriptor))
            else:
                assert adapter is not None
                result = await self._call_tool(adapter, tool_name, task, input_payload)
                results[index] = result
                runtime.record_tool_result(
                    step=step,
                    tool_call_id=tool_call_id,
                    result=result,
                    input_payload=input_payload,
                    warnings=warnings,
                    descriptor=descriptor,
                )

        if concurrent_work:
            assert adapter is not None
            gathered = await asyncio.gather(
                *[
                    self._call_tool(
                        adapter,
                        str(call.get("tool_name") or call.get("name") or ""),
                        task,
                        _json_dict(call.get("input")),
                    )
                    for _, call, _, _, _ in concurrent_work
                ]
            )
            for (
                index,
                call,
                warnings,
                tool_call_id,
                descriptor,
            ), result in zip(concurrent_work, gathered, strict=True):
                results[index] = result
                runtime.record_tool_result(
                    step=step,
                    tool_call_id=tool_call_id,
                    result=result,
                    input_payload=_json_dict(call.get("input")),
                    warnings=warnings,
                    descriptor=descriptor,
                )

        return [result for result in results if result is not None]

    async def _call_tool(
        self,
        adapter: ToolRegistryFunctionAdapter,
        tool_name: str,
        task: AgentTask,
        input_payload: JsonDict,
    ) -> ToolResult:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[ToolResult] = loop.create_future()

        def publish(result: ToolResult) -> None:
            if not future.done():
                future.set_result(result)

        def target() -> None:
            try:
                result = adapter.call_tool(
                    tool_name=tool_name,
                    task=task,
                    input_payload=input_payload,
                )
            except Exception as exc:
                result = self._blocked_tool_result(
                    task,
                    {"tool_name": tool_name, "input": input_payload},
                    code="tool_call_exception",
                    message=str(exc),
                )
            try:
                loop.call_soon_threadsafe(publish, result)
            except RuntimeError:
                # A timed-out task may close its loop before the daemon tool thread returns.
                return

        thread = threading.Thread(
            target=target,
            name=f"doxagent-tool-{tool_name}",
            daemon=True,
        )
        thread.start()
        timeout = self.config.tool_call_timeout_seconds
        if timeout is None:
            return await future
        try:
            return await asyncio.wait_for(future, timeout=max(0.001, timeout))
        except TimeoutError:
            return self._blocked_tool_result(
                task,
                {"tool_name": tool_name, "input": input_payload},
                code="tool_call_timeout",
                message=f"工具 {tool_name} 超过 {timeout} 秒未返回。",
                retryable=True,
            )

    def _blocked_tool_result(
        self,
        task: AgentTask,
        call: JsonDict,
        *,
        code: str,
        message: str,
        retryable: bool = False,
    ) -> ToolResult:
        return ToolResult(
            tool_name=str(call.get("tool_name") or call.get("name") or "unknown_tool"),
            status=ResultStatus.FAILED,
            output_summary=f"{code}: {message}",
            error=ToolError(code=code, message=message, retryable=retryable),
            output={"ticker": task.ticker, "agent_name": task.agent_name.value},
        )

    async def _complete_step(
        self,
        *,
        task: AgentTask,
        definition: AgentDefinition,
        assembled_prompt: AssembledPrompt,
        context_snapshot: Any | None,
        runtime: TaskMemoryRuntime,
        metadata: dict[str, str],
        micro: bool,
    ) -> ModelResponse:
        return await self._complete_model_request(
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
                                runtime=runtime,
                                tool_registry=self.tool_registry,
                                skill_registry=self.skill_registry,
                                active_context=runtime.active_context(micro=micro),
                                config=self.config,
                            ),
                        ),
                    ],
                    temperature=0.2,
                    timeout_seconds=self.config.model_request_timeout_seconds,
                    response_format=ResponseFormat.JSON,
                    metadata=metadata,
                )
            )

    async def _complete_model_request(self, request: ModelRequest) -> ModelResponse:
        loop = asyncio.get_running_loop()
        started = loop.time()
        timeout = request.timeout_seconds
        try:
            completion = self.model_gateway.complete(request)
            if timeout is None:
                return await completion
            return await asyncio.wait_for(completion, timeout=max(0.001, timeout))
        except TimeoutError:
            return ModelResponse(
                audit=ModelAuditSummary(
                    provider=request.provider,
                    model=request.model,
                    latency_seconds=max(0.0, loop.time() - started),
                    metadata=request.metadata,
                ),
                error=GatewayError(
                    code="model_request_timeout",
                    message=f"模型请求超过 {timeout} 秒未返回。",
                    retryable=True,
                    provider=request.provider,
                ),
            )
        except Exception as exc:
            return ModelResponse(
                audit=ModelAuditSummary(
                    provider=request.provider,
                    model=request.model,
                    latency_seconds=max(0.0, loop.time() - started),
                    metadata=request.metadata,
                ),
                error=GatewayError(
                    code="model_gateway_exception",
                    message=str(exc),
                    retryable=False,
                    provider=request.provider,
                ),
            )

    async def _prepare_active_context(
        self,
        *,
        task: AgentTask,
        definition: AgentDefinition,
        assembled_prompt: AssembledPrompt,
        context_snapshot: Any | None,
        runtime: TaskMemoryRuntime,
        metadata: dict[str, str],
        model_audits: list[JsonDict],
    ) -> bool:
        system_prompt = _react_system_prompt(assembled_prompt.instructions)
        normal_context = runtime.active_context(micro=False)
        normal_user_prompt = _react_user_prompt(
            task=task,
            definition=definition,
            assembled_prompt=assembled_prompt,
            context_snapshot=context_snapshot,
            runtime=runtime,
            tool_registry=self.tool_registry,
            skill_registry=self.skill_registry,
            active_context=normal_context,
            config=self.config,
        )
        available_tools = _available_tool_payloads(task, runtime, self.tool_registry)
        report = measure_context_budget(
            system_prompt=system_prompt,
            user_prompt=normal_user_prompt,
            active_context=normal_context,
            available_tools=available_tools,
            config=self.config.budget_config(),
            mode="normal",
        )
        runtime.record_context_budget(report)
        if not bool(report["over_micro_threshold"]):
            return False

        micro_context = runtime.active_context(micro=True)
        micro_user_prompt = _react_user_prompt(
            task=task,
            definition=definition,
            assembled_prompt=assembled_prompt,
            context_snapshot=context_snapshot,
            runtime=runtime,
            tool_registry=self.tool_registry,
            skill_registry=self.skill_registry,
            active_context=micro_context,
            config=self.config,
        )
        micro_report = measure_context_budget(
            system_prompt=system_prompt,
            user_prompt=micro_user_prompt,
            active_context=micro_context,
            available_tools=available_tools,
            config=self.config.budget_config(),
            mode="micro",
        )
        runtime.record_micro_maintenance(before=report, after=micro_report)
        runtime.record_context_budget(micro_report)
        if not (
            bool(micro_report["over_full_threshold"])
            or bool(micro_report["over_hard_budget"])
        ):
            return True

        attempts = 0
        while attempts <= self.config.max_full_compaction_retries:
            attempts += 1
            response = await self._complete_model_request(
                ModelRequest(
                    provider=self.provider,
                    model=self.model,
                    messages=[
                        ModelMessage(
                            role=MessageRole.SYSTEM,
                            content="\n\n".join(
                                [
                                    assembled_prompt.instructions,
                                    "## Full Compaction",
                                    (
                                        "你是当前 AgentTask 的同一个主 agent。只维护当前 Memory "
                                        "State，不调用工具、不输出最终业务结果、"
                                        "不改写任何原始 observation。"
                                    ),
                                ]
                            ),
                        ),
                        ModelMessage(
                            role=MessageRole.USER,
                            content=json.dumps(
                                {
                                    "task": {
                                        "task_id": task.task_id,
                                        "task_type": task.task_type.value,
                                        "required_output_schema": task.required_output_schema,
                                    },
                                    "full_compaction_reason": micro_report,
                                    "active_context": micro_context,
                                    "maintenance_action_schema": maintenance_action_schema(),
                                    "protected_fresh_observation_count": len(
                                        micro_context.get("fresh_observations", [])
                                    ),
                                    "language_rules": CHINESE_OUTPUT_RULES,
                                },
                                ensure_ascii=False,
                                default=str,
                            ),
                        ),
                    ],
                    temperature=0,
                    timeout_seconds=self.config.model_request_timeout_seconds,
                    response_format=ResponseFormat.JSON,
                    metadata={**metadata, "react_compaction": "true"},
                )
            )
            model_audits.append(response.audit.model_dump(mode="json"))
            if response.error is not None:
                runtime.record_full_compaction_failure(response.error.message)
                continue
            action = _parse_action(response)
            if action is None:
                runtime.record_full_compaction_failure(
                    "Full Compaction returned an invalid maintenance action."
                )
                continue
            before_tokens = int(micro_report["projected_input_tokens"])
            runtime.apply_full_compaction(action, before=micro_report)
            micro_context = runtime.active_context(micro=True)
            micro_user_prompt = _react_user_prompt(
                task=task,
                definition=definition,
                assembled_prompt=assembled_prompt,
                context_snapshot=context_snapshot,
                runtime=runtime,
                tool_registry=self.tool_registry,
                skill_registry=self.skill_registry,
                active_context=micro_context,
                config=self.config,
            )
            micro_report = measure_context_budget(
                system_prompt=system_prompt,
                user_prompt=micro_user_prompt,
                active_context=micro_context,
                available_tools=available_tools,
                config=self.config.budget_config(),
                mode="full_compaction_result",
            )
            runtime.record_context_budget(micro_report)
            reduced = int(micro_report["projected_input_tokens"]) < before_tokens
            if reduced and not bool(micro_report["over_hard_budget"]):
                return True
            runtime.add_warning(
                "Full Compaction 未充分降低上下文占用，将重试或执行安全 fallback。",
                source="full_compaction",
            )

        while bool(micro_report["over_hard_budget"]):
            if runtime.safe_budget_fallback() is None:
                runtime.add_warning(
                    "Active Context 的固定内容已超过硬预算，无法继续卸载 retained observation。",
                    source="context_budget",
                )
                break
            micro_context = runtime.active_context(micro=True)
            micro_user_prompt = _react_user_prompt(
                task=task,
                definition=definition,
                assembled_prompt=assembled_prompt,
                context_snapshot=context_snapshot,
                runtime=runtime,
                tool_registry=self.tool_registry,
                skill_registry=self.skill_registry,
                active_context=micro_context,
                config=self.config,
            )
            micro_report = measure_context_budget(
                system_prompt=system_prompt,
                user_prompt=micro_user_prompt,
                active_context=micro_context,
                available_tools=available_tools,
                config=self.config.budget_config(),
                mode="safe_fallback",
            )
            runtime.record_context_budget(micro_report)
        return True

    async def _maybe_run_pre_final_challenge(
        self,
        *,
        step: int,
        task: AgentTask,
        definition: AgentDefinition,
        assembled_prompt: AssembledPrompt,
        context_snapshot: Any | None,
        runtime: TaskMemoryRuntime,
        action: JsonDict,
        response_text: str | None,
        tool_results: list[ToolResult],
        metadata: dict[str, str],
        model_audits: list[JsonDict],
    ) -> tuple[JsonDict, str | None]:
        if not bool(action.get("is_complete", False)):
            return action, response_text
        if runtime.pre_final_challenge_completed:
            return action, response_text

        reloaded_refs = runtime.reload_final_observations()
        challenge_enabled = bool(task.input_context.get("pre_final_challenge", False)) or (
            "research_frame" in task.input_context
        )
        if not challenge_enabled and not reloaded_refs:
            return action, response_text

        reasons: list[str] = []
        memory_audit = runtime.memory.audit()
        synthesis = memory_audit.get("working_synthesis", [])
        agenda = memory_audit.get("research_agenda", [])
        retained = memory_audit.get("retained_observations", [])
        if not synthesis:
            reasons.append("Working Synthesis 为空，需确认 final 是否只是复述工具结果。")
        active_agenda = [
            item
            for item in agenda
            if isinstance(item, dict) and item.get("status") == "active"
        ]
        if active_agenda:
            reasons.append(f"仍有 {len(active_agenda)} 个 active Research Agenda。")
        if len(tool_results) >= 2 and not synthesis:
            reasons.append("已有多次 tool result，但尚未形成可复用 Synthesis。")
        linked_refs = {
            ref
            for item in synthesis
            if isinstance(item, dict)
            for ref in item.get("observation_refs", [])
            if isinstance(ref, str)
        }
        retained_refs = {
            str(item.get("ref")) for item in retained if isinstance(item, dict)
        }
        if retained_refs and not retained_refs.intersection(linked_refs):
            reasons.append("存在 Retained Observation，但尚未与有效 Synthesis 建立关联。")
        if reloaded_refs:
            reasons.append(
                "final 所需 INDEX_ONLY observation 已重新加载，必须基于原文重新生成 final。"
            )
        if not reasons:
            return action, response_text

        response = await self._complete_model_request(
            ModelRequest(
                provider=self.provider,
                model=self.model,
                messages=[
                    ModelMessage(
                        role=MessageRole.SYSTEM,
                        content="\n\n".join(
                            [
                                _react_system_prompt(assembled_prompt.instructions),
                                "## Pre-final Research Challenge",
                                (
                                    "检查当前研究是否足以完成 output contract。"
                                    "若不足，返回继续研究的普通 ReAct action；"
                                    "若已充分，基于当前原文重新返回完整 final action。"
                                ),
                            ]
                        ),
                    ),
                    ModelMessage(
                        role=MessageRole.USER,
                        content=json.dumps(
                            {
                                "challenge_reasons": reasons,
                                "proposed_final_action": action,
                                "active_context": runtime.active_context(micro=False),
                                "context_snapshot": agent_visible_context_snapshot(
                                    context_snapshot
                                ),
                                "output_contract": _output_contract(
                                    task.required_output_schema,
                                    task=task,
                                ),
                                "available_tools": _available_tool_payloads(
                                    task,
                                    runtime,
                                    self.tool_registry,
                                ),
                                "response_schema": {
                                    **memory_action_schema(),
                                    "plan_update": ["…"],
                                    "reasoning_summary": "中文公开摘要",
                                    "is_complete": "boolean",
                                    "completion_reason": "中文完成原因",
                                    "tool_calls": [
                                        {
                                            "tool_name": "registered tool name",
                                            "input": {"key": "value"},
                                        }
                                    ],
                                    "delegations": [],
                                    "final_payload": "完整业务 schema",
                                },
                            },
                            ensure_ascii=False,
                            default=str,
                        ),
                    ),
                ],
                temperature=0.2,
                timeout_seconds=self.config.model_request_timeout_seconds,
                response_format=ResponseFormat.JSON,
                metadata={**metadata, "react_pre_final_challenge": "true"},
            )
        )
        model_audits.append(response.audit.model_dump(mode="json"))
        if response.error is not None:
            runtime.record_pre_final_challenge(
                {"reasons": reasons, "status": "failed", "error": response.error.message}
            )
            runtime.add_warning(
                f"Pre-final challenge 失败，保留原 final：{response.error.message}",
                source="pre_final_challenge",
            )
            return action, response_text
        challenged_action = _parse_action(response)
        if challenged_action is None:
            runtime.record_pre_final_challenge(
                {"reasons": reasons, "status": "invalid_action"}
            )
            runtime.add_warning(
                "Pre-final challenge 返回无效 action，保留原 final。",
                source="pre_final_challenge",
            )
            return action, response_text
        challenged_action = _coerce_direct_final_action(
            challenged_action,
            task.required_output_schema,
        )
        runtime.record_pre_final_challenge(
            {
                "reasons": reasons,
                "status": "completed",
                "is_complete": bool(challenged_action.get("is_complete", False)),
            }
        )
        runtime.record_action(step, challenged_action)
        return challenged_action, response.text or json.dumps(
            challenged_action,
            ensure_ascii=False,
        )

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
        runtime: TaskMemoryRuntime,
        completion_reason: str,
    ) -> AgentResult:
        structured = _normalize_final_payload(
            structured,
            task=task,
            required_output_schema=task.required_output_schema,
            tool_results=tool_results,
            delegation_results=delegation_results,
        )
        reviewer_acceptance_warnings = _pop_reviewer_acceptance_warnings(structured)
        if "ExpectationDetailResult" in _schema_names(task.required_output_schema):
            structured = _recover_expectation_detail_arrays_from_text(
                structured,
                text=text,
            )
        schema_error = _final_payload_schema_error(structured, task.required_output_schema)
        if schema_error is not None:
            return _failed(
                task,
                "invalid_final_payload",
                schema_error,
                tool_results=tool_results,
                delegation_results=delegation_results,
                runtime=runtime,
                details={"required_output_schema": task.required_output_schema},
            )
        required_tool_names = _strings(task.input_context.get("required_tool_names"))
        failed_required = _failed_required_tools(required_tool_names, tool_results)
        if failed_required:
            warning = (
                "必需的 ReAct 工具调用缺失或失败；将以 unknowns/data gaps 继续："
                f"{', '.join(failed_required)}。"
            )
            runtime.add_warning(warning, source="required_tool_gap")
            runtime.event_log.append(
                "required_tool_gap",
                {
                    "required_tool_names": required_tool_names,
                    "failed": failed_required,
                    "status": "warning",
                },
            )
        successful_tool_results = [
            result for result in tool_results if result.status is ResultStatus.SUCCEEDED
        ]
        evidence_refs = _evidence_refs(successful_tool_results, delegation_results)
        runtime.reload_final_observations()
        runtime.record_final(structured, completion_reason)
        market_evidence_snapshot = runtime.market_evidence_snapshot()
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
                _REVIEWER_ACCEPTANCE_WARNINGS_KEY: reviewer_acceptance_warnings,
                "react_audit": runtime.persisted_audit(),
                "market_evidence_snapshot": market_evidence_snapshot,
                "skill_ids": sorted(runtime.loaded_skills),
                "skill_versions": {
                    skill_id: str(skill["version"])
                    for skill_id, skill in runtime.loaded_skills.items()
                },
                "prompt_block_ids": (
                    task.prompt_bundle.prompt_block_ids if task.prompt_bundle else []
                ),
                "internal_task_skill_ids": (
                    task.prompt_bundle.internal_task_skill_ids if task.prompt_bundle else []
                ),
                "external_skill_package_ids": sorted(runtime.loaded_skills),
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
            tool_calls=[tool_result_to_summary(result) for result in successful_tool_results],
        )


def _max_steps_research_section_fallback(
    task: AgentTask,
    *,
    tool_results: list[ToolResult],
    delegation_results: list[AgentResult],
    runtime: TaskMemoryRuntime,
) -> tuple[JsonDict, str] | None:
    if "ResearchSection" not in _schema_names(task.required_output_schema):
        return None
    successful = [
        result
        for result in tool_results
        if result.status is ResultStatus.SUCCEEDED and result.evidence_refs
    ]
    if not successful:
        return None

    evidence_refs = [
        item.model_dump(mode="json") for item in _evidence_refs(tool_results, delegation_results)
    ]
    section_key = str(task.input_context.get("required_section_key") or "research_section")
    labels = {
        "fundamental_report": "基本面研究",
        "macro_report": "宏观与市场环境研究",
        "industry_report": "行业与竞争研究",
        "market_trace_report": "价格与市场行为研究",
        "market_narrative_report": "市场叙事研究",
    }
    label = labels.get(section_key, section_key)
    success_tools = _unique_tool_names(successful)
    failed_tools = _unique_tool_names(
        result for result in tool_results if result.status is not ResultStatus.SUCCEEDED
    )
    source_note = _tool_result_source_note(successful)
    gap_note = (
        f"同时存在未完成或失败的工具调用：{'、'.join(failed_tools)}；这些失败已作为数据缺口处理。"
        if failed_tools
        else "未记录阻断性的工具失败。"
    )
    synthesis = runtime.memory.audit().get("working_synthesis", [])
    synthesis_note = f"当前 Working Synthesis：{synthesis[:3]}" if synthesis else ""
    text = (
        f"{task.ticker} 的{label}在 ReAct 达到 max_steps 前未收到模型的完整 final_payload，"
        "workflow 因此基于已经成功返回的工具证据生成保守恢复段落。"
        f"已成功使用的工具包括：{'、'.join(success_tools)}。{source_note}"
        f"{gap_note}"
        "该段只能支持后续 Blackboard 初始化继续推进和人工/LLM 复核，不能被解释为无保留的"
        "最终投资结论。后续监控应优先复核缺失基准、同业或异常数据，并把价格反应、相对表现、"
        "成交量和波动率变化与 expectation units 中的已定价/未定价假设连接起来。"
        f"{synthesis_note}"
    )
    summary = (
        f"{task.ticker} 的{label}由 ReAct max_steps 恢复逻辑生成；"
        f"证据来自 {'、'.join(success_tools)}，失败或异常工具已作为不确定性保留。"
    )
    structured = {
        "text": text,
        "summary": summary,
        "evidence_refs": evidence_refs,
        "author_agent": task.agent_name.value,
        "reviewer_agents": [],
    }
    return structured, text


def _max_steps_review_result_fallback(
    task: AgentTask,
    *,
    tool_results: list[ToolResult],
    delegation_results: list[AgentResult],
    runtime: TaskMemoryRuntime,
) -> tuple[JsonDict, str, str] | None:
    schemas = set(_schema_names(task.required_output_schema))
    schema_name: str | None = None
    if "DoxAtlasAuditResult" in schemas:
        schema_name = "DoxAtlasAuditResult"
    elif "ExpectationFieldReviewResult" in schemas:
        schema_name = "ExpectationFieldReviewResult"
    if schema_name is None:
        return None
    if not tool_results and len(runtime.observations.raw_store) == 0:
        return None

    evidence_refs = [
        item.model_dump(mode="json") for item in _evidence_refs(tool_results, delegation_results)
    ]
    if not evidence_refs:
        evidence_refs = [_agent_output_evidence_ref(task)]
    successful = [
        result for result in tool_results if result.status is ResultStatus.SUCCEEDED
    ]
    failed = [
        result for result in tool_results if result.status is not ResultStatus.SUCCEEDED
    ]
    successful_tools = _unique_tool_names(successful)
    failed_tools = _unique_tool_names(failed)
    review_scope = _strings(task.input_context.get("review_scope")) or ["document"]
    scope_text = ", ".join(review_scope[:6])
    success_text = ", ".join(successful_tools) if successful_tools else "none"
    failed_text = ", ".join(failed_tools) if failed_tools else "none"
    status = "needs_more_evidence" if successful else "not_checked"
    rationale = (
        f"{task.agent_name.value} reached ReAct max_steps before producing a complete "
        f"{schema_name} final_payload for scope {scope_text}. Successful tools: "
        f"{success_text}. Failed or unavailable tools: {failed_text}. Treat this as a "
        "review coverage gap, not as field-level support."
    )
    finding = {
        "field_path": "document",
        "status": status,
        "rationale": rationale,
        "evidence_refs": evidence_refs,
    }
    unknowns = [
        (
            f"{schema_name} did not complete a final_payload before max_steps; "
            "field-level support remains only partially reviewed."
        )
    ]
    if schema_name == "DoxAtlasAuditResult":
        structured = {
            "verdict": "needs_revision",
            "revision_required": True,
            "findings": [finding],
            "evidence_refs": evidence_refs,
            "objections": [],
            "delegations": [],
            "unknowns": unknowns,
            "rationale": rationale,
        }
    else:
        structured = {
            "findings": [finding],
            "evidence_refs": evidence_refs,
            "objections": [],
            "delegations": [],
            "unknowns": unknowns,
            "rationale": rationale,
        }
    return structured, rationale, schema_name


def _unique_tool_names(results: Any) -> list[str]:
    names: list[str] = []
    for result in results:
        name = getattr(result, "tool_name", "")
        if name:
            names.append(str(name))
    return list(dict.fromkeys(names))


def _tool_result_source_note(results: list[ToolResult]) -> str:
    snippets: list[str] = []
    for result in results:
        if result.output_summary:
            snippets.append(str(result.output_summary))
            continue
        for ref in result.evidence_refs:
            if ref.summary:
                snippets.append(str(ref.summary))
                break
    if not snippets:
        return ""
    return "已取得的证据摘要：" + "；".join(snippets[:4]) + "。"


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
            (
                "Do not expose hidden chain-of-thought; use concise reasoning_summary only. "
                "All human-readable natural-language values in plan_update, synthesis_update, "
                "research_update, retain_observations, reasoning_summary, completion_reason, "
                "delegation questions/context, and final_payload must be Simplified Chinese "
                "unless quoting source text, identifiers, tickers, tool names, or enum values."
            ),
            (
                "Return one JSON object matching the ReAct action protocol. "
                "Put memory updates, plan_update, is_complete, tool_calls, delegations, and "
                "final_payload "
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
    runtime: TaskMemoryRuntime,
    tool_registry: ToolRegistry | None,
    skill_registry: SkillRegistry,
    active_context: JsonDict,
    config: ReActHarnessConfig,
) -> str:
    tool_descriptors = (
        tool_registry.describe_allowed(task.permissions) if tool_registry is not None else []
    )
    available_tools = _available_tool_payloads(task, runtime, tool_registry)
    tool_call_policy = {
        "required_tool_names": _strings(task.input_context.get("required_tool_names")),
        "available_tools_are_authoritative": True,
        "required_tool_gap_policy": (
                    "如果 required tool 无法满足，在 final_payload 中用中文明确写入 unknowns，"
                    "不得假装已取得证据。"
        ),
    }
    tool_requirements = task.input_context.get("tool_requirements", [])
    if tool_requirements:
        tool_call_policy["tool_requirements"] = tool_requirements
    doxatlas_contract_brief = _doxatlas_contract_brief(tool_descriptors)
    if doxatlas_contract_brief:
        tool_call_policy["doxatlas_contract_brief"] = doxatlas_contract_brief
    available_skills = [
        _available_skill_catalog_item(skill)
        for skill in _available_skill_definitions(task, definition, skill_registry)
    ]
    visible_context_snapshot = agent_visible_context_snapshot(context_snapshot)
    request_payload = {
            "react_protocol": {
                "max_steps": config.max_steps,
                "max_tool_calls_per_name": config.max_tool_calls_per_name,
                "max_tool_call_batches": config.max_tool_call_batches,
                "tool_call_limit_scope": (
                    "The limit applies to consecutive ReAct loops for the same tool name "
                    "inside this task node, not to the number of same-name calls inside "
                    "one loop. Multiple same-name calls in one loop are allowed."
                ),
                "response_schema": {
                    "language_rule": (
                        "所有面向用户或评估的自然语言值必须使用简体中文；"
                        "仅专有名词、ticker、工具名、enum、source 原文可保留英文。"
                    ),
                    "plan_update": ["简短中文公开进度；不要使用英文句子"],
                    **memory_action_schema(),
                    "reasoning_summary": "中文公开理由摘要，不要包含隐藏 chain-of-thought",
                    "is_complete": "boolean",
                    "completion_reason": "中文完成原因",
                    "tool_calls": [
                        {"tool_name": "registered tool name", "input": {"key": "value"}}
                    ],
                    "skill_calls": [
                        {"skill_id": "available skill id", "reason": "中文说明为何需要该技能"}
                    ],
                    "delegations": [
                        {
                            "target_agent": "agent enum value",
                            "task_type": "optional task type",
                            "question": "中文委托问题",
                            "context_summary": "中文边界上下文",
                            "required_output_schema": "optional schema",
                        }
                    ],
                    "final_payload": (
                        "完成时返回 AgentResult-compatible 结构化 payload，"
                        "内部自然语言必须中文"
                    ),
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
            "output_contract": _output_contract(task.required_output_schema, task=task),
            "available_tools": available_tools,
            "available_skills": available_skills,
            "loaded_skills": list(runtime.loaded_skills.values()),
            "task_memory": active_context,
    }
    if visible_context_snapshot is not None:
        request_payload["context_snapshot"] = visible_context_snapshot
    return json.dumps(request_payload, ensure_ascii=False, default=str)


def _available_tool_payloads(
    task: AgentTask,
    runtime: TaskMemoryRuntime,
    tool_registry: ToolRegistry | None,
) -> list[JsonDict]:
    descriptors = (
        tool_registry.describe_allowed(task.permissions) if tool_registry is not None else []
    )
    available = [_agent_visible_tool_descriptor(descriptor) for descriptor in descriptors]
    if len(runtime.observations.block_store) > 0:
        available.append(read_observation_descriptor())
    return available


def _agent_visible_tool_descriptor(descriptor: ToolDescriptor) -> dict[str, Any]:
    data = descriptor.model_dump(mode="json", exclude_none=True)
    data.pop("concurrent_safe", None)
    return data


def _doxatlas_contract_brief(descriptors: list[ToolDescriptor]) -> str | None:
    if not any(
        descriptor.name.startswith("doxa_") or descriptor.name.startswith("doxatlas.")
        for descriptor in descriptors
    ):
        return None
    return (
        "DoxAtlas uses scoped short ids: parent scope appears once, child lists use "
        "R/T/N/E/P/M/S/D/I codes. Prefer event scope run_id+narrative_code+event_code. "
        "Do not pass user_id, ticker to proposition/ignored-proposition tools, bare "
        "narrative_code to scoped tools, or DoxAgent internal event_id. If scope is missing, "
        "recover DoxAtlas run_id/event codes from a narrative report or finalize with a data gap."
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


def _parse_action(response: ModelResponse) -> JsonDict | None:
    payload: Any = response.structured
    if payload is None and response.text is not None:
        payload = _parse_json_object_from_text(response.text)
        if payload is None:
            return None
    if not isinstance(payload, dict):
        return None
    payload = _unwrap_text_encoded_action_payload(payload)
    payload = _unwrap_action_payload(payload)
    return cast(JsonDict, payload)


def _unwrap_text_encoded_action_payload(payload: JsonDict) -> JsonDict:
    for key in ("text", "output_text", "content"):
        value = payload.get(key)
        if not isinstance(value, str):
            continue
        parsed = _parse_json_object_from_text(value)
        if isinstance(parsed, dict):
            return parsed
    return payload


def _parse_json_object_from_text(text: str) -> JsonDict | None:
    stripped = text.strip()
    candidates = [stripped]
    fenced = _JSON_FENCE_RE.match(stripped)
    if fenced:
        candidates.append(fenced.group(1).strip())
    first = stripped.find("{")
    last = stripped.rfind("}")
    if first != -1 and last != -1 and last > first:
        candidates.append(stripped[first : last + 1])
    for candidate in candidates:
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return cast(JsonDict, parsed)
    decoder = json.JSONDecoder()
    for match in reversed(list(re.finditer(r"{", stripped))):
        try:
            parsed, _ = decoder.raw_decode(stripped[match.start() :])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return cast(JsonDict, parsed)
    return None


def _coerce_direct_final_action(action: JsonDict, required_output_schema: str) -> JsonDict:
    control_keys = {
        "plan_update",
        "synthesis_update",
        "research_update",
        "retain_observations",
        "retained_observation_update",
        "compaction_reasoning_summary",
        "is_complete",
        "completion_reason",
        "final_payload",
        "skill_calls",
    }
    if any(key in action for key in control_keys):
        return action
    if _looks_like_react_prompt_echo(action):
        return action
    if not _looks_like_direct_final_payload(
        action,
        required_output_schema,
    ) and _has_final_payload_schema(required_output_schema):
        return action
    return {
        "is_complete": True,
        "completion_reason": "model returned direct structured payload",
        "final_payload": action,
        "tool_calls": [],
        "skill_calls": [],
        "delegations": [],
    }


def _has_final_payload_schema(required_output_schema: str) -> bool:
    return any(
        _final_payload_schema_model(schema_name) is not None
        for schema_name in _schema_names(required_output_schema)
    )


def _final_payload_schema_model(schema_name: str) -> type[BaseModel] | None:
    model = _FINAL_PAYLOAD_SCHEMAS.get(schema_name)
    if model is not None:
        return model
    if schema_name not in _RUNTIME_FINAL_PAYLOAD_SCHEMA_NAMES:
        return None
    module = importlib.import_module("doxagent.persistent_runtime.schema")
    candidate = getattr(module, schema_name, None)
    if isinstance(candidate, type) and issubclass(candidate, BaseModel):
        return candidate
    return None


def _looks_like_react_prompt_echo(payload: JsonDict) -> bool:
    return (
        isinstance(payload.get("react_protocol"), dict)
        and isinstance(payload.get("task"), dict)
        and (
            "output_contract" in payload
            or "tool_call_policy" in payload
            or "context_snapshot" in payload
        )
    )


def _looks_like_direct_final_payload(payload: JsonDict, required_output_schema: str) -> bool:
    schema_keys = {
        "ResearchSection": {
            "text",
            "summary",
            "report",
            "analysis",
            "section_text",
        },
        "ExpectationFieldReviewResult": {
            "findings",
            "rationale",
            "overall_assessment",
            "patches_reviewed",
            "review_findings",
        },
        "DoxAtlasAuditResult": {
            "findings",
            "verdict",
            "revision_required",
            "overall_status",
            "audit_findings",
        },
        "DelegatedRetrievalResult": {
            "answer",
            "claim_verdict",
            "retrieval_summary",
            "source_refs",
            "query_log",
        },
        "ExpectationShellConstructionResult": {
            "shells",
            "expectation_shells",
            "expectations",
        },
        "ExpectationConstructionResult": {
            "proposed_patches",
            "patches",
            "expectation_patches",
            "expectation_units",
        },
        "ExpectationDetailResult": {
            "proposed_patches",
            "patches",
            "realized_facts",
            "key_variables",
            "event_monitoring_direction",
        },
        "ExpectationDetailCandidateResult": {
            "candidate",
            "expectation_unit",
            "realized_facts",
            "key_variables",
            "event_monitoring_direction",
        },
        "Document2ResolutionPlan": {
            "expectation_id",
            "decisions",
            "revised_candidate",
            "unresolved_reason",
        },
        "Document2FieldRepairResult": {
            "task_id",
            "expectation_id",
            "field_family",
            "decisions",
            "revised_candidate",
            "realized_facts",
            "key_variables",
            "event_monitoring_direction",
            "market_view",
        },
        "KnownEventsDocument": {
            "document_id",
            "document_type",
            "events",
            "known_events",
        },
        "MonitoringConfigDocument": {
            "document_id",
            "document_type",
            "monitoring_items",
            "tool_input",
        },
        "MonitoringPolicyDocument": {
            "document_id",
            "document_type",
            "policies",
            "direct_trade_rules",
            "push_to_agent_rules",
            "cache_rules",
        },
        "W1Result": {
            "is_new",
            "novelty_label",
            "matched_known_event_ids",
            "confidence",
            "reasoning",
        },
        "W2Result": {
            "matched_policy_code",
            "type",
            "reasoning",
        },
        "A2Result": {
            "is_new",
            "verification_status",
            "reasoning",
            "evidence_refs",
        },
        "O3Result": {
            "primary_action",
            "confidence",
            "side_effects",
            "trade_intent",
            "known_events_patch",
            "blackboard_target",
            "reasoning",
        },
    }
    keys = set(payload)
    return any(
        bool(keys & schema_keys.get(schema_name, set()))
        for schema_name in _schema_names(required_output_schema)
    )


def _unwrap_action_payload(payload: JsonDict) -> JsonDict:
    action_keys = {
        "plan_update",
        "synthesis_update",
        "research_update",
        "retain_observations",
        "retained_observation_update",
        "compaction_reasoning_summary",
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
    runtime: TaskMemoryRuntime | None = None,
    details: JsonDict | None = None,
) -> AgentResult:
    tool_results = tool_results or []
    delegation_results = delegation_results or []
    evidence_refs = _evidence_refs(tool_results, delegation_results)
    if runtime is not None:
        runtime.record_failure(code=code, message=message, retryable=retryable)
    return AgentResult(
        task_id=task.task_id,
        agent_name=task.agent_name,
        status=ResultStatus.FAILED,
        payload={
            "runtime": "react",
            "react_audit": runtime.persisted_audit() if runtime else {},
            "market_evidence_snapshot": (
                runtime.market_evidence_snapshot() if runtime else {}
            ),
        },
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
    if "ExpectationDetailResult" in _schema_names(required_output_schema):
        patches = payload.get("proposed_patches")
        if not isinstance(patches, list) or len(patches) != 1:
            return "ExpectationDetailResult requires exactly one proposed_patches item."
    errors: list[str] = []
    schema_checked = False
    for schema_name in _schema_names(required_output_schema):
        model = _final_payload_schema_model(schema_name)
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


def _pop_reviewer_acceptance_warnings(payload: JsonDict) -> list[JsonDict]:
    raw = payload.pop(_REVIEWER_ACCEPTANCE_WARNINGS_INTERNAL_KEY, [])
    if not isinstance(raw, list):
        return []
    return [dict(item) for item in raw if isinstance(item, dict)]


def _schema_names(required_output_schema: str) -> list[str]:
    return schema_names(required_output_schema)


def _monitoring_policy_output_contract() -> JsonDict:
    return {
        "final_payload": {
            "document_id": "doc_<id>",
            "document_type": "monitoring_policy",
            "ticker": "<ticker>",
            "created_at": "ISO-8601 timestamp",
            "policies": [
                {
                    "policy_id": "policy_<id>",
                    "policy_type": "direct_trade | escalate",
                    "scope": {
                        "expectation_unit_id": "expectation_<id>",
                        "event_type": "earnings | order | regulatory | industry | macro",
                    },
                    "trigger": {"condition": "observable message-content trigger"},
                    "confirmation": {
                        "market_confirmation": "optional non-trigger confirmation"
                    },
                    "action": {
                        "side": "long | short | exit",
                        "conviction": "low | medium | high",
                        "size_bucket": "small | normal | aggressive",
                    },
                    "risk_guard": {
                        "guardrail": "condition that blocks direct trade or escalates"
                    },
                    "strategy_note": "short runtime routing note",
                    "reasoning": "one concise reason for this policy",
                    "evidence_fields": [],
                }
            ],
            "direct_trade_rules": [],
            "push_to_agent_rules": [],
            "cache_rules": [],
            "no_action_rationale": None,
        },
        "rules": [
            "MonitoringPolicyDocument is generated by O4 and never executes trades.",
            "policy_type must be direct_trade or escalate; do not output cache policies.",
            (
                "Include both direct_trade and escalate policies, or fill doc-level "
                "no_action_rationale explaining any omitted policy type."
            ),
            (
                "direct_trade.action needs side, conviction, size_bucket; "
                "escalate.action needs send_to, question, priority."
            ),
            (
                "Trigger conditions must be message-content based, not price, volume, "
                "technical, correlation, time, source_condition, cache_label, or handling."
            ),
        ],
    }


def _output_contract(required_output_schema: str, *, task: AgentTask | None = None) -> JsonDict:
    contracts: JsonDict = {}
    for schema_name in _schema_names(required_output_schema):
        if schema_name == "ExpectationShellConstructionResult":
            contracts[schema_name] = {
                "final_payload": {
                    "shells": [
                        {
                            "expectation_id": "expectation_<id>",
                            "expectation_name": "short driver-based expectation name",
                            "direction": "bullish | bearish | neutral | risk",
                            "why_it_matters": "why this expectation belongs in the Blackboard",
                            "market_view": {
                                "text": "market narrative and thesis only",
                                "summary": "one sentence market view",
                                "evidence_refs": [],
                                "author_agent": "O1",
                                "reviewer_agents": ["A1"],
                            },
                            "evidence_refs": [],
                            "unknowns": [],
                            "rationale": "why this shell is proposed",
                        }
                    ],
                    "evidence_refs": [],
                    "delegations": [],
                    "unknowns": [],
                    "rationale": "construction rationale",
                },
                "rules": [
                    "Generate 1 to 3 differentiated expectation shells.",
                    "Do not include realized_facts, key_variables, or event_monitoring_direction.",
                    "Do not create Blackboard patches in this phase.",
                ],
            }
        elif schema_name == "ExpectationDetailResult":
            contracts[schema_name] = {
                "final_payload": {
                    "proposed_patches": [
                        {
                            "patch_id": "patch_<id>",
                            "target": {
                                "document_type": "expectation_unit",
                                "ticker": "<ticker>",
                                "expectation_id": "same as expectation_shell.expectation_id",
                                "field_path": "document",
                            },
                            "operation": "create",
                            "before": None,
                            "after": "complete ExpectationUnitDocument preserving shell I/II",
                            "rationale": "why this completed document is proposed",
                            "evidence_refs": [],
                            "author_agent": "O1",
                            "validation_status": "pending",
                        }
                    ],
                    "evidence_refs": [],
                    "delegations": [],
                    "unknowns": [],
                    "rationale": "detail completion rationale",
                },
                "rules": [
                    "Return exactly one expectation_unit create patch.",
                    (
                        "Preserve expectation_id, expectation_name, direction, "
                        "why_it_matters, and market_view from expectation_shell."
                    ),
                    (
                        "Complete realized_facts, realized_facts_summary, key_variables, "
                        "and event_monitoring_direction."
                    ),
                    (
                        "event_monitoring_direction must be an object with "
                        "known_event_notice string, positive_events list[str], and "
                        "negative_events list[str]. Do not return known_upcoming_events."
                    ),
                    (
                        "positive_events and negative_events must be specific monitorable "
                        "event triggers, not generic deployment/commercialization "
                        "placeholders and not objects."
                    ),
                    (
                        "Every realized_fact and key_variable must include evidence_refs "
                        "when available; if market price evidence is unavailable, state "
                        "the uncertainty inside price_reaction and unknowns."
                    ),
                ],
            }
        elif schema_name == "ExpectationDetailCandidateResult":
            contracts[schema_name] = {
                "final_payload": {
                    "candidate": {
                        "document_id": "doc_<id>",
                        "document_type": "expectation_unit",
                        "ticker": "<ticker>",
                        "created_at": "ISO-8601 timestamp",
                        "updated_at": None,
                        "expectation_id": "same as expectation_shell.expectation_id",
                        "expectation_name": "same as expectation_shell.expectation_name",
                        "direction": "same as expectation_shell.direction",
                        "why_it_matters": "same as expectation_shell.why_it_matters",
                        "market_view": {
                            "text": (
                                "preserve or faithfully extend "
                                "expectation_shell.market_view.text"
                            ),
                            "summary": (
                                "preserve or faithfully extend "
                                "expectation_shell.market_view.summary"
                            ),
                            "evidence_refs": [],
                            "author_agent": "O1",
                            "reviewer_agents": [],
                        },
                        "realized_facts": [
                            {
                                "event_id": "event_<id>",
                                "description": (
                                    "specific realized fact tied to this expectation"
                                ),
                                "evidence_refs": [],
                                "price_reaction": {
                                    "price_change": (
                                        "specific move or evidence gap statement"
                                    ),
                                    "price_pattern": (
                                        "specific pattern or "
                                        "unknown_due_to_missing_market_data"
                                    ),
                                    "interpretation": (
                                        "priced in, partly priced in, or evidence gap"
                                    ),
                                    "evidence_refs": [],
                                },
                            }
                        ],
                        "realized_facts_summary": (
                            "summary of known facts, priced-in evidence, and uncertainty"
                        ),
                        "key_variables": [
                            {
                                "variable_id": "variable_<id>",
                                "name": "specific variable name",
                                "current_status": "specific current status",
                                "certainty": "plain short certainty text",
                                "evidence_refs": [],
                            }
                        ],
                        "event_monitoring_direction": {
                            "known_event_notice": (
                                "known date/event note or no fixed known date"
                            ),
                            "positive_events": ["specific positive trigger"],
                            "negative_events": ["specific negative trigger"],
                        },
                    },
                    "evidence_refs": [],
                    "delegations": [],
                    "unknowns": [],
                    "rationale": "detail completion rationale",
                },
                "rules": [
                    "Return exactly one complete candidate document, not BlackboardPatch.",
                    (
                        "Do not return proposed_patches, patches, changes, path maps, "
                        "partial updates, list-wrapped candidates, or multiple candidates."
                    ),
                    (
                        "Preserve expectation_id, expectation_name, direction, "
                        "why_it_matters, and market_view from expectation_shell."
                    ),
                    (
                        "Complete realized_facts, realized_facts_summary, key_variables, "
                        "and event_monitoring_direction."
                    ),
                    (
                        "event_monitoring_direction must be an object with "
                        "known_event_notice string, positive_events list[str], and "
                        "negative_events list[str]. Do not return known_upcoming_events."
                    ),
                    (
                        "Every realized_fact and key_variable must include evidence_refs "
                        "when available; if market price evidence is unavailable, state "
                        "the uncertainty inside price_reaction and unknowns."
                    ),
                    (
                        "If market price evidence is unavailable, state the uncertainty "
                        "inside price_reaction; do not invent price numbers."
                    ),
                ],
            }
        elif schema_name == "Document2ResolutionPlan":
            contracts[schema_name] = {
                "final_payload": {
                    "expectation_id": "affected expectation id",
                    "decision": "resolved | accepted | partially_accepted | rejected | deferred",
                    "decisions": [
                        {
                            "objection_id": "must match one unresolved objection id",
                            "finding_id": None,
                            "decision": (
                                "resolved | accepted | partially_accepted | rejected | deferred"
                            ),
                            "resolution_note": (
                                "concise reason, citing compact patch fields or reviewer evidence"
                            ),
                            "changed_paths": [
                                "document.<field_path> touched or confirmed"
                            ],
                            "evidence_refs": [],
                        }
                    ],
                    "target_finding_ids": [],
                    "revised_candidate": None,
                    "evidence_requests": [],
                    "unresolved_finding_ids": [],
                    "unresolved_reason": None,
                    "rationale": "short summary of the resolution plan",
                },
                "revised_candidate_shape_when_needed": {
                    "document_id": "existing or new doc id",
                    "document_type": "expectation_unit",
                    "ticker": "<ticker>",
                    "created_at": "ISO-8601 timestamp",
                    "updated_at": None,
                    "expectation_id": "same affected expectation id",
                    "expectation_name": "same expectation name unless explicitly changed",
                    "direction": "bullish | bearish | neutral | risk",
                    "why_it_matters": "complete why-it-matters",
                    "market_view": {
                        "text": "complete market view",
                        "summary": "short summary",
                        "evidence_refs": [],
                        "author_agent": "O1",
                        "reviewer_agents": [],
                    },
                    "realized_facts": [
                        {
                            "event_id": "event_<id>",
                            "description": "complete realized fact",
                            "evidence_refs": [],
                            "price_reaction": {
                                "price_change": "specific move or evidence gap statement",
                                "price_pattern": (
                                    "specific pattern or "
                                    "unknown_due_to_missing_market_data"
                                ),
                                "interpretation": "price reaction interpretation",
                                "evidence_refs": [],
                            },
                        }
                    ],
                    "realized_facts_summary": "complete summary",
                    "key_variables": [
                        {
                            "variable_id": "variable_<id>",
                            "name": "variable name",
                            "current_status": "current status",
                            "certainty": "plain short certainty text",
                            "evidence_refs": [],
                        }
                    ],
                    "event_monitoring_direction": {
                        "known_event_notice": "known event note",
                        "positive_events": ["specific positive trigger"],
                        "negative_events": ["specific negative trigger"],
                    },
                },
                "rules": [
                    "This is a resolution-plan task, not a patch-submission task.",
                    (
                        "Do not return BlackboardPatch, proposed_patches, patches, "
                        "changes, path maps, partial updates, list-wrapped "
                        "revised_candidate, or multiple revised candidates."
                    ),
                    (
                        "Return exactly one decisions item for each "
                        "input_context.unresolved_objections item."
                    ),
                    (
                        "If a revision is needed, return revised_candidate as a complete "
                        "ExpectationUnitDocument preserving expectation identity; "
                        "otherwise set revised_candidate to null."
                    ),
                    (
                        "Only include revised_candidate when decision is accepted or "
                        "partially_accepted and the blocker requires actual content revision."
                    ),
                    (
                        "For resolved, rejected, accepted, or partially_accepted decisions, "
                        "include changed_paths or evidence_refs; do not silently close blockers."
                    ),
                    (
                        "O1's decision is advisory: transaction revalidation decides whether "
                        "blockers close or remain open."
                    ),
                ],
            }
        elif schema_name == "Document2FieldRepairResult":
            contracts[schema_name] = {
                "final_payload": {
                    "task_id": "must match input_context.field_repair_task.task_id",
                    "expectation_id": "must match input_context.field_repair_task.expectation_id",
                    "field_family": (
                        "realized_facts | key_variables | event_monitoring_direction | "
                        "market_view | market_evidence | cross_field"
                    ),
                    "decision": "resolved | accepted | partially_accepted | rejected | deferred",
                    "decisions": [
                        {
                            "objection_id": "must match one task objection id when present",
                            "finding_id": "finding id being addressed, when applicable",
                            "decision": (
                                "resolved | accepted | partially_accepted | rejected | deferred"
                            ),
                            "resolution_note": "separate concise decision record",
                            "changed_paths": ["document.<field_path>"],
                            "evidence_refs": [
                                {
                                    "evidence_id": "evidence_<id>",
                                    "source_type": "agent_output",
                                    "source_id": "source_<id>",
                                    "title": "evidence title",
                                    "summary": "evidence summary",
                                    "retrieval_metadata": {},
                                    "confidence": 0.8,
                                    "citation_scope": "field repair decision",
                                }
                            ],
                        }
                    ],
                    "target_finding_ids": ["finding_id"],
                    "realized_facts": None,
                    "key_variables": None,
                    "event_monitoring_direction": None,
                    "market_view": None,
                    "revised_candidate": None,
                    "evidence_requests": [
                        "Need primary-source evidence for the observed price reaction."
                    ],
                    "unresolved_finding_ids": ["finding_id"],
                    "unresolved_reason": None,
                    "rationale": "short rationale for this single repair task",
                },
                "field_type_requirements": {
                    "evidence_requests": (
                        "list[str] only. Never output objects like "
                        "{'question': '...', 'target_field': '...', 'reason': '...'}."
                    ),
                    "target_finding_ids": "list[str] only.",
                    "unresolved_finding_ids": "list[str] only.",
                    "decisions[].evidence_refs": (
                        "list[EvidenceRef object], not list[str]. If only an evidence id "
                        "is known, leave evidence_refs empty and use evidence_requests."
                    ),
                },
                "typed_field_examples": {
                    "realized_facts": [
                        {
                            "event_id": "event_<id>",
                            "description": "complete realized fact",
                            "price_reaction": {
                                "price_change": "specific move or evidence gap statement",
                            "price_pattern": (
                                "specific pattern or "
                                "unknown_due_to_missing_market_data"
                            ),
                                "interpretation": "price reaction interpretation",
                                "evidence_refs": [],
                            },
                            "evidence_refs": [],
                        }
                    ],
                    "key_variables": [
                        {
                            "variable_id": "variable_<id>",
                            "name": "variable name",
                            "current_status": "current status",
                            "certainty": "plain short certainty text",
                            "evidence_refs": [],
                        }
                    ],
                    "event_monitoring_direction": {
                        "known_event_notice": "known event note",
                        "positive_events": ["specific positive trigger"],
                        "negative_events": ["specific negative trigger"],
                    },
                    "market_view": {
                        "text": "complete market view",
                        "summary": "short summary",
                        "evidence_refs": [],
                        "author_agent": "O1",
                        "reviewer_agents": [],
                    },
                },
                "decision_branch_rules": {
                    "accepted_or_partially_accepted": [
                        (
                            "For single-field tasks, return exactly one complete "
                            "replacement value for the allowed typed field."
                        ),
                        "For single-field tasks, do not output revised_candidate.",
                        (
                            "For field_family=cross_field, return exactly one complete "
                            "ExpectationUnitDocument as revised_candidate."
                        ),
                        (
                            "Do not output patches, changes, path_map, JSON Patch "
                            "operations, or multiple candidates."
                        ),
                    ],
                    "resolved_rejected_or_deferred": [
                        "Do not output typed field updates.",
                        "Do not output revised_candidate.",
                        (
                            "Use decisions, changed_paths, evidence_refs, unresolved_reason, "
                            "and evidence_requests to explain the result."
                        ),
                        "For deferred, provide unresolved_reason.",
                    ],
                },
                "market_evidence_mapping": {
                    "rule": (
                        "For field_family=market_evidence, the allowed typed output "
                        "field is market_view."
                    ),
                    "valid": {
                        "field_family": "market_evidence",
                        "market_view": "<ResearchSection>",
                    },
                    "invalid": {"field_family": "market_evidence", "market_evidence": {}},
                },
                "cross_field_identity_rules": [
                    (
                        "For field_family=cross_field, revised_candidate must preserve "
                        "expectation_id, expectation_name, and direction from the current "
                        "candidate unless the task explicitly says otherwise."
                    ),
                    "The transaction layer, not O1, decides whether blockers close.",
                ],
                "rules": [
                    "Resolve exactly one input_context.field_repair_task.",
                    (
                        "For field_family other than cross_field, do not output "
                        "revised_candidate; output only the complete replacement value "
                        "for the allowed typed field."
                    ),
                    (
                        "For field_family=cross_field, output exactly one complete "
                        "ExpectationUnitDocument as revised_candidate and no typed field updates."
                    ),
                    (
                        "For field_family=market_evidence, output market_view; never "
                        "output a top-level market_evidence field."
                    ),
                    (
                        "Do not output patches, proposed_patches, changes, path_map, "
                        "JSON Patch operations, partial document fragments, or multiple candidates."
                    ),
                    (
                        "evidence_requests must be plain strings; target_finding_ids "
                        "and unresolved_finding_ids must be strings; evidence_refs "
                        "must be full EvidenceRef objects."
                    ),
                    "Do not include event_time in RealizedFact.",
                    (
                        "O1 proposes a repair; transaction revalidation decides "
                        "whether blockers close."
                    ),
                ],
            }
        elif schema_name == "ExpectationConstructionResult":
            if (
                task is not None
                and task.input_context.get("resolution_mode")
                == "field_review_objection_resolution"
            ):
                contracts[schema_name] = {
                    "final_payload": {
                        "proposed_patches": (
                            "[] unless an accepted or partially_accepted objection "
                            "requires a concrete revised expectation_unit patch"
                        ),
                        "evidence_refs": [],
                        "delegations": [],
                        "unknowns": [],
                        "rationale": "short Chinese summary of the resolution decisions",
                        "resolved_objection_ids": [],
                        "accepted_objection_ids": [],
                        "partially_accepted_objection_ids": [],
                        "rejected_objection_ids": [],
                        "objection_resolutions": [
                            {
                                "objection_id": "must match one unresolved objection id",
                                "decision": (
                                    "resolved | accepted | partially_accepted | rejected"
                                ),
                                "resolution_note": (
                                    "concise Chinese reason, citing compact patch fields "
                                    "or reviewer evidence"
                                ),
                                "changed_paths": [
                                    "document.<field_path> touched or confirmed"
                                ],
                                "evidence_refs": [],
                            }
                        ],
                    },
                    "rules": [
                        (
                            "This is an objection-resolution task, not a fresh expectation "
                            "construction task."
                        ),
                        "Do not call tools in this task; reuse input_context evidence_refs.",
                        (
                            "Return exactly one objection_resolutions item for each "
                            "input_context.unresolved_objections item."
                        ),
                        (
                            "Do not generate 2 to 3 expectation patches. Keep "
                            "proposed_patches empty unless an objection is accepted or "
                            "partially_accepted and a concrete revision is required."
                        ),
                        (
                            "If decision is accepted or partially_accepted, include a revised "
                            "proposed_patch only for the affected expectation_id, using the "
                            "compact pending_patches as the revision source."
                        ),
                        (
                            "A revised proposed_patch must put changed expectation fields under "
                            "patch.after as a partial expectation_unit object, or under "
                            "patch.changes as document path updates. Do not leave the revised "
                            "field content only as patch top-level keys."
                        ),
                        "Never return unaffected expectation patches in this resolution batch.",
                        (
                            "If decision is resolved or rejected, do not return a full patch; "
                            "use changed_paths and evidence_refs to make the closure auditable."
                        ),
                        (
                            "Each resolution must include changed_paths or evidence_refs; "
                            "do not silently close objections."
                        ),
                    ],
                }
                continue
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
                                "direction": "bullish | bearish | neutral | risk",
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
                    "objection_resolutions": [
                        {
                            "objection_id": "objection_<id>",
                            "decision": (
                                "resolved | accepted | partially_accepted | rejected"
                            ),
                            "resolution_note": "specific reason and evidence for the decision",
                            "changed_paths": [],
                            "evidence_refs": [],
                        }
                    ],
                },
                "rules": [
                    "Use proposed_patches, not expectations or expectation_units.",
                    "Generate 2 to 3 expectation_unit create patches for GenerateExpectationUnits.",
                    "Each patch.after must be a complete ExpectationUnitDocument.",
                    "target.expectation_id must exactly equal after.expectation_id.",
                    "If evidence is partial, still produce the patch and list gaps in unknowns.",
                    (
                        "When closing objections, every objection id must also appear in "
                        "objection_resolutions with a decision, note, and supporting evidence "
                        "or changed path."
                    ),
                ],
            }
        elif schema_name == "DoxAtlasAuditResult":
            contracts[schema_name] = {
                "final_payload": {
                    "verdict": "pass | pass_with_warnings | needs_revision | blocked",
                    "revision_required": False,
                    "findings": [
                        {
                            "field_path": (
                                "expectation_name | direction | market_view | realized_facts"
                            ),
                            "status": (
                                "supported | unsupported | needs_more_evidence | "
                                "contradicted | not_checked"
                            ),
                            "rationale": "short field-level audit rationale",
                            "recommended_statement": (
                                "optional corrected DoxAtlas-traceable formulation"
                            ),
                            "evidence_refs": [],
                        }
                    ],
                    "evidence_refs": [],
                    "objections": [],
                    "delegations": [],
                    "unknowns": [],
                    "rationale": "one short audit rationale",
                },
                "rules": [
                    (
                        "Do not return ResearchSection fields such as text, summary, "
                        "author_agent, or reviewer_agents."
                    ),
                    "Use findings for field-level audit results; keep rationale concise.",
                    (
                        "When disagreeing with O1, include recommended_statement or "
                        "an equivalent corrected formulation instead of only saying "
                        "the field is unsupported."
                    ),
                    (
                        "Evidence refs are helpful but optional; do not fabricate them. "
                        "Only put complete EvidenceRef objects in evidence_refs. Each "
                        "EvidenceRef must include evidence_id, source_type, source_id, "
                        "title, summary, confidence, and citation_scope. If you only "
                        "have a partial id, title, summary, source_id, or material clue, "
                        "put it in rationale or recommended_statement instead of "
                        "evidence_refs."
                    ),
                    "Use objections only for issues requiring O1 revision before promotion.",
                    "Use delegations only when A2 external retrieval is required.",
                ],
            }
        elif schema_name == "ExpectationFieldReviewResult":
            contracts[schema_name] = {
                "final_payload": {
                    "findings": [
                        {
                            "field_path": (
                                "realized_facts | key_variables.current_state | "
                                "event_monitoring_direction | market_evidence"
                            ),
                            "target_paths": [
                                "all affected field paths when this finding spans fields"
                            ],
                            "status": (
                                "supported | unsupported | needs_more_evidence | contradicted"
                            ),
                            "rationale": "short field-level review rationale",
                            "recommended_statement": (
                                "optional better or corrected formulation for the field"
                            ),
                            "evidence_refs": [],
                        }
                    ],
                    "evidence_refs": [],
                    "objections": [],
                    "delegations": [],
                    "unknowns": [],
                    "rationale": "one short review rationale",
                },
                "rules": [
                    (
                        "Do not return ticker, review_timestamp, overall_assessment, "
                        "or patches_reviewed."
                    ),
                    "Use findings for field-level reviewer output.",
                    (
                        "When disagreeing with O1, include recommended_statement or "
                        "an equivalent corrected formulation instead of only saying "
                        "the field is unsupported."
                    ),
                    (
                        "Evidence refs are helpful but optional; do not fabricate them. "
                        "Only put complete EvidenceRef objects in evidence_refs. Each "
                        "EvidenceRef must include evidence_id, source_type, source_id, "
                        "title, summary, confidence, and citation_scope. If you only "
                        "have a partial id, title, summary, source_id, or material clue, "
                        "put it in rationale or recommended_statement instead of "
                        "evidence_refs."
                    ),
                    (
                        "For every finding, identify the narrowest affected field_path. "
                        "If the issue spans multiple fields, set field_path to document "
                        "or the primary field and list every affected path in target_paths."
                    ),
                    (
                        "Use target_paths only for field addressing; do not use it to "
                        "propose edits."
                    ),
                    (
                        "Keep findings separate when they express distinct concerns, "
                        "even if they point to the same field."
                    ),
                    "Use objections only for issues that must block promotion.",
                ],
            }
        elif schema_name == "DelegatedRetrievalResult":
            contracts[schema_name] = {
                "final_payload": {
                    "answer": "concise human-readable conclusion for the requester",
                    "claim_verdict": (
                        "supported | unsupported | partially_supported | "
                        "inconclusive | unknown | not_applicable"
                    ),
                    "retrieval_summary": "short basis, key sources, and remaining caveats",
                    "evidence_refs": [],
                    "source_refs": [],
                    "confidence": 0.0,
                    "unknowns": [],
                    "query_log": ["meaningful query used"],
                    "tool_calls": [],
                    "delegation_id": None,
                    "can_complete_delegation": False,
                },
                "rules": [
                    "Return a compact search or verification conclusion, not raw result dumps.",
                    "Use claim_verdict=not_applicable for open-ended search tasks.",
                    (
                        "Use claim_verdict=inconclusive or unknown when public evidence is "
                        "insufficient."
                    ),
                    "Set can_complete_delegation=true only with enough public-source support.",
                ],
            }
        elif schema_name == "W1Result":
            contracts[schema_name] = {
                "final_payload": {
                    "is_new": True,
                    "novelty_label": (
                        "old_duplicate | known_event_recap | material_update | new_event"
                    ),
                    "matched_known_event_ids": [],
                    "confidence": "high | medium | low",
                    "reasoning": "one concise reason based on Known Events",
                },
                "rules": [
                    "Use only is_new and confidence for downstream route decisions.",
                    (
                        "is_new must be true only for material_update or new_event; "
                        "old_duplicate and known_event_recap must set is_new=false."
                    ),
                ],
            }
        elif schema_name == "W2Result":
            contracts[schema_name] = {
                "final_payload": {
                    "matched_policy_code": "policy_<id> or null",
                    "type": (
                        "Direct Trade Candidate | Escalate to Background Agent | "
                        "NULL | Irrelevant"
                    ),
                    "reasoning": "one concise policy-match reason",
                },
                "rules": [
                    "Do not output confidence.",
                    "DTC and EBA require matched_policy_code.",
                    "NULL and Irrelevant must use matched_policy_code=null.",
                    "NULL means relevant but no policy matched; it is not cache.",
                    "Irrelevant means false recall, low relevance, or low quality.",
                ],
            }
        elif schema_name == "A2Result":
            contracts[schema_name] = {
                "final_payload": {
                    "is_new": True,
                    "verification_status": (
                        "verified | likely_true | unverified | likely_false | denied"
                    ),
                    "reasoning": "one concise verification reason",
                    "evidence_refs": [],
                },
                "rules": [
                    "Return a lightweight fact verification result.",
                    "Do not create trading or archive side effects.",
                ],
            }
        elif schema_name == "O3Result":
            contracts[schema_name] = {
                "final_payload": {
                    "primary_action": (
                        "trading_record | ingest_queue | archive | objection | "
                        "objection_note"
                    ),
                    "confidence": "high | medium | low or null",
                    "side_effects": [],
                    "trade_intent": {
                        "side": "long | short | exit",
                        "conviction": "low | medium | high",
                        "size_bucket": "small | normal | aggressive",
                        "reasoning": "why this is only a trade intent",
                    },
                    "known_events_patch": {
                        "event_id": "known_event_<id>",
                        "event_time_or_window": "date/window or null",
                        "core_fact": "short durable fact",
                        "duplicate_detection_keys": [],
                    },
                    "blackboard_target": "document/field target for objection actions or null",
                    "objection_type": "objection | objection_note or null",
                    "reasoning": "one concise bounded-expert judgment",
                    "evidence_refs": [],
                },
                "rules": [
                    "O3 is a bounded expert: do not start an open-ended agent loop.",
                    "Use at most two model calls and one parallel tool-call batch.",
                    "Never call a broker or output a real order.",
                    "trading_record requires trade_intent.",
                    "objection and objection_note require blackboard_target.",
                    (
                        "If side_effects contains known_events_update, include "
                        "known_events_patch."
                    ),
                    "Low-confidence non-urgent updates should be objection_note.",
                ],
            }
        elif schema_name == "KnownEventsDocument":
            contracts[schema_name] = {
                "final_payload": {
                    "document_id": "doc_<id>",
                    "document_type": "known_events",
                    "ticker": "<ticker>",
                    "created_at": "ISO-8601 timestamp",
                    "events": [],
                },
                "rules": [
                    "Return durable known facts only; keep uncertainty in unknowns if available.",
                    "Each event requires a complete source EvidenceRef object, not a bare id.",
                    (
                        "Use actual event dates/windows when known; never substitute "
                        "the run timestamp."
                    ),
                ],
                "event_shape": {
                    "event_id": "event_<id>",
                    "event_time": None,
                    "event_window": "date or window if known",
                    "core_fact": "one atomic sourced fact",
                    "description": "short context without narrative ranking",
                    "duplicate_detection_keys": ["entity", "event_type", "date_or_window"],
                    "source": {
                        "evidence_id": "evidence_<id>",
                        "source_type": "document | tool | external | agent_output",
                        "source_id": "source identifier",
                        "title": "source title",
                        "summary": "why this source supports the event",
                        "retrieval_metadata": {},
                        "confidence": 0.8,
                        "citation_scope": "event-level citation scope",
                    },
                    "expectation_id": None,
                    "discussed_by_market": True,
                    "has_price_reaction": False,
                    "is_known_old_news": True,
                },
            }
        elif schema_name == "MonitoringConfigDocument":
            contracts[schema_name] = {
                "final_payload": {
                    "document_id": "doc_<id>",
                    "document_type": "monitoring_config",
                    "ticker": "<ticker>",
                    "created_at": "ISO-8601 timestamp",
                    "monitoring_items": [
                        {
                            "item_id": "monitor_<id>",
                            "tool_input": {
                                "ticker": "<ticker>",
                                "source_id": "benzinga_news",
                                "enabled": True,
                                "mode": "merge",
                                "reason": "one concise sentence explaining why this item exists",
                                "search_terms": [],
                            },
                            "expectation_id": "expectation_<id>",
                            "priority": "high | medium | low",
                            "trigger_condition": "specific observable trigger condition",
                            "reasoning": (
                                "one concise sentence explaining which expectation "
                                "or variable this item serves"
                            ),
                        }
                    ],
                },
                "rules": [
                    (
                        "Monitoring config must be API-shaped. tool_input may contain only "
                        "ticker, source_id, enabled, mode, reason, and the selected source's "
                        "allowed parameter fields."
                    ),
                    (
                        "Allowed source parameters: benzinga_news.search_terms, "
                        "tikhub_x_search.search_terms, tikhub_x_user_posts.usernames, "
                        "newswire_rss.rss_urls. finnhub_company_news and "
                        "stocktwits_messages are ticker-only."
                    ),
                    (
                        "Never put keywords, source_filters, extra, poll_interval_seconds, "
                        "expectation_id, priority, or trigger_condition inside tool_input."
                    ),
                ],
            }
        elif schema_name == "MonitoringConfigDocumentLegacyDisabled":
            contracts[schema_name] = {
                "final_payload": {
                    "document_id": "doc_<id>",
                    "document_type": "monitoring_config",
                    "ticker": "<ticker>",
                    "created_at": "ISO-8601 timestamp",
                    "monitoring_items": [
                        {
                            "item_id": "monitor_<id>",
                            "tool_input": {
                                "ticker": "<ticker>",
                                "source_id": "registered monitoring source_id",
                                "keywords": [],
                                "usernames": [],
                                "search_terms": [],
                                "rss_urls": [],
                                "source_filters": [],
                                "extra": {
                                    "expectation_id": "expectation_<id>",
                                    "priority": "high | medium | low",
                                    "trigger_condition": "具体、可观察的监控条件",
                                },
                                "reason": "一句话说明为什么配置这个监测项",
                                "mode": "merge",
                                "enabled": True,
                            },
                            "reasoning": "一句话说明该监测项服务的 expectation 或全局变量",
                        }
                    ],
                },
                "rules": [
                    (
                        "每个监控项必须优先输出 tool_input，"
                        "形状与 monitoring.update_ticker_config 一致。"
                    ),
                    "不要输出 poll_interval_seconds；轮询频率只能由用户修改。",
                    (
                        "如对应某个 expectation，必须在 "
                        "tool_input.extra.expectation_id 填写 expectation_id。"
                    ),
                    "reasoning 必须简短说明该项捕捉什么增量消息。",
                ],
            }
        elif schema_name == "MonitoringPolicyDocument":
            contracts[schema_name] = _monitoring_policy_output_contract()
        elif schema_name == "MonitoringPolicyDocumentLegacyDisabled":
            contracts[schema_name] = {
                "final_payload": {
                    "document_id": "doc_<id>",
                    "document_type": "monitoring_policy",
                    "ticker": "<ticker>",
                    "created_at": "ISO-8601 timestamp",
                    "policies": [
                        {
                            "policy_id": "policy_<id>",
                            "policy_type": "direct_trade | escalate",
                            "scope": {
                                "expectation_unit_id": "expectation_<id>",
                                "event_type": (
                                    "earnings | order | regulatory | industry | "
                                    "macro | competitor | supply_chain"
                                ),
                            },
                            "trigger": {"condition": "高置信度、可观察的消息触发条件"},
                            "confirmation": {
                                "market_confirmation": "价格/成交量/技术面/行业或宏观确认条件"
                            },
                            "action": {
                                "side": "long | short | exit",
                                "conviction": "low | medium | high",
                                "size_bucket": "small | normal | aggressive",
                            },
                            "risk_guard": {"guardrail": "不能生成 trade intent 或必须降级的条件"},
                            "reasoning": "一句话说明该 policy 为什么存在",
                            "evidence_fields": [],
                        }
                    ],
                    "direct_trade_rules": [],
                    "push_to_agent_rules": [],
                    "cache_rules": [],
                    "no_action_rationale": None,
                },
                "rules": [
                    (
                        "MonitoringPolicyDocument 由 O4 生成；"
                        "直接交易类只输出 trade intent，不得执行券商下单。"
                    ),
                    "policy_type 只能是 direct_trade 或 escalate；不要输出 cache policy。",
                    (
                        "必须覆盖 direct_trade、escalate 两类，"
                        "或用 no_action_rationale 解释省略原因。"
                    ),
                    (
                        "direct_trade.action 必须包含 side、conviction、size_bucket；"
                        "escalate.action 必须包含 send_to、question、priority。"
                    ),
                    (
                        "不要输出时间字段、source_condition、cache_label 或 handling；"
                        "source 可信度规则属于低参数 LLM system prompt。"
                    ),
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
        if "ExpectationShellConstructionResult" in _schema_names(required_output_schema):
            return _normalize_expectation_shell_construction_payload(
                payload,
                task=task,
                tool_results=tool_results,
                delegation_results=delegation_results,
            )
        if "ExpectationDetailResult" in _schema_names(required_output_schema):
            return _normalize_expectation_detail_payload(
                payload,
                task=task,
                tool_results=tool_results,
                delegation_results=delegation_results,
            )
        if "ExpectationDetailCandidateResult" in _schema_names(required_output_schema):
            from doxagent.workflows.document2.final_payload_adapter import (
                adapt_expectation_detail_candidate_payload,
            )

            return adapt_expectation_detail_candidate_payload(
                payload,
                task=task,
                tool_results=tool_results,
                delegation_results=delegation_results,
            )
        if "Document2ResolutionPlan" in _schema_names(required_output_schema):
            from doxagent.workflows.document2.final_payload_adapter import (
                adapt_document2_resolution_plan_payload,
            )

            return adapt_document2_resolution_plan_payload(
                payload,
                task=task,
                tool_results=tool_results,
                delegation_results=delegation_results,
            )
        if "ExpectationConstructionResult" in _schema_names(required_output_schema):
            return _normalize_expectation_construction_payload(
                payload,
                task=task,
                tool_results=tool_results,
                delegation_results=delegation_results,
            )
        if "DoxAtlasAuditResult" in _schema_names(required_output_schema):
            return _normalize_doxatlas_audit_payload(
                payload,
                task=task,
                tool_results=tool_results,
                delegation_results=delegation_results,
            )
        if "ExpectationFieldReviewResult" in _schema_names(required_output_schema):
            return _normalize_expectation_field_review_payload(
                payload,
                task=task,
                tool_results=tool_results,
                delegation_results=delegation_results,
            )
        if "DelegatedRetrievalResult" in _schema_names(required_output_schema):
            return _normalize_delegated_retrieval_payload(
                payload,
                task=task,
                tool_results=tool_results,
                delegation_results=delegation_results,
            )
        if "KnownEventsDocument" in _schema_names(required_output_schema):
            return _normalize_known_events_document_payload(
                payload,
                task=task,
                tool_results=tool_results,
                delegation_results=delegation_results,
            )
        if "MonitoringConfigDocument" in _schema_names(required_output_schema):
            return _normalize_monitoring_config_document_payload(payload, task=task)
        if "MonitoringPolicyDocument" in _schema_names(required_output_schema):
            return _normalize_monitoring_policy_document_payload(payload, task=task)
        if "O3Result" in _schema_names(required_output_schema):
            return _normalize_o3_result_payload(payload)
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


def _normalize_known_events_document_payload(
    payload: JsonDict,
    *,
    task: AgentTask,
    tool_results: list[ToolResult],
    delegation_results: list[AgentResult],
) -> JsonDict:
    fallback_evidence = _valid_evidence_ref_payloads(payload.get("evidence_refs"))
    if not fallback_evidence:
        fallback_evidence = [
            item.model_dump(mode="json")
            for item in _evidence_refs(tool_results, delegation_results)
        ] or [_agent_output_evidence_ref(task)]
    events: list[JsonDict] = []
    raw_events = payload.get("events") or payload.get("known_events") or []
    for item in raw_events if isinstance(raw_events, list) else []:
        if not isinstance(item, dict):
            continue
        source = item.get("source")
        source_ref = (
            _valid_evidence_ref_payloads([source])[0]
            if isinstance(source, dict) and _valid_evidence_ref_payloads([source])
            else fallback_evidence[0]
        )
        description = _realized_fact_description(
            item.get("description") or item.get("summary") or item
        )
        event_time = _known_event_time(item, description)
        duplicate_keys = _strings(
            item.get("duplicate_detection_keys")
            or item.get("duplicate_keys")
            or item.get("keywords")
        ) or [str(item.get("event_id") or item.get("id") or description[:80])]
        events.append(
            {
                "event_id": str(item.get("event_id") or item.get("id") or new_id("event")),
                "event_time": event_time,
                "event_window": item.get("event_window"),
                "core_fact": str(item.get("core_fact") or description),
                "description": description,
                "duplicate_detection_keys": duplicate_keys,
                "source": source_ref,
                "expectation_id": item.get("expectation_id"),
                "discussed_by_market": bool(item.get("discussed_by_market", True)),
                "has_price_reaction": bool(item.get("has_price_reaction"))
                or _known_event_has_price_reaction(description),
                "is_known_old_news": bool(item.get("is_known_old_news"))
                or _known_event_is_old_news(event_time),
            }
        )
    return {
        "document_id": str(payload.get("document_id") or new_id("doc")),
        "document_type": "known_events",
        "ticker": str(payload.get("ticker") or task.ticker),
        "created_at": _event_time(payload.get("created_at")),
        "events": events,
    }


def _normalize_o3_result_payload(payload: JsonDict) -> JsonDict:
    normalized = dict(payload)
    raw_side_effects = normalized.get("side_effects")
    side_effects: list[str] = []
    if isinstance(raw_side_effects, list):
        for item in raw_side_effects:
            if isinstance(item, str):
                cleaned = item.strip()
                if cleaned:
                    side_effects.append(cleaned)
                continue
            if not isinstance(item, dict):
                continue
            effect_type = str(
                item.get("type")
                or item.get("side_effect")
                or item.get("effect")
                or item.get("action")
                or ""
            ).strip()
            if "known_events" in effect_type or item.get("known_events_patch"):
                effect_type = "known_events_update"
                patch = (
                    item.get("known_events_patch")
                    or item.get("patch")
                    or item.get("payload")
                )
                if isinstance(patch, dict) and not isinstance(
                    normalized.get("known_events_patch"),
                    dict,
                ):
                    normalized["known_events_patch"] = patch
            if effect_type:
                side_effects.append(effect_type)
    elif isinstance(raw_side_effects, str) and raw_side_effects.strip():
        side_effects.append(raw_side_effects.strip())
    if side_effects or raw_side_effects is not None:
        normalized["side_effects"] = side_effects
    raw_evidence_refs = normalized.get("evidence_refs")
    if isinstance(raw_evidence_refs, list):
        normalized["evidence_refs"] = [
            item if isinstance(item, dict) else {"ref": str(item)}
            for item in raw_evidence_refs
            if isinstance(item, dict) or str(item).strip()
        ]
    return normalized


def _normalize_monitoring_config_document_payload(
    payload: JsonDict,
    *,
    task: AgentTask,
) -> JsonDict:
    raw_items = payload.get("monitoring_items") or payload.get("items") or []
    items: list[JsonDict] = []
    for item in raw_items if isinstance(raw_items, list) else []:
        if not isinstance(item, dict):
            continue
        trigger_condition = str(
            item.get("trigger_condition")
            or item.get("condition")
            or item.get("description")
            or item.get("reasoning")
            or "监控与 ticker 相关的信号变化。"
        )
        raw_tool_input = dict(item.get("tool_input") or {})
        reasoning = str(item.get("reasoning") or raw_tool_input.get("reason") or trigger_condition)
        tool_input = _monitoring_config_api_tool_input(
            item,
            ticker=str(payload.get("ticker") or task.ticker),
            reasoning=reasoning,
        )
        items.append(
            {
                "item_id": str(item.get("item_id") or item.get("id") or new_id("monitor")),
                "tool_input": tool_input,
                "reasoning": reasoning,
                "base_keywords": _strings(item.get("base_keywords")),
                "extra_objects": _strings(item.get("extra_objects") or item.get("objects")),
                "extra_keywords": _strings(item.get("extra_keywords") or item.get("keywords")),
                "related_entities": _strings(item.get("related_entities")),
                "expectation_id": item.get("expectation_id"),
                "priority": str(item.get("priority") or "medium"),
                "trigger_condition": trigger_condition,
            }
        )
    return {
        "document_id": str(payload.get("document_id") or new_id("doc")),
        "document_type": "monitoring_config",
        "ticker": str(payload.get("ticker") or task.ticker),
        "created_at": _event_time(payload.get("created_at")),
        "monitoring_items": items,
    }


_MONITORING_SOURCE_ALLOWED_PARAMETERS: dict[str, tuple[str, ...]] = {
    "benzinga_news": ("search_terms",),
    "finnhub_company_news": (),
    "stocktwits_messages": (),
    "tikhub_x_search": ("search_terms",),
    "tikhub_x_user_posts": ("usernames",),
    "newswire_rss": ("rss_urls",),
}


def _monitoring_config_api_tool_input(
    item: JsonDict,
    *,
    ticker: str,
    reasoning: str,
) -> JsonDict:
    raw_tool_input = dict(item.get("tool_input") or {})
    source_id = str(raw_tool_input.get("source_id") or item.get("source_id") or "").strip()
    if not source_id:
        source_id = "stocktwits_messages"
    source_id = source_id.lower()
    tool_input: JsonDict = {
        "ticker": str(raw_tool_input.get("ticker") or ticker),
        "source_id": source_id,
        "enabled": bool(raw_tool_input.get("enabled", item.get("enabled", True))),
        "mode": str(raw_tool_input.get("mode") or item.get("mode") or "merge"),
        "reason": str(raw_tool_input.get("reason") or item.get("reason") or reasoning),
    }
    for parameter_field in _MONITORING_SOURCE_ALLOWED_PARAMETERS.get(source_id, ()):
        values = _dedupe_texts(
            [
                *_strings(raw_tool_input.get(parameter_field)),
                *_strings(item.get(parameter_field)),
            ]
        )
        if values:
            tool_input[parameter_field] = values
    return tool_input


def _normalize_monitoring_policy_document_payload(
    payload: JsonDict,
    *,
    task: AgentTask,
) -> JsonDict:
    policies = _normalize_policy_rule_payloads(
        payload.get("policies"),
        default_action_type="push_to_agent",
    )
    direct_rules = _normalize_policy_rule_payloads(
        payload.get("direct_trade_rules"),
        default_action_type="direct_trade",
    )
    push_rules = _normalize_policy_rule_payloads(
        payload.get("push_to_agent_rules") or payload.get("escalate_rules") or payload.get("rules"),
        default_action_type="push_to_agent",
    )
    if not policies:
        policies = [*direct_rules, *push_rules]
    if not direct_rules:
        direct_rules = [item for item in policies if item.get("policy_type") == "direct_trade"]
    if not push_rules:
        push_rules = [item for item in policies if item.get("policy_type") == "escalate"]
    return {
        "document_id": str(payload.get("document_id") or new_id("doc")),
        "document_type": "monitoring_policy",
        "ticker": str(payload.get("ticker") or task.ticker),
        "created_at": _event_time(payload.get("created_at")),
        "policies": policies,
        "direct_trade_rules": direct_rules,
        "push_to_agent_rules": push_rules,
        "cache_rules": [],
        "no_action_rationale": payload.get("no_action_rationale")
        or payload.get("omission_rationale"),
    }


def _normalize_policy_rule_payloads(value: Any, *, default_action_type: str) -> list[JsonDict]:
    rules: list[JsonDict] = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        action_type = str(item.get("action_type") or "")
        policy_type = str(item.get("policy_type") or "")
        if not policy_type:
            resolved_action = action_type or default_action_type
            policy_type = "escalate" if resolved_action == "push_to_agent" else resolved_action
        if not action_type:
            action_type = "push_to_agent" if policy_type == "escalate" else policy_type
        trigger_condition = str(
            item.get("trigger_condition")
            or item.get("condition")
            or item.get("description")
            or item.get("trigger")
            or "监控与 ticker 相关的信号。"
        )
        policy_id = str(
            item.get("policy_id")
            or item.get("rule_id")
            or item.get("id")
            or new_id("policy")
        )
        scope = dict(item.get("scope") or {})
        if item.get("expectation_id"):
            scope.setdefault("expectation_unit_id", item.get("expectation_id"))
        action = item.get("action")
        if not isinstance(action, dict):
            action = _policy_action_payload(action, policy_type=policy_type)
        rules.append(
            {
                "policy_id": policy_id,
                "rule_id": policy_id,
                "policy_type": policy_type,
                "action_type": action_type,
                "scope": scope,
                "trigger": item.get("trigger")
                if isinstance(item.get("trigger"), dict)
                else {"condition": trigger_condition},
                "trigger_condition": trigger_condition,
                "confirmation": item.get("confirmation")
                if isinstance(item.get("confirmation"), dict)
                else {"market_confirmation": str(item.get("confirmation") or "")},
                "expectation_id": item.get("expectation_id"),
                "action": action,
                "risk_guard": item.get("risk_guard")
                if isinstance(item.get("risk_guard"), dict)
                else {"guardrail": str(item.get("risk_guard") or "不生成真实 broker order。")},
                "strategy_note": _chinese_policy_strategy_note(
                    item.get("strategy_note")
                    or item.get("rationale")
                    or item.get("note"),
                    action_type=action_type,
                ),
                "reasoning": str(
                    item.get("reasoning")
                    or item.get("strategy_note")
                    or item.get("rationale")
                    or "该 policy 服务于 Document 3 运行时动作路由。"
                ),
                "evidence_fields": _strings(
                    item.get("evidence_fields") or item.get("required_evidence_fields")
                ),
                "escalation_path": item.get("escalation_path") or item.get("route"),
            }
        )
    return rules


def _has_chinese(value: Any) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in str(value or ""))


def _chinese_policy_action(value: Any, *, action_type: str) -> str:
    text = str(value or "").strip()
    if text and _has_chinese(text):
        return text
    if action_type == "direct_trade":
        return "标记为 direct_trade 候选，交由人工或 O3 复核"
    if action_type == "push_to_agent":
        return "推送给相关研究 agent 复核信号含义"
    return "推送给相关研究 agent 复核信号含义"


def _chinese_policy_strategy_note(value: Any, *, action_type: str) -> str:
    text = str(value or "").strip()
    if text and _has_chinese(text):
        return text
    if action_type == "direct_trade":
        return "仅作为路由候选，不触发券商下单。"
    if action_type == "push_to_agent":
        return "需要 agent 复核叙事、证据与价格反应。"
    return "需要 agent 复核叙事、证据与价格反应。"


def _policy_action_payload(value: Any, *, policy_type: str) -> JsonDict:
    if isinstance(value, dict):
        return value
    text = str(value or "").strip()
    if policy_type == "direct_trade":
        return {
            "side": "long",
            "conviction": "medium",
            "size_bucket": "normal",
            "note": text or "生成 trade intent，不生成真实订单。",
        }
    if policy_type == "escalate":
        return {
            "send_to": ["O1", "O4"],
            "question": text or "请复核该消息是否改变现有 expectation unit。",
            "priority": "medium",
        }
    return {
        "send_to": ["O3"],
        "question": text or "请复核运行时消息是否需要交易记录、归档或 blackboard 修正。",
        "priority": "medium",
    }


def _event_time(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str) and value.strip():
        text = value.strip()
        try:
            datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return datetime.now(UTC).isoformat()
        return text
    return datetime.now(UTC).isoformat()


def _event_time_hint(description: str) -> str | None:
    text = str(description or "")
    match = re.search(
        r"(20\d{2})\s*[-/.年]\s*(\d{1,2})(?:\s*[-/.月]\s*(\d{1,2}))?",
        text,
    )
    if match:
        year, month, day = match.group(1), match.group(2), match.group(3) or "1"
        return f"{year}-{int(month):02d}-{int(day):02d}"
    quarter_match = re.search(r"\b([1-4])Q\s*[' ]?(20\d{2}|\d{2})\b", text, re.IGNORECASE)
    if quarter_match:
        quarter = int(quarter_match.group(1))
        year_text = quarter_match.group(2)
        year = int(year_text) if len(year_text) == 4 else 2000 + int(year_text)
        return f"{year}-{((quarter - 1) * 3 + 1):02d}-01"
    quarter_match = re.search(r"\bQ([1-4])\s*[' ]?(20\d{2}|\d{2})\b", text, re.IGNORECASE)
    if quarter_match:
        quarter = int(quarter_match.group(1))
        year_text = quarter_match.group(2)
        year = int(year_text) if len(year_text) == 4 else 2000 + int(year_text)
        return f"{year}-{((quarter - 1) * 3 + 1):02d}-01"
    quarter_match = re.search(r"\b(20\d{2})\s*Q([1-4])\b", text, re.IGNORECASE)
    if quarter_match:
        year = int(quarter_match.group(1))
        quarter = int(quarter_match.group(2))
        return f"{year}-{((quarter - 1) * 3 + 1):02d}-01"
    fy_match = re.search(r"\bF[QY]\s*([1-4])?\s*(20\d{2})\b", text, re.IGNORECASE)
    if fy_match:
        quarter = int(fy_match.group(1) or 1)
        year = int(fy_match.group(2))
        return f"{year}-{((quarter - 1) * 3 + 1):02d}-01"
    computex_match = re.search(r"\bcomputex\s*(20\d{2})\b", text, re.IGNORECASE)
    if computex_match:
        return f"{int(computex_match.group(1))}-06-01"
    year_match = re.search(r"\b(20\d{2})\b", text)
    if year_match:
        return f"{int(year_match.group(1))}-01-01"
    return None


def _known_event_time(item: JsonDict, description: str) -> str:
    raw_time = item.get("event_time")
    date_hint = item.get("date")
    text_hint = _event_time_hint(
        " ".join(str(value) for value in (date_hint, description) if value)
    )
    if text_hint and (raw_time is None or _event_time_is_generic(raw_time)):
        return _event_time(text_hint)
    if date_hint:
        return _event_time(date_hint)
    if raw_time is not None:
        return _event_time(raw_time)
    if text_hint:
        return _event_time(text_hint)
    return _event_time(None)


def _event_time_is_generic(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    return bool(re.fullmatch(r"20\d{2}(-01-01)?", value.strip()))


def _known_event_has_price_reaction(description: str) -> bool:
    text = description.lower()
    markers = (
        "股价",
        "市值",
        "估值",
        "定价",
        "价格",
        "合约价",
        "现货价",
        "上涨",
        "下跌",
        "涨",
        "跌",
        "高点",
        "ath",
        "market cap",
        "price",
        "valuation",
    )
    return any(marker in text for marker in markers)


def _known_event_is_old_news(event_time: str) -> bool:
    try:
        parsed = datetime.fromisoformat(event_time.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.date() < datetime.now(UTC).date()


def _normalize_delegated_retrieval_payload(
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
    source_refs = (
        _valid_evidence_ref_payloads(payload.get("source_refs"))
        or _valid_evidence_ref_payloads(payload.get("sources"))
        or _valid_evidence_ref_payloads(payload.get("key_sources"))
        or evidence_refs
    )
    answer = _first_text(
        payload,
        "answer",
        "conclusion",
        "final_answer",
        "verification_result",
        "result",
        "text",
        "summary",
    )
    verdict = _normalize_retrieval_verdict(payload, answer, bool(source_refs))
    if not answer:
        if verdict in {"unknown", "inconclusive"}:
            answer = "无法确认：公开搜索结果不足以支持或否定该委托事实。"
        else:
            answer = "A2 completed public-source search for the delegated request."
    retrieval_summary = _first_text(
        payload,
        "retrieval_summary",
        "basis",
        "rationale",
        "source_summary",
        "summary",
    )
    if not retrieval_summary:
        retrieval_summary = answer
    unknowns = _strings(
        payload.get("unknowns")
        or payload.get("uncertainties")
        or payload.get("limitations")
        or payload.get("gaps")
    )
    if not source_refs and not unknowns:
        unknowns = ["未检索到可靠的公开来源证据。"]
    query_log = _strings(payload.get("query_log") or payload.get("queries"))
    if not query_log:
        query_log = _query_log_from_tool_results(tool_results)
    confidence = _normalize_retrieval_confidence(payload.get("confidence"), source_refs, verdict)
    tool_calls = _valid_tool_call_payloads(payload.get("tool_calls"))
    if not tool_calls:
        tool_calls = _tool_call_payloads_from_results(tool_results)
    can_complete = _retrieval_can_complete(payload, source_refs, verdict)
    return {
        "answer": answer,
        "claim_verdict": verdict,
        "retrieval_summary": retrieval_summary,
        "evidence_refs": evidence_refs,
        "source_refs": source_refs,
        "confidence": confidence,
        "unknowns": unknowns,
        "query_log": query_log,
        "tool_calls": tool_calls,
        "delegation_id": payload.get("delegation_id") or _delegation_id_from_task(task),
        "can_complete_delegation": can_complete,
    }


def _normalize_retrieval_verdict(payload: JsonDict, answer: str, has_sources: bool) -> str:
    raw = str(
        payload.get("claim_verdict")
        or payload.get("verification_status")
        or payload.get("verdict")
        or payload.get("status")
        or ""
    ).lower()
    if raw in {"supported", "support", "confirmed", "true", "yes", "verified"}:
        return "supported"
    if raw in {"unsupported", "not_supported", "denied", "false", "contradicted", "refuted"}:
        return "unsupported"
    if raw in {"partially_supported", "partial", "mixed"}:
        return "partially_supported"
    if raw in {"inconclusive", "cannot_confirm", "unable_to_confirm"}:
        return "inconclusive"
    if raw in {"unknown", "unclear", "insufficient_evidence"}:
        return "unknown"
    if raw in {"not_applicable", "na", "n/a", "search"}:
        return "not_applicable"
    lowered = answer.lower()
    if any(token in lowered for token in ("inconclusive", "无法确认", "cannot confirm")):
        return "inconclusive"
    if any(token in lowered for token in ("not supported", "unsupported", "否定")):
        return "unsupported"
    if any(token in lowered for token in ("partially", "mixed", "部分")):
        return "partially_supported"
    if any(token in lowered for token in ("supported", "confirmed", "支持")):
        return "supported"
    return "not_applicable" if has_sources else "inconclusive"


def _normalize_retrieval_confidence(
    value: Any,
    source_refs: list[JsonDict],
    verdict: str,
) -> float:
    try:
        parsed = float(str(value))
    except (TypeError, ValueError):
        confidences = [
            float(item["confidence"])
            for item in source_refs
            if isinstance(item.get("confidence"), int | float)
        ]
        if confidences:
            parsed = max(confidences)
        elif verdict in {"unknown", "inconclusive"}:
            parsed = 0.25
        elif source_refs:
            parsed = 0.6
        else:
            parsed = 0.0
    return max(0.0, min(1.0, parsed))


def _retrieval_can_complete(payload: JsonDict, source_refs: list[JsonDict], verdict: str) -> bool:
    raw = payload.get("can_complete_delegation")
    if isinstance(raw, bool):
        return raw
    return bool(source_refs and verdict not in {"unknown", "inconclusive"})


def _delegation_id_from_task(task: AgentTask) -> str | None:
    delegation = task.input_context.get("delegation")
    if isinstance(delegation, dict):
        value = delegation.get("delegation_id")
        if isinstance(value, str) and value.strip():
            return value
    return None


def _query_log_from_tool_results(tool_results: list[ToolResult]) -> list[str]:
    queries: list[str] = []
    for result in tool_results:
        for evidence_ref in result.evidence_refs:
            metadata = evidence_ref.retrieval_metadata
            query = metadata.get("query")
            if isinstance(query, str) and query.strip():
                queries.append(f"{result.tool_name}: {query.strip()}")
                continue
            urls = metadata.get("urls")
            if isinstance(urls, list) and urls:
                queries.append(f"{result.tool_name}: {len(urls)} URL(s)")
    return queries


def _valid_tool_call_payloads(value: Any) -> list[JsonDict]:
    tool_calls: list[JsonDict] = []
    for item in _dicts(value):
        try:
            tool_calls.append(
                {
                    "tool_name": str(item.get("tool_name") or item.get("name")),
                    "status": str(item.get("status") or ResultStatus.SUCCEEDED.value),
                    "input_summary": str(
                        item.get("input_summary")
                        or item.get("input")
                        or "Tool input was summarized by A2."
                    ),
                    "output_summary": item.get("output_summary"),
                    "evidence_refs": _valid_evidence_ref_payloads(item.get("evidence_refs")),
                }
            )
        except (TypeError, ValueError):
            continue
    return tool_calls


def _tool_call_payloads_from_results(tool_results: list[ToolResult]) -> list[JsonDict]:
    return [
        {
            "tool_name": result.tool_name,
            "status": result.status.value,
            "input_summary": "Tool call executed during A2 search.",
            "output_summary": result.output_summary,
            "evidence_refs": [
                evidence_ref.model_dump(mode="json") for evidence_ref in result.evidence_refs
            ],
        }
        for result in tool_results
    ]


def _normalize_expectation_field_review_payload(
    payload: JsonDict,
    *,
    task: AgentTask,
    tool_results: list[ToolResult],
    delegation_results: list[AgentResult],
) -> JsonDict:
    if _has_forbidden_review_payload_keys(payload):
        return payload
    if "findings" in payload and not isinstance(payload.get("findings"), list):
        return payload
    reviewer_warnings: list[JsonDict] = []
    evidence_refs = _valid_evidence_ref_payloads(payload.get("evidence_refs"))
    if not evidence_refs:
        evidence_refs = [
            item.model_dump(mode="json")
            for item in _evidence_refs(tool_results, delegation_results)
        ]
    findings = _normalize_expectation_field_review_findings(
        payload.get("findings")
        or payload.get("issues")
        or payload.get("review_findings")
        or payload.get("field_findings"),
        fallback_evidence=evidence_refs,
        reviewer_warnings=reviewer_warnings,
    )
    if not findings:
        findings = _field_review_findings_from_patches(
            payload.get("patches_reviewed"),
            fallback_evidence=evidence_refs,
            reviewer_warnings=reviewer_warnings,
        )
    rationale = _first_text(
        payload,
        "rationale",
        "overall_assessment",
        "assessment",
        "summary",
        "reasoning_summary",
        "text",
    )
    if not findings and rationale:
        findings = [
            {
                "field_path": "document",
                "target_paths": ["document"],
                "status": _normalize_field_review_status(payload.get("status") or rationale),
                "rationale": rationale,
                "evidence_refs": evidence_refs,
            }
        ]
    if not rationale:
        rationale = (
            str(findings[0]["rationale"])
            if findings
            else f"{task.agent_name.value} completed expectation-field review."
        )
    normalized = {
        "findings": findings,
        "evidence_refs": evidence_refs,
        "objections": _normalize_output_objections(
            payload.get("objections") or payload.get("blocking_objections"),
            task=task,
            fallback_evidence=evidence_refs,
        ),
        "delegations": _normalize_output_delegations(payload.get("delegations"), task=task),
        "unknowns": _strings(
            payload.get("unknowns")
            or payload.get("gaps")
            or payload.get("uncertainties")
            or payload.get("open_questions")
        ),
        "rationale": rationale,
    }
    if reviewer_warnings:
        normalized[_REVIEWER_ACCEPTANCE_WARNINGS_INTERNAL_KEY] = reviewer_warnings
    return normalized


def _field_review_findings_from_patches(
    value: Any,
    *,
    fallback_evidence: list[JsonDict],
    reviewer_warnings: list[JsonDict],
) -> list[JsonDict]:
    findings: list[JsonDict] = []
    for item in _dicts(value):
        default_field = str(
            item.get("field_path")
            or item.get("expectation_id")
            or item.get("patch_id")
            or "document"
        )
        nested = (
            item.get("findings")
            or item.get("issues")
            or item.get("concerns")
            or item.get("review_findings")
            or item.get("recommendations")
        )
        nested_findings = _normalize_expectation_field_review_findings(
            nested,
            fallback_evidence=fallback_evidence,
            default_field_path=default_field,
            reviewer_warnings=reviewer_warnings,
        )
        if nested_findings:
            findings.extend(nested_findings)
            continue
        findings.extend(
            _normalize_expectation_field_review_findings(
                item,
                fallback_evidence=fallback_evidence,
                default_field_path=default_field,
                reviewer_warnings=reviewer_warnings,
            )
        )
    return findings


def _normalize_expectation_field_review_findings(
    value: Any,
    *,
    fallback_evidence: list[JsonDict],
    reviewer_warnings: list[JsonDict],
    default_field_path: str = "document",
) -> list[JsonDict]:
    if isinstance(value, list):
        raw_items = value
    elif value is None:
        raw_items = []
    else:
        raw_items = [value]
    findings: list[JsonDict] = []
    for item in raw_items:
        if isinstance(item, dict):
            rationale = _first_text(
                item,
                "rationale",
                "reason",
                "assessment",
                "overall_assessment",
                "issue",
                "finding",
                "recommendation",
                "summary",
                "description",
            )
            if not rationale:
                rationale = _render_payload_fragment(item)
            if not rationale:
                continue
            findings.append(
                {
                    "field_path": str(
                        item.get("field_path")
                        or item.get("field")
                        or item.get("path")
                        or default_field_path
                    ),
                    "target_paths": _strings(
                        item.get("target_paths") or item.get("field_paths")
                    ),
                    "status": _normalize_field_review_status(
                        item.get("status")
                        or item.get("verdict")
                        or item.get("review_status")
                        or rationale
                    ),
                    "rationale": rationale,
                    "recommended_statement": _review_recommended_statement_payload(
                        item,
                        reviewer_warnings=reviewer_warnings,
                        finding_path=str(
                            item.get("field_path")
                            or item.get("field")
                            or item.get("path")
                            or default_field_path
                        ),
                    ),
                    "evidence_refs": _review_evidence_refs_payload(
                        item,
                        fallback_evidence=fallback_evidence,
                        reviewer_warnings=reviewer_warnings,
                        finding_path=str(
                            item.get("field_path")
                            or item.get("field")
                            or item.get("path")
                            or default_field_path
                        ),
                    ),
                }
            )
        elif str(item).strip():
            text = str(item)
            findings.append(
                {
                    "field_path": default_field_path,
                    "target_paths": [default_field_path],
                    "status": _normalize_field_review_status(text),
                    "rationale": text,
                    "recommended_statement": None,
                    "evidence_refs": fallback_evidence,
                }
            )
    return findings


def _normalize_field_review_status(value: Any) -> str:
    text = str(value or "").lower()
    if any(token in text for token in ("contradict", "conflict", "inconsistent")):
        return "contradicted"
    if any(token in text for token in ("unsupported", "not supported", "false")):
        return "unsupported"
    if any(
        token in text
        for token in (
            "needs_more",
            "more evidence",
            "insufficient",
            "missing",
            "lack",
            "gap",
            "unclear",
        )
    ):
        return "needs_more_evidence"
    return "supported"


def _normalize_doxatlas_audit_payload(
    payload: JsonDict,
    *,
    task: AgentTask,
    tool_results: list[ToolResult],
    delegation_results: list[AgentResult],
) -> JsonDict:
    if _has_forbidden_review_payload_keys(payload):
        return payload
    if "findings" in payload and not isinstance(payload.get("findings"), list):
        return payload
    reviewer_warnings: list[JsonDict] = []
    evidence_refs = _valid_evidence_ref_payloads(payload.get("evidence_refs"))
    if not evidence_refs:
        evidence_refs = [
            item.model_dump(mode="json")
            for item in _evidence_refs(tool_results, delegation_results)
        ]
    findings = _normalize_doxatlas_audit_findings(
        payload.get("findings")
        or payload.get("issues")
        or payload.get("audit_findings")
        or payload.get("field_findings"),
        fallback_evidence=evidence_refs,
        reviewer_warnings=reviewer_warnings,
    )
    if not findings:
        finding = _audit_finding_from_payload(
            payload,
            fallback_evidence=evidence_refs,
            reviewer_warnings=reviewer_warnings,
        )
        if finding is not None:
            findings = [finding]
    objections = _normalize_output_objections(
        payload.get("objections"),
        task=task,
        fallback_evidence=evidence_refs,
    )
    delegations = _normalize_output_delegations(payload.get("delegations"), task=task)
    verdict = _normalize_audit_verdict(payload, findings, objections, delegations)
    revision_required = _audit_revision_required(payload, verdict, findings, objections)
    rationale = str(
        payload.get("rationale")
        or payload.get("audit_rationale")
        or payload.get("reason")
        or payload.get("summary")
        or "A1 completed DoxAtlas audit."
    )
    normalized = {
        "verdict": verdict,
        "revision_required": revision_required,
        "findings": findings,
        "evidence_refs": evidence_refs,
        "objections": objections,
        "delegations": delegations,
        "unknowns": _strings(payload.get("unknowns") or payload.get("gaps")),
        "rationale": rationale,
    }
    if reviewer_warnings:
        normalized[_REVIEWER_ACCEPTANCE_WARNINGS_INTERNAL_KEY] = reviewer_warnings
    return normalized


def _normalize_doxatlas_audit_findings(
    value: Any,
    *,
    fallback_evidence: list[JsonDict],
    reviewer_warnings: list[JsonDict],
) -> list[JsonDict]:
    raw_items: list[Any]
    if isinstance(value, list):
        raw_items = value
    elif value is None:
        raw_items = []
    else:
        raw_items = [value]
    findings: list[JsonDict] = []
    for item in raw_items:
        if isinstance(item, dict):
            rationale = str(
                item.get("rationale")
                or item.get("reason")
                or item.get("description")
                or item.get("issue")
                or item.get("finding")
                or "A1 audit finding."
            )
            findings.append(
                {
                    "field_path": str(
                        item.get("field_path")
                        or item.get("field")
                        or item.get("path")
                        or "document"
                    ),
                    "status": _normalize_audit_finding_status(
                        item.get("status") or item.get("verdict") or rationale
                    ),
                    "rationale": rationale,
                    "recommended_statement": _review_recommended_statement_payload(
                        item,
                        reviewer_warnings=reviewer_warnings,
                        finding_path=str(
                            item.get("field_path")
                            or item.get("field")
                            or item.get("path")
                            or "document"
                        ),
                    ),
                    "evidence_refs": _review_evidence_refs_payload(
                        item,
                        fallback_evidence=fallback_evidence,
                        reviewer_warnings=reviewer_warnings,
                        finding_path=str(
                            item.get("field_path")
                            or item.get("field")
                            or item.get("path")
                            or "document"
                        ),
                    ),
                }
            )
        elif str(item).strip():
            findings.append(
                {
                    "field_path": "document",
                    "status": _normalize_audit_finding_status(item),
                    "rationale": str(item),
                    "recommended_statement": None,
                    "evidence_refs": fallback_evidence,
                }
            )
    return findings


def _audit_finding_from_payload(
    payload: JsonDict,
    *,
    fallback_evidence: list[JsonDict],
    reviewer_warnings: list[JsonDict],
) -> JsonDict | None:
    text = _first_text(
        payload,
        "finding",
        "issue",
        "audit_result",
        "decision",
        "rationale",
        "summary",
        "text",
    )
    if not text:
        return None
    return {
        "field_path": str(payload.get("field_path") or payload.get("field") or "document"),
        "status": _normalize_audit_finding_status(payload.get("status") or text),
        "rationale": text,
        "recommended_statement": _review_recommended_statement_payload(
            payload,
            reviewer_warnings=reviewer_warnings,
            finding_path=str(payload.get("field_path") or payload.get("field") or "document"),
        ),
        "evidence_refs": fallback_evidence,
    }


def _normalize_audit_verdict(
    payload: JsonDict,
    findings: list[JsonDict],
    objections: list[JsonDict],
    delegations: list[JsonDict],
) -> str:
    raw = str(
        payload.get("verdict")
        or payload.get("overall_status")
        or payload.get("audit_status")
        or payload.get("status")
        or ""
    ).lower()
    if raw in {"pass", "passed", "approved", "supported", "ok"}:
        return "pass"
    if raw in {"pass_with_warnings", "warning", "warnings", "needs_more_evidence"}:
        return "pass_with_warnings"
    if raw in {"needs_revision", "revise", "revision_required", "unsupported"}:
        return "needs_revision"
    if raw in {"blocked", "block", "contradicted", "failed", "reject"}:
        return "blocked"
    statuses = {str(item.get("status")) for item in findings}
    if objections or delegations or "contradicted" in statuses:
        return "blocked"
    if "unsupported" in statuses:
        return "needs_revision"
    if "needs_more_evidence" in statuses or "not_checked" in statuses:
        return "pass_with_warnings"
    return "pass"


def _audit_revision_required(
    payload: JsonDict,
    verdict: str,
    findings: list[JsonDict],
    objections: list[JsonDict],
) -> bool:
    raw = payload.get("revision_required")
    if isinstance(raw, bool):
        return raw
    statuses = {str(item.get("status")) for item in findings}
    return bool(
        verdict in {"needs_revision", "blocked"}
        or objections
        or statuses.intersection({"unsupported", "contradicted"})
    )


def _normalize_audit_finding_status(value: Any) -> str:
    text = str(value or "").lower()
    if any(token in text for token in ("contradict", "conflict")):
        return "contradicted"
    if any(token in text for token in ("unsupported", "not support", "missing support")):
        return "unsupported"
    if any(token in text for token in ("needs_more", "more evidence", "insufficient", "unclear")):
        return "needs_more_evidence"
    if any(token in text for token in ("not_checked", "not checked", "not applicable")):
        return "not_checked"
    return "supported"


def _normalize_output_objections(
    value: Any,
    *,
    task: AgentTask,
    fallback_evidence: list[JsonDict],
) -> list[JsonDict]:
    objections: list[JsonDict] = []
    raw_items: list[Any] = value if isinstance(value, list) else []
    for item in raw_items:
        if isinstance(item, dict):
            reason = str(
                item.get("reason")
                or item.get("rationale")
                or item.get("description")
                or item.get("issue")
                or ""
            ).strip()
            if not reason:
                continue
            target = _normalize_delegation_scope(item.get("target"), task)
            target_path = item.get("target_path") or _target_path_from_payload(target)
            taxonomy = str(item.get("taxonomy") or item.get("category") or "general")
            objections.append(
                {
                    "objection_id": str(item.get("objection_id") or new_id("objection")),
                    "source_agent": str(item.get("source_agent") or task.agent_name.value),
                    "target": target,
                    "severity": _normalize_objection_severity(item.get("severity"), reason),
                    "reason": reason,
                    "evidence_refs": _valid_evidence_ref_payloads(item.get("evidence_refs"))
                    or fallback_evidence,
                    "taxonomy": taxonomy,
                    "dedupe_hash": item.get("dedupe_hash")
                    or item.get("hash")
                    or f"{target_path}|{taxonomy.lower()}|{' '.join(reason.lower().split())[:120]}",
                    "target_path": target_path,
                    "merged_objection_ids": _strings(item.get("merged_objection_ids")),
                    "status": str(item.get("status") or ObjectionStatus.OPEN.value),
                    "resolution_note": item.get("resolution_note"),
                }
            )
        elif str(item).strip():
            reason = str(item)
            target = _normalize_delegation_scope(None, task)
            target_path = _target_path_from_payload(target)
            objections.append(
                {
                    "objection_id": new_id("objection"),
                    "source_agent": task.agent_name.value,
                    "target": target,
                    "severity": _audit_objection_severity(reason),
                    "reason": reason,
                    "evidence_refs": fallback_evidence,
                    "taxonomy": "general",
                    "dedupe_hash": (
                        f"{target_path}|general|{' '.join(reason.lower().split())[:120]}"
                    ),
                    "target_path": target_path,
                    "merged_objection_ids": [],
                    "status": ObjectionStatus.OPEN.value,
                    "resolution_note": None,
                }
            )
    return objections


def _normalize_objection_severity(value: Any, reason: str) -> str:
    raw = str(value or "").strip().lower()
    if raw in {item.value for item in ObjectionSeverity}:
        return raw
    if raw in {"blocker", "blocking", "critical", "severe", "serious", "major", "material"}:
        return ObjectionSeverity.BLOCKING.value
    if raw in {"medium", "moderate", "materiality"}:
        return ObjectionSeverity.MEDIUM.value
    if raw in {"minor", "low", "small"}:
        return ObjectionSeverity.LOW.value
    if raw in {"high"}:
        return ObjectionSeverity.HIGH.value
    return _audit_objection_severity(reason)


def _target_path_from_payload(target: JsonDict) -> str:
    object_id = target.get("document_id") or target.get("expectation_id") or "default"
    return f"{target.get('document_type')}:{object_id}:{target.get('field_path')}"


def _audit_objection_severity(reason: str) -> str:
    lowered = reason.lower()
    if any(token in lowered for token in ("contradict", "false", "material", "blocking")):
        return ObjectionSeverity.BLOCKING.value
    return ObjectionSeverity.MEDIUM.value


def _first_text(payload: JsonDict, *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


_REVIEW_RECOMMENDED_STATEMENT_KEYS = (
    "recommended_statement",
    "corrected_formulation",
    "corrected_statement",
    "recommended_formulation",
)
_FORBIDDEN_REVIEW_PAYLOAD_KEYS = frozenset(
    {"patches", "proposed_patches", "changes", "path_map", "path_maps"}
)


def _has_forbidden_review_payload_keys(payload: JsonDict) -> bool:
    return any(key in payload for key in _FORBIDDEN_REVIEW_PAYLOAD_KEYS)


def _review_recommended_statement_payload(
    payload: JsonDict,
    *,
    reviewer_warnings: list[JsonDict] | None = None,
    finding_path: str = "document",
) -> Any:
    for key in _REVIEW_RECOMMENDED_STATEMENT_KEYS:
        if key not in payload:
            continue
        value = payload.get(key)
        if value is None:
            return None
        if isinstance(value, str):
            return value.strip() or None
        if reviewer_warnings is not None:
            reviewer_warnings.append(
                {
                    "issue": "invalid_recommended_statement_removed",
                    "severity": "non_fatal",
                    "finding_path": finding_path,
                    "field": key,
                    "expected": "plain string",
                    "actual_type": type(value).__name__,
                }
            )
        return None
    return None


def _review_evidence_refs_payload(
    payload: JsonDict,
    *,
    fallback_evidence: list[JsonDict],
    reviewer_warnings: list[JsonDict] | None = None,
    finding_path: str = "document",
) -> Any:
    if "evidence_refs" not in payload:
        return fallback_evidence
    raw_refs = payload.get("evidence_refs")
    if raw_refs == []:
        return []
    if not isinstance(raw_refs, list):
        if reviewer_warnings is not None:
            reviewer_warnings.append(
                {
                    "issue": "invalid_evidence_refs_removed",
                    "severity": "non_fatal",
                    "finding_path": finding_path,
                    "invalid_evidence_ref_count": 1,
                    "missing_fields": sorted(_REQUIRED_EVIDENCE_REF_FIELDS),
                    "actual_type": type(raw_refs).__name__,
                }
            )
        return []
    valid_refs, warning = _split_valid_review_evidence_refs(
        raw_refs,
        finding_path=finding_path,
    )
    if warning is not None and reviewer_warnings is not None:
        reviewer_warnings.append(warning)
    return valid_refs


def _split_valid_review_evidence_refs(
    raw_refs: list[Any],
    *,
    finding_path: str,
) -> tuple[list[JsonDict], JsonDict | None]:
    valid_refs: list[JsonDict] = []
    invalid_count = 0
    missing_fields: set[str] = set()
    invalid_types: list[str] = []
    for raw_ref in raw_refs:
        if isinstance(raw_ref, EvidenceRef):
            valid_refs.append(raw_ref.model_dump(mode="json"))
            continue
        if not isinstance(raw_ref, dict):
            invalid_count += 1
            invalid_types.append(type(raw_ref).__name__)
            missing_fields.update(_REQUIRED_EVIDENCE_REF_FIELDS)
            continue
        try:
            valid_refs.append(EvidenceRef.model_validate(raw_ref).model_dump(mode="json"))
        except ValidationError as exc:
            invalid_count += 1
            for error in exc.errors():
                if error.get("type") == "missing" and error.get("loc"):
                    missing_fields.add(str(error["loc"][-1]))
    if invalid_count == 0:
        return valid_refs, None
    warning: JsonDict = {
        "issue": "invalid_evidence_refs_removed",
        "severity": "non_fatal",
        "finding_path": finding_path,
        "invalid_evidence_ref_count": invalid_count,
        "missing_fields": sorted(missing_fields),
    }
    if invalid_types:
        warning["invalid_types"] = sorted(set(invalid_types))
    return valid_refs, warning


def _normalize_expectation_construction_payload(
    payload: JsonDict,
    *,
    task: AgentTask,
    tool_results: list[ToolResult],
    delegation_results: list[AgentResult],
    allow_global_research_fallback: bool = True,
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
    if allow_global_research_fallback and not normalized_patches:
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
        "objection_resolutions": _normalize_objection_resolutions(
            payload.get("objection_resolutions") or payload.get("resolution_decisions"),
            fallback_evidence=evidence_refs,
        ),
    }


def _normalize_objection_resolutions(
    value: Any,
    *,
    fallback_evidence: list[JsonDict],
) -> list[JsonDict]:
    decisions: list[JsonDict] = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        objection_id = item.get("objection_id")
        if not isinstance(objection_id, str) or not objection_id.strip():
            continue
        decision = str(item.get("decision") or item.get("status") or "resolved")
        if decision not in {"resolved", "accepted", "partially_accepted", "rejected"}:
            decision = "resolved"
        note = str(
            item.get("resolution_note")
            or item.get("note")
            or item.get("rationale")
            or "O1 provided a structured objection resolution."
        )
        decisions.append(
            {
                "objection_id": objection_id,
                "decision": decision,
                "resolution_note": note,
                "changed_paths": _strings(item.get("changed_paths")),
                "evidence_refs": _valid_evidence_ref_payloads(item.get("evidence_refs"))
                or fallback_evidence,
            }
        )
    return decisions


def _normalize_expectation_shell_construction_payload(
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
    shell_items = payload.get("shells")
    if not isinstance(shell_items, list):
        shell_items = payload.get("expectation_shells")
    if not isinstance(shell_items, list):
        shell_items = payload.get("expectations")
    if not isinstance(shell_items, list):
        proposed_patches = payload.get("proposed_patches")
        if not isinstance(proposed_patches, list):
            proposed_patches = payload.get("patches")
        if isinstance(proposed_patches, list):
            shell_items = [
                item.get("after")
                for item in proposed_patches
                if isinstance(item, dict) and isinstance(item.get("after"), dict)
            ]
    if not isinstance(shell_items, list):
        singular = payload.get("expectation_shell") or payload.get("expectation")
        shell_items = [singular] if isinstance(singular, dict) else []
    shells = [
        _normalize_expectation_shell_payload(item, task=task, fallback_evidence=evidence_refs)
        for item in shell_items
        if isinstance(item, dict)
    ]
    if not shells:
        seen_names = {str(item.get("expectation_name") or "").strip().lower() for item in shells}
        for fallback in _fallback_expectation_shells_from_global_research(task, payload):
            normalized = _normalize_expectation_shell_payload(
                fallback,
                task=task,
                fallback_evidence=evidence_refs,
            )
            name_key = str(normalized.get("expectation_name") or "").strip().lower()
            if name_key and name_key in seen_names:
                continue
            shells.append(normalized)
            seen_names.add(name_key)
            if shells:
                break
    if len(shells) > 3:
        shells = shells[:3]
    return {
        "shells": shells,
        "evidence_refs": evidence_refs,
        "delegations": _normalize_output_delegations(payload.get("delegations"), task=task),
        "unknowns": _strings(payload.get("unknowns")),
        "rationale": str(payload.get("rationale") or payload.get("summary") or "O1 construction."),
    }


def _normalize_expectation_shell_payload(
    payload: JsonDict,
    *,
    task: AgentTask,
    fallback_evidence: list[JsonDict],
) -> JsonDict:
    expectation_id = str(
        payload.get("expectation_id")
        or payload.get("id")
        or new_id("expectation")
    )
    name = str(
        payload.get("expectation_name")
        or payload.get("name")
        or payload.get("title")
        or expectation_id
    )
    why_it_matters = str(
        payload.get("why_it_matters")
        or payload.get("description")
        or payload.get("thesis")
        or name
    )
    market_view = payload.get("market_view")
    if not isinstance(market_view, dict):
        market_view = {
            "text": str(payload.get("market_view") or payload.get("description") or why_it_matters),
            "summary": name,
            "evidence_refs": fallback_evidence,
            "author_agent": task.agent_name.value,
            "reviewer_agents": [AgentName.A1_DOXATLAS_AUDIT.value],
        }
    else:
        market_view = {
            "text": str(
                market_view.get("text")
                or market_view.get("description")
                or why_it_matters
            ),
            "summary": str(market_view.get("summary") or name),
            "evidence_refs": _valid_evidence_ref_payloads(market_view.get("evidence_refs"))
            or fallback_evidence,
            "author_agent": str(market_view.get("author_agent") or task.agent_name.value),
            "reviewer_agents": _valid_agent_names(market_view.get("reviewer_agents"))
            or [AgentName.A1_DOXATLAS_AUDIT.value],
        }
    return {
        "expectation_id": expectation_id,
        "expectation_name": name,
        "direction": _normalize_expectation_direction(payload.get("direction") or why_it_matters),
        "why_it_matters": why_it_matters,
        "market_view": market_view,
        "evidence_refs": _valid_evidence_ref_payloads(payload.get("evidence_refs"))
        or fallback_evidence,
        "unknowns": _strings(payload.get("unknowns")),
        "rationale": str(payload.get("rationale") or why_it_matters),
    }


def _normalize_expectation_detail_payload(
    payload: JsonDict,
    *,
    task: AgentTask,
    tool_results: list[ToolResult],
    delegation_results: list[AgentResult],
) -> JsonDict:
    normalized = _normalize_expectation_construction_payload(
        payload,
        task=task,
        tool_results=tool_results,
        delegation_results=delegation_results,
        allow_global_research_fallback=False,
    )
    if not normalized["proposed_patches"]:
        shell = task.input_context.get("expectation_shell")
        if isinstance(shell, dict) and _payload_has_expectation_detail_fields(payload):
            expectation = dict(payload.get("expectation_unit") or payload)
            expectation.setdefault("expectation_id", shell.get("expectation_id"))
            expectation.setdefault("expectation_name", shell.get("expectation_name"))
            expectation.setdefault("direction", shell.get("direction"))
            expectation.setdefault("why_it_matters", shell.get("why_it_matters"))
            expectation.setdefault("market_view", shell.get("market_view"))
            normalized["proposed_patches"] = [
                _patch_from_expectation_payload(
                    expectation,
                    task=task,
                    fallback_evidence=normalized["evidence_refs"],
                )
            ]
    return _force_expectation_detail_shell_identity(normalized, task=task)


def _payload_has_expectation_detail_fields(payload: JsonDict) -> bool:
    detail_keys = {
        "expectation_unit",
        "realized_facts",
        "realized_facts_summary",
        "known_facts_summary",
        "key_variables",
        "event_monitoring_direction",
        "positive_events",
        "negative_events",
        "price_reaction",
        "market_reaction",
        "pricing_assessment",
        "pricing_status",
    }
    return bool(detail_keys & set(payload))


def _force_expectation_detail_shell_identity(payload: JsonDict, *, task: AgentTask) -> JsonDict:
    shell = task.input_context.get("expectation_shell")
    if not isinstance(shell, dict):
        return payload
    shell_id = shell.get("expectation_id")
    if not shell_id:
        return payload
    shell_fields = {
        "expectation_id": shell_id,
        "expectation_name": shell.get("expectation_name"),
        "direction": shell.get("direction"),
        "why_it_matters": shell.get("why_it_matters"),
        "market_view": shell.get("market_view"),
    }
    patches = payload.get("proposed_patches")
    if not isinstance(patches, list):
        return payload
    for patch in patches:
        if not isinstance(patch, dict):
            continue
        target = patch.get("target")
        after = patch.get("after")
        if not isinstance(target, dict) or not isinstance(after, dict):
            continue
        if target.get("document_type") != DocumentType.EXPECTATION_UNIT.value:
            continue
        target["expectation_id"] = str(shell_id)
        target["ticker"] = task.ticker
        target["document_id"] = None
        for key, value in shell_fields.items():
            if value is not None:
                after[key] = value
        after["ticker"] = task.ticker
    return payload


def _recover_expectation_detail_arrays_from_text(payload: JsonDict, *, text: str) -> JsonDict:
    patches = payload.get("proposed_patches")
    if not isinstance(patches, list) or not text:
        return payload

    recovered_facts = _extract_json_array_after_key(text, "realized_facts")
    recovered_variables = _extract_json_array_after_key(text, "key_variables")
    recovered_summary = _extract_json_string_after_key(text, "realized_facts_summary")
    if recovered_facts is None and recovered_variables is None and not recovered_summary:
        return payload

    for patch in patches:
        if not isinstance(patch, dict):
            continue
        after = patch.get("after")
        if not isinstance(after, dict):
            continue
        market_view = after.get("market_view")
        market_evidence = (
            market_view.get("evidence_refs") if isinstance(market_view, dict) else None
        )
        fallback_evidence = _merge_evidence_ref_payloads(
            payload.get("evidence_refs"),
            patch.get("evidence_refs"),
            after.get("evidence_refs"),
            market_evidence,
        )
        had_facts = _has_nonempty_list(after.get("realized_facts"))
        if not had_facts and recovered_facts:
            normalized_facts = _normalize_realized_facts(
                recovered_facts,
                fallback_evidence_refs=fallback_evidence,
            )
            if normalized_facts:
                after["realized_facts"] = normalized_facts
        if not _has_nonempty_list(after.get("key_variables")) and recovered_variables:
            normalized_variables = _normalize_variable_statuses(
                recovered_variables,
                fallback_evidence_refs=fallback_evidence,
            )
            if normalized_variables:
                after["key_variables"] = normalized_variables
        if recovered_summary and (
            not had_facts or _is_placeholder_realized_summary(after.get("realized_facts_summary"))
        ):
            after["realized_facts_summary"] = recovered_summary
    return payload


def _extract_json_array_after_key(text: str, key: str) -> list[Any] | None:
    for start in _json_value_starts_after_key(text, key):
        if start >= len(text) or text[start] != "[":
            continue
        value = _load_balanced_json_value(text, start, "[", "]")
        if isinstance(value, list):
            return value
    return None


def _extract_json_string_after_key(text: str, key: str) -> str | None:
    decoder = json.JSONDecoder()
    for start in _json_value_starts_after_key(text, key):
        if start >= len(text) or text[start] != '"':
            continue
        try:
            value, _ = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _json_value_starts_after_key(text: str, key: str) -> list[int]:
    pattern = re.compile(
        rf'(?<![A-Za-z0-9_])["\']?{re.escape(key)}["\']?\s*:',
        re.IGNORECASE,
    )
    starts: list[int] = []
    for match in pattern.finditer(text):
        index = match.end()
        while index < len(text) and text[index].isspace():
            index += 1
        starts.append(index)
    return starts


def _load_balanced_json_value(
    text: str,
    start: int,
    open_char: str,
    close_char: str,
) -> Any | None:
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == open_char:
            depth += 1
        elif char == close_char:
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : index + 1])
                except json.JSONDecodeError:
                    return None
    return None


def _has_nonempty_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value)


def _is_placeholder_realized_summary(value: Any) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return True
    placeholder_tokens = (
        "no realized facts",
        "no known facts",
        "needs downstream review",
        "downstream review",
        "needs monitoring",
        "unknown",
    )
    return any(token in text for token in placeholder_tokens)


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
            or "全局研究将里程碑执行识别为主要预期轴。"
        ),
        "description": summary,
        "realized_facts_summary": "已兑现事实需要在下游复核中补全。",
        "key_variables": [],
        "positive_events": ["已确认的部署、合作伙伴或商业化里程碑。"],
        "negative_events": ["部署延迟、融资压力或商业化证据不足。"],
    }


def _fallback_expectation_shells_from_global_research(
    task: AgentTask,
    payload: JsonDict,
) -> list[JsonDict]:
    context = task.input_context.get("global_research_context")
    if not isinstance(context, dict):
        return []
    sections = context.get("sections")
    if not isinstance(sections, dict) or not sections:
        return []
    summary = _global_research_summary_text(sections)
    if not summary:
        return []
    rationale = str(payload.get("rationale") or payload.get("summary") or "").strip()
    base_reason = rationale or "全局研究提供了可拆分为独立预期轴的市场叙事。"
    return [
        {
            "expectation_id": new_id("expectation"),
            "expectation_name": f"{task.ticker} AI/HBM demand durability",
            "direction": "bullish",
            "why_it_matters": (
                "AI 服务器和 HBM 需求是否持续，是市场对存储上行周期、收入增速和估值重估"
                f"最核心的正向预期轴。{base_reason}"
            ),
            "description": (
                "市场正在定价 MU 受益于 AI/HBM 需求扩张、DRAM/NAND 价格改善和高毛利"
                "产品占比提升；该预期需要在后续 detail 阶段用 DoxAtlas 叙事、财务和价格"
                f"证据拆解已定价与未定价部分。\n{summary}"
            ),
            "realized_facts_summary": "已兑现事实需要在下游复核中补全。",
            "key_variables": [],
            "positive_events": ["HBM 订单、AI 服务器需求、毛利率指引或高端存储价格继续改善。"],
            "negative_events": ["HBM 认证、客户拉货或 AI 基建需求低于市场预期。"],
            "unknowns": ["HBM 需求持续性和客户集中度需要 detail 阶段证据化。"],
        },
        {
            "expectation_id": new_id("expectation"),
            "expectation_name": f"{task.ticker} memory cycle and margin risk",
            "direction": "bearish",
            "why_it_matters": (
                "存储行业周期、供给扩张和高资本开支可能改变市场对 MU 毛利率、自由现金流"
                "和估值倍数的容忍度，是与正向 AI/HBM 叙事相互独立的风险预期轴。"
            ),
            "description": (
                "市场需要区分 AI/HBM 结构性需求与传统 DRAM/NAND 周期风险：若供给扩张、"
                "价格见顶、资本开支压力或宏观需求走弱，当前乐观预期可能被重新定价。"
                f"\n{summary}"
            ),
            "realized_facts_summary": "已兑现事实需要在下游复核中补全。",
            "key_variables": [],
            "positive_events": ["库存纪律、供给受限、价格续涨或自由现金流改善。"],
            "negative_events": ["DRAM/NAND 价格回落、capex 上修、库存累积或毛利率指引下修。"],
            "unknowns": ["传统存储周期与 HBM 结构性需求的分离程度需要 detail 阶段证据化。"],
        },
    ]


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
    operation = str(payload.get("operation") or PatchOperation.CREATE.value)
    after = payload.get("after")
    after_from_partial_update = False
    if after is None:
        partial_after: JsonDict = {}
        if isinstance(payload.get("changes"), dict):
            partial_after = _deep_merge_json_dicts(
                partial_after,
                _after_from_patch_changes(payload["changes"]),
            )
        partial_after = _deep_merge_json_dicts(
            partial_after,
            _after_from_flat_expectation_patch_fields(payload),
        )
        if partial_after:
            after = partial_after
            after_from_partial_update = True
    if (
        isinstance(after, dict)
        and target["document_type"] == DocumentType.EXPECTATION_UNIT.value
        and not (operation == PatchOperation.UPDATE.value and after_from_partial_update)
    ):
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
        "operation": operation,
        "before": payload.get("before"),
        "after": after,
        "rationale": str(payload.get("rationale") or "O1 expectation construction."),
        "evidence_refs": evidence_refs,
        "author_agent": str(payload.get("author_agent") or task.agent_name.value),
        "validation_status": str(
            payload.get("validation_status") or ValidationStatus.PENDING.value
        ),
    }


def _after_from_flat_expectation_patch_fields(payload: JsonDict) -> JsonDict:
    return {
        key: payload[key]
        for key in _EXPECTATION_UNIT_FLAT_PATCH_FIELDS
        if key in payload and payload[key] is not None
    }


def _after_from_patch_changes(changes: JsonDict) -> JsonDict:
    after: JsonDict = {}
    for raw_path, value in changes.items():
        path = raw_path.removeprefix("document.").strip(".")
        if not path:
            continue
        _assign_patch_change_path(after, _patch_change_path_tokens(path), value)
    return after


def _patch_change_path_tokens(path: str) -> list[str | int]:
    tokens: list[str | int] = []
    for part in [item for item in path.split(".") if item]:
        cursor = 0
        while cursor < len(part):
            bracket_index = part.find("[", cursor)
            if bracket_index == -1:
                key = part[cursor:]
                if key:
                    tokens.append(key)
                break
            key = part[cursor:bracket_index]
            if key:
                tokens.append(key)
            end_index = part.find("]", bracket_index)
            if end_index == -1:
                tokens.append(part[bracket_index:])
                break
            raw_index = part[bracket_index + 1 : end_index].strip()
            if raw_index.isdigit():
                tokens.append(int(raw_index))
            else:
                tokens.append(raw_index)
            cursor = end_index + 1
    return tokens


def _assign_patch_change_path(target: JsonDict, tokens: list[str | int], value: Any) -> None:
    if not tokens:
        return
    cursor: Any = target
    for index, token in enumerate(tokens):
        is_last = index == len(tokens) - 1
        next_token = None if is_last else tokens[index + 1]
        if isinstance(token, int):
            if not isinstance(cursor, list):
                return
            while len(cursor) <= token:
                cursor.append(None)
            if is_last:
                cursor[token] = value
                return
            if not isinstance(cursor[token], (dict, list)):
                cursor[token] = [] if isinstance(next_token, int) else {}
            cursor = cursor[token]
            continue
        if not isinstance(cursor, dict):
            return
        if is_last:
            cursor[token] = value
            return
        child = cursor.get(token)
        if not isinstance(child, (dict, list)):
            child = [] if isinstance(next_token, int) else {}
            cursor[token] = child
        cursor = child


def _deep_merge_json_dicts(base: JsonDict, overlay: JsonDict) -> JsonDict:
    merged = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_json_dicts(cast(JsonDict, merged[key]), value)
        else:
            merged[key] = value
    return merged


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
    variable_evidence_refs = _valid_evidence_ref_payloads(market_view.get("evidence_refs"))
    document_evidence_refs = _merge_evidence_ref_payloads(
        fallback_evidence,
        variable_evidence_refs,
    )
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
        "realized_facts": _normalize_realized_facts(
            payload.get("realized_facts"),
            fallback_evidence_refs=document_evidence_refs,
        ),
        "realized_facts_summary": realized_summary,
        "key_variables": _normalize_variable_statuses(
            payload.get("key_variables"),
            fallback_evidence_refs=variable_evidence_refs,
        ),
        "event_monitoring_direction": _normalize_event_monitoring_direction(payload),
    }


def _normalize_realized_facts(
    value: Any,
    *,
    fallback_evidence_refs: list[JsonDict] | None = None,
) -> list[JsonDict]:
    facts: list[JsonDict] = []
    fallback = list(fallback_evidence_refs or [])
    for item in value if isinstance(value, list) else []:
        if isinstance(item, dict):
            evidence_refs = _merge_evidence_ref_payloads(item.get("evidence_refs"), fallback)
            description_value = item.get("description")
            if isinstance(description_value, dict):
                description_source: Any = description_value
            elif any(
                item.get(key) not in (None, "")
                for key in (
                    "fact",
                    "when",
                    "why_it_matters",
                    "pricing_status",
                    "pricing_assessment",
                )
            ):
                description_source = item
            else:
                description_source = description_value or item.get("text") or item
            price_reaction = (
                item.get("price_reaction")
                or item.get("market_reaction")
                or item.get("pricing_assessment")
                or item.get("pricing_status")
            )
            facts.append(
                {
                    "event_id": str(item.get("event_id") or item.get("id") or new_id("event")),
                    "description": _realized_fact_description(description_source),
                    "price_reaction": _normalize_price_reaction(
                        price_reaction,
                        fallback_evidence_refs=evidence_refs,
                    ),
                    "evidence_refs": evidence_refs,
                }
            )
        elif str(item).strip():
            evidence_refs = list(fallback)
            facts.append(
                {
                    "event_id": new_id("event"),
                    "description": str(item),
                    "price_reaction": _normalize_price_reaction(
                        None,
                        fallback_evidence_refs=evidence_refs,
                    ),
                    "evidence_refs": evidence_refs,
                }
            )
    return facts


def _realized_fact_description(value: Any) -> str:
    if isinstance(value, dict):
        preferred_keys = (
            "fact",
            "description",
            "when",
            "why_it_matters",
            "pricing_status",
            "pricing_assessment",
        )
        parts = [
            f"{key}: {value[key]}"
            for key in preferred_keys
            if value.get(key) not in (None, "")
        ]
        if parts:
            return "; ".join(parts)
    text = str(value or "").strip()
    return text or "已确认的市场事件。"


def _normalize_price_reaction(
    value: Any,
    *,
    fallback_evidence_refs: list[JsonDict] | None = None,
) -> JsonDict:
    fallback = list(fallback_evidence_refs or [])
    if isinstance(value, dict):
        evidence_refs = _valid_evidence_ref_payloads(value.get("evidence_refs")) or fallback
        return {
            "price_change": str(
                value.get("price_change")
                or value.get("move")
                or value.get("reaction")
                or "unknown"
            ),
            "price_pattern": str(
                value.get("price_pattern")
                or value.get("pattern")
                or value.get("pricing_status")
                or "unknown"
            ),
            "interpretation": str(
                value.get("interpretation")
                or value.get("rationale")
                or value.get("description")
                or value.get("pricing_assessment")
                or "价格反应尚未建立。"
            ),
            "evidence_refs": evidence_refs,
        }
    text = str(value or "").strip()
    if text:
        return {
            "price_change": text,
            "price_pattern": "described",
            "interpretation": text,
            "evidence_refs": fallback,
        }
    return {
        "price_change": "unknown",
        "price_pattern": "unknown",
        "interpretation": "价格反应尚未建立。",
        "evidence_refs": fallback,
    }


def _merge_evidence_ref_payloads(*groups: Any) -> list[JsonDict]:
    refs: list[JsonDict] = []
    seen: set[str] = set()
    for group in groups:
        for ref in _valid_evidence_ref_payloads(group):
            key = str(
                ref.get("evidence_id")
                or f"{ref.get('source_type')}:{ref.get('source_id')}:{ref.get('title')}"
            )
            if key in seen:
                continue
            seen.add(key)
            refs.append(ref)
    return refs


def _normalize_variable_statuses(
    value: Any,
    *,
    fallback_evidence_refs: list[JsonDict] | None = None,
) -> list[JsonDict]:
    variables: list[JsonDict] = []
    fallback = list(fallback_evidence_refs or [])
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
                        or item.get("relevance")
                        or item.get("unresolved")
                        or "unknown"
                    ),
                    "certainty": str(item.get("certainty") or item.get("confidence") or "unknown"),
                    "evidence_refs": _valid_evidence_ref_payloads(item.get("evidence_refs"))
                    or fallback,
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
                value.get("known_event_notice") or "监控新的已确认事件。"
            ),
            "positive_events": _event_strings(value.get("positive_events")),
            "negative_events": _event_strings(value.get("negative_events")),
        }
    return {
        "known_event_notice": "监控新的已确认事件。",
        "positive_events": _event_strings(payload.get("positive_events")),
        "negative_events": _event_strings(payload.get("negative_events")),
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
        "summary": "供应商证据不可用，已保留模型输出溯源。",
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
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _dedupe_texts(values: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = value.strip()
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(cleaned)
    return deduped


def _event_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    for item in value:
        if isinstance(item, dict):
            parts = [
                item.get("event") or item.get("trigger") or item.get("name"),
                item.get("monitoring_signal") or item.get("monitoring"),
                item.get("impact"),
            ]
            text = "; ".join(str(part).strip() for part in parts if str(part or "").strip())
        else:
            text = str(item)
        text = " ".join(text.split())
        if text:
            normalized.append(text[:600])
    return normalized


def _valid_agent_names(value: Any) -> list[str]:
    valid = {item.value for item in AgentName}
    return [item for item in _strings(value) if item in valid]


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
    "gateway_error_to_agent_error",
    "tool_request_from_call",
]
