import asyncio
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from doxagent.site_strategy.budget import AdmissionDeferred, JointBudget
from doxagent.site_strategy.schema import SitePurpose


def permit(budget, purpose, identity="one", **extra):
    return budget.permit(purpose, site_key=identity, identity_id=identity,
        site_max_concurrency=1, site_min_interval_ms=0, identity_max_concurrency=1,
        identity_min_interval_ms=0, timeout_seconds=1, **extra)


async def test_detached_body_keeps_age_and_wins_next_release_without_holding_a_slot():
    budget = JointBudget(max_concurrency=1)
    ready = (datetime.now(UTC)-timedelta(seconds=20)).isoformat()
    async with permit(budget, SitePurpose.CRAWLER):
        before = time.monotonic()
        with pytest.raises(AdmissionDeferred) as delayed:
            async with permit(budget, SitePurpose.BODY, task_key="old:1", ready_since=ready):
                pytest.fail("occupied browser must not be entered")
        assert time.monotonic()-before < .2
        assert .1 <= delayed.value.retry_seconds <= 2
        claim = budget._body_claims["old:1"]
        assert budget._active == 1 and not claim.attached
    # A newer task may not steal the freed capacity while old demand is detached.
    with pytest.raises(AdmissionDeferred):
        async with permit(budget, SitePurpose.BODY, task_key="new:1", ready_since=datetime.now(UTC).isoformat()):
            pytest.fail("must preserve old priority")
    async with permit(budget, SitePurpose.BODY, task_key="old:1", ready_since=ready):
        assert budget._active == 1
    assert "old:1" not in budget._body_claims
    budget.forget_identity("one")
    assert not budget._waiters and budget._active == 0


async def test_busy_identity_demand_does_not_reserve_free_capacity_on_another_identity():
    budget = JointBudget(max_concurrency=2)
    async with permit(budget, SitePurpose.CRAWLER, "busy"):
        with pytest.raises(AdmissionDeferred):
            async with permit(budget, SitePurpose.BODY, "busy", task_key="a:1"):
                pass
        async with permit(budget, SitePurpose.CRAWLER, "free"):
            assert budget._active == 2
    budget.forget_identity("busy")


async def test_crawler_gets_bounded_progress_under_continuous_body_demand():
    budget = JointBudget(max_concurrency=1)
    entered = asyncio.Event()

    async def crawler():
        async with permit(budget, SitePurpose.CRAWLER):
            entered.set()

    async with permit(budget, SitePurpose.BODY):
        task = asyncio.create_task(crawler())
        await asyncio.sleep(.02)
        next(w for w in budget._waiters if w.purpose is SitePurpose.CRAWLER).enqueued_at -= 6
        # Three BODY grants are enough to yield the next contended opportunity.
        budget._body_streak = 3
        with pytest.raises(AdmissionDeferred):
            async with permit(budget, SitePurpose.BODY, task_key="pending:1"):
                pass
    await asyncio.wait_for(entered.wait(), .5)
    await task
    budget.forget_identity("one")


async def test_no_demand_leaves_all_existing_crawler_capacity_available():
    budget = JointBudget(max_concurrency=4)
    release = asyncio.Event()
    count = 0

    async def work(identity):
        nonlocal count
        async with permit(budget, SitePurpose.CRAWLER, identity):
            count += 1
            await release.wait()

    tasks = [asyncio.create_task(work(str(i))) for i in range(4)]
    await asyncio.sleep(.03)
    assert count == 4
    release.set()
    await asyncio.gather(*tasks)


async def test_abandoned_claim_expires_and_new_input_generation_replaces_old_one():
    budget = JointBudget(max_concurrency=1)
    async with permit(budget, SitePurpose.CRAWLER):
        for key in ["article:1", "article:2"]:
            with pytest.raises(AdmissionDeferred):
                async with permit(budget, SitePurpose.BODY, task_key=key):
                    pass
        assert set(budget._body_claims) == {"article:2"}
    budget._expire_claims(time.monotonic()+4)
    assert not budget._body_claims and not budget._waiters


