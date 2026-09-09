import asyncio
from collections import Counter
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from doxagent.cdecr_integration.historical_loader import (
    BenzingaHistoricalNewsProvider,
    FinnhubHistoricalNewsProvider,
)
from doxagent.settings import DoxAgentSettings
from doxagent.ticker_initialization import (
    InitializationRepository,
    InitializationWorker,
    NodeResult,
    NodeSpec,
)


@pytest.mark.asyncio
async def test_historical_slices_retry_failed_day_and_continue(tmp_path):
    repo = InitializationRepository(tmp_path / "control.db")
    run = repo.submit(
        "MU", datetime(2026, 9, 6, tzinfo=UTC), [NodeSpec(key="cdecr", block="CDECR")]
    )
    calls = Counter()
    returned = []

    def handle(request):
        date = request.url.params["from"]
        calls[date] += 1
        assert request.url.params["to"] == date
        if date == "2026-09-05" and calls[date] < 3:
            return httpx.Response(503)
        return httpx.Response(
            200,
            json=[
                {
                    "id": date,
                    "datetime": int(datetime.fromisoformat(date).replace(tzinfo=UTC).timestamp()),
                    "headline": f"MU update {date}",
                    "summary": "A deterministic test news item.",
                    "url": f"https://example.com/{date}",
                }
            ],
        )

    settings = DoxAgentSettings(_env_file=None).model_copy(
        update={"finnhub_api_key": "offline-secret"}
    )
    client = httpx.Client(transport=httpx.MockTransport(handle))
    provider = FinnhubHistoricalNewsProvider(settings, client=client, request_gap_seconds=0)

    class Adapter:
        async def reconcile(self, context):
            return await self.execute(context) if context.node.receipt.get("started") else None

        async def execute(self, context):
            context.checkpoint(started=True)
            returned.extend(
                await asyncio.to_thread(
                    provider.fetch,
                    ticker="MU",
                    window_start=context.run.research_cutoff_at - timedelta(days=2),
                    window_end=context.run.research_cutoff_at,
                )
            )
            return NodeResult()

    try:
        result = await InitializationWorker(repo, lambda _: Adapter()).run_once()
        assert result.status == "SUCCEEDED"
        assert calls == {"2026-09-04": 1, "2026-09-05": 3, "2026-09-06": 1}
        assert len(returned) == 3
        assert provider.failed_slices == []
        assert not [
            n.key
            for n in repo.nodes(run.initialization_id)
            if n.inputs.get("managed_by") and n.status == "FAILED"
        ]
        assert "offline-secret" not in str(repo.nodes(run.initialization_id))
    finally:
        provider.close()


@pytest.mark.asyncio
async def test_historical_slice_failure_is_isolated_after_two_retries(tmp_path):
    repo = InitializationRepository(tmp_path / "control.db")
    run = repo.submit(
        "MU", datetime(2026, 9, 6, tzinfo=UTC), [NodeSpec(key="cdecr", block="CDECR")]
    )
    calls = Counter()
    returned = []

    def handle(request):
        day = request.url.params["from"]
        calls[day] += 1
        assert request.url.params["to"] == day
        if day == "2026-09-05":
            return httpx.Response(503)
        return httpx.Response(
            200,
            json=[
                {
                    "id": day,
                    "datetime": int(datetime.fromisoformat(day).replace(tzinfo=UTC).timestamp()),
                    "headline": f"MU update {day}",
                    "summary": "A deterministic test news item.",
                    "url": f"https://example.com/{day}",
                }
            ],
        )

    settings = DoxAgentSettings(_env_file=None).model_copy(
        update={"finnhub_api_key": "offline-secret"}
    )
    client = httpx.Client(transport=httpx.MockTransport(handle))
    provider = FinnhubHistoricalNewsProvider(settings, client=client, request_gap_seconds=0)

    class Adapter:
        async def reconcile(self, context):
            return await self.execute(context) if context.node.receipt.get("started") else None

        async def execute(self, context):
            context.checkpoint(started=True)
            returned.extend(
                await asyncio.to_thread(
                    provider.fetch,
                    ticker="MU",
                    window_start=context.run.research_cutoff_at - timedelta(days=2),
                    window_end=context.run.research_cutoff_at,
                )
            )
            return NodeResult()

    try:
        result = await InitializationWorker(repo, lambda _: Adapter()).run_once()
        assert result.status == "SUCCEEDED"
        assert calls == {"2026-09-04": 1, "2026-09-05": 3, "2026-09-06": 1}
        assert len(returned) == 2
        assert provider.failed_slices == ["2026-09-05"]
        failed = [
            n.key
            for n in repo.nodes(run.initialization_id)
            if n.inputs.get("managed_by") and n.status == "FAILED"
        ]
        assert failed == ["cdecr.history:finnhub_company_news:MU:2026-09-05"]
    finally:
        provider.close()


@pytest.mark.asyncio
async def test_benzinga_fetches_daily_ranges_and_retries_page(tmp_path):
    calls = Counter()

    def handle(request):
        date_from = request.url.params["dateFrom"]
        assert request.url.params["dateTo"] == date_from
        calls[date_from] += 1
        if date_from == "2026-09-05" and calls[date_from] < 3:
            return httpx.Response(503)
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": date_from,
                        "created": f"{date_from}T12:00:00+00:00",
                        "title": f"MU update {date_from}",
                        "description": "A deterministic test news item.",
                        "url": f"https://example.com/{date_from}",
                    }
                ]
            },
        )

    settings = DoxAgentSettings(_env_file=None).model_copy(
        update={"benzinga_api_key": "offline-secret"}
    )
    client = httpx.Client(transport=httpx.MockTransport(handle))
    provider = BenzingaHistoricalNewsProvider(
        settings,
        client=client,
        max_pages=1,
        request_gap_seconds=0,
    )
    try:
        rows = await asyncio.to_thread(
            provider.fetch,
            ticker="MU",
            window_start=datetime(2026, 9, 4, tzinfo=UTC),
            window_end=datetime(2026, 9, 6, tzinfo=UTC),
        )
        assert calls == {"2026-09-04": 1, "2026-09-05": 3, "2026-09-06": 1}
        assert len(rows) == 3
        assert provider.failed_slices == []
    finally:
        provider.close()
