"""Deterministic DAG executor for extracted Vibe-Trading team specs."""

from collections.abc import Callable, Mapping

from doxagent.adapters.vibe_trading.results import (
    VibeAgentOutput,
    VibeTaskGraph,
    VibeTaskGraphNode,
)
from doxagent.adapters.vibe_trading.specs import VibeTaskSpec, VibeTeamSpec

VibeTaskRenderer = Callable[
    [VibeTeamSpec, VibeTaskSpec, Mapping[str, VibeAgentOutput], dict[str, str]],
    VibeAgentOutput,
]


class DeterministicVibeTeamExecutor:
    """Execute a Vibe task graph without invoking the original runtime.

    The executor preserves task dependency order and passes upstream task output
    to the renderer. It does not execute Vibe tools or shell commands.
    """

    def __init__(self, team: VibeTeamSpec, renderer: VibeTaskRenderer) -> None:
        self._team = team
        self._renderer = renderer
        self._team.assert_valid_references()

    @property
    def team(self) -> VibeTeamSpec:
        return self._team

    def run(self, variables: dict[str, str]) -> list[VibeAgentOutput]:
        self._team.validate_variables(variables)
        outputs_by_task: dict[str, VibeAgentOutput] = {}

        for layer in self._team.topological_layers():
            for task_id in layer:
                task = self._team.task(task_id)
                upstream = {
                    upstream_id: outputs_by_task[upstream_id] for upstream_id in task.depends_on
                }
                outputs_by_task[task_id] = self._renderer(
                    self._team,
                    task,
                    upstream,
                    variables,
                )

        return [outputs_by_task[task.task_id] for task in self._team.tasks]

    def task_graph(self) -> VibeTaskGraph:
        layers = self._team.topological_layers()
        layer_by_task = {
            task_id: layer_index
            for layer_index, layer in enumerate(layers)
            for task_id in layer
        }
        nodes = [
            VibeTaskGraphNode(
                task_id=task.task_id,
                agent_id=task.agent_id,
                role=self._team.agent(task.agent_id).role,
                layer_index=layer_by_task[task.task_id],
                depends_on=task.depends_on,
                input_from=task.input_from,
            )
            for task in self._team.tasks
        ]
        return VibeTaskGraph(
            preset_name=self._team.name,
            source_project=self._team.source_project,
            nodes=nodes,
            layers=layers,
        )
