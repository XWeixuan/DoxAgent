import asyncio
import threading
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from doxagent.codex_worker.w3_market import W3MarketPreparer, offline_market_view
from doxagent.persistent_runtime_v2.w3_research_store import W3ResearchStore, compact


class Provider:
    def __init__(self):
        self.lock = threading.Lock()
        self.active = self.peak = 0
        self.calls = []
        self.closed = False

    def history(self, ticker, bar, duration, venue, end):
        with self.lock:
            assert not self.closed
            self.active += 1
            self.peak = max(self.active, self.peak)
            self.calls.append((ticker, bar, venue))
        try:
            time.sleep(0.005)
            return {"bars": []}
        finally:
            with self.lock:
                self.active -= 1

    def close(self):
        assert self.active == 0
        self.closed = True


@pytest.mark.asyncio
async def test_twenty_cases_share_fixed_connection_bounded_requests_and_cancel_safely(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("doxagent.codex_worker.w3_market.market_active", lambda _: True)
    provider = Provider()
    market = W3MarketPreparer(
        W3ResearchStore(tmp_path), SimpleNamespace(w3_market_refresh_seconds=30), provider
    )
    manifests = [
        dict(
            case_id=f"c{i}",
            ticker="MU",
            deadline_at=(datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        )
        for i in range(20)
    ]
    try:
        for manifest in manifests:
            market.start(manifest)
        start = time.monotonic()
        assert market.view(manifests[0], {})["series"]["fine"]["status"] == "PREPARING"
        assert time.monotonic() - start < 0.1
        for _ in range(300):
            if all(market.cases[m["case_id"]].get("fine") for m in manifests) and market.fixed.get(
                "MU", {}
            ).get("hourly"):
                break
            await asyncio.sleep(0.01)
        assert all(market.cases[m["case_id"]].get("fine") for m in manifests)
        assert sum(bar == "1 day" for _, bar, _ in provider.calls) == 1
        assert provider.peak <= 2
        for manifest in manifests:
            market.stop(manifest["case_id"])
    finally:
        await market.close()
    assert not market.tasks and not market.cases and provider.closed


@pytest.mark.asyncio
async def test_cancellation_keeps_request_gate_until_blocking_call_finishes(tmp_path):
    provider = Provider()
    market = W3MarketPreparer(
        W3ResearchStore(tmp_path), SimpleNamespace(w3_market_refresh_seconds=30), provider
    )
    tasks = [asyncio.create_task(market.request("MU", "5 mins", "14400 S")) for _ in range(20)]
    await asyncio.sleep(0.001)
    for task in tasks[:10]:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await market.close()
    assert provider.peak <= 2 and provider.active == 0


def test_offline_market_preserves_ten_minute_bars_and_cutoff():
    evidence = {
        "price_10min.json": compact(
            dict(
                bar_size="10 mins",
                bars=[
                    dict(
                        date="2026-10-16T13:00:00+00:00",
                        bar_end_at="2026-10-16T13:10:00+00:00",
                        close=12,
                    ),
                    dict(
                        date="2026-10-16T13:10:00+00:00",
                        bar_end_at="2026-10-16T13:20:00+00:00",
                        close=13,
                    ),
                ],
            )
        )
    }
    view = offline_market_view(evidence, "2026-10-16T13:15:00+00:00", "MU")
    assert len(view["series"]["fine"]["rows"]) == 1
    assert view["series"]["fine"]["bar_size"] == "10 mins"
    assert view["series"]["hourly"]["status"] == "UNAVAILABLE"


def test_since_version_does_not_repeat_unchanged_bars_after_poll(tmp_path, monkeypatch):
    monkeypatch.setattr("doxagent.codex_worker.w3_market.market_active", lambda _: True)
    market = W3MarketPreparer(
        W3ResearchStore(tmp_path), SimpleNamespace(w3_market_refresh_seconds=30), Provider()
    )
    manifest = dict(case_id="c", ticker="MU")
    market.cases["c"] = dict(
        fine={"status": "PARTIAL", "rows": [["t", 12]], "coverage": {"requested_to": "first"}},
        observed_at=datetime.now(UTC).isoformat(),
    )
    first = market.view(manifest, {})
    market.cases["c"]["fine"]["coverage"]["requested_to"] = "second"
    second = market.view(manifest, {"since_version": first["view_version"]})
    assert second["unchanged"]
