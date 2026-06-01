"""ModelGateway-backed MAF AgentRunner implementation."""

import asyncio
import json
from typing import Any

from doxagent.agents.config import AgentRegistry, default_agent_registry
from doxagent.agents.runtime.chat_client import ModelGatewayChatClient
from doxagent.agents.runtime.factory import MafAgentFactory
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
from doxagent.models import AgentError, AgentResult, AgentTask, ResultStatus
from doxagent.skills.injection import SkillInjector
from doxagent.tools import ToolRegistry, ToolResult


class ModelGatewayAgentRunner:
    """Run DoxAgent tasks through a MAF Agent backed by ModelGateway."""

    def __init__(
        self,
        *,
        registry: AgentRegistry | None = None,
        skill_injector: SkillInjector | None = None,
        model_gateway: ModelGateway | None = None,
        context_builder: ContextBuilder | None = None,
        tool_registry: ToolRegistry | None = None,
        default_provider: ProviderName = ProviderName.MOCK,
        default_model: str = "mock-model",
        tool_mode: ToolMode = "mock",
        agent_factory: MafAgentFactory | None = None,
    ) -> None:
        self.registry = registry or default_agent_registry()
        self.skill_injector = skill_injector or SkillInjector()
        self.model_gateway = model_gateway or ModelGateway(MockModelClient(structured={}))
        self.context_builder = context_builder
        self.tool_registry = resolve_tool_registry(tool_mode, tool_registry)
        self.default_provider = default_provider
        self.default_model = default_model
        self.tool_mode = tool_mode
        self.agent_factory = agent_factory or MafAgentFactory()

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
        task = self.skill_injector.inject(task, definition)
        context_snapshot = (
            self.context_builder.build(task, task.run_metadata.run_id)
            if self.context_builder is not None
            else None
        )
        tool_results = self._run_requested_tools(task)
        tool_calls = [tool_result_to_summary(result) for result in tool_results]
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
        agent = self.agent_factory.create(definition, chat_client, tools=[])
        prompt = self._prompt(task, context_snapshot, tool_results)
        try:
            response = await agent.run(
                prompt,
                options={
                    "model": self.default_model,
                    "temperature": 0.2,
                },
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
            return self._failed(task, "missing_model_response", "MAF returned no model response.")
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
                "structured": structured,
                "text": str(response),
                "model_audit": model_response.audit.model_dump(mode="json"),
                "skill_ids": task.skill_bundle.skill_ids if task.skill_bundle else [],
                "skill_versions": task.skill_bundle.skill_versions if task.skill_bundle else {},
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
            "skill_versions": json.dumps(
                task.skill_bundle.skill_versions if task.skill_bundle else {},
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
