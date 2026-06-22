"""Offline mock tools for Phase 4 contracts."""

from doxagent.models import EvidenceSourceType, ResultStatus
from doxagent.tools.registry import ToolRegistry
from doxagent.tools.schema import ToolRequest, ToolResult


class MockToolClient:
    def __init__(
        self,
        *,
        tool_name: str,
        output_summary: str,
        source_type: EvidenceSourceType,
    ) -> None:
        self.tool_name = tool_name
        self.output_summary = output_summary
        self.source_type = source_type
        self.calls = 0

    def call(self, request: ToolRequest) -> ToolResult:
        self.calls += 1
        result = ToolResult(
            tool_name=self.tool_name,
            status=ResultStatus.SUCCEEDED,
            output={
                "ticker": request.ticker,
                "agent_name": request.agent_name.value,
                "fixture": self.tool_name,
                "input": request.input,
            },
            output_summary=self.output_summary,
            raw={"offline": True, "tool_name": self.tool_name},
        )
        return result.model_copy(
            update={
                "evidence_refs": [
                    result.to_evidence_ref(
                        source_type=self.source_type,
                        source_id=f"{self.tool_name}:{request.ticker}",
                        title=f"{self.tool_name} fixture",
                        citation_scope=self.tool_name,
                        confidence=0.7,
                    ),
                ],
            },
            deep=True,
        )


def default_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        "doxatlas.query",
        MockToolClient(
            tool_name="doxatlas.query",
            output_summary="Mock DoxAtlas 叙事结果。",
            source_type=EvidenceSourceType.DOXATLAS_SOURCE,
        ),
    )
    registry.register(
        "doxa_get_narrative_report",
        MockToolClient(
            tool_name="doxa_get_narrative_report",
            output_summary="Mock DoxAtlas 叙事报告结果。",
            source_type=EvidenceSourceType.DOXATLAS_SOURCE,
        ),
    )
    registry.register(
        "doxatlas.source_lookup",
        MockToolClient(
            tool_name="doxatlas.source_lookup",
            output_summary="Mock DoxAtlas source lookup 结果。",
            source_type=EvidenceSourceType.DOXATLAS_SOURCE,
        ),
    )
    registry.register(
        "market_data.snapshot",
        MockToolClient(
            tool_name="market_data.snapshot",
            output_summary="Mock market data snapshot.",
            source_type=EvidenceSourceType.MARKET_DATA,
        ),
    )
    registry.register(
        "fact_check.search",
        MockToolClient(
            tool_name="fact_check.search",
            output_summary="Mock fact-check search result.",
            source_type=EvidenceSourceType.FACT_CHECK,
        ),
    )
    registry.register(
        "tavily.search",
        MockToolClient(
            tool_name="tavily.search",
            output_summary="Mock Tavily search result.",
            source_type=EvidenceSourceType.EXTERNAL_REPORT,
        ),
    )
    registry.register(
        "tavily.extract",
        MockToolClient(
            tool_name="tavily.extract",
            output_summary="Mock Tavily extract result.",
            source_type=EvidenceSourceType.EXTERNAL_REPORT,
        ),
    )
    registry.register(
        "anysearch.search",
        MockToolClient(
            tool_name="anysearch.search",
            output_summary="Mock AnySearch search result.",
            source_type=EvidenceSourceType.EXTERNAL_REPORT,
        ),
    )
    registry.register(
        "external_research.mock",
        MockToolClient(
            tool_name="external_research.mock",
            output_summary="Mock external research result.",
            source_type=EvidenceSourceType.EXTERNAL_REPORT,
        ),
    )
    return registry
