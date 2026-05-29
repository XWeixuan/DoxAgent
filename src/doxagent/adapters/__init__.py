"""External capability adapters."""

from doxagent.adapters.financial_services import (
    IndustryResearchAgentModule,
    IndustryResearchResult,
)
from doxagent.adapters.vibe_trading import (
    FundamentalBriefAgentModule,
    FundamentalBriefResult,
    MacroContextAgentModule,
    MacroContextResult,
)

__all__ = [
    "FundamentalBriefAgentModule",
    "FundamentalBriefResult",
    "IndustryResearchAgentModule",
    "IndustryResearchResult",
    "MacroContextAgentModule",
    "MacroContextResult",
]
