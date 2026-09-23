from __future__ import annotations

import httpx
import pytest

from doxagent.message_bus_v2.distribution import DistributionWorker
from doxagent.message_bus_v2.jev import JevClient
from doxagent.message_bus_v2.monitoring_terms import TickerMonitoringTerms
from doxagent.message_bus_v2.schema import RawMessageInput, utc_now


@pytest.mark.asyncio
async def test_jev_reads_keyed_noul_without_order_assumptions() -> None:
    def responder(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/alpha/decisions"
        payload = __import__("json").loads(request.content)
        assert set(payload["questions"]) == {"MU", "NVDA"}
        return httpx.Response(
            200,
            json={
                "answers": {
                    "NVDA": {"type": "noul", "noul": 0.1},
                    "MU": {"type": "noul", "noul": 0.9},
                }
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(responder))
    jev = JevClient("test-key", client=client)
    base = {
        "expected_revision": 0,
        "l1_concepts": [{"concept_id": "company", "expressions": {"en": "memory"}}],
        "l2": {"en": {"groups": [{"id": "direct", "any": [{"literal": "memory"}]}]}},
        "definition": {"relevant": "memory", "irrelevant": "other"},
    }
    terms = {
        ticker: TickerMonitoringTerms.model_validate({**base, "ticker": ticker})
        for ticker in ("MU", "NVDA")
    }
    message = RawMessageInput(
        title="Micron expands HBM",
        source="test",
        url="https://example.com/a",
        published_at=utc_now(),
        raw_payload={},
    )
    assert await jev.classify(message, terms) == {"MU": 0.9, "NVDA": 0.1}
    await client.aclose()


@pytest.mark.asyncio
async def test_partial_jev_answers_retry_only_missing_ticker_and_segment(monkeypatch) -> None:
    base = {
        "expected_revision": 0,
        "l1_concepts": [{"concept_id": "company", "expressions": {"en": "chip"}}],
        "l2": {"en": {"groups": [{"id": "direct", "any": [{"literal": "chip"}]}]}},
        "definition": {"relevant": "chips", "irrelevant": "unrelated"},
    }
    definitions = {
        ticker: TickerMonitoringTerms.model_validate({**base, "ticker": ticker})
        for ticker in ("MU", "NVDA")
    }
    calls: list[set[str]] = []

    class PartialJev:
        async def classify(self, message, terms):
            calls.append(set(terms))
            if len(calls) == 1:
                return {"MU": 0.9}
            return {"NVDA": 0.1}

    async def no_delay(_seconds: float) -> None:
        return None

    monkeypatch.setattr("doxagent.message_bus_v2.distribution.asyncio.sleep", no_delay)
    worker = DistributionWorker.__new__(DistributionWorker)
    worker.jev = PartialJev()
    message = RawMessageInput(
        title="chip industry",
        body="A" * 20000,
        source="test",
        url="https://example.com/chips",
        published_at=utc_now(),
        raw_payload={},
    )
    scores, attempts, errors = await worker._jev(message, definitions)
    assert scores == {"MU": 0.9, "NVDA": 0.1}
    assert attempts == {"MU": 1, "NVDA": 2}
    assert errors == {}
    assert calls == [{"MU", "NVDA"}, {"NVDA"}, {"NVDA"}]