async def test_body_anchor_is_not_a_listing_anchor_and_is_closed_exactly_once():
    from doxagent.site_strategy.fetch_anchors import FetchAnchor, FetchAnchors
    pool = FetchAnchors()
    identity = SimpleNamespace(identity_id="i", revision=1, access=SimpleNamespace(max_concurrency=1))
    gate = SimpleNamespace(blocked=False, close=AsyncMock())
    page = SimpleNamespace(is_closed=lambda:False)
    lease = SimpleNamespace(__aexit__=AsyncMock())
    anchor = FetchAnchor(lease, page, gate, None, 1, "egress", time.monotonic(), purpose="BODY")
    assert pool.put(identity, anchor, freeze=False, limit=1)
    assert await pool.take(identity, SimpleNamespace(egress_id="egress")) is None
    assert await pool.take(identity, SimpleNamespace(egress_id="egress"), purpose="BODY") is anchor
    assert pool.put(identity, anchor, freeze=False, limit=1)
    await pool.close()
    lease.__aexit__.assert_awaited_once()


async def test_admission_receipt_is_not_cached_after_capacity_changes():
    from doxagent.site_strategy.schema import AccessDisposition
    from doxagent.site_strategy.service import SiteStrategyService
    service = object.__new__(SiteStrategyService)
    # Deferrals must not touch cache state at all; normal receipts retain their cache.
    result = SimpleNamespace(disposition=AccessDisposition.BUDGET_DEFERRED)
    await service._put_cache(result, cache_key="same-body")


def test_durable_body_order_uses_ready_age_instead_of_reassigned_due_time(tmp_path):
    from tests.test_content_enrichment_hub import _setup
    from doxagent.message_bus_v2.live_repository import LiveRepository
    repo, _, _, _ = _setup(tmp_path)
    live = LiveRepository(repo); live.install()
    now = datetime.now(UTC)
    with repo.transaction() as db:
        for key, age, due_age in [('old',3600,0),('new',5,2)]:
            at=(now-timedelta(seconds=age)).isoformat()
            db.execute("INSERT INTO live_articles(article_key,source_id,url,publisher,material_hint,first_seen_at,"
                "input_ref,state,due_at,work_kind,ready_since) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (key,'news','https://example.test/'+key,'publisher',key,at,'{}','FETCH_DUE',
                 (now-timedelta(seconds=due_age)).isoformat(),'first_download',at))
    assert live.claim_candidates(limit=1)==[('old',1)]
    assert live.claim(limit=1)[0][0]=='old'
    with repo._connect() as db:
        row=db.execute("SELECT ready_since,first_seen_at FROM live_articles WHERE article_key='old'").fetchone()
        assert row[0]==row[1]


@pytest.mark.parametrize('scope,retained,stops',[
    ('publisher:newsfile',1,0),('publisher:newsfile',0,1),('publisher:other',1,1)])
async def test_only_verified_newsfile_body_survives_public_work_complete(monkeypatch,scope,retained,stops):
    from contextvars import ContextVar
    from doxagent.site_strategy import worker_runtime as module
    from doxagent.site_strategy.runtime import RuntimeResponse
    from doxagent.site_strategy.schema import AccessRequest, AccessMode, BrowserRuntimeKind
    runtime=object.__new__(module.WorkerSiteAccessRuntime)
    runtime._business_slots=asyncio.Semaphore(4);runtime._recovery_slots=asyncio.Semaphore(1)
    runtime._reserved_recovery=ContextVar('reserved-test',default=False);runtime._tokens={}
    runtime.workers=SimpleNamespace(command=AsyncMock(return_value={}),workers={
        'public':SimpleNamespace(stage='IDLE',pending={})})
    runtime.browser_runtimes=SimpleNamespace(stop_identity=AsyncMock())
    response=RuntimeResponse(200,'https://example.test/article',{},stage_timings={'body_page_retained':retained})
    monkeypatch.setattr(module,'decode_response',lambda _:response)
    identity=SimpleNamespace(identity_id='public',execution_mode='public_identity',owner_scope_id=scope,
                             runtime_kind=BrowserRuntimeKind.MANAGED_PLAYWRIGHT)
    request=AccessRequest(operation_id='body:test',url=response.final_url,purpose=SitePurpose.BODY,mode=AccessMode.BROWSER,remaining_budget_ms=1000)
    await runtime._browser(request,SimpleNamespace(model_dump=lambda **_:{}),identity,None)
    assert runtime.browser_runtimes.stop_identity.await_count==stops
    assert runtime._business_slots._value==4
