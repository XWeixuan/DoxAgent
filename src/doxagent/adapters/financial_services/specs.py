"""Lightweight specs for the financial-services Market Researcher adapter."""

from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict, Field

from doxagent.models import NonEmptyStr


class FinancialServicesAdapterModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class FinancialServicesAgentSpec(FinancialServicesAdapterModel):
    agent_id: NonEmptyStr
    role: NonEmptyStr
    prompt_summary: NonEmptyStr
    tools: list[NonEmptyStr] = Field(default_factory=list)
    skills: list[NonEmptyStr] = Field(default_factory=list)
    can_touch_untrusted_docs: bool = False
    can_write_artifacts: bool = False
    connector_names: list[NonEmptyStr] = Field(default_factory=list)


class FinancialServicesTaskSpec(FinancialServicesAdapterModel):
    task_id: NonEmptyStr
    agent_id: NonEmptyStr
    skill_name: NonEmptyStr
    prompt_template: NonEmptyStr
    depends_on: list[NonEmptyStr] = Field(default_factory=list)
    input_from: dict[NonEmptyStr, NonEmptyStr] = Field(default_factory=dict)


class FinancialServicesTeamSpec(FinancialServicesAdapterModel):
    name: NonEmptyStr
    title: NonEmptyStr
    description: NonEmptyStr
    source_project: NonEmptyStr = "anthropics/financial-services"
    agents: list[FinancialServicesAgentSpec]
    tasks: list[FinancialServicesTaskSpec]

    def agent(self, agent_id: str) -> FinancialServicesAgentSpec:
        for agent in self.agents:
            if agent.agent_id == agent_id:
                return agent
        raise KeyError(f"Unknown financial-services agent: {agent_id}")

    def task(self, task_id: str) -> FinancialServicesTaskSpec:
        for task in self.tasks:
            if task.task_id == task_id:
                return task
        raise KeyError(f"Unknown financial-services task: {task_id}")

    def all_task_ids(self) -> set[str]:
        return {task.task_id for task in self.tasks}

    def topological_layers(self) -> list[list[str]]:
        remaining = self.all_task_ids()
        completed: set[str] = set()
        layers: list[list[str]] = []

        while remaining:
            ready = [
                task.task_id
                for task in self.tasks
                if task.task_id in remaining and set(task.depends_on).issubset(completed)
            ]
            if not ready:
                unresolved = ", ".join(sorted(remaining))
                raise ValueError(
                    f"Financial-services task graph has unresolved dependencies: {unresolved}"
                )
            layers.append(ready)
            completed.update(ready)
            remaining.difference_update(ready)

        return layers

    def assert_valid_references(self) -> None:
        agent_ids = {agent.agent_id for agent in self.agents}
        task_ids = self.all_task_ids()
        errors: list[str] = []

        for task in self.tasks:
            if task.agent_id not in agent_ids:
                errors.append(f"{task.task_id} references unknown agent {task.agent_id}")
            errors.extend(_unknown_refs(task.depends_on, task_ids, task.task_id, "depends_on"))
            errors.extend(
                _unknown_refs(task.input_from.values(), task_ids, task.task_id, "input_from")
            )

        if errors:
            raise ValueError("; ".join(errors))


def _unknown_refs(
    refs: Iterable[str],
    known: set[str],
    task_id: str,
    field_name: str,
) -> list[str]:
    return [
        f"{task_id}.{field_name} references unknown task {ref}" for ref in refs if ref not in known
    ]
