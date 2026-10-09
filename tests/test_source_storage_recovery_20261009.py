import sqlite3
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from doxagent.message_bus_v2.manifests import initial_sources
from doxagent.message_bus_v2.market_sources import GlobeNewswireSearchAdapter
from doxagent.message_bus_v2.schema import PollContext, TickerSourceBinding
from doxagent.site_strategy.api import create_app
from doxagent.site_strategy.client import SiteAccessError
from doxagent.site_strategy.repository import SiteStrategyRepository
from doxagent.site_strategy.schema import (
    AccessEvent,
    AccessRequest,
    AccessResult,
    SitePurpose,
    utc_now,
)
from doxagent.site_strategy.seeds import bootstrap_seed
from doxagent.site_strategy.service import SiteStrategyService
from doxagent.site_strategy.storage import StorageMonitor, storage_failure

GNW = """<div class="newsLink pl-0"><div class="date-source">October 02, 2026 03:30 ET</div>
<div class="mainLink"><a href="/news-release/2026/10/02/123/0/en/chip-update.html">
Chip production capacity expansion</a></div><p>Capacity summary</p></div>"""


@asynccontextmanager
async def permit():
    yield


def context(source_id, **kwargs):
    return PollContext(
        ticker="MU",
        source=next(s for s in initial_sources() if s.source_id == source_id),
        requested_at=datetime(2026, 10, 2, 8, tzinfo=UTC),
        request_permit=permit,
        binding=TickerSourceBinding(binding_id=f"MU:{source_id}", ticker="MU", source_id=source_id),
        **kwargs,
    )


def sql_error(code):
    exc = sqlite3.OperationalError("test storage error")
    exc.sqlite_errorcode = code
    exc.sqlite_errorname = "TEST"
    return exc


@pytest.fixture
def service(tmp_path):
    repo = SiteStrategyRepository(tmp_path / "site.db")
    service = SiteStrategyService(repo, profile_root=tmp_path / "profiles")
    bootstrap_seed(repo, service)
    yield service
    repo.close()


def test_storage_codes_and_recovery(tmp_path, monkeypatch):
    available = [30 * 1024**3]
    monkeypatch.setattr(
        "doxagent.site_strategy.storage.shutil.disk_usage",
        lambda p: SimpleNamespace(free=available[0]),
    )
    monitor = StorageMonitor(tmp_path / "site.db")
    assert monitor.diagnostics()["state"] == "ready"
    available[0] = 0
    monitor.failed(sql_error(sqlite3.SQLITE_FULL))
    assert monitor.diagnostics()["unavailable"]
    available[0] = 30 * 1024**3
    monitor._sampled = float("-inf")
    assert not monitor.diagnostics()["unavailable"]
    monitor.failed(sql_error(sqlite3.SQLITE_IOERR | 256))
    assert monitor.diagnostics()["unavailable"]
    monitor.committed()
    assert not monitor.diagnostics()["unavailable"]
    assert storage_failure(sql_error(sqlite3.SQLITE_BUSY))
    assert not storage_failure(sql_error(sqlite3.SQLITE_ERROR))


def test_actual_sqlite_full_transaction_is_recorded_and_recoverable(tmp_path):
    repo = SiteStrategyRepository(tmp_path / "capacity.db")
    try:
        with repo.transaction() as db:
            db.execute("create table capacity_test(data blob)")
        pages = repo._connection.execute("pragma page_count").fetchone()[0]
        repo._connection.execute(f"pragma max_page_count={pages}")
        with pytest.raises(sqlite3.OperationalError):
            with repo.transaction() as db:
                db.execute("insert into capacity_test values(zeroblob(2097152))")
        assert repo.storage.last_error["code"] == sqlite3.SQLITE_FULL
        assert repo.storage.diagnostics()["unavailable"]
        repo._connection.execute("pragma max_page_count=1073741823")
        with repo.transaction() as db:
            db.execute("insert into capacity_test values('ok')")
        assert not repo.storage.diagnostics()["unavailable"]
    finally:
        repo.close()


