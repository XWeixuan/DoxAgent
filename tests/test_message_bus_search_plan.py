from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from doxagent.message_bus_v2.monitoring_terms import TickerMonitoringTerms
from doxagent.message_bus_v2.news_adapters import ReutersSiteSearchAdapter
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.schema import PollContext, UpdateActor
from doxagent.message_bus_v2.search_plan import build_query_plan
from doxagent.message_bus_v2.service import MessageBusV2Service


@asynccontextmanager
async def _permit() -> AsyncIterator[None]:
    yield


async def test_three_reuters_queries_isolate_failure_cursor_and_deduplicate(tmp_path) -> None:
    repository = MessageBusV2Repository(tmp_path / "bus.sqlite3")
    service = MessageBusV2Service(repository)
    service.bootstrap()
    source = service.require_source("reuters_site_search")
    binding = service.configure_binding(
        ticker="MU",
        source_id=source.source_id,
        source_parameters={"max_pages": 1},
        actor=UpdateActor.SYSTEM,
    )
    terms = TickerMonitoringTerms.model_validate(
        {
            "ticker": "MU",
            "expected_revision": 0,
            "l1_concepts": [
                {"concept_id": value.lower(), "expressions": {"en": value}}
                for value in ("Micron", "memory", "HBM")
            ],
            "l2": {"en": {"groups": [{"id": "company", "any": [{"literal": "Micron"}]}]}},
            "definition": {"relevant": "Memory", "irrelevant": "Other"},
        }
    )
    plan = build_query_plan(source, terms, 1)
    assert len({query.query_key for query in plan.queries}) == 3

    class Browser:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def reuters_search(self, query: str, offset: int):
            self.calls.append(query)
            if query == "memory":
                raise RuntimeError("one query failed")
            return [
                {
                    "title": "Micron HBM announcement",
                    "date": "September 14, 2026",
                    "url": "/technology/micron-hbm-2026-09-14/",
                }
            ]

    browser = Browser()
    context = PollContext(
        ticker="MU",
        source=source,
        binding=binding,
        requested_at=datetime(2026, 9, 14, 12, tzinfo=UTC),
        request_permit=_permit,
        query_plan=plan.model_dump(mode="json"),
        window_start=datetime(2026, 9, 14, tzinfo=UTC),
        window_cutoff=datetime(2026, 9, 14, 12, tzinfo=UTC),
    )
    result = await ReutersSiteSearchAdapter(browser).poll(context)
    assert browser.calls == ["Micron", "memory", "HBM"]
    assert len(result.messages) == 1
    assert result.window_coverage == "PARTIAL"
    assert result.window_done is False
    keys = [query.query_key for query in plan.queries]
    assert result.next_checkpoint["queries"][keys[0]]["done"] is True
    assert result.next_checkpoint["queries"][keys[1]]["done"] is False
