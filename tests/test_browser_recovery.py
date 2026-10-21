from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from doxagent.site_strategy.governance_models import (
    BrowserRecovery,
    OwnedBrowserTarget,
    MaintenanceSession,
)
from doxagent.site_strategy.governance_repository import GovernanceRepository
from doxagent.site_strategy.isolation_migration import apply_migration, migration_plan
from doxagent.site_strategy.repository import SiteStrategyRepository
from doxagent.site_strategy.runtime import RuntimeResponse
from doxagent.site_strategy.schema import AccessRequest, SiteIdentityAuth, utc_now
from doxagent.site_strategy.seeds import bootstrap_seed
from doxagent.site_strategy.service import SiteStrategyService, ProfileUnavailableError
from doxagent.site_strategy.recovery import recovery_context, verify_capture
from doxagent.site_strategy.verification import VerificationReport

URL = "https://www.investing.com/equities/vector-acquisition-news"
LISTING = (
    '<h2>RKLB News</h2><article data-test="article-item">'
    '<a data-test="article-title-link" href="/news/stock-market-news/rocket-12345">'
    'Rocket Lab news</a><time datetime="2026-10-21 15:17:49"/></article>'
)


@pytest.fixture
def setup(tmp_path):
    repo = SiteStrategyRepository(tmp_path / "registry.db")
    service = SiteStrategyService(repo, profile_root=tmp_path / "profiles")
    bootstrap_seed(repo, service)
    plan = migration_plan(repo)
    apply_migration(
        repo, plan, expected_revision=plan["expected_revision"], backup_path=tmp_path / "before.db"
    )
    store = GovernanceRepository(repo)
    scope = store.get_scope("source:investing_ticker_news")
    yield repo, service, store, scope
    repo.close()


def retained(setup, *, human=False, purpose="CRAWLER"):
    repo, service, store, scope = setup
    target = store.save_target(
        OwnedBrowserTarget(
            identity_id=scope.primary_identity_id,
            browser_instance_id="chrome",
            worker_epoch=1,
            target_id="original",
            state="LIVE",
        )
    )
    context = recovery_context(
        AccessRequest(operation_id="poll", url=URL, purpose=purpose, mode="BROWSER")
    )
    value = store.begin_recovery(
        BrowserRecovery(
            identity_id=scope.primary_identity_id,
            scope_id=scope.scope_id,
            site_id="investing",
            original_allocation_id=target.allocation_id,
            original_target_id=target.target_id,
            original_instance_id="chrome",
            handling_mode="HUMAN" if human else "AUTO",
            affected_contexts=[context],
            assessment={"provider": "CLOUDFLARE", "kind": "AUTO_CHECK"},
        ).model_dump()
    )
    return value, target, context


def test_auto_handoff_is_not_a_hidden_human_task(setup):
    value, target, _ = retained(setup)
    repo, _, store, scope = setup
    assert store.current_challenge(value.identity_id) is None
    assert store.get_target(target.allocation_id).owner_kind == "RECOVERY"
    profile = repo.get_profile(repo.get_identity(value.identity_id).profile_id)
    assert profile.maintenance_session_id is None
    assert (
        not repo.get_runtime(f"scope:{scope.scope_id}")
        .combinations[value.identity_id]
        .manual_attention_required
    )


def test_human_handoff_keeps_exact_original_and_shared_recovery(setup):
    value, target, _ = retained(setup, human=True)
    task = setup[2].current_challenge(value.identity_id)
    assert task.recovery_id == value.recovery_id
    assert task.original_target_id == target.target_id
    assert setup[2].get_target(target.allocation_id).owner_kind == "CHALLENGE"


def test_lost_target_cannot_be_handed_off_as_viewer_ready(setup):
    repo, _, store, scope = setup
    target = store.save_target(
        OwnedBrowserTarget(
            identity_id=scope.primary_identity_id,
            browser_instance_id="chrome",
            worker_epoch=1,
            target_id="lost",
            state="LOST",
        )
    )
    with pytest.raises(RuntimeError, match="OWNERSHIP_HANDOFF_FAILED"):
        store.begin_recovery(
            BrowserRecovery(
                identity_id=scope.primary_identity_id,
                scope_id=scope.scope_id,
                site_id="investing",
                original_allocation_id=target.allocation_id,
                original_target_id="lost",
                original_instance_id="chrome",
            ).model_dump()
        )


