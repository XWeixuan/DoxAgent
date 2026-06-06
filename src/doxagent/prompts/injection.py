"""Prompt selection and injection utilities."""

from typing import Any

from doxagent.agents.config import AgentDefinition
from doxagent.models import AgentTask
from doxagent.prompts.registry import PromptRegistry, default_prompt_registry
from doxagent.prompts.schema import (
    ExternalSkillPackageDefinition,
    InternalTaskSkillDefinition,
    PromptBlockDefinition,
    PromptBlockType,
    PromptBundle,
)


class PromptInjectionPolicy:
    def select(
        self,
        task: AgentTask,
        agent_definition: AgentDefinition,
        registry: PromptRegistry,
    ) -> PromptBundle:
        workflow_node = task.run_metadata.workflow_node
        prompt_blocks = self._select_prompt_blocks(task, agent_definition, registry, workflow_node)
        internal_skills = self._select_internal_skills(
            task,
            agent_definition,
            registry,
            workflow_node,
        )
        external_packages = self._select_external_packages(task, registry)
        return PromptBundle(
            prompt_blocks=[definition.summarize() for definition in prompt_blocks],
            internal_task_skills=[definition.summarize() for definition in internal_skills],
            external_skill_packages=[definition.summarize() for definition in external_packages],
        )

    def _select_prompt_blocks(
        self,
        task: AgentTask,
        agent_definition: AgentDefinition,
        registry: PromptRegistry,
        workflow_node: str | None,
    ) -> list[PromptBlockDefinition]:
        selected: dict[str, PromptBlockDefinition] = {}
        for definition in registry.find_prompt_blocks(
            task.agent_name,
            task.task_type,
            workflow_node,
        ):
            selected[definition.resource_id] = definition
        for resource_id in agent_definition.runtime.prompt_block_ids:
            selected[resource_id] = _expect_prompt_block(registry.get(resource_id))
        for resource_id in self._requested_ids(task.input_context, "prompt_block_ids"):
            selected[resource_id] = _expect_prompt_block(registry.get(resource_id))
        return sorted(selected.values(), key=_prompt_block_sort_key)

    def _select_internal_skills(
        self,
        task: AgentTask,
        agent_definition: AgentDefinition,
        registry: PromptRegistry,
        workflow_node: str | None,
    ) -> list[InternalTaskSkillDefinition]:
        selected: dict[str, InternalTaskSkillDefinition] = {}
        for resource_id in agent_definition.runtime.default_internal_task_skill_ids:
            definition = _expect_internal_skill(registry.get(resource_id))
            if _definition_matches_task(definition, task, workflow_node):
                selected[resource_id] = definition
        for definition in registry.find_internal_task_skills(
            task.agent_name,
            task.task_type,
            workflow_node,
        ):
            selected.setdefault(definition.resource_id, definition)
        for resource_id in self._requested_ids(task.input_context, "internal_task_skill_ids"):
            selected[resource_id] = _expect_internal_skill(registry.get(resource_id))
        return [selected[resource_id] for resource_id in sorted(selected)]

    def _select_external_packages(
        self,
        task: AgentTask,
        registry: PromptRegistry,
    ) -> list[ExternalSkillPackageDefinition]:
        """Select only skill packages explicitly loaded by runtime.

        Agent startup receives an external skill catalog, not the package bodies.
        Bodies are loaded on demand by the ReAct harness and, for compatibility
        paths, may be passed through loaded_external_skill_package_ids.
        """

        selected: dict[str, ExternalSkillPackageDefinition] = {}
        for resource_id in self._requested_ids(
            task.input_context,
            "loaded_external_skill_package_ids",
        ):
            selected[resource_id] = _expect_external_package(registry.get(resource_id))
        return [selected[resource_id] for resource_id in sorted(selected)]

    def _requested_ids(self, input_context: dict[str, Any], key: str) -> list[str]:
        raw = input_context.get(key, [])
        if raw is None:
            return []
        if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
            raise ValueError(f"input_context['{key}'] must be a list of id strings.")
        return raw


class PromptInjector:
    def __init__(
        self,
        registry: PromptRegistry | None = None,
        policy: PromptInjectionPolicy | None = None,
    ) -> None:
        self.registry = registry or default_prompt_registry()
        self.policy = policy or PromptInjectionPolicy()

    def inject(self, task: AgentTask, agent_definition: AgentDefinition) -> AgentTask:
        bundle = self.policy.select(task, agent_definition, self.registry)
        return task.model_copy(update={"prompt_bundle": bundle}, deep=True)


def _expect_prompt_block(definition: object) -> PromptBlockDefinition:
    if not isinstance(definition, PromptBlockDefinition):
        raise ValueError("Expected prompt block definition.")
    return definition


def _expect_internal_skill(definition: object) -> InternalTaskSkillDefinition:
    if not isinstance(definition, InternalTaskSkillDefinition):
        raise ValueError("Expected internal task skill definition.")
    return definition


def _expect_external_package(definition: object) -> ExternalSkillPackageDefinition:
    if not isinstance(definition, ExternalSkillPackageDefinition):
        raise ValueError("Expected external skill package definition.")
    return definition


def _prompt_block_sort_key(item: PromptBlockDefinition) -> tuple[int, str]:
    order = {
        PromptBlockType.SYSTEM: 0,
        PromptBlockType.AGENT: 1,
        PromptBlockType.WORKFLOW: 2,
    }
    return (order[item.block_type], item.resource_id)


def _definition_matches_task(
    definition: InternalTaskSkillDefinition,
    task: AgentTask,
    workflow_node: str | None,
) -> bool:
    if definition.applicable_agents and task.agent_name not in definition.applicable_agents:
        return False
    if definition.applicable_task_types and task.task_type not in definition.applicable_task_types:
        return False
    if workflow_node is not None and definition.workflow_nodes:
        return workflow_node in definition.workflow_nodes
    return True
