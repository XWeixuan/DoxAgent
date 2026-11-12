"""Reliability regressions with no publisher/provider traffic or browser input."""

import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from doxagent.site_strategy.datadome_solver import record_solver_diagnostic
from doxagent.site_strategy.governance_models import OwnedBrowserTarget
from doxagent.site_strategy.human_verification import manual_review_reason
from doxagent.site_strategy.recovery import control_available, control_backoff
from doxagent.site_strategy.schema import AccessRequest, BrowserRuntimeKind, utc_now
from doxagent.site_strategy.service import recovery_pending_reason
from doxagent.site_strategy.worker_manager import IdentityOperationError
from doxagent.site_strategy.worker_runtime import WorkerSiteAccessRuntime
from doxagent.site_strategy.targets import TargetOwner
from tests import test_browser_recovery as base
from tests import test_datadome_solver as paid


@pytest.fixture
def setup(tmp_path):
    yield from base.setup.__wrapped__(tmp_path)


@pytest.fixture
def solver_setup(setup):
    yield from paid.solver_setup.__wrapped__(setup)


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["browser_cdp_attach_failed", "owned_target_cleanup_pending"])
async def test_attach_failure_retires_controller_without_replay_or_native_drain(setup, reason):
    repo, _, store, scope = setup
    retained, target, _ = base.retained(setup)
    identity = repo.get_identity(scope.primary_identity_id).model_copy(
        update={"runtime_kind": BrowserRuntimeKind.EXTERNAL_CHROME})
    runtime = WorkerSiteAccessRuntime.__new__(WorkerSiteAccessRuntime)
    runtime.repository = repo
    error = IdentityOperationError("RUNTIME_UNAVAILABLE", reason)
    runtime.workers = SimpleNamespace(command=AsyncMock(side_effect=error), retire=AsyncMock())
    runtime._business_slots = asyncio.Semaphore(1)
    runtime._recovery_slots = asyncio.Semaphore(1)
    request = AccessRequest(operation_id="poll", url=base.URL, purpose="CRAWLER",
                            mode="BROWSER", remaining_budget_ms=60000)
    with pytest.raises(IdentityOperationError) as failed:
        await runtime._browser(request, SimpleNamespace(site_id="reuters", model_dump=lambda **_: {}),
                               identity, repo.get_egress(identity.egress_id))
    runtime.workers.command.assert_awaited_once()
    runtime.workers.retire.assert_awaited_once_with(identity.identity_id, graceful=False)
    assert failed.value.controller_recycled and not failed.value.control_recovery_attempted
    assert store.get_target(target.allocation_id).state == "LIVE"
    assert store.get_recovery(identity.identity_id).original_target_id == retained.original_target_id
    assert runtime._business_slots._value == 1


def test_control_backoff_persists_separate_page_state_and_resets(setup):
    _, _, store, _ = setup
    current, target, _ = base.retained(setup, human=True)
    original = current.assessment.copy()
    for expected, count in [(60, 1), (120, 2), (300, 3), (600, 4), (600, 5)]:
        current, delay = control_backoff(current, "browser_cdp_attach_failed")
        store.save_recovery(current, expected_generation=current.generation)
        current = store.get_recovery(current.identity_id)
        assert delay == expected and current.document_recovery["control"]["failures"] == count
        assert expected - 2 <= (current.next_probe_at - utc_now()).total_seconds() <= expected
        assert recovery_pending_reason(current) == manual_review_reason(current) == "browser_control_unavailable"
        assert current.assessment == original and current.original_target_id == target.target_id
    current = control_available(current)
    assert not current.document_recovery.get("control")
    assert recovery_pending_reason(current) != "browser_control_unavailable"