def test_diagnostic_failure_does_not_mask_business_result(service, monkeypatch):
    def fail(event):
        raise sql_error(sqlite3.SQLITE_FULL)

    monkeypatch.setattr(service.repository, "_append_event", fail)
    service.repository.append_event(
        AccessEvent(operation_id="test", site_id="trendforce", category="ACCESS_RESULT")
    )


@pytest.mark.asyncio
async def test_full_before_browser_and_core_failure_are_deferred(service, monkeypatch):
    request = AccessRequest(
        operation_id="full",
        mode="BROWSER",
        purpose=SitePurpose.CRAWLER,
        url="https://www.trendforce.com/news/",
    )
    monkeypatch.setattr(service.repository.storage, "diagnostics", lambda: {"unavailable": True})
    result = await service.execute(request)
    assert result.reason_code == "storage_unavailable"
    assert result.retry_not_before > utc_now() - timedelta(seconds=1)
    assert SiteAccessError(result).site_access_deferred
    monkeypatch.setattr(service.repository.storage, "diagnostics", lambda: {"unavailable": False})

    async def fail(request):
        raise sql_error(sqlite3.SQLITE_FULL)

    monkeypatch.setattr(service, "_execute_deadlined", fail)
    result = await service.execute(request)
    assert result.reason_code == "storage_unavailable"
    assert not service._inflight


def test_readiness_and_outbox_reject_storage_failure(service, monkeypatch):
    app = create_app(service, worker_token="worker", admin_token="admin", close_service=False)
    client = TestClient(app)
    monkeypatch.setattr(service.repository.storage, "diagnostics", lambda: {"unavailable": True})
    assert client.get("/healthz").status_code == 200
    assert client.get("/readyz").status_code == 503
    assert (
        client.post(
            "/v1/outcomes:batch", json={"body": []}, headers={"Authorization": "Bearer worker"}
        ).status_code
        == 503
    )
    monkeypatch.setattr(service.repository.storage, "diagnostics", lambda: {"unavailable": False})

    def fail(values):
        raise sql_error(sqlite3.SQLITE_FULL)

    monkeypatch.setattr(service, "save_outcomes", fail)
    assert (
        client.post(
            "/v1/outcomes:batch", json={"body": []}, headers={"Authorization": "Bearer worker"}
        ).status_code
        == 503
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,success,failure,deferred,calls",
    [
        ("fail", 0, 2, 0, 2),
        ("mixed", 1, 1, 0, 2),
        ("empty", 2, 0, 0, 2),
        ("storage", 0, 0, 2, 1),
    ],
)
async def test_globe_query_health(mode, success, failure, deferred, calls):
    seen = []

    class Browser:
        async def get(self, url, **kwargs):
            seen.append(url)
            if mode == "storage":
                raise SiteAccessError(
                    AccessResult(
                        request_id="r",
                        operation_id="op",
                        disposition="SERVICE_UNAVAILABLE",
                        failure_category="RUNTIME_UNAVAILABLE",
                        reason_code="storage_unavailable",
                        site_id="globenewswire",
                        runtime_key="globenewswire",
                        strategy_revision=1,
                    )
                )
            if mode == "fail" or (mode == "mixed" and len(seen) == 1):
                raise RuntimeError("failed")
            return 200, url, {}, "<main>No results found</main>" if mode == "empty" else GNW

    ctx = context(
        "globenewswire_search",
        query_plan={
            "queries": [
                {"query": "Micron", "query_key": "a"},
                {"query": "Memory", "query_key": "b"},
            ]
        },
    )
    result = await GlobeNewswireSearchAdapter(Browser()).poll(ctx)
    meta = result.acquisition_metadata
    assert (
        meta["query_success_count"],
        meta["query_failure_count"],
        meta["query_deferred_count"],
    ) == (success, failure, deferred)
    assert len(seen) == calls
