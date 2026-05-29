"""Deterministic workflow executor for financial-services adapter specs."""

from collections.abc import Callable, Mapping

from doxagent.adapters.financial_services.data import (
    IndustryResearchFixtureData,
    IndustryResearchRequest,
)
from doxagent.adapters.financial_services.results import (
    FinancialServicesAgentOutput,
    FinancialServicesTaskGraph,
    FinancialServicesTaskGraphNode,
)
from doxagent.adapters.financial_services.specs import (
    FinancialServicesTaskSpec,
    FinancialServicesTeamSpec,
)

FinancialServicesTaskRenderer = Callable[
    [
        FinancialServicesTeamSpec,
        FinancialServicesTaskSpec,
        Mapping[str, FinancialServicesAgentOutput],
        IndustryResearchRequest,
        IndustryResearchFixtureData,
    ],
    FinancialServicesAgentOutput,
]


class DeterministicFinancialServicesExecutor:
    """Run the migrated Market Researcher DAG without Anthropic managed runtime."""

    def __init__(
        self,
        team: FinancialServicesTeamSpec,
        renderer: FinancialServicesTaskRenderer,
    ) -> None:
        self._team = team
        self._renderer = renderer
        self._team.assert_valid_references()

    @property
    def team(self) -> FinancialServicesTeamSpec:
        return self._team

    def run(
        self,
        request: IndustryResearchRequest,
        data: IndustryResearchFixtureData,
    ) -> list[FinancialServicesAgentOutput]:
        outputs_by_task: dict[str, FinancialServicesAgentOutput] = {}
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
                    request,
                    data,
                )
        return [outputs_by_task[task.task_id] for task in self._team.tasks]

    def task_graph(self) -> FinancialServicesTaskGraph:
        layers = self._team.topological_layers()
        layer_by_task = {
            task_id: layer_index
            for layer_index, layer in enumerate(layers)
            for task_id in layer
        }
        nodes = [
            FinancialServicesTaskGraphNode(
                task_id=task.task_id,
                agent_id=task.agent_id,
                skill_name=task.skill_name,
                role=self._team.agent(task.agent_id).role,
                layer_index=layer_by_task[task.task_id],
                depends_on=task.depends_on,
                input_from=task.input_from,
            )
            for task in self._team.tasks
        ]
        return FinancialServicesTaskGraph(
            preset_name=self._team.name,
            source_project=self._team.source_project,
            nodes=nodes,
            layers=layers,
        )