@pytest.mark.asyncio
async def test_failed_probe_records_control_failure_and_closes_receipt(setup):
    repo, service, store, _ = setup
    current, _, _ = base.retained(setup)
    store.save_recovery(current.model_copy(update={"next_probe_at": utc_now()}),
                        expected_generation=current.generation)
    service.recovery._observe = AsyncMock(side_effect=RuntimeError("DOM unavailable"))
    service.runtime.probe_recovery = AsyncMock(side_effect=IdentityOperationError(
        "RUNTIME_UNAVAILABLE", "browser_cdp_attach_failed"))
    await service.recovery._run(current.identity_id)
    current = store.get_recovery(current.identity_id)
    assert current.document_recovery["control"]["failures"] == 1
    assert current.probe_stage == "CONTROL_UNAVAILABLE"
    assert current.next_due_at > utc_now() + timedelta(seconds=55)
    assert current.next_probe_at > utc_now() + timedelta(seconds=55)
    rows = repo._connection.execute("select category,payload_json from access_events "
                                    "where category in ('PROBE_STARTED','PROBE_FINISHED')").fetchall()
    assert len(rows) == 2
    finished = json.loads(next(row[1] for row in rows if row[0] == "PROBE_FINISHED"))
    assert finished["result"] == "CONTROL_UNAVAILABLE" and finished["request_sent"] is False
    assert not service.profile_use._exclusive


def test_probation_exits_continuously_and_restores_configured_rate(setup, monkeypatch):
    repo, service, _, scope = setup
    clock = [utc_now()]
    monkeypatch.setattr("doxagent.site_strategy.service.utc_now", lambda: clock[0])
    start = clock[0]
    state = repo.get_identity_runtime(scope.primary_identity_id)
    repo.save_identity_runtime(state.model_copy(update={
        "recovered_at": start, "probation_until": start + timedelta(seconds=300),
        "probation_interval_ms": 10000, "natural_success_at": start,
    }), expected_generation=state.generation)
    results = []
    for seconds in (299, 300, 301, 450, 599, 600, 900):
        clock[0] = start + timedelta(seconds=seconds)
        results.append(service._identity_navigation_interval(scope.primary_identity_id, 1500))
    assert results[:2] == [10000, 10000]
    assert results[1] - results[2] <= 30
    assert results == sorted(results, reverse=True)
    assert results[-2:] == [1500, 1500]


@pytest.mark.parametrize("elapsed, protected", [(406, True), (721, False), (3600, False)])
def test_late_relapse_is_recorded_and_recovery_window_is_explicit(setup, elapsed, protected):
    repo, _, store, _ = setup
    old, _, _ = base.retained(setup)
    store.save_recovery(old.model_copy(update={"phase": "RESOLVED"}), expected_generation=old.generation)
    now = utc_now()
    recovered = now - timedelta(seconds=elapsed)
    state = repo.get_identity_runtime(old.identity_id)
    repo.save_identity_runtime(state.model_copy(update={"capability_windows": {"CRAWLER": {
        "recovery_id": old.recovery_id, "recovered_at": recovered.isoformat(),
        "capability_epoch": 1, "probation_until": (recovered + timedelta(seconds=300)).isoformat(),
    }}}), expected_generation=state.generation)
    target = store.save_target(OwnedBrowserTarget(identity_id=old.identity_id,
        browser_instance_id="chrome", worker_epoch=1, target_id="next", state="LIVE"))
    store.begin_recovery(old.model_copy(update={
        "recovery_id": "new-incident", "phase": "OBSERVING",
        "assessment": {"kind": "INTERACTIVE"},
        "original_allocation_id": target.allocation_id, "original_target_id": target.target_id,
    }))
    relapse = repo.get_identity_runtime(old.identity_id).recent_relapses[-1]
    assert relapse["previous_recovery_id"] == old.recovery_id
    assert relapse["within_recovery_window"] is protected
    assert elapsed <= relapse["seconds_to_recurrence"] < elapsed + 2


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["APPLIED", "FAILED", "SUPERSEDED", "SUBMITTING", "APPLYING"])
async def test_terminal_job_has_no_browser_input_no_inspection_and_no_purchase(solver_setup, status):
    solver, recovery, scope, identity, egress, browser = solver_setup
    job = solver.new_job(recovery, {}, "route")
    job.update(status=status, task_id=123, applied_at=utc_now().isoformat())
    solver.save(job)
    assert not await solver.tick(recovery, scope, identity, egress)
    browser.inspect.assert_not_awaited()
    browser.prepare_retry.assert_not_awaited()
    browser.apply.assert_not_awaited()
    solver.provider.request.assert_not_awaited()
    assert solver.coordinator.store.get_recovery(recovery.identity_id).phase != "RESOLVED"