def test_expired_lease_is_reclaimed_without_resetting_incident(setup):
    value, _, _ = retained(setup)
    store = setup[2]
    first = store.claim_recovery(value.identity_id, owner="owner")
    assert store.claim_recovery(value.identity_id, owner="other") is None
    second = store.claim_recovery(
        value.identity_id, owner="other", now=utc_now() + timedelta(seconds=101)
    )
    assert second.generation == first.generation + 1
    assert second.recovery_id == value.recovery_id and second.opened_at == value.opened_at
    with pytest.raises(RuntimeError, match="stale_recovery"):
        store.finish_recovery(
            first,
            VerificationReport(challenge="CLEARED", crawler="READY", runtime_instance_id="chrome"),
            context=value.affected_contexts[0],
            resolved_by="fresh_probe",
        )


def test_auth_unknown_does_not_renew_old_valid_timestamp(setup):
    repo, _, store, scope = setup
    prior = utc_now() - timedelta(days=1)
    repo.save_site_identity_auth(
        SiteIdentityAuth(
            site_id="investing",
            identity_id=scope.primary_identity_id,
            auth_state="VALID",
            verified_at=prior,
        )
    )
    store.commit_verification(
        scope, scope.primary_identity_id, VerificationReport(reason="observation_timeout")
    )
    auth = repo.get_site_identity_auth("investing", scope.primary_identity_id)
    assert auth.verified_at == prior and auth.last_check_result == "UNKNOWN"
    assert auth.last_check_at > prior


@pytest.mark.asyncio
async def test_lost_page_fresh_business_probe_resolves_without_verify(setup):
    repo, service, store, scope = setup
    value, target, _ = retained(setup, human=True)
    store.save_target(store.get_target(target.allocation_id).model_copy(update={"state": "LOST"}))
    store.save_recovery(
        value.model_copy(update={"page_status": "LOST", "next_probe_at": utc_now()}),
        expected_generation=value.generation,
    )
    service.runtime.probe_recovery = AsyncMock(
        return_value=RuntimeResponse(
            200, URL, {}, LISTING, provenance=SimpleNamespace(instance_id="chrome")
        )
    )
    service.runtime.close_recovery = AsyncMock()
    await service.recovery._run(value.identity_id)
    final = store.get_recovery(value.identity_id)
    assert final.phase == "RESOLVED", final.last_error
    task = store.get_challenge(final.challenge_task_id)
    assert task.state == "RESOLVED" and task.resolution_reason == "BUSINESS_RECOVERED_ON_NEW_PAGE"
    assert store.get_target(target.allocation_id).state == "LOST"  # history remains true


@pytest.mark.asyncio
async def test_open_human_writer_is_not_navigated_or_stolen(setup):
    _, service, store, scope = setup
    value, _, _ = retained(setup, human=True)
    store.save_recovery(
        value.model_copy(update={"page_status": "LOST", "next_probe_at": utc_now()}),
        expected_generation=value.generation,
    )
    store.save_session(
        MaintenanceSession(
            kind="CHALLENGE",
            scope_id=scope.scope_id,
            identity_id=value.identity_id,
            site_id="investing",
            task_id=value.challenge_task_id,
        )
    )
    service.runtime.probe_recovery = AsyncMock(side_effect=AssertionError("must not navigate"))
    await service.recovery._run(value.identity_id)
    service.runtime.probe_recovery.assert_not_awaited()
    assert store.get_recovery(value.identity_id).last_error == "user_maintenance"


@pytest.mark.asyncio
async def test_automatic_verification_failure_stays_retriable_without_human_task(setup):
    _, service, store, _ = setup
    value, _, _ = retained(setup)
    service.recovery._observe = AsyncMock(side_effect=TimeoutError())
    await service.recovery._run(value.identity_id)
    final = store.get_recovery(value.identity_id)
    assert final.phase == "BACKOFF" and final.next_due_at > final.opened_at
    assert final.lease_until is None and store.current_challenge(value.identity_id) is None


