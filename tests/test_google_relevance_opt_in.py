"""Opt-in Google search hits must pass the existing article relevance path."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from doxagent.message_bus_v2.live import LiveDeliveryWorker
from doxagent.message_bus_v2.manifests import initial_sources
from doxagent.message_bus_v2.monitoring_terms import TickerMonitoringTerms
from doxagent.message_bus_v2.news_adapters import GoogleNewsSearchRssAdapter
from doxagent.message_bus_v2.schema import RawMessageInput
from doxagent.settings import DoxAgentSettings


async def test_context_is_anded_with_company_in_the_actual_google_request(tmp_path):
    from tests.test_message_bus_v2_news_sources import _context

    context, _ = _context(tmp_path, "google_news_search_rss", {
        "search_terms": ["Intel"], "context_terms": ["chip", "Intel Corporation"],
        "require_relevance": True,
    })
    requests = []

    def listing(request):
        requests.append(request)
        return httpx.Response(200, text="<rss><channel/></rss>")

    async with httpx.AsyncClient(transport=httpx.MockTransport(listing)) as client:
        await GoogleNewsSearchRssAdapter(DoxAgentSettings(_env_file=None), client).poll(context)
    assert requests[0].url.params["q"] == 'Intel ("chip" OR "Intel Corporation") when:1d'


@pytest.mark.parametrize("enabled,company", [(False, False), (True, False), (True, True)])
async def test_google_default_publish_vs_opt_in_regex_and_semantic_routing(enabled, company):
    source = next(s for s in initial_sources() if s.source_id == "google_news_search_rss")
    terms = TickerMonitoringTerms.model_validate({
        "ticker": "INTC", "expected_revision": 0,
        "l1_concepts": [{"concept_id": "company", "expressions": {"en": "Intel"}}],
        "l2": {"en": {"groups": [{"id": "cpu", "any": [{"literal": "Xeon"}]}]}},
        "definition": {"relevant": "Intel CPU news", "irrelevant": "Sports intelligence"},
    })
    binding = SimpleNamespace(enabled=True, source_parameters={"require_relevance": enabled})
    row = {"terms_revision": 1, "ticker": "INTC", "body_digest": "body"}
    message = RawMessageInput(url="https://publisher.test/article", raw_payload={},
                              title="Intel Xeon launch" if company else "Football recruiting intel")
    worker = object.__new__(LiveDeliveryWorker)
    worker.bus = SimpleNamespace(storage=SimpleNamespace(call=AsyncMock()))
    worker.repository = SimpleNamespace(decision=object())
    worker.publish = AsyncMock()
    await worker.process(row, (message, source, binding, (1, terms)))
    if enabled and not company:
        worker.publish.assert_not_awaited()
        assert worker.bus.storage.call.await_args.args[2] == "L2_DUE"
    else:
        worker.publish.assert_awaited_once()


async def test_negative_decision_keeps_opted_in_google_article_out_of_bus():
    source = next(s for s in initial_sources() if s.source_id == "google_news_search_rss")
    row = {"article_key": "article", "ticker": "INTC"}
    binding = SimpleNamespace(enabled=True, source_parameters={"require_relevance": True})
    worker = object.__new__(LiveDeliveryWorker)
    worker.bus = SimpleNamespace(storage=SimpleNamespace(call=AsyncMock()))
    worker.repository = SimpleNamespace(decision=object())
    worker._prepare_batch = object()
    prepared = [(row, object(), source, binding, (1, object()))]
    worker.bus.storage.prepare = AsyncMock(return_value=prepared)
    worker.classifier = SimpleNamespace(_jev=AsyncMock(return_value=({"INTC": .1}, {}, {})))
    worker.publish = AsyncMock()
    worker.active_articles = {"article"}
    await worker.classify([row])
    worker.publish.assert_not_awaited()
    assert worker.bus.storage.call.await_args.args[2] == "IRRELEVANT"
