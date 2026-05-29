"""Vibe-Trading adapter modules."""

from doxagent.adapters.vibe_trading.executor import DeterministicVibeTeamExecutor
from doxagent.adapters.vibe_trading.modules import (
    FundamentalBriefAgentModule,
    MacroContextAgentModule,
)
from doxagent.adapters.vibe_trading.presets import (
    fundamental_research_team_spec,
    macro_rates_fx_desk_spec,
)
from doxagent.adapters.vibe_trading.results import (
    FundamentalBriefResult,
    MacroContextResult,
    VibeAgentOutput,
    VibeTaskGraph,
    VibeTaskGraphNode,
)
from doxagent.adapters.vibe_trading.specs import (
    VibeAgentSpec,
    VibeTaskSpec,
    VibeTeamSpec,
    VibeVariableSpec,
)

__all__ = [
    "DeterministicVibeTeamExecutor",
    "FundamentalBriefAgentModule",
    "FundamentalBriefResult",
    "MacroContextAgentModule",
    "MacroContextResult",
    "VibeAgentOutput",
    "VibeAgentSpec",
    "VibeTaskGraph",
    "VibeTaskGraphNode",
    "VibeTaskSpec",
    "VibeTeamSpec",
    "VibeVariableSpec",
    "fundamental_research_team_spec",
    "macro_rates_fx_desk_spec",
]