def test_probe_uses_actual_affected_ticker_not_just_200(setup):
    _, service, _, scope = setup
    _, _, context = retained(setup)
    capture = RuntimeResponse(
        200,
        URL,
        {},
        "<header>Investing.com</header>",
        provenance=SimpleNamespace(instance_id="chrome"),
    )
    assert verify_capture(service, scope, context, capture).crawler != "READY"
    capture.body = LISTING
    assert verify_capture(service, scope, context, capture).crawler == "READY"


@pytest.mark.asyncio
async def test_recovery_gate_does_not_affect_http_transport(setup):
    from doxagent.site_strategy.schema import AccessMode, SitePurpose

    _, service, _, scope = setup
    retained(setup)
    resolved = service._resolve_request(
        AccessRequest(
            operation_id="p",
            url=URL,
            purpose="CRAWLER",
            mode="BROWSER",
            origin_scope_id=scope.scope_id,
        )
    )
    combos = service._purpose_combinations(resolved, SitePurpose.CRAWLER)
    _, browser, _ = await service.health.candidates(
        resolved.runtime_key, combos, excluded=set(), purpose=SitePurpose.CRAWLER
    )
    _, http, _ = await service.health.candidates(
        resolved.runtime_key,
        combos,
        excluded=set(),
        purpose=SitePurpose.CRAWLER,
        transport=AccessMode.HTTP_PUBLIC,
    )
    assert scope.primary_identity_id not in [c.identity_id for c in browser]
    assert scope.primary_identity_id in [c.identity_id for c in http]


@pytest.mark.asyncio
async def test_stale_business_finally_cannot_close_transferred_original(setup):
    from doxagent.site_strategy.targets import TargetOwner
    from doxagent.site_strategy.runtime import _close_owned_page

    repo, _, store, _ = setup
    value, target, _ = retained(setup)
    page = SimpleNamespace(
        _doxagent_target_owner=TargetOwner(repo),
        _doxagent_allocation_id=target.allocation_id,
        _doxagent_target_id=target.target_id,
    )
    cdp = SimpleNamespace(send=AsyncMock())
    await _close_owned_page(page, cdp)
    cdp.send.assert_not_awaited()
    assert store.get_target(target.allocation_id).state == "LIVE"


@pytest.mark.asyncio
async def test_pending_identity_returns_deferral_without_publisher_request(setup):
    from doxagent.site_strategy.schema import AccessDisposition

    _, service, _, scope = setup
    value, _, _ = retained(setup)
    # Disable the unproven spare for this test; it must not hide the primary state.
    for combo in service._resolve_request(
        AccessRequest(
            operation_id="x",
            url=URL,
            purpose="CRAWLER",
            mode="BROWSER",
            origin_scope_id=scope.scope_id,
        )
    ).access.combinations:
        if combo.identity_id != value.identity_id:
            combo.enabled = False
    service.runtime.execute = AsyncMock(side_effect=AssertionError("must not navigate"))
    result = await service.execute(
        AccessRequest(
            operation_id="pending",
            url=URL,
            purpose="CRAWLER",
            mode="BROWSER",
            origin_scope_id=scope.scope_id,
            excluded_combinations=[scope.fallback_identity_id],
        )
    )
    assert result.disposition == AccessDisposition.RECOVERY_PENDING
    assert result.recovery_id == value.recovery_id
    assert result.retry_at >= utc_now() + timedelta(seconds=59)
    with service.repository._lock:
        count = service.repository._connection.execute(
            "select count(*) from access_events where category='ACCESS_RESULT'").fetchone()[0]
    assert count == 0  # Gate reads are neither website attempts nor full event spam.
    service.runtime.execute.assert_not_awaited()


