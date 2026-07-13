"""ReAct harness for autonomous, audited tool use inside one agent task."""

from __future__ import annotations

import asyncio
import importlib
import json
import re
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, cast

from pydantic import BaseModel, ValidationError

from doxagent.agents.config import AgentDefinition
from doxagent.agents.runtime.memory import (
    READ_OBSERVATION_TOOL_NAME,
    ContextBudgetConfig,
    InMemoryObservationArchive,
    ObservationArchive,
    TaskMemoryRuntime,
    estimated_tokens,
    maintenance_action_schema,
    measure_context_budget,
    memory_action_schema,
    passive_observation_budget,
    read_observation_descriptor,
)
from doxagent.agents.runtime.tools import ToolRegistryFunctionAdapter, tool_result_to_summary
from doxagent.annotations import TextAnnotationProcessor
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
    ResultStatus,
    new_id,
)
from doxagent.models.output_schemas import REQUIRED_OUTPUT_SCHEMA_MODELS, schema_names
from doxagent.prompts.assembler import CHINESE_OUTPUT_RULES
from doxagent.prompts.registry import PromptRegistry, default_prompt_registry
from doxagent.prompts.schema import AssembledPrompt, PromptBlockDefinition
from doxagent.skills import UnknownSkillError
from doxagent.skills.registry import SkillRegistry, default_skill_registry
from doxagent.skills.schema import SkillDefinition
from doxagent.tools import ToolDescriptor, ToolError, ToolRegistry, ToolRequest, ToolResult
from doxagent.workflow_memory import CompiledWorkflowInput

JsonDict = dict[str, Any]
DelegationHandler = Callable[[JsonDict], Awaitable[AgentResult]]

MAX_TOOL_CALLS_PER_NAME = 3
_FINAL_PAYLOAD_SCHEMAS: dict[str, type[BaseModel]] = REQUIRED_OUTPUT_SCHEMA_MODELS
_REVIEWER_ACCEPTANCE_WARNINGS_KEY = "reviewer_acceptance_warnings"
_REVIEWER_ACCEPTANCE_WARNINGS_INTERNAL_KEY = "_reviewer_acceptance_warnings"
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
_FULL_COMPACTION_PROMPT_ID = "workflow.full_compaction"


