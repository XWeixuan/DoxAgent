"""ModelGateway-backed MAF AgentRunner implementation."""

import asyncio
import json
from dataclasses import replace
from typing import Any, cast

from doxagent.agents.config import AgentDefinition, AgentRegistry, default_agent_registry
from doxagent.agents.runtime.chat_client import ModelGatewayChatClient
from doxagent.agents.runtime.factory import MafAgentFactory
from doxagent.agents.runtime.react import ReActAgentHarness, ReActHarnessConfig
from doxagent.agents.runtime.tools import (
    ToolMode,
    ToolRegistryFunctionAdapter,
    has_required_tool_failure,
    requested_tool_calls,
    resolve_tool_registry,
    tool_result_to_summary,
)
from doxagent.context import ContextBuilder
from doxagent.gateway import MockModelClient, ModelGateway, ProviderName, ResponseFormat
from doxagent.models import (
    AgentError,
    AgentName,
    AgentResult,
    AgentTask,
    ResultStatus,
    TaskType,
    new_id,
)
from doxagent.prompts import PromptAssembler, PromptInjector
from doxagent.prompts.schema import AssembledPrompt
from doxagent.skills.injection import SkillInjector
from doxagent.tools import ToolRegistry, ToolResult


class ModelGatewayAgentRunner:
    """Run DoxAgent tasks through a MAF Agent backed by ModelGateway."""

    def __init__(
        self,
        *,
        registry: AgentRegistry | None = None,
        prompt_injector: PromptInjector | None = None,
        prompt_assembler: PromptAssembler | None = None,
        skill_injector: SkillInjector | None = None,
        model_gateway: ModelGateway | None = None,
        context_builder: ContextBuilder | None = None,
        tool_registry: ToolRegistry | None = None,
        default_provider: ProviderName = ProviderName.MOCK,
        default_model: str = "mock-model",
        tool_mode: ToolMode = "mock",
        agent_factory: MafAgentFactory | None = None,
        react_config: ReActHarnessConfig | None = None,
        model_timeout_seconds: float | None = None,
        max_delegation_depth: int = 1,
    ) -> None:
        self.registry = registry or default_agent_registry()
        self.prompt_injector = prompt_injector or PromptInjector()
        self.prompt_assembler = prompt_assembler or PromptAssembler()
        self.skill_injector = skill_injector or SkillInjector()
        self.model_gateway = model_gateway or ModelGateway(MockModelClient(structured={}))
        self.context_builder = context_builder
        self.tool_registry = resolve_tool_registry(tool_mode, tool_registry)
        self.default_provider = default_provider
        self.default_model = default_model
        self.tool_mode = tool_mode
        self.agent_factory = agent_factory or MafAgentFactory()
        self.react_config = react_config or ReActHarnessConfig()
        self.model_timeout_seconds = model_timeout_seconds
        self.max_delegation_depth = max_delegation_depth

    def run(self, task: AgentTask) -> AgentResult:
        try:
            return asyncio.run(self.async_run(task))
        except RuntimeError as exc:
            return AgentResult(
                task_id=task.task_id,
                agent_name=task.agent_name,
                status=ResultStatus.FAILED,
                error=AgentError(
                    code="maf_runtime_error",
                    message=str(exc),
                    retryable=False,
                ),
            )

    async def async_run(self, task: AgentTask) -> AgentResult:
        definition = self.registry.get(task.agent_name)
        if definition.task_types and task.task_type not in definition.task_types:
            return self._failed(task, "task_type_not_allowed", "Agent cannot run this task type.")
        task = self.prompt_injector.inject(task, definition)
        task = self.skill_injector.inject(task, definition)
        context_snapshot = (
            self.context_builder.build(task, task.run_metadata.run_id)
            if self.context_builder is not None
            else None
        )
        if task.prompt_bundle is None:
            return self._failed(
                task,
                "missing_prompt_bundle",
                "Prompt injection produced no bundle.",
            )

        assembled_prompt = self.prompt_assembler.assemble(
            task,
            definition,
            task.prompt_bundle,
            context_snapshot,
            [],
        )
        mode = self._execution_mode(task, definition)
        if mode == "react":
            harness = ReActAgentHarness(
                model_gateway=self.model_gateway,
                tool_registry=self.tool_registry,
                provider=self.default_provider,
                model=self.default_model,
                tool_mode=self.tool_mode,
                config=self._react_config_for_task(task),
            )
            return await harness.run(
                task=task,
                definition=definition,
                assembled_prompt=assembled_prompt,
                context_snapshot=context_snapshot,
                metadata=self._metadata(task),
                delegate=lambda payload: self._run_delegation(task, payload),
            )
        if mode == "single_shot":
            return await self._async_run_model_once(
                task,
                definition,
                context_snapshot,
                assembled_prompt,
                run_requested_tools=False,
            )
        if mode == "caller_planned_tools":
            return await self._async_run_model_once(
                task,
                definition,
                context_snapshot,
                assembled_prompt,
                run_requested_tools=True,
            )
        return self._failed(
            task,
            "invalid_execution_mode",
            f"Unsupported agent execution mode: {mode}",
        )

    async def _async_run_model_once(
        self,
        task: AgentTask,
        definition: AgentDefinition,
        context_snapshot: Any | None,
        assembled_prompt: AssembledPrompt,
        *,
        run_requested_tools: bool,
    ) -> AgentResult:
        tool_results = self._run_requested_tools(task) if run_requested_tools else []
        tool_calls = [tool_result_to_summary(result) for result in tool_results]
        if run_requested_tools and task.prompt_bundle is not None:
            assembled_prompt = self.prompt_assembler.assemble(
                task,
                definition,
                task.prompt_bundle,
                context_snapshot,
                tool_results,
            )
        if has_required_tool_failure(task, tool_results):
            return self._failed(
                task,
                "required_tool_failed",
                "A required runtime tool call failed.",
                tool_calls=tool_calls,
                details={
                    "tool_results": [
                        result.model_dump(mode="json") for result in tool_results
                    ],
                },
            )

        chat_client = ModelGatewayChatClient(
            self.model_gateway,
            provider=self.default_provider,
            model=self.default_model,
            response_format=ResponseFormat.JSON,
            metadata_builder=lambda _: self._metadata(task),
        )
        agent = self.agent_factory.create(
            definition,
            chat_client,
            instructions=assembled_prompt.instructions,
            tools=[],
        )
        try:
            options: dict[str, Any] = {
                "model": self.default_model,
                "temperature": 0.2,
            }
            max_tokens = _single_shot_max_tokens(task)
            if max_tokens is not None:
                options["max_tokens"] = max_tokens
            timeout_seconds = _single_shot_timeout_seconds(task)
            if self.model_timeout_seconds is not None:
                timeout_seconds = (
                    min(timeout_seconds, self.model_timeout_seconds)
                    if timeout_seconds is not None
                    else self.model_timeout_seconds
                )
            if timeout_seconds is not None:
                options["timeout_seconds"] = timeout_seconds
            response = await agent.run(
                assembled_prompt.user_prompt,
                options=cast(Any, options),
            )
        except Exception as exc:
            return self._failed(
                task,
                "maf_execution_error",
                str(exc),
                tool_calls=tool_calls,
            )

        model_response = chat_client.last_model_response
        if model_response is None:
            return self._failed(task, "missing_model_response", "MAF 未返回模型响应。")
        if model_response.error is not None:
            return self._failed(
                task,
                "model_gateway_error",
                model_response.error.message,
                retryable=model_response.error.retryable,
                tool_calls=tool_calls,
                details={"gateway_error": model_response.error.model_dump(mode="json")},
            )

        structured = self._structured_payload(model_response.structured, str(response))
        if structured is None:
            return self._failed(
                task,
                "invalid_structured_output",
                "Model response could not be parsed as a JSON object.",
                tool_calls=tool_calls,
                details={"text": str(response)},
            )
        return AgentResult(
            task_id=task.task_id,
            agent_name=task.agent_name,
            status=ResultStatus.SUCCEEDED,
            payload={
                "runtime": "maf",
                "execution_mode": "caller_planned_tools"
                if run_requested_tools
                else "single_shot",
                "structured": structured,
                "text": str(response),
                "model_audit": model_response.audit.model_dump(mode="json"),
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
                "context_snapshot": context_snapshot.model_dump(mode="json")
                if context_snapshot is not None
                else None,
            },
            evidence_refs=[
                evidence
                for result in tool_results
                for evidence in result.evidence_refs
            ],
            tool_calls=tool_calls,
        )

    async def _run_delegation(self, parent_task: AgentTask, payload: dict[str, Any]) -> AgentResult:
        if not parent_task.permissions.can_delegate:
            return self._failed(
                parent_task,
                "delegation_not_allowed",
                "Agent permissions do not allow delegation.",
            )
        current_depth = int(parent_task.input_context.get("_react_delegation_depth") or 0)
        if current_depth >= self.max_delegation_depth:
            return self._failed(
                parent_task,
                "delegation_depth_exceeded",
                "ReAct delegation depth limit was reached.",
            )
        try:
            target_agent = AgentName(str(payload["target_agent"]))
            definition = self.registry.get(target_agent)
        except Exception as exc:
            return self._failed(
                parent_task,
                "invalid_delegation_target",
                str(exc),
            )
        task_type = parent_task.task_type
        if payload.get("task_type"):
            try:
                normalized_task_type = self._normalize_delegation_task_type(
                    str(payload["task_type"])
                )
                task_type = TaskType(normalized_task_type)
            except ValueError:
                return self._failed(
                    parent_task,
                    "invalid_delegation_task_type",
                    f"Unknown delegated task type: {payload['task_type']}",
                )
        child_task = AgentTask(
            task_id=new_id("task"),
            ticker=parent_task.ticker,
            agent_name=target_agent,
            task_type=task_type,
            input_context={
                "delegated_question": str(payload.get("question") or ""),
                "delegation_context": str(payload.get("context_summary") or ""),
                "parent_task_id": parent_task.task_id,
                "_react_delegation_depth": current_depth + 1,
            },
            required_output_schema=str(
                payload.get("required_output_schema") or definition.runtime.output_schema
            ),
            permissions=definition.runtime.to_permissions(),
            run_metadata=parent_task.run_metadata.model_copy(
                update={"parent_task_id": parent_task.task_id},
                deep=True,
            ),
        )
        return await self.async_run(child_task)

    def _execution_mode(self, task: AgentTask, definition: AgentDefinition) -> str:
        raw_mode = task.input_context.get("execution_mode", definition.runtime.execution_mode)
        return str(raw_mode)

    def _react_config_for_task(self, task: AgentTask) -> ReActHarnessConfig:
        budget = task.input_context.get("o3_runtime_budget")
        if task.agent_name is not AgentName.O3_TRADING_STRATEGY or not isinstance(budget, dict):
            return self.react_config
        max_model_calls = _positive_int(budget.get("max_model_calls"))
        max_tool_batches = _positive_int(budget.get("max_parallel_tool_call_batches"))
        if max_model_calls is None and max_tool_batches is None:
            return self.react_config
        return replace(
            self.react_config,
            max_steps=max_model_calls or self.react_config.max_steps,
            max_tool_calls_per_name=max_tool_batches
            or self.react_config.max_tool_calls_per_name,
            max_tool_call_batches=max_tool_batches
            or self.react_config.max_tool_call_batches,
        )

    def _normalize_delegation_task_type(self, value: str) -> str:
        if value in {"data_retrieval", "market_data", "retrieval"}:
            return TaskType.DELEGATED_RETRIEVAL.value
        return value

    def _run_requested_tools(self, task: AgentTask) -> list[ToolResult]:
        if self.tool_registry is None:
            return []
        adapter = ToolRegistryFunctionAdapter(self.tool_registry)
        results: list[ToolResult] = []
        for request in requested_tool_calls(task):
            input_payload = request.get("input", {})
            results.append(
                adapter.call_tool(
                    tool_name=str(request["tool_name"]),
                    task=task,
                    input_payload=input_payload if isinstance(input_payload, dict) else {},
                )
            )
        return results

    def _prompt(
        self,
        task: AgentTask,
        context_snapshot: Any | None,
        tool_results: list[ToolResult],
    ) -> str:
        return json.dumps(
            {
                "task": task.model_dump(mode="json"),
                "context_snapshot": context_snapshot.model_dump(mode="json")
                if context_snapshot is not None
                else None,
                "tool_results": [result.model_dump(mode="json") for result in tool_results],
                "rules": [
                    "Return a JSON object.",
                    "Do not write Blackboard state directly.",
                    "Put proposed stable changes in AgentResult-compatible structures only.",
                ],
            },
            ensure_ascii=True,
        )

    def _metadata(self, task: AgentTask) -> dict[str, str]:
        return {
            "ticker": task.ticker,
            "agent_name": task.agent_name.value,
            "run_id": task.run_metadata.run_id,
            "task_type": task.task_type.value,
            "workflow_node": task.run_metadata.workflow_node or "",
            "output_schema": task.required_output_schema,
            "parse_status": "pending",
            "schema_status": "pending",
            "write_status": "pending",
            "blackboard_target": ",".join(task.permissions.writable_targets),
            "skill_versions": json.dumps(
                task.skill_bundle.skill_versions if task.skill_bundle else {},
                ensure_ascii=True,
            ),
            "prompt_versions": json.dumps(
                task.prompt_bundle.versions if task.prompt_bundle else {},
                ensure_ascii=True,
            ),
        }

    def _structured_payload(self, structured: Any | None, text: str) -> dict[str, Any] | None:
        if isinstance(structured, dict):
            return structured
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None

    def _failed(
        self,
        task: AgentTask,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        tool_calls: list[Any] | None = None,
        details: dict[str, Any] | None = None,
    ) -> AgentResult:
        return AgentResult(
            task_id=task.task_id,
            agent_name=task.agent_name,
            status=ResultStatus.FAILED,
            tool_calls=tool_calls or [],
            error=AgentError(
                code=code,
                message=message,
                retryable=retryable,
                details=details or {},
            ),
        )


def _positive_int(value: object) -> int | None:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _single_shot_max_tokens(task: AgentTask) -> int | None:
    if task.task_type is TaskType.RUNTIME_W1_NOVELTY:
        return 384
    if task.task_type is TaskType.RUNTIME_W2_POLICY:
        return 512
    return None


def _single_shot_timeout_seconds(task: AgentTask) -> float | None:
    if task.task_type in {
        TaskType.RUNTIME_W1_NOVELTY,
        TaskType.RUNTIME_W2_POLICY,
    }:
        return 60.0
    return None