def test_migration_reclassifies_auto_without_recreating_profile_or_egress(setup):
    from doxagent.site_strategy.governance_models import ChallengeTask
    from doxagent.site_strategy.recovery import migrate_recoveries

    repo, service, store, scope = setup
    identity = repo.get_identity(scope.primary_identity_id)
    profile_before = repo.get_profile(identity.profile_id)
    egress_before = repo.get_egress(identity.egress_id)
    target = store.save_target(
        OwnedBrowserTarget(
            identity_id=identity.identity_id,
            browser_instance_id="chrome",
            worker_epoch=1,
            target_id="old",
            state="LIVE",
        )
    )
    task = store.handoff_challenge(
        ChallengeTask(
            scope_id=scope.scope_id,
            site_id="investing",
            identity_id=identity.identity_id,
            purpose="CRAWLER",
            operation_id="oldpoll",
            original_url=URL,
            original_target_id=target.target_id,
            browser_instance_id="chrome",
            allocation_id=target.allocation_id,
            reason="challenge_required",
        )
    )
    dry = migrate_recoveries(service)
    assert not dry["applied"] and store.get_recovery(identity.identity_id) is None
    migrate_recoveries(service, apply=True)
    assert store.get_challenge(task.task_id).resolution_reason == "RECLASSIFIED_AUTO_CHECK"
    assert store.get_recovery(identity.identity_id).handling_mode == "AUTO"
    assert store.get_target(target.allocation_id).owner_kind == "RECOVERY"
    assert repo.get_profile(identity.profile_id).directory_key == profile_before.directory_key
    assert repo.get_egress(identity.egress_id) == egress_before
    assert migrate_recoveries(service, apply=True)["records"] == []


def test_rollback_projection_has_no_new_fields_and_never_applies(setup):
    from doxagent.site_strategy.recovery import rollback_export

    repo, service, store, scope = setup
    value, target, _ = retained(setup)
    export = rollback_export(service)
    assert export["projection_only"]
    assert all("challenge" not in r["spec"] for r in export["tables"]["site_strategy_revisions"])
    assert store.get_target(target.allocation_id).owner_kind == "RECOVERY"


def test_partial_crawler_proof_does_not_resolve_a_body_task(setup):
    _, _, store, _ = setup
    value, _, context = retained(setup, human=True, purpose="BODY")
    crawler = {**context, "purpose": "CRAWLER"}
    store.save_recovery(
        value.model_copy(update={"affected_contexts": [context, crawler]}),
        expected_generation=value.generation,
    )
    claimed = store.claim_recovery(value.identity_id, owner="test")
    final = store.finish_recovery(
        claimed,
        VerificationReport(challenge="CLEARED", crawler="READY", runtime_instance_id="chrome"),
        context=crawler,
        resolved_by="fresh_probe",
    )
    assert final.phase == "BACKOFF"
    assert store.current_challenge(value.identity_id).purpose == "BODY"
    assert final.affected_contexts[0]["purpose"] == "BODY"


def test_nine_ticker_retries_share_one_incident_without_evicting_body(setup):
    _, _, store, scope = setup
    value, target, context = retained(setup)
    body = {**context, "purpose": "BODY"}
    store.save_recovery(
        value.model_copy(update={"affected_contexts": [context, body]}),
        expected_generation=value.generation,
    )
    for index in range(9):
        next_value = value.model_copy(
            update={"affected_contexts": [{**context, "operation_id": f"poll:{index}"}]}
        )
        value = store.begin_recovery(next_value)
    assert len(value.affected_contexts) == 2
    assert {c["purpose"] for c in value.affected_contexts} == {"BODY", "CRAWLER"}
    assert len(store.list_recoveries()) == 1
    assert value.original_allocation_id == target.allocation_id


def test_auto_login_is_throttled_by_account_not_identity(setup):
    store = setup[2]
    assert store.claim_auto_login("reuters-account")
    store.finish_auto_login("reuters-account", "MFA_REQUIRED")
    assert not store.claim_auto_login("reuters-account")


@pytest.mark.asyncio
async def test_replaced_auto_scene_cleanup_keeps_ownership_and_cannot_close_new_human(setup):
    from doxagent.site_strategy.targets import TargetOwner

    repo, _, store, scope = setup
    value, old, context = retained(setup)
    new = store.save_target(OwnedBrowserTarget(identity_id=scope.primary_identity_id,
        browser_instance_id="chrome", worker_epoch=1, target_id="new-human", state="LIVE"))
    store.begin_recovery(value.model_copy(update={"original_allocation_id": new.allocation_id,
        "original_target_id": new.target_id, "handling_mode": "HUMAN"}))
    cdp = SimpleNamespace(send=AsyncMock(side_effect=[{"targetInfos": [
        {"targetId": "original"}, {"targetId": "new-human"}]}, {"success": True}]))
    await TargetOwner(repo).reconcile(cdp, value.identity_id, "chrome")
    assert store.get_target(old.allocation_id).owner_kind == "RECOVERY"
    assert store.get_target(old.allocation_id).state == "CLOSED"
    assert store.get_target(new.allocation_id).state == "LIVE"
    assert cdp.send.await_args_list[-1].args == ("Target.closeTarget", {"targetId": "original"})


