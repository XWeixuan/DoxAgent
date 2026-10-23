"""Deterministic compound regressions: no publisher/provider network calls."""

import asyncio
import json
import time
from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from doxagent.site_strategy.governance_models import MaintenanceSession, OwnedBrowserTarget
from doxagent.site_strategy.retention import RetentionWorker
from doxagent.site_strategy.schema import (
    AccessDisposition,
    AccessRequest,
    CombinationRuntime,
    SitePurpose,
    utc_now,
)
from doxagent.site_strategy.verification import VerificationReport
from doxagent.site_strategy.verification_frames import observe_verification_document
from tests import test_browser_recovery as base
from tests import test_datadome_solver as paid


@pytest.fixture
def setup(tmp_path):
    yield from base.setup.__wrapped__(tmp_path)


@pytest.fixture
def solver_setup(setup):
    yield from paid.solver_setup.__wrapped__(setup)


def proof(setup, identity_id, purpose=SitePurpose.CRAWLER):
    repo, _, store, scope = setup
    runtime = repo.get_identity_runtime(identity_id)
    repo.save_identity_runtime(
        runtime.model_copy(update={"instance_id": "chrome"}), expected_generation=runtime.generation
    )
    store.record_capability(
        scope,
        identity_id,
        purpose.value,
        "READY" if purpose is SitePurpose.CRAWLER else "FULL",
        url=base.URL,
        instance_id="chrome",
    )


@pytest.mark.asyncio
async def test_restored_warm_primary_replaces_bad_active_backup_without_navigation(setup):
    repo, service, _, scope = setup
    state = repo.get_runtime(f"scope:{scope.scope_id}")
    state.active_combination_id = scope.fallback_identity_id
    state.combinations.setdefault(
        scope.fallback_identity_id, CombinationRuntime()
    ).manual_attention_required = True
    repo.save_runtime(state, expected_generation=state.generation)
    proof(setup, scope.primary_identity_id)
    service.runtime.execute = AsyncMock(side_effect=AssertionError("warm proof needs no canary"))
    task = service.failover.reconcile(scope, SitePurpose.CRAWLER)
    assert task is not None
    result = await task
    assert result["ready"] and result["warm"]
    assert repo.get_runtime(state.runtime_key).active_combination_id == scope.primary_identity_id
    service.runtime.execute.assert_not_awaited()


def test_healthy_backup_is_sticky_and_stale_instance_not_admitted(setup):
    repo, service, _, scope = setup
    state = repo.get_runtime(f"scope:{scope.scope_id}")
    state.active_combination_id = scope.fallback_identity_id
    repo.save_runtime(state, expected_generation=state.generation)
    proof(setup, scope.primary_identity_id)
    assert service.failover.reconcile(scope, SitePurpose.CRAWLER) is None
    runtime = repo.get_identity_runtime(scope.primary_identity_id)
    repo.save_identity_runtime(
        runtime.model_copy(update={"instance_id": "replacement"}),
        expected_generation=runtime.generation,
    )
    status = service.failover.candidate_status(
        scope, scope.primary_identity_id, SitePurpose.CRAWLER
    )
    assert not status["proof_ready"] and not status["selectable"]


def test_open_viewer_prevents_warm_activation(setup):
    _, service, store, scope = setup
    proof(setup, scope.primary_identity_id)
    store.save_session(
        MaintenanceSession(
            kind="LOGIN",
            scope_id=scope.scope_id,
            identity_id=scope.primary_identity_id,
            site_id=scope.owner_site_id,
        )
    )
    assert not service.failover.candidate_status(
        scope, scope.primary_identity_id, SitePurpose.CRAWLER
    )["selectable"]


@pytest.mark.asyncio
async def test_provider_cost_survives_uncertain_cookie_application(solver_setup):
    solver, recovery, scope, identity, egress, browser = solver_setup
    assert await solver.tick(recovery, scope, identity, egress)
    solver.provider.request.return_value = {
        "status": "ready",
        "cost": "0.00145",
        "solution": {"cookie": "SECRET"},
    }
    browser.apply.side_effect = TimeoutError()
    assert not await solver.tick(recovery, scope, identity, egress)
    job = solver.jobs(recovery.recovery_id)[0]
    assert job["provider_cost"] == "0.00145" and job["verification_state"] == "INDETERMINATE"
    assert "SECRET" not in json.dumps(job)
    assert not await solver.tick(recovery, scope, identity, egress)
    browser.apply.assert_awaited_once()


