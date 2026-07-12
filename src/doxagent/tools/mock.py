"""Offline mock tools for Phase 4 contracts."""

from doxagent.models import ResultStatus
from doxagent.tools.registry import ToolRegistry
from doxagent.tools.schema import ToolRequest, ToolResult


class MockToolClient:
    def __init__(
        self,
        *,
        tool_name: str,
        output_summary: str,
        source_kind: str,
    ) -> None:
        self.tool_name = tool_name
        self.output_summary = output_summary
        self.source_kind = source_kind
        self.calls = 0

    def call(self, request: ToolRequest) -> ToolResult:
        self.calls += 1
        return ToolResult(
            tool_name=self.tool_name,
            status=ResultStatus.SUCCEEDED,
            output={
                "ticker": request.ticker,
                "agent_name": request.agent_name.value,
                "fixture": self.tool_name,
                "input": request.input,
                "source_coordinates": {
                    "source_kind": self.source_kind,
                    "source_id": f"{self.tool_name}:{request.ticker}",
                    "tool_name": self.tool_name,
                },
            },
            output_summary=self.output_summary,
            raw={"offline": True, "tool_name": self.tool_name},
        )


def default_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        "doxatlas.query",
        MockToolClient(
            tool_name="doxatlas.query",
            output_summary="Mock DoxAtlas 叙事结果。",
            source_kind="doxatlas_source",
        ),
    )
    registry.register(
        "doxa_get_narrative_report",
        MockToolClient(
            tool_name="doxa_get_narrative_report",
            output_summary="Mock DoxAtlas 叙事报告结果。",
            source_kind="doxatlas_source",
        ),
    )
    registry.register(
        "doxatlas.source_lookup",
        MockToolClient(
            tool_name="doxatlas.source_lookup",
            output_summary="Mock DoxAtlas source lookup 结果。",
            source_kind="doxatlas_source",
        ),
    )
    registry.register(
        "market_data.snapshot",
        MockToolClient(
            tool_name="market_data.snapshot",
            output_summary="Mock market data snapshot.",
            source_kind="market_data",
        ),
    )
    registry.register(
        "fact_check.search",
        MockToolClient(
            tool_name="fact_check.search",
            output_summary="Mock fact-check search result.",
            source_kind="fact_check",
        ),
    )
    registry.register(
        "tavily.search",
        MockToolClient(
            tool_name="tavily.search",
            output_summary="Mock Tavily search result.",
            source_kind="external_report",
        ),
    )
    registry.register(
        "tavily.extract",
        MockToolClient(
            tool_name="tavily.extract",
            output_summary="Mock Tavily extract result.",
            source_kind="external_report",
        ),
    )
    registry.register(
        "anysearch.search",
        MockToolClient(
            tool_name="anysearch.search",
            output_summary="Mock AnySearch search result.",
            source_kind="external_report",
        ),
    )
    registry.register(
        "external_research.mock",
        MockToolClient(
            tool_name="external_research.mock",
            output_summary="Mock external research result.",
            source_kind="external_report",
        ),
    )
    return registry