@pytest.mark.parametrize("with_capture", [False, True])
def test_diagnostics_are_bounded_redacted_and_skip_identical_business_results(solver_setup, with_capture):
    solver, recovery, _, identity, _, _ = solver_setup
    repo = solver.coordinator.service.repository
    job = solver.new_job(recovery, {}, "route")
    job.update(status="APPLIED", task_id=123, verification_state="STILL_BLOCKED")
    solver.save(job)
    for i in range(20):
        capture = SimpleNamespace(final_url="https://www.reuters.com/site-search/?secret=x",
            status_code=200, headers={"Authorization": "SECRET"}, body="SECRET_" + str(i), provenance=None)
        record_solver_diagnostic(repo, identity.identity_id, "CRAWLER", {
            "outcome": "SUCCESS", "http_status": 200, "query_key": str(i),
            "url": "https://private.test/?secret=x", "cookie": "SECRET",
            "reason": "https://private.test/",
        }, capture=capture if with_capture else None)
    saved = solver.jobs(recovery.recovery_id)[0]
    assert len(saved["diagnostic_timeline"]) == 1
    assert "SECRET" not in json.dumps(saved) and "private.test" not in json.dumps(saved)
    assert saved["verification_state"] == "STILL_BLOCKED"  # Diagnostic is never a health promotion.
    for i in range(15):
        record_solver_diagnostic(repo, identity.identity_id, "BODY", {
            "outcome": "SUCCESS" if i % 2 else "EXTRACTION_ERROR", "http_status": 200,
            "body_quality": "FULL" if i % 2 else "PREVIEW",
        })
    saved = solver.jobs(recovery.recovery_id)[0]
    assert len(saved["diagnostic_timeline"]) == 8
    assert len(saved["diagnostics"]) == 2


@pytest.mark.asyncio
async def test_terminal_reason_is_shared_by_source_and_maintenance(solver_setup):
    solver, recovery, _, identity, _, _ = solver_setup
    job = solver.new_job(recovery, {}, "route")
    job.update(status="APPLIED", task_id=123, verification_state="STILL_BLOCKED",
               solver_verification="INDETERMINATE")
    solver.save(job)
    await solver.coordinator._defer(recovery, "verification_pending", delay=60)
    current = solver.coordinator.store.get_recovery(identity.identity_id)
    assert recovery_pending_reason(current) == manual_review_reason(current, job)
    assert current.last_error == "provider_result_still_blocked_manual_review"


@pytest.mark.asyncio
async def test_native_surviving_closed_business_is_reclaimed_but_original_and_unknown_are_kept(setup):
    repo, _, store, _ = setup
    recovery, original, _ = base.retained(setup, human=True)
    orphan = store.save_target(OwnedBrowserTarget(identity_id=recovery.identity_id,
        browser_instance_id="chrome", worker_epoch=1, target_id="stale-closed",
        owner_kind="BUSINESS", state="CLOSED"))
    owner = TargetOwner(repo)
    assert all(t.target_id != orphan.target_id for t in owner.list_for_instance(recovery.identity_id, "chrome"))
    live = {original.target_id, orphan.target_id, "unknown-human"}
    closed = []

    async def send(method, params=None):
        if method == "Target.getTargets":
            return {"targetInfos": [{"targetId": t} for t in live]}
        assert method == "Target.closeTarget"
        closed.append(params["targetId"])
        live.discard(params["targetId"])
        return {"success": True}

    await owner.reconcile(SimpleNamespace(send=send), recovery.identity_id, "chrome")
    assert closed == [orphan.target_id]
    assert store.get_target(orphan.allocation_id).state == "CLOSED"
    assert live == {original.target_id, "unknown-human"}
    assert store.get_target(original.allocation_id).owner_kind == "CHALLENGE"


