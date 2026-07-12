"""Agent runner boundary for DoxAgent-owned contracts."""

from collections.abc import Callable
from typing import Any, Protocol

from openai import AsyncOpenAI

from doxagent.agents.config import AgentRegistry, default_agent_registry
from doxagent.agents.runtime.react import ReActHarnessConfig
from doxagent.agents.runtime.runner import ModelGatewayAgentRunner
from doxagent.annotations import TextAnnotationProcessor
from doxagent.annotations.postgres import PostgresObservationAnnotationStore
from doxagent.gateway import (
    BailianChatCompletionsModelClient,
    BailianResponsesModelClient,
    ModelClient,
    ModelGateway,
    ProviderName,
    tracing_extra_from_metadata,
    wrap_provider_client,
)
from doxagent.model_usage import ModelUsageRecorder
from doxagent.models import AgentResult, AgentTask, ResultStatus
from doxagent.prompts import PromptInjector
from doxagent.settings import DoxAgentSettings
from doxagent.skills.injection import SkillInjector
from doxagent.tools import default_real_tool_registry


class AgentRunner(Protocol):
    def run(self, task: AgentTask) -> AgentResult:
        """Run one task and return the standard DoxAgent result contract."""


class MockAgentRunner:
    def __init__(
        self,
        registry: AgentRegistry | None = None,
        result_factory: Callable[[AgentTask], AgentResult] | None = None,
        prompt_injector: PromptInjector | None = None,
        skill_injector: SkillInjector | None = None,
    ) -> None:
        self.registry = registry or default_agent_registry()
        self.result_factory = result_factory
        self.prompt_injector = prompt_injector or PromptInjector()
        self.skill_injector = skill_injector or SkillInjector()
        self.calls = 0

    def run(self, task: AgentTask) -> AgentResult:
        self.calls += 1
        definition = self.registry.get(task.agent_name)
        task = self.prompt_injector.inject(task, definition)
        task = self.skill_injector.inject(task, definition)
        if self.result_factory is not None:
            return self.result_factory(task)
        skill_bundle = task.skill_bundle
        prompt_bundle = task.prompt_bundle
        return AgentResult(
            task_id=task.task_id,
            agent_name=task.agent_name,
            status=ResultStatus.SUCCEEDED,
            payload={
                "agent_name": definition.agent_name.value,
                "task_type": task.task_type.value,
                "output_schema": definition.runtime.output_schema,
                "context_keys": sorted(task.input_context.keys()),
                "prompt_block_ids": prompt_bundle.prompt_block_ids if prompt_bundle else [],
                "internal_task_skill_ids": (
                    prompt_bundle.internal_task_skill_ids if prompt_bundle else []
                ),
                "external_skill_package_ids": (
                    prompt_bundle.external_skill_package_ids if prompt_bundle else []
                ),
                "prompt_versions": prompt_bundle.versions if prompt_bundle else {},
                "skill_ids": skill_bundle.skill_ids if skill_bundle else [],
                "skill_versions": skill_bundle.skill_versions if skill_bundle else {},
            },
        )


class MafAgentAdapter:
    """Compatibility wrapper for the ModelGateway-backed MAF runner."""

    def __init__(self, runner: ModelGatewayAgentRunner | None = None, **kwargs: Any) -> None:
        if runner is not None and kwargs:
            raise ValueError("Pass either runner or runner configuration kwargs, not both.")
        if runner is not None:
            self.runner = runner
        elif kwargs:
            self.runner = ModelGatewayAgentRunner(**kwargs)
        else:
            self.runner = default_real_agent_runner()

    def run(self, task: AgentTask) -> AgentResult:
        return self.runner.run(task)


def default_real_agent_runner(
    *,
    registry: AgentRegistry | None = None,
    settings: DoxAgentSettings | None = None,
    **kwargs: Any,
) -> ModelGatewayAgentRunner:
    """Create the production default runner: Bailian Responses API plus real tools."""

    resolved_settings = settings or DoxAgentSettings()
    resolved_settings.apply_langsmith_environment()
    client = _build_bailian_sdk_client(
        api_key=resolved_settings.require_dashscope_api_key(),
        settings=resolved_settings,
    )
    fallback_clients: list[ModelClient] = []
    for fallback_key in resolved_settings.dashscope_fallback_api_keys():
        fallback_clients.append(
            _build_bailian_model_client(
                _build_bailian_sdk_client(
                    api_key=fallback_key,
                    settings=resolved_settings,
                ),
                settings=resolved_settings,
            )
        )
    runner_kwargs = dict(kwargs)
    runner_kwargs.setdefault(
        "model_timeout_seconds",
        resolved_settings.model_request_timeout_seconds,
    )
    runner_kwargs.setdefault(
        "react_config",
        ReActHarnessConfig(
            model_request_timeout_seconds=resolved_settings.model_request_timeout_seconds,
            tool_call_timeout_seconds=resolved_settings.react_tool_call_timeout_seconds,
        ),
    )
    if resolved_settings.storage_mode == "postgres":
        annotation_store = PostgresObservationAnnotationStore(
            resolved_settings.require_database_url()
        )
        runner_kwargs.setdefault(
            "annotation_processor", TextAnnotationProcessor(annotation_store)
        )
        runner_kwargs.setdefault("observation_archive", annotation_store)
    return ModelGatewayAgentRunner(
        registry=registry,
        model_gateway=ModelGateway(
            _build_bailian_model_client(client, settings=resolved_settings),
            fallbacks=fallback_clients,
            usage_recorder=ModelUsageRecorder.from_settings(resolved_settings),
        ),
        tool_registry=default_real_tool_registry(resolved_settings),
        default_provider=ProviderName.BAILIAN,
        default_model=resolved_settings.dashscope_model,
        tool_mode="real",
        **runner_kwargs,
    )


def _build_bailian_sdk_client(
    *,
    api_key: str,
    settings: DoxAgentSettings,
) -> object:
    base_url = (
        settings.dashscope_chat_base_url
        if _is_deepseek_model(settings.dashscope_model)
        else settings.dashscope_base_url
    )
    client = AsyncOpenAI(
        api_key=api_key,
        base_url=base_url,
    )
    return wrap_provider_client(
        ProviderName.BAILIAN,
        client,
        tracing_enabled=settings.langsmith_enabled,
        tracing_extra=tracing_extra_from_metadata(
            {
                "runtime": "doxagent",
                "provider": ProviderName.BAILIAN.value,
                "model": settings.dashscope_model,
            }
        ),
    )


def _build_bailian_model_client(
    client: object,
    *,
    settings: DoxAgentSettings,
) -> ModelClient:
    if _is_deepseek_model(settings.dashscope_model):
        return BailianChatCompletionsModelClient(
            client,
            enable_thinking=settings.dashscope_enable_thinking,
            thinking_budget=settings.dashscope_thinking_budget,
        )
    return BailianResponsesModelClient(
        client,
        enable_thinking=settings.dashscope_enable_thinking,
        thinking_budget=settings.dashscope_thinking_budget,
    )


def _is_deepseek_model(model: str) -> bool:
    return model.lower().startswith("deepseek-")
