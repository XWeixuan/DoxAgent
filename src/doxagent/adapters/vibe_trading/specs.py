"""Lightweight Vibe-Trading team specifications.

These models capture the preset shape DoxAgent needs without importing the
original Vibe-Trading runtime or reading its YAML files at execution time.
"""

from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict, Field

from doxagent.models import NonEmptyStr


class VibeAdapterModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class VibeVariableSpec(VibeAdapterModel):
    name: NonEmptyStr
    description: NonEmptyStr
    required: bool = True


class VibeAgentSpec(VibeAdapterModel):
    agent_id: NonEmptyStr
    role: NonEmptyStr
    system_prompt: NonEmptyStr
    tools: list[NonEmptyStr] = Field(default_factory=list)
    skills: list[NonEmptyStr] = Field(default_factory=list)
    max_iterations: int = Field(default=50, ge=1)
    timeout_seconds: int = Field(default=1800, ge=1)
    max_retries: int = Field(default=1, ge=0)


class VibeTaskSpec(VibeAdapterModel):
    task_id: NonEmptyStr
    agent_id: NonEmptyStr
    prompt_template: NonEmptyStr
    depends_on: list[NonEmptyStr] = Field(default_factory=list)
    input_from: dict[NonEmptyStr, NonEmptyStr] = Field(default_factory=dict)


class VibeTeamSpec(VibeAdapterModel):
    name: NonEmptyStr
    title: NonEmptyStr
    description: NonEmptyStr
    agents: list[VibeAgentSpec]
    tasks: list[VibeTaskSpec]
    variables: list[VibeVariableSpec]
    source_project: NonEmptyStr = "HKUDS/Vibe-Trading"

    def agent(self, agent_id: str) -> VibeAgentSpec:
        for agent in self.agents:
            if agent.agent_id == agent_id:
                return agent
        raise KeyError(f"Unknown Vibe agent: {agent_id}")

    def task(self, task_id: str) -> VibeTaskSpec:
        for task in self.tasks:
            if task.task_id == task_id:
                return task
        raise KeyError(f"Unknown Vibe task: {task_id}")

    def required_variable_names(self) -> list[str]:
        return [variable.name for variable in self.variables if variable.required]

    def validate_variables(self, values: dict[str, str]) -> None:
        missing = [
            name for name in self.required_variable_names() if not values.get(name, "").strip()
        ]
        if missing:
            joined = ", ".join(missing)
            raise ValueError(f"Missing required Vibe preset variables: {joined}")

    def topological_layers(self) -> list[list[str]]:
        task_ids = [task.task_id for task in self.tasks]
        remaining = set(task_ids)
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
                raise ValueError(f"Vibe task graph has unresolved dependencies: {unresolved}")
            layers.append(ready)
            completed.update(ready)
            remaining.difference_update(ready)

        return layers

    def all_task_ids(self) -> set[str]:
        return {task.task_id for task in self.tasks}

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