@pytest.mark.asyncio
async def test_paid_result_billing_collected_with_lost_original(solver_setup):
    solver, recovery, scope, identity, egress, browser = solver_setup
    await solver.tick(recovery, scope, identity, egress)
    browser.inspect.reset_mock()
    solver.provider.request.return_value = {"status": "ready", "cost": "0.00145"}
    assert not await solver.tick(
        recovery.model_copy(update={"page_status": "LOST"}), scope, identity, egress
    )
    assert solver.jobs(recovery.recovery_id)[0]["provider_cost"] == "0.00145"
    browser.inspect.assert_not_awaited()
    browser.apply.assert_not_awaited()


def test_applied_confirmation_has_finite_window_and_one_probe_reservation(solver_setup):
    solver, recovery, *_ = solver_setup
    job = solver.new_job(recovery, {}, "binding")
    job.update(status="APPLIED", applied_at=(utc_now() - timedelta(seconds=6)).isoformat())
    solver.save(job)
    assert 0 < solver.confirmation_delay(recovery, VerificationReport(challenge="BLOCKED")) <= 9
    assert solver.confirmation_delay(recovery, VerificationReport()) == 0
    assert solver.confirmation_delay(recovery, VerificationReport()) > 0
    job = solver.jobs(recovery.recovery_id)[0]
    job["applied_at"] = (utc_now() - timedelta(seconds=121)).isoformat()
    solver.save(job)
    assert solver.confirmation_delay(recovery, VerificationReport(challenge="BLOCKED")) is None
    assert solver.jobs(recovery.recovery_id)[0]["verification_state"] == "STILL_BLOCKED"


@pytest.mark.asyncio
async def test_identity_paid_limit_spans_incidents(solver_setup):
    solver, recovery, scope, identity, egress, _ = solver_setup
    for index in range(2):
        job = solver.new_job(
            recovery.model_copy(update={"recovery_id": f"old-{index}"}), {}, "binding"
        )
        job.update(status="FAILED", submitted_at=utc_now().isoformat())
        solver.save(job)
    assert not await solver.tick(recovery, scope, identity, egress)
    solver.provider.request.assert_not_awaited()
    assert solver.coordinator.store.get_recovery(identity.identity_id).phase != "RESOLVED"


def test_retention_protects_active_paid_record_and_target(setup):
    _, service, store, _ = setup
    recovery, target, _ = base.retained(setup, human=True)
    solver = service.recovery.datadome_solver
    job = solver.new_job(recovery, {}, "binding")
    old = (utc_now() - timedelta(days=40)).isoformat()
    job.update(status="APPLIED", updated_at=old)
    with service.repository.transaction() as db:
        db.execute(
            "update datadome_solver_jobs set updated_at=?,data_json=? where job_id=?",
            (old, json.dumps(job), job["job_id"]),
        )
    target = store.get_target(target.allocation_id)
    store.save_target(
        target.model_copy(update={"state": "LOST", "updated_at": utc_now() - timedelta(days=40)})
    )
    RetentionWorker(service.repository).run_once()
    assert solver.jobs(recovery.recovery_id)
    assert store.get_target(target.allocation_id)


def test_expired_probe_has_one_interrupted_terminal_even_after_repeat_claim(setup):
    repo, service, store, _ = setup
    recovery, _, _ = base.retained(setup)
    claimed = store.claim_recovery(recovery.identity_id, owner="old")
    store.save_recovery(
        claimed.model_copy(
            update={
                "probe_id": "durable-probe",
                "probe_stage": "SITE",
                "probe_started_at": utc_now(),
            }
        ),
        expected_generation=claimed.generation,
    )
    store.claim_recovery(recovery.identity_id, owner="new", now=utc_now() + timedelta(seconds=101))
    service.recovery.event(claimed, "PROBE_FINISHED", probe_id="durable-probe", result="TIMEOUT")
    with repo._lock:
        rows = repo._connection.execute(
            "select payload_json from access_events where category='PROBE_FINISHED'"
        ).fetchall()
    assert len(rows) == 1
    assert json.loads(rows[0][0])["result"] == "INTERRUPTED"


