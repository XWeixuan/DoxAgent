import asyncio
from collections import Counter
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from doxagent.cdecr_integration.historical_loader import FinnhubHistoricalNewsProvider
from doxagent.settings import DoxAgentSettings
from doxagent.ticker_initialization import (
    InitializationRepository,
    InitializationWorker,
    NodeResult,
    NodeSpec,
)


@pytest.mark.asyncio
async def test_historical_slices_resume_without_refetch_and_budget_is_not_multiplied(tmp_path):
    repo = InitializationRepository(tmp_path / "control.db")
    run = repo.submit(
        "MU", datetime(2026, 9, 5, tzinfo=UTC), [NodeSpec(key="cdecr", block="CDECR")]
    )
    calls = Counter()
    repaired = False

    def handle(request):
        date = request.url.params["from"]
        calls[date] += 1
        return httpx.Response(200 if date != "2026-09-05" or repaired else 503, json=[])

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
            await asyncio.to_thread(
                provider.fetch,
                ticker="MU",
                window_start=context.run.research_cutoff_at - timedelta(days=1),
                window_end=context.run.research_cutoff_at,
            )
            return NodeResult()

    try:
        result = await InitializationWorker(repo, lambda _: Adapter()).run_once()
        assert result.status == "FAILED"
        assert calls == {"2026-09-04": 1, "2026-09-05": 2}
        failed = [
            n.key
            for n in repo.nodes(run.initialization_id)
            if n.inputs.get("managed_by") and n.status == "FAILED"
        ]
        assert len(failed) == 1
        repaired = True
        repo.resume(run.initialization_id, node_key=failed[0], reason="provider restored")
        result = await InitializationWorker(repo, lambda _: Adapter()).run_once()
        assert result.status == "SUCCEEDED"
        assert calls == {"2026-09-04": 1, "2026-09-05": 3}
        assert "offline-secret" not in str(repo.nodes(run.initialization_id))
    finally:
        provider.close()
