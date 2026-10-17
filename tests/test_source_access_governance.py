"""Regression contracts for isolated source ownership and human recovery."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest

from doxagent.site_strategy.governance_models import (
    ChallengeTask,
    MaintenanceOperation,
    MaintenanceSession,
    OwnedBrowserTarget,
    SourceAccessScope,
)
from doxagent.site_strategy.governance_repository import GovernanceRepository
from doxagent.site_strategy.repository import SiteStrategyRepository
from doxagent.site_strategy.schema import BrowserIdentitySpec, utc_now
from doxagent.site_strategy.scope import current_routing, source_routed


@pytest.fixture
def registry(tmp_path):
    repository = SiteStrategyRepository(tmp_path / "registry.sqlite3")
    for name in ("primary", "backup", "other"):
        repository.apply_identity(
            BrowserIdentitySpec(
                identity_id=name,
                profile_id=name,
                egress_id="direct",
            ),
            expected_revision=None,
            actor="test",
        )
    yield GovernanceRepository(repository)
    repository.close()


def test_source_identity_cas_and_exclusive_ownership(registry):
    source = SourceAccessScope(
        scope_id="source:news",
        source_id="news",
        owner_site_id="site",
        primary_identity_id="primary",
        fallback_identity_id="backup",
    )
    saved = registry.apply_scope(source, None)
    assert saved.revision == 1
    with pytest.raises(RuntimeError, match="revision conflict"):
        registry.apply_scope(source, None)
    with pytest.raises(ValueError, match="already owned"):
        registry.apply_scope(
            source.model_copy(update={"scope_id": "source:other", "source_id": "other"}), None
        )
    assert len(registry.list_scopes()) == 1


def test_challenge_handoff_is_atomic_and_deduplicated(registry):
    target = registry.save_target(
        OwnedBrowserTarget(
            identity_id="primary",
            browser_instance_id="chrome-a",
            worker_epoch=1,
            target_id="target-a",
            state="LIVE",
        )
    )
    task = ChallengeTask(
        scope_id="source:news",
        identity_id="primary",
        site_id="site",
        purpose="crawler",
        operation_id="poll",
        original_url="https://a.test/x?t=secret",
        original_target_id="target-a",
        browser_instance_id="chrome-a",
        allocation_id=target.allocation_id,
        reason="challenge_required",
    )
    stored = registry.handoff_challenge(task)
    assert stored.original_url == "https://a.test/x"
    assert registry.get_target(target.allocation_id).owner_kind == "CHALLENGE"
    assert registry.handoff_challenge(task.model_copy(update={"task_id": "another"})) == stored
    with pytest.raises(ValueError, match="immutable"):
        registry.save_challenge(stored.model_copy(update={"original_target_id": "different"}))
    assert len(registry.list_challenges()) == 1


def test_human_target_never_reenters_business_cleanup(registry):
    target = registry.save_target(
        OwnedBrowserTarget(
            identity_id="primary",
            browser_instance_id="chrome-a",
            worker_epoch=1,
            target_id="target-a",
            owner_kind="LOGIN",
            state="LIVE",
        )
    )
    with pytest.raises(ValueError, match="human-owned"):
        registry.save_target(target.model_copy(update={"owner_kind": "BUSINESS"}))
    with pytest.raises(ValueError, match="instance/epoch"):
        registry.save_target(target.model_copy(update={"worker_epoch": 2}))


def test_pending_challenges_do_not_occupy_viewer_seat(registry):
    for name in ("a", "b", "c"):
        target = registry.save_target(
            OwnedBrowserTarget(
                identity_id=name,
                browser_instance_id=name,
                worker_epoch=1,
                target_id=name,
                state="LIVE",
            )
        )
        registry.handoff_challenge(
            ChallengeTask(
                identity_id=name,
                browser_instance_id=name,
                allocation_id=target.allocation_id,
                original_target_id=name,
                original_url="https://a.test/",
                scope_id=f"source:{name}",
                site_id=name,
                purpose="crawler",
                operation_id=name,
                reason="challenge_required",
            )
        )
    registry.save_session(
        MaintenanceSession(kind="LOGIN", scope_id="source:d", identity_id="other", site_id="d")
    )
    assert len(registry.list_challenges()) == 3


def test_operation_idempotency_rejects_reused_different_action(registry):
    op = MaintenanceOperation(
        action="OPEN_LOGIN",
        idempotency_key="one",
        identity_id="primary",
        deadline_at=utc_now() + timedelta(seconds=40),
    )
    registry.save_operation(op)
    assert registry.save_operation(op.model_copy(update={"operation_id": "retry"})) == op
    with pytest.raises(ValueError, match="different command"):
        registry.save_operation(
            op.model_copy(update={"operation_id": "other", "action": "CLOSE_LOGIN"})
        )


@pytest.mark.asyncio
async def test_source_context_is_task_local_and_resets_on_exception():
    class Poller:
        @source_routed
        async def poll(self, source, fail=False):
            await asyncio.sleep(0)
            assert current_routing().origin_scope_id == f"source:{source.source_id}"
            if fail:
                raise RuntimeError("expected")
            return current_routing().origin_scope_id

    poller = Poller()
    assert await asyncio.gather(
        poller.poll(SimpleNamespace(source_id="a")), poller.poll(SimpleNamespace(source_id="b"))
    ) == ["source:a", "source:b"]
    with pytest.raises(RuntimeError):
        await poller.poll(SimpleNamespace(source_id="c"), fail=True)
    assert current_routing() is None


def test_challenge_clear_is_independent_of_subscription_body_and_sticky_role(registry):
    from doxagent.site_strategy.schema import (
        BrowserProfile,
        ProxyEgress,
        SiteRuntimeState,
    )
    from doxagent.site_strategy.verification import VerificationReport

    repository = registry.repository
    repository.upsert_egress(
        ProxyEgress(
            egress_id="direct",
            node_ref="DIRECT",
            node_fingerprint="direct",
            listener_port=7890,
            endpoint="http://127.0.0.1:7890",
        )
    )
    repository.save_profile(
        BrowserProfile(
            profile_id="primary", site_id="site", bound_egress_id="direct", directory_key="primary"
        )
    )
    scope = registry.apply_scope(
        SourceAccessScope(
            scope_id="source:news",
            source_id="news",
            owner_site_id="site",
            primary_identity_id="primary",
            fallback_identity_id="backup",
        ),
        None,
    )
    repository.save_runtime(
        SiteRuntimeState(runtime_key="scope:source:news", active_combination_id="backup"),
        expected_generation=0,
    )
    target = registry.save_target(
        OwnedBrowserTarget(
            identity_id="primary",
            browser_instance_id="chrome",
            worker_epoch=1,
            target_id="original",
            state="LIVE",
        )
    )
    task = registry.handoff_challenge(
        ChallengeTask(
            scope_id=scope.scope_id,
            identity_id="primary",
            site_id="site",
            purpose="BODY",
            operation_id="test",
            original_url="https://a.test/article",
            original_target_id="original",
            browser_instance_id="chrome",
            allocation_id=target.allocation_id,
            reason="challenge_required",
        )
    )
    assert repository.get_profile("primary").operational_state.value == "MAINTENANCE"
    report = VerificationReport(
        challenge="CLEARED",
        authentication="ENTITLEMENT_MISSING",
        body="ENTITLEMENT_MISSING",
        crawler="READY",
        reason="subscription_required",
    )
    registry.commit_verification(scope, "primary", report)
    assert registry.current_challenge("primary") is None
    assert registry.list_challenges(include_resolved=True)[0].task_id == task.task_id
    assert registry.get_target(target.allocation_id).owner_kind == "CHALLENGE"
    assert repository.get_runtime("scope:source:news").active_combination_id == "backup"
    assert (
        repository.get_site_identity_auth("site", "primary").auth_state.value
        == "ENTITLEMENT_MISSING"
    )
    assert report.health_applied


def test_empty_200_is_not_access_capability(registry):
    from doxagent.site_strategy.runtime import RuntimeResponse
    from doxagent.site_strategy.schema import AuthPolicy, BodyStrategyPointer
    from doxagent.site_strategy.verification import validate_capture

    resolved = SimpleNamespace(body=BodyStrategyPointer(ref="builtin:generic@1"), auth=AuthPolicy())
    result = validate_capture(
        RuntimeResponse(200, "https://a.test/", {}, "<html></html>"), resolved
    )
    assert result.challenge == "UNKNOWN"
    assert result.body not in {"FULL", "SHORT_FULL"}