@pytest.mark.asyncio
async def test_original_recovers_after_handoff_without_verify_or_new_navigation(setup):
    _, service, store, _ = setup
    value, _, _ = retained(setup, human=True)
    store.save_recovery(
        value.model_copy(update={"opened_at": utc_now() - timedelta(seconds=50)}),
        expected_generation=value.generation,
    )
    service.recovery._observe = AsyncMock(
        return_value=RuntimeResponse(
            0, URL, {}, LISTING, provenance=SimpleNamespace(instance_id="chrome")
        )
    )
    service.runtime.probe_recovery = AsyncMock(side_effect=AssertionError("must not navigate"))
    service.runtime.close_recovery = AsyncMock()
    await service.recovery._run(value.identity_id)
    assert store.get_recovery(value.identity_id).phase == "RESOLVED"
    service.runtime.probe_recovery.assert_not_awaited()


@pytest.mark.asyncio
async def test_fresh_probe_replaces_lost_original_with_a_real_new_human_target(setup):
    _, service, store, scope = setup
    value, old, context = retained(setup, human=True)
    service.recovery._observe = AsyncMock(
        side_effect=RuntimeError("original_challenge_target_lost")
    )
    store.save_recovery(
        value.model_copy(update={"next_probe_at": utc_now()}), expected_generation=value.generation
    )

    async def probe(request, identity, egress):
        assert request.recipe_parameters["_retain_recovery_probe"]
        target = store.save_target(
            OwnedBrowserTarget(
                identity_id=identity.identity_id,
                browser_instance_id="chrome",
                worker_epoch=1,
                target_id="new-human",
                state="LIVE",
            )
        )
        updated = store.begin_recovery(
            BrowserRecovery(
                identity_id=identity.identity_id,
                scope_id=scope.scope_id,
                site_id="investing",
                original_allocation_id=target.allocation_id,
                original_target_id="new-human",
                original_instance_id="chrome",
                handling_mode="HUMAN",
                affected_contexts=[context],
            )
        )
        assert updated.recovery_id == value.recovery_id
        return RuntimeResponse(
            403,
            URL,
            {},
            '<iframe src="https://geo.captcha-delivery.com/"/>Slide right to secure your access',
            provenance=SimpleNamespace(instance_id="chrome"),
        )

    service.runtime.probe_recovery = probe
    service.runtime.close_recovery = AsyncMock()
    await service.recovery._run(value.identity_id)
    current = store.get_recovery(value.identity_id)
    assert current.original_target_id == "new-human", current.last_error
    assert store.current_challenge(value.identity_id).original_target_id == "new-human"
    assert store.get_target(old.allocation_id).state == "LOST"
    assert current.lease_owner is None


def test_pending_login_auto_check_keeps_user_ownership_and_uses_business_canary(setup):
    from doxagent.site_strategy.recovery import register_login_recovery
    from doxagent.site_strategy.challenge_detection import assess

    _, service, store, scope = setup
    target = store.save_target(
        OwnedBrowserTarget(
            identity_id=scope.primary_identity_id,
            browser_instance_id="chrome",
            worker_epoch=1,
            target_id="login",
            state="LIVE",
        )
    )
    session = store.save_session(
        MaintenanceSession(
            kind="LOGIN",
            scope_id=scope.scope_id,
            site_id="investing",
            identity_id=scope.primary_identity_id,
            control_token="private-token",
            allocation_id=target.allocation_id,
            target_id="login",
            browser_instance_id="chrome",
        )
    )
    store.handoff_login(target.allocation_id, session.control_token, "investing")
    value = register_login_recovery(
        service,
        scope,
        session,
        assess("<title>Just a moment</title>Checking your browser", blocked=True),
    )
    assert value.handling_mode == "AUTO"
    assert store.get_target(target.allocation_id).owner_kind == "LOGIN"
    assert store.current_challenge(scope.primary_identity_id) is None
    assert value.affected_contexts[0]["url"] == scope.verification_plan["crawler"]["url"]


