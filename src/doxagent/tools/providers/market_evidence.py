"""Lane-neutral provider surface for governed market evidence.

The implementations originated in the legacy O4 provider module. New C5 and
Market Situation O4 code imports this neutral surface; the old module remains a
compatibility implementation for historical Document 1 imports.
"""

from doxagent.tools.providers.o4_market import (
    AlphaVantageO4Client as AlphaVantageMarketEvidenceClient,
)
from doxagent.tools.providers.o4_market import (
    MarketRelativePerformanceClient,
    MarketSellSideConsensusClient,
    YFinancePeerRelativeValuationClient,
    YFinanceShortInterestClient,
)
from doxagent.tools.providers.o4_market import (
    _project_alpha_o4 as _project_alpha_market_evidence,
)

__all__ = [
    "AlphaVantageMarketEvidenceClient",
    "MarketRelativePerformanceClient",
    "MarketSellSideConsensusClient",
    "YFinancePeerRelativeValuationClient",
    "YFinanceShortInterestClient",
    "_project_alpha_market_evidence",
]