@dataclass(frozen=True)
class ReActHarnessConfig:
    max_steps: int = 5
    max_tool_calls_per_name: int = MAX_TOOL_CALLS_PER_NAME
    max_tool_call_batches: int | None = None
    model_context_window: int = 128_000
    micro_maintenance_ratio: float = 0.90
    full_compaction_ratio: float = 1.0
    max_full_compaction_retries: int = 1
    model_request_timeout_seconds: float | None = None
    tool_call_timeout_seconds: float | None = 180.0

    def budget_config(self) -> ContextBudgetConfig:
        return ContextBudgetConfig(
            model_context_window=self.model_context_window,
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
        prompt_registry: PromptRegistry | None = None,
        config: ReActHarnessConfig | None = None,
        annotation_processor: TextAnnotationProcessor | None = None,
        observation_archive: ObservationArchive | None = None,
    ) -> None:
        self.model_gateway = model_gateway
        self.tool_registry = tool_registry
        self.skill_registry = skill_registry or default_skill_registry()
        self.prompt_registry = prompt_registry or default_prompt_registry()
        full_compaction_prompt = self.prompt_registry.get(_FULL_COMPACTION_PROMPT_ID)
        if not isinstance(full_compaction_prompt, PromptBlockDefinition):
            raise ValueError(
                f"{_FULL_COMPACTION_PROMPT_ID} must be a prompt block definition."
            )
        self.full_compaction_system_prompt = full_compaction_prompt.body
        self.provider = provider
        self.model = model
        self.tool_mode = tool_mode
        self.config = config or ReActHarnessConfig()
        self.annotation_processor = annotation_processor or TextAnnotationProcessor()
        self.observation_archive = observation_archive or InMemoryObservationArchive()

    async def run(
        self,
        *,
        task: AgentTask,
        definition: AgentDefinition,
        assembled_prompt: AssembledPrompt,
        context_snapshot: CompiledWorkflowInput,
        metadata: dict[str, str],
        delegate: DelegationHandler,
    ) -> AgentResult:
        runtime = TaskMemoryRuntime(task)
        runtime.event_log.append(
            "workflow_memory_compiled",
            context_snapshot.audit.model_dump(mode="json"),
        )
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
            runtime.record_action(
                step,
                action,
                reasoning_content=response.reasoning_content,
            )
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
        context_snapshot: CompiledWorkflowInput,
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
        context_snapshot: CompiledWorkflowInput,
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

    def _budgeted_active_context(
        self,
        *,
        task: AgentTask,
        definition: AgentDefinition,
        assembled_prompt: AssembledPrompt,
        context_snapshot: CompiledWorkflowInput,
        runtime: TaskMemoryRuntime,
        system_prompt: str,
        available_tools: list[JsonDict],
        micro: bool,
        mode: str,
    ) -> tuple[JsonDict, str, JsonDict]:
        base_context = runtime.active_context(micro=micro, include_passive=False)
        base_user_prompt = _react_user_prompt(
            task=task,
            definition=definition,
            assembled_prompt=assembled_prompt,
            context_snapshot=context_snapshot,
            runtime=runtime,
            tool_registry=self.tool_registry,
            skill_registry=self.skill_registry,
            active_context=base_context,
            config=self.config,
        )
        base_report = measure_context_budget(
            system_prompt=system_prompt,
            user_prompt=base_user_prompt,
            active_context=base_context,
            available_tools=available_tools,
            config=self.config.budget_config(),
            mode=f"{mode}_without_passive",
        )
        other_input_tokens = int(base_report["projected_input_tokens"])
        passive_budget = passive_observation_budget(other_input_tokens)

        while True:
            runtime.set_passive_budget_tokens(passive_budget)
            active_context = runtime.active_context(micro=micro)
            user_prompt = _react_user_prompt(
                task=task,
                definition=definition,
                assembled_prompt=assembled_prompt,
                context_snapshot=context_snapshot,
                runtime=runtime,
                tool_registry=self.tool_registry,
                skill_registry=self.skill_registry,
                active_context=active_context,
                config=self.config,
            )
            report = measure_context_budget(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                active_context=active_context,
                available_tools=available_tools,
                config=self.config.budget_config(),
                mode=mode,
            )
            projected = int(report["projected_input_tokens"])
            passive_loaded = active_context.get("passive_observation_carryover", [])
            if (
                not passive_loaded
                or other_input_tokens >= 96_000
                or projected <= 96_000
                or passive_budget <= 0
            ):
                break
            loaded_token_total = sum(estimated_tokens(block) for block in passive_loaded)
            reduced_budget = max(
                0,
                loaded_token_total - (projected - 96_000) - 1,
            )
            if reduced_budget >= passive_budget:
                break
            passive_budget = reduced_budget

        report.update(
            {
                "other_input_tokens": other_input_tokens,
                "passive_budget_tokens": passive_budget,
                "passive_loaded_block_count": len(
                    active_context.get("passive_observation_carryover", [])
                ),
                "passive_context_ceiling_tokens": 96_000,
            }
        )
        return active_context, user_prompt, report

    async def _prepare_active_context(
        self,
        *,
        task: AgentTask,
        definition: AgentDefinition,
        assembled_prompt: AssembledPrompt,
        context_snapshot: CompiledWorkflowInput,
        runtime: TaskMemoryRuntime,
        metadata: dict[str, str],
        model_audits: list[JsonDict],
    ) -> bool:
        system_prompt = _react_system_prompt(assembled_prompt.instructions)
        available_tools = _available_tool_payloads(task, runtime, self.tool_registry)
        normal_context, _, report = self._budgeted_active_context(
            task=task,
            definition=definition,
            assembled_prompt=assembled_prompt,
            context_snapshot=context_snapshot,
            runtime=runtime,
            system_prompt=system_prompt,
            available_tools=available_tools,
            micro=False,
            mode="normal",
        )
        runtime.record_context_budget(report)
        if not bool(report["over_micro_threshold"]):
            return False

        micro_context, _, micro_report = self._budgeted_active_context(
            task=task,
            definition=definition,
            assembled_prompt=assembled_prompt,
            context_snapshot=context_snapshot,
            runtime=runtime,
            system_prompt=system_prompt,
            available_tools=available_tools,
            micro=True,
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
                    messages=self._full_compaction_messages(
                        task=task,
                        micro_report=micro_report,
                        micro_context=micro_context,
                    ),
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
            micro_context, _, micro_report = self._budgeted_active_context(
                task=task,
                definition=definition,
                assembled_prompt=assembled_prompt,
                context_snapshot=context_snapshot,
                runtime=runtime,
                system_prompt=system_prompt,
                available_tools=available_tools,
                micro=True,
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
            micro_context, _, micro_report = self._budgeted_active_context(
                task=task,
                definition=definition,
                assembled_prompt=assembled_prompt,
                context_snapshot=context_snapshot,
                runtime=runtime,
                system_prompt=system_prompt,
                available_tools=available_tools,
                micro=True,
                mode="safe_fallback",
            )
            runtime.record_context_budget(micro_report)
        return True

    def _full_compaction_messages(
        self,
        *,
        task: AgentTask,
        micro_report: JsonDict,
        micro_context: JsonDict,
    ) -> list[ModelMessage]:
        return [
            ModelMessage(
                role=MessageRole.SYSTEM,
                content=self.full_compaction_system_prompt,
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
        ]

    async def _maybe_run_pre_final_challenge(
        self,
        *,
        step: int,
        task: AgentTask,
        definition: AgentDefinition,
        assembled_prompt: AssembledPrompt,
        context_snapshot: CompiledWorkflowInput,
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
                                "task_contract": context_snapshot.task_contract.model_dump(
                                    mode="json"
                                ),
                                "workflow_memory": (
                                    context_snapshot.workflow_memory.model_view()
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
        runtime.record_action(
            step,
            challenged_action,
            reasoning_content=response.reasoning_content,
        )
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
        context_snapshot: CompiledWorkflowInput,
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
        runtime.reload_final_observations()
        runtime.record_final(structured, completion_reason)
        try:
            self.observation_archive.save_task(
                run_id=task.run_metadata.run_id,
                task_id=task.task_id,
                observations=runtime.observations,
            )
        except Exception as exc:
            runtime.add_warning(
                f"observation_archive_failed:{type(exc).__name__}:{exc}",
                source="observation_archive",
            )
        market_evidence_snapshot = runtime.market_evidence_snapshot()
        result = AgentResult(
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
                "context_assembly_audit": context_snapshot.audit.model_dump(mode="json"),
            },
            tool_calls=[tool_result_to_summary(item) for item in successful_tool_results],
        )
        annotation_batch = self.annotation_processor.process(
            run_id=task.run_metadata.run_id,
            task_id=task.task_id,
            result_id=new_id("result"),
            payload=result.payload,
            aliases=runtime.observations.aliases,
        )
        annotated_payload = dict(annotation_batch.plain_payload)
        annotated_payload["text_annotations"] = {
            "processed_texts": [
                item.model_dump(mode="json")
                for item in annotation_batch.processed_texts
                if item.raw_tagged_text != item.plain_text
            ],
            "citations": [item.model_dump(mode="json") for item in annotation_batch.citations],
            "times": [item.model_dump(mode="json") for item in annotation_batch.times],
            "warnings": annotation_batch.warnings,
            "metrics": annotation_batch.metrics.model_dump(mode="json"),
        }
        return result.model_copy(update={"payload": annotated_payload}, deep=True)


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
        if result.status is ResultStatus.SUCCEEDED
    ]
    if not successful:
        return None

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
            "objections": [],
            "delegations": [],
            "unknowns": unknowns,
            "rationale": rationale,
        }
    else:
        structured = {
            "findings": [finding],
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
        for ref in []:
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
    context_snapshot: CompiledWorkflowInput,
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
            "task_contract": context_snapshot.task_contract.model_dump(mode="json"),
            "tool_call_policy": tool_call_policy,
            "output_contract": _output_contract(task.required_output_schema, task=task),
            "available_tools": available_tools,
            "available_skills": available_skills,
            "loaded_skills": list(runtime.loaded_skills.values()),
            "workflow_memory": context_snapshot.workflow_memory.model_view(),
            "task_memory": active_context,
    }
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
        and isinstance(payload.get("task_contract"), dict)
        and (
            "output_contract" in payload
            or "tool_call_policy" in payload
            or "workflow_memory" in payload
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
        tool_calls=[tool_result_to_summary(result) for result in tool_results],
        error=AgentError(code=code, message=message, retryable=retryable, details=details or {}),
    )


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
    """Expose the canonical schema without legacy provenance-specific examples."""

    schemas: JsonDict = {}
    for schema_name in _schema_names(required_output_schema):
        model = _final_payload_schema_model(schema_name)
        if model is not None:
            schemas[schema_name] = model.model_json_schema()
    return {
        "required_output_schema": required_output_schema,
        "json_schemas": schemas,
        "rules": [
            (
                "Return natural-language or Markdown business content inside the "
                "declared JSON contract."
            ),
            "Do not invent source identifiers or internal Observation Block identifiers.",
            "Annotation failures never change the requested business result or workflow status.",
        ],
    }


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
    """Return model output without synthesizing or repairing business content.

    The canonical Pydantic output schema is the only repair/validation boundary.
    Annotation parsing runs later and is intentionally independent of the
    requested workflow document type.
    """
    del task, required_output_schema, tool_results, delegation_results
    return dict(payload)


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