def test_protocol_two_downgrades_pending_without_breaking_old_readers(setup):
    from fastapi.testclient import TestClient
    from doxagent.site_strategy.api import create_app
    from doxagent.site_strategy.schema import AccessResult

    _, service, _, _ = setup
    service.execute = AsyncMock(
        return_value=AccessResult(
            request_id="r",
            operation_id="o",
            site_id="investing",
            runtime_key="investing",
            strategy_revision=1,
            disposition="RECOVERY_PENDING",
            recovery_id="test-recovery",
            reason_code="site_recovery_pending",
            handling_mode="AUTO",
        )
    )
    client = TestClient(create_app(service, worker_token="worker", admin_token="admin"))
    payload = AccessRequest(
        operation_id="o", url=URL, purpose="CRAWLER", mode="BROWSER"
    ).model_dump(mode="json")
    old = client.post(
        "/v1/access/execute",
        json=payload,
        headers={"Authorization": "Bearer worker", "X-Site-Access-Protocol": "2"},
    ).json()
    new = client.post(
        "/v1/access/execute",
        json=payload,
        headers={"Authorization": "Bearer worker", "X-Site-Access-Protocol": "3"},
    ).json()
    assert old["disposition"] == "BUDGET_DEFERRED" and "recovery_id" not in old
    assert new["disposition"] == "RECOVERY_PENDING" and new["recovery_id"] == "test-recovery"
    resolved = client.get('/v1/resolve', params={"url": URL},
        headers={"Authorization": "Bearer worker", "X-Site-Access-Protocol": "2"}).json()
    assert "challenge" not in resolved and "auto_login" not in resolved["auth"]


@pytest.mark.asyncio
async def test_login_form_stops_before_filling_on_challenge():
    from doxagent.site_strategy.recovery import reuters_login_form

    page = SimpleNamespace(
        url="https://www.reuters.com/",
        content=AsyncMock(
            return_value='<iframe src="https://geo.captcha-delivery.com/captcha/"/>Slide right to secure your access'
        ),
    )
    assert await reuters_login_form(page, {"username": "example", "password": "test-only"}) == {
        "status": "CHALLENGE_REQUIRED"
    }


@pytest.mark.asyncio
async def test_login_form_rejects_foreign_redirect_without_reading_dom():
    from doxagent.site_strategy.recovery import reuters_login_form

    page = SimpleNamespace(url="https://other.invalid/", content=AsyncMock())
    assert (await reuters_login_form(page, {}))["status"] == "UNTRUSTED_LOGIN_REDIRECT"
    page.content.assert_not_awaited()


@pytest.mark.asyncio
async def test_automatic_check_waits_for_native_document_without_navigation(setup, monkeypatch):
    import doxagent.site_strategy.runtime as module
    from doxagent.site_strategy.schema import ChallengePolicy

    _, service, _, _ = setup
    clock = [0.0]

    async def sleep(seconds):
        clock[0] += seconds

    async def send(method, *args, **kwargs):
        if method == "Target.attachToTarget":
            return {"sessionId": "observation"}
        if method == "DOM.getDocument":
            return {"root": {"nodeId": 1}}
        if method == "DOM.getOuterHTML":
            return {
                "outerHTML": LISTING
                if clock[0] >= 8
                else "<title>Just a moment</title>Checking your browser"
            }
        return {}

    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(module.asyncio, "sleep", sleep)
    result = await service.runtime._wait_automatic_document(
        SimpleNamespace(browser_cdp=SimpleNamespace(send=send)),
        SimpleNamespace(url=URL),
        SimpleNamespace(root_target_id="original"),
        SimpleNamespace(challenge=ChallengePolicy()),
        "<title>Just a moment</title>Checking your browser",
        75,
    )
    assert result == LISTING and clock[0] == 8
