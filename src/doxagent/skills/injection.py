"""Skill selection and injection utilities."""

from typing import Any

from doxagent.agents.config import AgentDefinition
from doxagent.models import AgentTask
from doxagent.skills.registry import SkillRegistry, default_skill_registry
from doxagent.skills.schema import SkillBundle, SkillDefinition, summarize_skill


class SkillInjectionPolicy:
    def select(
        self,
        task: AgentTask,
        agent_definition: AgentDefinition,
        registry: SkillRegistry,
    ) -> SkillBundle:
        selected: dict[str, SkillDefinition] = {}

        for skill_id in agent_definition.runtime.default_external_skill_package_ids:
            selected[skill_id] = registry.get(skill_id)

        for definition in registry.find_for_agent(task.agent_name, task.task_type):
            if definition.skill_id in selected:
                continue
            if task.task_type in definition.applicable_task_types:
                selected[definition.skill_id] = definition

        for skill_id in self._requested_skill_ids(task.input_context, "external_skill_package_ids"):
            selected[skill_id] = registry.get(skill_id)

        for skill_id in self._requested_skill_ids(task.input_context, "skill_ids"):
            selected[skill_id] = registry.get(skill_id)

        ordered = [selected[skill_id] for skill_id in sorted(selected)]
        return SkillBundle(skills=[summarize_skill(definition) for definition in ordered])

    def _requested_skill_ids(self, input_context: dict[str, Any], key: str) -> list[str]:
        raw = input_context.get(key, [])
        if raw is None:
            return []
        if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
            raise ValueError(f"input_context['{key}'] must be a list of skill id strings.")
        return raw


class SkillInjector:
    def __init__(
        self,
        registry: SkillRegistry | None = None,
        policy: SkillInjectionPolicy | None = None,
    ) -> None:
        self.registry = registry or default_skill_registry()
        self.policy = policy or SkillInjectionPolicy()

    def inject(self, task: AgentTask, agent_definition: AgentDefinition) -> AgentTask:
        bundle = self.policy.select(task, agent_definition, self.registry)
        return task.model_copy(update={"skill_bundle": bundle}, deep=True)