@pytest.mark.asyncio
async def test_positive_close_ack_without_native_absence_remains_pending(setup):
    repo, _, store, scope = setup
    orphan = store.save_target(OwnedBrowserTarget(identity_id=scope.primary_identity_id,
        browser_instance_id="chrome", worker_epoch=1, target_id="still-live", state="LIVE"))
    cdp = SimpleNamespace(send=AsyncMock(side_effect=lambda method, params=None: (
        {"targetInfos": [{"targetId": "still-live"}]} if method == "Target.getTargets" else {"success": True})))
    await TargetOwner(repo).reconcile(cdp, scope.primary_identity_id, "chrome")
    assert store.get_target(orphan.allocation_id).state == "CLOSE_PENDING"


def test_closed_bookkeeping_cannot_override_current_human_original(setup):
    repo, _, store, _ = setup
    recovery, original, _ = base.retained(setup, human=True)
    with pytest.raises(ValueError, match="human-owned targets"):
        store.save_target(original.model_copy(update={"owner_kind": "BUSINESS", "state": "CLOSED"}))
    result = TargetOwner(repo)._event("close_claim", {"allocation_id": original.allocation_id,
        "native_target_id": original.target_id, "native_instance_id": "chrome"})
    assert not result["claimed"]
    assert store.get_recovery(recovery.identity_id).original_target_id == original.target_id


@pytest.mark.asyncio
async def test_external_failed_cleanup_releases_lease_but_keeps_durable_pending(monkeypatch):
    from doxagent.site_strategy.external_runtime import _ExternalLease

    lease = object.__new__(_ExternalLease)
    owner = SimpleNamespace(event=AsyncMock())
    lease.page = SimpleNamespace(_doxagent_target_owner=owner, _doxagent_allocation_id="allocation")
    lease.entry = SimpleNamespace(cdp=None, active_pages=1, last_used=0)
    lease.adapter = SimpleNamespace(_lock=asyncio.Lock(), _page_slots=asyncio.Semaphore(0))
    lease._closed = False
    monkeypatch.setattr("doxagent.site_strategy.external_runtime._close_owned_page",
                        AsyncMock(side_effect=RuntimeError("owned_target_cleanup_pending")))
    with pytest.raises(RuntimeError):
        await lease.__aexit__(None, None, None)
    assert lease._closed and lease.entry.active_pages == 0
    assert lease.adapter._page_slots._value == 1
    assert owner.event.await_args.args == ("state", {"allocation_id": "allocation",
        "state": "CLOSE_PENDING", "cleanup_failed": True})


@pytest.mark.asyncio
async def test_http_error_survives_failed_snapshot_without_re_navigation(monkeypatch):
    from doxagent.message_bus_v2 import reuters_sources as module

    response = SimpleNamespace(status=403, all_headers=AsyncMock(side_effect=RuntimeError("gone")))
    page = SimpleNamespace(url="https://www.reuters.com/site-search/", evaluate=AsyncMock(return_value={}),
        goto=AsyncMock(return_value=response), content=AsyncMock(side_effect=RuntimeError("page navigating")))
    monkeypatch.setattr(module, "wait_for_native_document_redirect", AsyncMock(return_value=response))
    with pytest.raises(RuntimeError) as error:
        await module._capture_reuters_search_dom(page, "Micron", 0, navigation={})
    assert error.value.status_code == 403 and error.value.response_body == ""
    page.goto.assert_awaited_once()
    page.content.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_native_close_proof_is_control_error_not_transport_retry():
    from doxagent.site_strategy.service import classify_exception
    from doxagent.site_strategy.targets import close_native_target

    cdp = SimpleNamespace(send=AsyncMock(side_effect=TimeoutError()))
    with pytest.raises(RuntimeError) as error:
        await close_native_target(cdp, "owned")
    category, reason = classify_exception(error.value)
    assert category.value == "RUNTIME_UNAVAILABLE" and reason == "owned_target_cleanup_pending"