@pytest.mark.asyncio
async def test_probe_failure_closes_receipt_and_releases_writer(setup):
    repo, service, store, _ = setup
    recovery, _, _ = base.retained(setup)
    store.save_recovery(
        recovery.model_copy(update={"next_probe_at": utc_now()}),
        expected_generation=recovery.generation,
    )
    service.recovery._observe = AsyncMock(side_effect=RuntimeError("temporarily unreadable"))
    service.runtime.probe_recovery = AsyncMock(side_effect=TimeoutError())
    await service.recovery._run(recovery.identity_id)
    with repo._lock:
        rows = repo._connection.execute(
            "select category,payload_json from access_events where category like 'PROBE_%'"
        ).fetchall()
    assert [r[0] for r in rows] == ["PROBE_STARTED", "PROBE_FINISHED"]
    assert json.loads(rows[-1][1])["result"] == "TIMEOUT"
    budget = service.runtime.probe_recovery.await_args.args[0].remaining_budget_ms
    assert 10000 <= budget <= 75000
    assert not service.profile_use._exclusive


@pytest.mark.asyncio
async def test_identical_queries_coalesce_but_consumer_ids_stay_independent(setup):
    _, service, _, scope = setup
    entered = asyncio.Event()
    release = asyncio.Event()

    async def execute(request):
        entered.set()
        await release.wait()
        return service._result(
            request,
            service._resolve_request(request),
            AccessDisposition.SUCCESS,
            reason="ok",
            started=time.monotonic(),
        )

    service._execute_idempotent = AsyncMock(side_effect=execute)
    request = AccessRequest(
        operation_id="ticker-a",
        url=base.URL,
        purpose="CRAWLER",
        mode="BROWSER",
        origin_scope_id=scope.scope_id,
        recipe_ref="builtin:investing_ticker@1",
        recipe_parameters={"ticker": "RKLB"},
    )
    other = request.model_copy(update={"request_id": "second", "operation_id": "ticker-b"})
    first = asyncio.create_task(service.execute(request))
    await entered.wait()
    second = asyncio.create_task(service.execute(other))
    await asyncio.sleep(0)
    release.set()
    a, b = await asyncio.gather(first, second)
    assert a.request_id != b.request_id and b.operation_id == "ticker-b"
    assert b.served_from_shared_capture and a.capture_id == b.capture_id
    service._execute_idempotent.assert_awaited_once()
    assert service._shared_key(request) != service._shared_key(
        request.model_copy(update={"recipe_parameters": {"ticker": "INTC"}})
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "visible,readable,css_visibility,expected",
    [
        (True, True, "visible", "INTERACTIVE"),
        (False, True, "visible", "NONE"),
        (True, False, "visible", "UNKNOWN"),
        (True, True, "hidden", "NONE"),
        (True, True, None, "UNKNOWN"),
    ],
)
async def test_shared_observation_hidden_asset_vs_real_oopif(
    visible, readable, css_visibility, expected
):
    url = "https://geo.captcha-delivery.com/captcha/?t=fe"
    html = '<article>Normal news</article><iframe src="' + url + '"></iframe>'

    async def call(method, params=None, **kwargs):
        if method == "DOM.querySelectorAll":
            return {"nodeIds": [2]}
        if method == "DOM.describeNode":
            return {"node": {"frameId": "owner-frame", "attributes": ["src", url]}}
        if method == "DOM.getBoxModel":
            return {"model": {"width": 900 if visible else 0, "height": 600 if visible else 0}}
        if method == "CSS.getComputedStyleForNode":
            return {
                "computedStyle": [
                    {"name": "visibility", "value": css_visibility},
                    {"name": "opacity", "value": "1"},
                ]
                if css_visibility is not None
                else []
            }
        if method == "Target.getTargetInfo":
            return {"targetInfo": {"targetId": "owner-frame", "type": "iframe", "url": url}}
        if method == "Target.attachToTarget":
            return {"sessionId": "child"}
        if method == "DOM.getDocument":
            return {"root": {"nodeId": 3}}
        if method == "DOM.getOuterHTML":
            return {"outerHTML": "<p>Slide right to secure your access</p>" if readable else ""}
        return {}

    document = await observe_verification_document(call, root={"nodeId": 1}, html=html)
    assert document["assessment"].kind == expected
    assert document["frames"][0]["frame_read_state"] == ("READABLE" if readable else "EMPTY")


