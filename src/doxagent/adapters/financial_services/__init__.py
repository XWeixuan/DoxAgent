"""Adapters for anthropics/financial-services capabilities."""

from doxagent.adapters.financial_services.data import (
    IndustryResearchDataProvider,
    IndustryResearchRequest,
    MockIndustryResearchDataProvider,
    SourceRef,
    UnknownItem,
)
from doxagent.adapters.financial_services.modules import IndustryResearchAgentModule
from doxagent.adapters.financial_services.presets import market_researcher_team_spec
from doxagent.adapters.financial_services.results import (
    FinancialServicesAgentOutput,
    FinancialServicesTaskGraph,
    FinancialServicesTaskGraphNode,
    IndustryResearchResult,
)
from doxagent.adapters.financial_services.specs import (
    FinancialServicesAgentSpec,
    FinancialServicesTaskSpec,
    FinancialServicesTeamSpec,
)

__all__ = [
    "FinancialServicesAgentOutput",
    "FinancialServicesAgentSpec",
    "FinancialServicesTaskGraph",
    "FinancialServicesTaskGraphNode",
    "FinancialServicesTaskSpec",
    "FinancialServicesTeamSpec",
    "IndustryResearchAgentModule",
    "IndustryResearchDataProvider",
    "IndustryResearchRequest",
    "IndustryResearchResult",
    "MockIndustryResearchDataProvider",
    "SourceRef",
    "UnknownItem",
    "market_researcher_team_spec",
]
