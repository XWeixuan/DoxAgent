"""ModelGateway-backed MAF AgentRunner implementation."""

import asyncio
import json
import platform
from collections.abc import Coroutine
from dataclasses import replace
from typing import Any, cast

from doxagent.agents.config import AgentDefinition, AgentRegistry, default_agent_registry
from doxagent.agents.runtime.chat_client import ModelGatewayChatClient
from doxagent.agents.runtime.factory import MafAgentFactory
from doxagent.agents.runtime.memory import (
    InMemoryObservationArchive,
    ObservationAliasRegistry,
    ObservationArchive,
)
from doxagent.agents.runtime.react import ReActAgentHarness, ReActHarnessConfig
from doxagent.agents.runtime.tools import (
    ToolMode,
    ToolRegistryFunctionAdapter,
    has_required_tool_failure,
    requested_tool_calls,
    resolve_tool_registry,
    tool_result_to_summary,
)
from doxagent.annotations import (
    InMemoryAnnotationStore,
    TextAnnotationProcessor,
    render_time_tags,
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
from doxagent.workflow_memory import (
    CompiledWorkflowInput,
    WorkflowMemoryCompiler,
    WorkflowMemoryError,
)
from doxagent.workflow_memory.projectors import BlackboardDocumentBodyProjector


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
        workflow_memory_compiler: WorkflowMemoryCompiler | None = None,
        tool_registry: ToolRegistry | None = None,
        default_provider: ProviderName = ProviderName.MOCK,
        default_model: str = "mock-model",
        tool_mode: ToolMode = "mock",
        agent_factory: MafAgentFactory | None = None,
        react_config: ReActHarnessConfig | None = None,
        model_timeout_seconds: float | None = None,
        max_delegation_depth: int = 1,
        annotation_processor: TextAnnotationProcessor | None = None,
        observation_archive: ObservationArchive | None = None,
    ) -> None:
        self.registry = registry or default_agent_registry()
        self.prompt_injector = prompt_injector or PromptInjector()
        self.prompt_assembler = prompt_assembler or PromptAssembler()
        self.skill_injector = skill_injector or SkillInjector()
        self.model_gateway = model_gateway or ModelGateway(MockModelClient(structured={}))
        annotation_store = InMemoryAnnotationStore()
        self.annotation_processor = annotation_processor or TextAnnotationProcessor(
            annotation_store
        )
        active_annotation_store = self.annotation_processor.store
        def text_renderer(value: str) -> str:
            if active_annotation_store is None or not hasattr(
                active_annotation_store, "times_for_text"
            ):
                return value
            return render_time_tags(
                value,
                active_annotation_store.times_for_text(value),
            )
        body_projector = BlackboardDocumentBodyProjector(text_renderer=text_renderer)
        self.context_builder = context_builder
        self.workflow_memory_compiler = workflow_memory_compiler or (
            WorkflowMemoryCompiler.from_repository(
                context_builder.blackboard.repository,
                body_projector=body_projector,
            )
            if context_builder is not None
            else WorkflowMemoryCompiler(body_projector=body_projector)
        )
        self.tool_registry = resolve_tool_registry(tool_mode, tool_registry)
        self.default_provider = default_provider
        self.default_model = default_model
        self.tool_mode = tool_mode
        self.agent_factory = agent_factory or MafAgentFactory()
        self.react_config = react_config or ReActHarnessConfig()
        self.model_timeout_seconds = model_timeout_seconds
        self.max_delegation_depth = max_delegation_depth
        self.observation_archive = observation_archive or InMemoryObservationArchive()

    def run(self, task: AgentTask) -> AgentResult:
        try:
            return _run_agent_coroutine(self.async_run(task))
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
        if task.prompt_bundle is None:
            return self._failed(
                task,
                "missing_prompt_bundle",
                "Prompt injection produced no bundle.",
            )
        try:
            workflow_input = self.workflow_memory_compiler.compile(task)
        except WorkflowMemoryError as exc:
            return self._failed(
                task,
                "workflow_memory_compilation_failed",
                str(exc),
                details={"error_type": exc.__class__.__name__},
            )

        assembled_prompt = self.prompt_assembler.assemble(
            task,
            definition,
            task.prompt_bundle,
            workflow_input,
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
                prompt_registry=self.prompt_injector.registry,
                config=self._react_config_for_task(task),
                annotation_processor=self.annotation_processor,
                observation_archive=self.observation_archive,
            )
            return await harness.run(
                task=task,
                definition=definition,
                assembled_prompt=assembled_prompt,
                context_snapshot=workflow_input,
                metadata=self._metadata(task),
                delegate=lambda payload: self._run_delegation(task, payload),
            )
        if mode == "single_shot":
            return await self._async_run_model_once(
                task,
                definition,
                workflow_input,
                assembled_prompt,
                run_requested_tools=False,
            )
        if mode == "caller_planned_tools":
            return await self._async_run_model_once(
                task,
                definition,
                workflow_input,
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
        workflow_input: CompiledWorkflowInput,
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
                workflow_input,
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
            system_prompt=assembled_prompt.instructions,
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
        result = AgentResult(
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
                "context_assembly_audit": workflow_input.audit.model_dump(mode="json"),
            },
            tool_calls=tool_calls,
        )
        annotation_batch = self.annotation_processor.process(
            run_id=task.run_metadata.run_id,
            task_id=task.task_id,
            result_id=new_id("result"),
            payload=result.payload,
            aliases=ObservationAliasRegistry(),
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
        budget = task.input_context.get("react_runtime_budget")
        if not isinstance(budget, dict):
            budget = (
                task.input_context.get("o3_runtime_budget")
                if task.agent_name is AgentName.O3_TRADING_STRATEGY
                else None
            )
        if not isinstance(budget, dict):
            return self.react_config
        max_steps = _positive_int(
            _first_present(budget, "max_steps", "max_model_calls")
        )
        max_tool_calls_per_name = _positive_int(
            _first_present(budget, "max_tool_calls_per_name")
        )
        max_tool_batches = _nonnegative_int(
            _first_present(
                budget,
                "max_tool_call_batches",
                "max_parallel_tool_call_batches",
            )
        )
        if max_tool_calls_per_name is None and max_tool_batches is not None:
            max_tool_calls_per_name = max(1, max_tool_batches)
        model_timeout = _positive_float(
            _first_present(budget, "model_request_timeout_seconds")
        )
        model_context_window = _positive_int(
            _first_present(budget, "model_context_window")
        )
        micro_ratio = _ratio(_first_present(budget, "micro_maintenance_ratio"))
        full_ratio = _ratio(_first_present(budget, "full_compaction_ratio"))
        resolved_micro_ratio = (
            micro_ratio
            if micro_ratio is not None
            else self.react_config.micro_maintenance_ratio
        )
        resolved_full_ratio = (
            full_ratio
            if full_ratio is not None
            else self.react_config.full_compaction_ratio
        )
        if resolved_micro_ratio >= resolved_full_ratio:
            resolved_micro_ratio = self.react_config.micro_maintenance_ratio
            resolved_full_ratio = self.react_config.full_compaction_ratio
        if (
            max_steps is None
            and max_tool_calls_per_name is None
            and max_tool_batches is None
            and model_timeout is None
            and model_context_window is None
            and micro_ratio is None
            and full_ratio is None
        ):
            return self.react_config
        return replace(
            self.react_config,
            max_steps=max_steps
            if max_steps is not None
            else self.react_config.max_steps,
            max_tool_calls_per_name=max_tool_calls_per_name
            if max_tool_calls_per_name is not None
            else self.react_config.max_tool_calls_per_name,
            max_tool_call_batches=max_tool_batches
            if max_tool_batches is not None
            else self.react_config.max_tool_call_batches,
            model_request_timeout_seconds=model_timeout
            if model_timeout is not None
            else self.react_config.model_request_timeout_seconds,
            model_context_window=model_context_window
            if model_context_window is not None
            else self.react_config.model_context_window,
            micro_maintenance_ratio=resolved_micro_ratio,
            full_compaction_ratio=resolved_full_ratio,
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

    def _metadata(self, task: AgentTask) -> dict[str, str]:
        source_message_id = _source_message_id(task.input_context)
        return {
            "ticker": task.ticker,
            "agent_name": task.agent_name.value,
            "run_id": task.run_metadata.run_id,
            "task_type": task.task_type.value,
            "workflow_node": task.run_metadata.workflow_node or "",
            "runtime_node": _runtime_node_for_task(task),
            "source_message_id": source_message_id or "",
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


def _nonnegative_int(value: object) -> int | None:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _positive_float(value: object) -> float | None:
    try:
        parsed = float(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _ratio(value: object) -> float | None:
    parsed = _positive_float(value)
    return parsed if parsed is not None and parsed <= 1 else None


def _first_present(payload: dict[str, Any], *keys: str) -> object:
    for key in keys:
        if key in payload:
            return payload[key]
    return None


def _source_message_id(input_context: dict[str, Any]) -> str | None:
    source_message = input_context.get("source_message")
    if isinstance(source_message, dict):
        for key in ("source_message_id", "standard_message_id", "message_id"):
            value = source_message.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    value = input_context.get("source_message_id")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _runtime_node_for_task(task: AgentTask) -> str:
    if task.task_type is TaskType.RUNTIME_W1_NOVELTY:
        return "W1"
    if task.task_type is TaskType.RUNTIME_W2_POLICY:
        return "W2"
    if task.task_type is TaskType.RUNTIME_O3_JUDGMENT:
        return "O3"
    return task.run_metadata.workflow_node or task.agent_name.value


def _run_agent_coroutine(coro: Coroutine[Any, Any, AgentResult]) -> AgentResult:
    if platform.system() == "Windows":
        loop = asyncio.SelectorEventLoop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()
    return asyncio.run(coro)


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