@pytest.mark.asyncio
async def test_single_deadline_reduces_probe_after_observation_and_control_wait(setup, monkeypatch):
    _, service, store, _ = setup
    recovery, _, _ = base.retained(setup)
    store.save_recovery(
        recovery.model_copy(update={"next_probe_at": utc_now()}),
        expected_generation=recovery.generation,
    )
    now = [time.monotonic()]
    monkeypatch.setattr(
        "doxagent.site_strategy.recovery.time", SimpleNamespace(monotonic=lambda: now[0])
    )

    async def observe(*args):
        now[0] += 10
        raise RuntimeError("DOM unavailable")

    @asynccontextmanager
    async def capacity(**kwargs):
        now[0] += 20
        yield

    service.recovery._observe = observe
    service.runtime.recovery_capacity = capacity
    service.runtime.probe_recovery = AsyncMock(side_effect=TimeoutError())
    await service.recovery._run(recovery.identity_id)
    assert service.runtime.probe_recovery.await_args.args[0].remaining_budget_ms == 50000


@pytest.mark.asyncio
async def test_cancelled_probe_closes_receipt_and_frees_profile(setup):
    repo, service, store, _ = setup
    recovery, _, _ = base.retained(setup)
    store.save_recovery(
        recovery.model_copy(update={"next_probe_at": utc_now()}),
        expected_generation=recovery.generation,
    )
    service.recovery._observe = AsyncMock(side_effect=RuntimeError("DOM unavailable"))
    service.runtime.probe_recovery = AsyncMock(side_effect=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await service.recovery._run(recovery.identity_id)
    with repo._lock:
        rows = repo._connection.execute(
            "select payload_json from access_events where category='PROBE_FINISHED'"
        ).fetchall()
    assert len(rows) == 1 and json.loads(rows[0][0])["result"] == "CANCELLED"
    assert not service.profile_use._exclusive


@pytest.mark.asyncio
async def test_orphan_provider_collection_never_needs_browser_control(solver_setup):
    solver, recovery, scope, identity, egress, browser = solver_setup
    await solver.tick(recovery, scope, identity, egress)
    solver.coordinator.store.save_recovery(
        recovery.model_copy(update={"phase": "RESOLVED"}), expected_generation=recovery.generation
    )
    browser.inspect.reset_mock()
    solver.provider.request.return_value = {
        "status": "ready",
        "cost": "0.00145",
        "solution": {"cookie": "SECRET"},
    }
    await solver.collect_orphan_results()
    job = solver.jobs(recovery.recovery_id)[0]
    assert job["verification_state"] == "SUPERSEDED" and job["provider_cost"] == "0.00145"
    assert "SECRET" not in json.dumps(job)
    browser.inspect.assert_not_awaited()


def test_probation_fields_persist_and_do_not_end_without_natural_success(setup):
    repo, service, _, scope = setup
    state = repo.get_identity_runtime(scope.primary_identity_id)
    updated = state.model_copy(
        update={
            "recovered_at": utc_now() - timedelta(minutes=6),
            "probation_until": utc_now() - timedelta(minutes=1),
            "last_recovery_id": "incident",
        }
    )
    repo.save_identity_runtime(updated, expected_generation=state.generation)
    assert service._identity_navigation_interval(scope.primary_identity_id, 3000) == 10000
    repo.save_identity_runtime(
        updated.model_copy(update={"natural_success_at": utc_now()}),
        expected_generation=state.generation,
    )
    assert service._identity_navigation_interval(scope.primary_identity_id, 3000) == 3000


def test_fast_relapse_persists_across_incident_and_updates_only_old_job(setup):
    repo, service, store, _ = setup
    old, _, _ = base.retained(setup)
    store.save_recovery(
        old.model_copy(update={"phase": "RESOLVED"}), expected_generation=old.generation
    )
    runtime = repo.get_identity_runtime(old.identity_id)
    repo.save_identity_runtime(
        runtime.model_copy(update={"recovered_at": utc_now(), "last_recovery_id": old.recovery_id}),
        expected_generation=runtime.generation,
    )
    solver = service.recovery.datadome_solver
    job = solver.new_job(old, {}, "binding")
    job.update(
        status="APPLIED", verification_state="BUSINESS_VERIFIED", verified_purposes=["CRAWLER"]
    )
    solver.save(job)
    target = store.save_target(
        OwnedBrowserTarget(
            identity_id=old.identity_id,
            browser_instance_id="chrome",
            worker_epoch=1,
            target_id="new-scene",
            state="LIVE",
        )
    )
    new = store.begin_recovery(
        old.model_copy(
            update={
                "recovery_id": "new-incident",
                "phase": "OBSERVING",
                "original_allocation_id": target.allocation_id,
                "original_target_id": target.target_id,
            }
        )
    )
    assert new.recovery_id == "new-incident"
    runtime = repo.get_identity_runtime(old.identity_id)
    assert runtime.recent_relapses[-1]["previous_recovery_id"] == old.recovery_id
    assert solver.jobs(old.recovery_id)[0]["recurrence_at"]
    assert not solver.jobs(new.recovery_id)


def test_legacy_applied_migration_is_additive_and_idempotent(solver_setup):
    solver, recovery, *_ = solver_setup
    job = solver.new_job(recovery, {}, "binding")
    job.pop("verification_state")
    job.update(status="APPLIED", task_id=123, cost="0.00145")
    solver.save(job)
    solver.normalize_legacy_jobs()
    solver.normalize_legacy_jobs()
    saved = solver.jobs(recovery.recovery_id)
    assert len(saved) == 1 and saved[0]["task_id"] == 123
    assert saved[0]["status"] == "APPLIED" and saved[0]["verification_state"] == "INDETERMINATE"
    assert "business_verified_at" not in saved[0]


def test_provider_unsolvable_has_terminal_verification_state(solver_setup, monkeypatch):
    solver, recovery, *_ = solver_setup
    job = solver.new_job(recovery, {}, "binding")
    job.update(status="PENDING", task_id=123)
    monkeypatch.setattr(solver.coordinator.service.failover, "schedule", lambda *args: None)
    solver.fail(job, recovery, "ERROR_CAPTCHA_UNSOLVABLE")
    saved = solver.jobs(recovery.recovery_id)[0]
    assert saved["status"] == "FAILED" and saved["verification_state"] == "STILL_BLOCKED"
    assert saved.get("provider_cost") is None


@pytest.mark.parametrize(
    "kind,query,ready",
    [
        ("NONE", "Micron", True),
        ("INTERACTIVE", "Micron", False),
        ("UNKNOWN", "Micron", False),
        ("NONE", "Samsung", False),
    ],
)
def test_reuters_original_document_proves_only_matching_unblocked_query(setup, kind, query, ready):
    from doxagent.site_strategy.recovery import recovery_context, verify_capture
    from doxagent.site_strategy.runtime import RuntimeResponse

    _, service, store, _ = setup
    scope = store.get_scope("source:reuters_site_search")
    request = AccessRequest(
        operation_id="read-original",
        url="https://www.reuters.com/site-search/?query=Micron",
        purpose="CRAWLER",
        mode="BROWSER",
        recipe_ref="builtin:reuters_search@1",
        recipe_parameters={"query": "Micron"},
    )
    capture = RuntimeResponse(
        0,
        "https://www.reuters.com/site-search/?query=" + query + "&offset=0",
        {},
        body='<main><h1>Search results for Micron</h1><a href="/technology/micron-earnings-2026-10-23/">Micron announces new earnings outlook</a></main>',
        assessment={"kind": kind},
        provenance=SimpleNamespace(instance_id="chrome"),
    )
    report = verify_capture(service, scope, recovery_context(request), capture, observed=True)
    assert (report.crawler == "READY") is ready


@pytest.mark.parametrize(
    "text", ["Search results for Ondas 0 results", "No search results match the term “Ondas”"]
)
def test_reuters_legitimate_zero_results_are_business_proof(setup, text):
    from doxagent.site_strategy.recovery import recovery_context, verify_capture
    from doxagent.site_strategy.runtime import RuntimeResponse

    _, service, store, _ = setup
    scope = store.get_scope("source:reuters_site_search")
    request = AccessRequest(
        operation_id="zero-results",
        url="https://www.reuters.com/site-search/?query=Ondas&offset=0",
        purpose="CRAWLER",
        mode="BROWSER",
        recipe_ref="builtin:reuters_search@1",
        recipe_parameters={"query": "Ondas"},
    )
    capture = RuntimeResponse(
        0,
        request.url,
        {},
        body="<main>" + text + "</main>",
        assessment={"kind": "NONE"},
        provenance=SimpleNamespace(instance_id="chrome"),
    )
    report = verify_capture(service, scope, recovery_context(request), capture, observed=True)
    assert report.challenge == "CLEARED" and report.crawler == "READY"


@pytest.mark.asyncio
async def test_reuters_live_recipe_accepts_same_zero_result_contract():
    from doxagent.message_bus_v2.reuters_sources import capture_reuters_search

    async def wait(expression, **kwargs):
        assert "No search results match the term" in expression

    page = SimpleNamespace(
        goto=AsyncMock(return_value=SimpleNamespace(status=200)),
        wait_for_function=wait,
        evaluate=AsyncMock(return_value=[]),
    )
    assert await capture_reuters_search(page, "Ondas", 0) == []
