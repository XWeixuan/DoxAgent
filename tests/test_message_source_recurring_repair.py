from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.reuters_sources import capture_reuters_search
from doxagent.message_bus_v2.schema import AcquisitionFailure, PollResult, PollStatus
from doxagent.message_bus_v2.service import MessageBusV2Service
from doxagent.site_strategy.budget import _JointWaiter
from doxagent.site_strategy.external_runtime import ExternalChromeRuntime, _ExternalLease
from doxagent.site_strategy.health import CombinationHealthManager
from doxagent.site_strategy.repository import SiteStrategyRepository
from doxagent.site_strategy.schema import (
    AccessCombination,
    FailureCategory,
    SitePurpose,
)
from doxagent.site_strategy.service import classify_exception


def fake_playwright(returncode=None):
    process = SimpleNamespace(returncode=returncode, pid=None)
    return SimpleNamespace(
        _impl_obj=SimpleNamespace(
            _connection=SimpleNamespace(_transport=SimpleNamespace(_proc=process))
        ),
        stop=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_dead_driver_recovers_singleflight_without_stopping_chrome(monkeypatch):
    client = SimpleNamespace(stop=AsyncMock(), release_controller=AsyncMock())
    runtime = ExternalChromeRuntime(client)
    old = fake_playwright(134)
    runtime._playwright = old
    runtime.driver_state = "READY"
    calls = []

    async def launch():
        calls.append(1)
        await asyncio.sleep(0.01)
        runtime._playwright = fake_playwright()
        runtime.driver_state = "READY"
        runtime.driver_epoch += 1

    monkeypatch.setattr(runtime, "_start_driver", launch)
    await asyncio.gather(*(runtime.start() for _ in range(5)))
    assert calls == [1]
    old.stop.assert_awaited_once()
    client.stop.assert_not_awaited()
    client.release_controller.assert_not_awaited()
    assert runtime.driver_ready


@pytest.mark.asyncio
async def test_rotation_waits_for_busy_or_acquiring_identity(monkeypatch):
    runtime = ExternalChromeRuntime(SimpleNamespace())
    runtime._playwright = fake_playwright()
    runtime.driver_state = "READY"
    runtime._driver_started = time.monotonic() - 15000
    start = AsyncMock()
    detach = AsyncMock()
    monkeypatch.setattr(runtime, "_start_driver", start)
    monkeypatch.setattr(runtime, "_detach_driver", detach)
    runtime._acquiring = 1
    await runtime.maintain_driver()
    detach.assert_not_awaited()
    runtime._acquiring = 0
    await runtime.maintain_driver()
    detach.assert_awaited_once()
    start.assert_awaited_once()


@pytest.mark.asyncio
async def test_probe_detaches_session():
    runtime = ExternalChromeRuntime(SimpleNamespace())
    runtime._playwright = fake_playwright()
    runtime.driver_state = "READY"
    runtime._driver_started = time.monotonic()
    session = SimpleNamespace(send=AsyncMock(), detach=AsyncMock())
    runtime._entries["x"] = SimpleNamespace(
        browser=SimpleNamespace(new_browser_cdp_session=AsyncMock(return_value=session)),
        active_pages=0,
    )
    await runtime.maintain_driver()
    session.send.assert_awaited_once_with("Browser.getVersion")
    session.detach.assert_awaited_once()


@pytest.mark.asyncio
async def test_dead_driver_does_not_close_human_maintenance_target():
    runtime = ExternalChromeRuntime(SimpleNamespace())
    runtime.rotation_paused = True
    runtime.driver_state = "FAILED"
    cdp = SimpleNamespace(
        _policies={"human-login-target": object()}, send=AsyncMock(), close=AsyncMock()
    )
    runtime._entries["human"] = SimpleNamespace(
        cdp=cdp, identity=SimpleNamespace(identity_id="human")
    )
    await runtime._detach_driver()
    cdp.send.assert_not_awaited()
    cdp.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_old_lease_release_does_not_touch_new_entry_or_double_release():
    runtime = ExternalChromeRuntime(SimpleNamespace(), max_pages=1)
    await runtime._page_slots.acquire()
    identity = SimpleNamespace(identity_id="x", revision=1)
    instance = SimpleNamespace(instance_id="old", generation=1)
    old = SimpleNamespace(identity=identity, instance=instance, cdp=object(), active_pages=1)
    new = SimpleNamespace(active_pages=1)
    lease = _ExternalLease(runtime, old, object())
    runtime._entries["x"] = new
    await lease.abandon()
    await lease.abandon()
    assert old.active_pages == 0 and new.active_pages == 1
    assert runtime._page_slots._value == 1


@pytest.mark.asyncio
async def test_pending_rotation_runs_when_the_last_business_page_releases(monkeypatch):
    runtime = ExternalChromeRuntime(SimpleNamespace(), max_pages=1)
    await runtime._page_slots.acquire()
    identity = SimpleNamespace(identity_id="x", revision=1)
    entry = SimpleNamespace(
        identity=identity,
        instance=SimpleNamespace(instance_id="x", generation=1),
        cdp=object(),
        active_pages=1,
    )
    runtime._entries["x"] = entry
    runtime._rotation_due = True
    rotate = AsyncMock()
    monkeypatch.setattr(runtime, "maintain_driver", rotate)
    monkeypatch.setattr("doxagent.site_strategy.external_runtime._close_owned_page", AsyncMock())
    await _ExternalLease(runtime, entry, object()).__aexit__(None, None, None)
    rotate.assert_awaited_once()
    assert entry.active_pages == 0 and runtime._page_slots._value == 1


@pytest.mark.asyncio
async def test_reuters_http_denial_keeps_page_and_headers_for_classifier():
    response = SimpleNamespace(
        status=403,
        all_headers=AsyncMock(
            return_value={
                "server": "test",
                "set-cookie": "must-not-leak",
            }
        ),
    )
    page = SimpleNamespace(
        goto=AsyncMock(return_value=response),
        content=AsyncMock(return_value="<title>Verify you are human</title>"),
        url="https://www.reuters.com/site-search/?query=Micron",
    )
    with pytest.raises(RuntimeError) as caught:
        await capture_reuters_search(page, "Micron", 0)
    assert caught.value.status_code == 403
    assert caught.value.response_headers == {"server": "test"}
    assert "human" in caught.value.response_body


def test_short_queue_crawler_aging():
    crawler = _JointWaiter("s", "i", SitePurpose.CRAWLER, 1, 0, 1, 1, 0.5)
    assert crawler.rank(0.4)[0] == 2
    assert crawler.rank(0.6)[0] == 1
    assert classify_exception(RuntimeError("Connection closed while reading from the driver")) == (
        FailureCategory.RUNTIME_UNAVAILABLE,
        "browser_driver_unavailable",
    )


@pytest.mark.asyncio
async def test_unknown_denial_persisted_distinct_and_purpose_scoped(tmp_path):
    repository = SiteStrategyRepository(tmp_path / "site.db")
    health = CombinationHealthManager(repository)
    now = datetime.now(UTC)
    for op in ["a", "a", "b", "c"]:
        await health.mark_unknown_denial("reuters", "main", SitePurpose.CRAWLER, op, now=now)
    combo = AccessCombination(combination_id="main", profile_id="p", egress_id="e")
    _, ready, retry = await health.candidates(
        "reuters", [combo], excluded=set(), purpose=SitePurpose.CRAWLER, now=now
    )
    assert ready == [] and retry
    _, ready, _ = await health.candidates(
        "reuters", [combo], excluded=set(), purpose=SitePurpose.BODY, now=now
    )
    assert ready == [combo]
    await health.clear_purpose_denial("reuters", "main", SitePurpose.PROBE)
    assert "CRAWLER" in repository.get_runtime("reuters").combinations["main"].purpose_denials
    _, ready, _ = await health.candidates(
        "reuters",
        [combo],
        excluded=set(),
        purpose=SitePurpose.CRAWLER,
        now=now + timedelta(minutes=6),
    )
    assert ready == [combo]


@pytest.mark.asyncio
async def test_all_queries_failed_keeps_real_success_then_empty_success_recovers(tmp_path):
    repository = MessageBusV2Repository(tmp_path / "bus.db")
    service = MessageBusV2Service(repository)
    service.bootstrap()
    service.start_ticker("MU")
    source = service.require_source("reuters_site_search")
    binding = repository.get_binding("MU:reuters_site_search")
    assert binding is not None
    now = datetime.now(UTC)
    await service.accept_poll_result(
        source=source, binding=binding, result=PollResult(), attempted_at=now
    )
    failure = AcquisitionFailure(
        source_id=source.source_id,
        binding_id=binding.binding_id,
        ticker="MU",
        error_code="search_query_failed",
        error_message="http_403",
        raw_hash="abc",
        original_payload={},
    )
    await service.accept_poll_result(
        source=source,
        binding=binding,
        attempted_at=now + timedelta(seconds=60),
        result=PollResult(
            failures=[failure],
            acquisition_metadata={"query_failure_count": 1, "query_success_count": 0},
        ),
    )
    failed = repository.get_poll_state(binding)
    assert failed.status is PollStatus.FAILED and failed.consecutive_failures == 1
    assert failed.last_success_at == now
    await service.accept_poll_result(
        source=source,
        binding=binding,
        result=PollResult(site_access_deferred=True),
        attempted_at=now + timedelta(seconds=120),
    )
    deferred = repository.get_poll_state(binding)
    assert deferred.last_success_at == now and deferred.consecutive_failures == 1
    assert deferred.failure_since == failed.failure_since
    assert deferred.last_error_code == failed.last_error_code
    await service.accept_poll_result(
        source=source,
        binding=binding,
        result=PollResult(
            acquisition_metadata={"query_success_count": 1, "query_failure_count": 0}
        ),
        attempted_at=now + timedelta(seconds=180),
    )
    assert repository.get_poll_state(binding).status is PollStatus.SUCCEEDED
