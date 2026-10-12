from __future__ import annotations

import asyncio
import sqlite3
import time
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from doxagent.message_bus_v2.scheduler import GlobalPollScheduler
from doxagent.message_bus_v2.yahoo_sources import page_http_failure
from doxagent.persistent_runtime_v2.bus_orchestration import BusOrchestration
from doxagent.persistent_runtime_v2.journal import RuntimeJournal, is_sqlite_contention
from doxagent.site_strategy.client import SiteAccessError
from doxagent.site_strategy.external_runtime import ExternalChromeRuntime
from doxagent.site_strategy.runtime import _RawBrowserCDP
from doxagent.site_strategy.schema import AccessMode, AccessResult, SitePurpose


@pytest.mark.asyncio
async def test_real_sqlite_lock_is_bounded_and_network_loop_remains_live(tmp_path):
    journal = RuntimeJournal(tmp_path / "runtime.sqlite3", busy_timeout_seconds=0.02)
    orchestration = BusOrchestration(journal)
    blocker = sqlite3.connect(journal.path)
    blocker.execute("BEGIN IMMEDIATE")
    ticks = []

    async def io():
        for _ in range(10):
            await asyncio.sleep(0.01)
            ticks.append(1)

    start = time.monotonic()
    results = await asyncio.gather(
        orchestration._journal_call(journal.set, "test", "key", "value"),
        io(),
        return_exceptions=True,
    )
    assert is_sqlite_contention(results[0])
    assert len(ticks) == 10 and time.monotonic() - start < 1.5
    blocker.rollback()
    blocker.close()
    await orchestration._journal_call(journal.set, "test", "key", "value")
    assert journal.get("test", "key") == "value"


@pytest.mark.asyncio
async def test_bus_loop_retries_busy_but_not_ioerr(tmp_path):
    journal = RuntimeJournal(tmp_path / "runtime.sqlite3", busy_timeout_seconds=0.01)
    blocker = sqlite3.connect(journal.path)
    blocker.execute("BEGIN IMMEDIATE")
    stop = asyncio.Event()
    calls = []

    async def run_once():
        calls.append(1)
        if len(calls) == 1:
            journal.set("test", "k", 1)
        stop.set()

    scheduler = GlobalPollScheduler.__new__(GlobalPollScheduler)
    scheduler.run_once = run_once
    await scheduler.run_forever(loop_sleep_seconds=0.01, stop=stop)
    assert len(calls) == 2
    blocker.rollback()
    blocker.close()
    error = sqlite3.OperationalError("disk I/O error")
    error.sqlite_errorcode = sqlite3.SQLITE_IOERR
    assert not is_sqlite_contention(error)


def test_egress_defer_is_not_success_or_risk():
    result = AccessResult(
        request_id="r",
        operation_id="o",
        disposition="SERVICE_UNAVAILABLE",
        failure_category="EGRESS_UNAVAILABLE",
        reason_code="egress_unavailable",
        retry_not_before=__import__("datetime").datetime.now(__import__("datetime").UTC)
        + timedelta(seconds=120),
        site_id="s",
        runtime_key="s",
        strategy_revision=1,
    )
    assert SiteAccessError(result).site_access_deferred
    assert not SiteAccessError(
        result.model_copy(update={"reason_code": "manual_attention_required"})
    ).site_access_deferred


@pytest.mark.asyncio
async def test_raw_cdp_observes_task_exception_and_retries_unmanaged_resume():
    cdp = _RawBrowserCDP("ws://unused")
    cdp.send = AsyncMock(side_effect=[TimeoutError(), {}])
    await cdp._attached({"sessionId": "s", "targetInfo": {"targetId": "t", "type": "page"}})
    assert cdp.send.await_count == 2

    async def bad():
        raise RuntimeError("not emitted")

    cdp._track(bad())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert not cdp._tasks and cdp._task_errors == 1


@pytest.mark.asyncio
async def test_one_slow_probe_does_not_reset_live_driver():
    runtime = ExternalChromeRuntime(SimpleNamespace())
    runtime._playwright = SimpleNamespace()
    runtime.driver_state = "READY"
    runtime._driver_started = time.monotonic()
    browser = SimpleNamespace(
        new_browser_cdp_session=AsyncMock(side_effect=TimeoutError()), is_connected=lambda: True
    )
    runtime._entries["i"] = SimpleNamespace(browser=browser)
    runtime.start = AsyncMock()
    await runtime.maintain_driver()
    runtime.start.assert_not_awaited()
    await runtime.maintain_driver()
    runtime.start.assert_awaited_once()


def test_yahoo_diagnostic_strips_query():
    error = page_http_failure(
        404, {}, phase="page_js_fetch", url="https://finance.yahoo.com/xhr/ncp?secret=not_logged"
    )
    assert error.diagnostics == {
        "phase": "page_js_fetch",
        "url": "https://finance.yahoo.com/xhr/ncp",
        "status": 404,
    }


@pytest.mark.asyncio
async def test_effective_candidates_are_read_only(tmp_path):
    from doxagent.site_strategy.repository import SiteStrategyRepository
    from doxagent.site_strategy.seeds import bootstrap_seed
    from doxagent.site_strategy.service import SiteStrategyService

    repository = SiteStrategyRepository(tmp_path / "site.sqlite3")
    service = SiteStrategyService(repository, profile_root=tmp_path / "profiles")
    bootstrap_seed(repository, service)
    resolved = service.resolve("https://www.barrons.com/")
    before = repository._connection.total_changes
    rows = await service.effective_candidates(resolved, SitePurpose.BODY, AccessMode.BROWSER)
    assert rows and repository._connection.total_changes == before
    assert any("auth_required" in row["skip_reasons"] for row in rows)
    repository.close()


@pytest.mark.asyncio
async def test_jev_402_opens_circuit_without_guessing_relevance():
    from doxagent.message_bus_v2.distribution import DistributionWorker
    from doxagent.message_bus_v2.schema import RawMessageInput, utc_now
    from tests.test_message_bus_distribution import _terms

    response = httpx.Response(
        402, request=httpx.Request("POST", "https://openrouter.ai/api/alpha/decisions")
    )
    jev = SimpleNamespace(
        classify=AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "payment", request=response.request, response=response
            )
        )
    )
    worker = DistributionWorker(SimpleNamespace(), SimpleNamespace(), SimpleNamespace(), jev=jev)
    message = RawMessageInput(
        url="https://example.com/test",
        raw_payload={},
        title="test",
        body="test content",
        published_at=utc_now(),
    )
    terms = {"MU": _terms("MU", "Micron")}
    scores, attempts, errors = await worker._jev(message, terms)
    assert scores == {} and errors == {"MU": "JEV_PROVIDER_UNAVAILABLE"}
    await worker._jev(message, terms)
    assert jev.classify.await_count == 1
