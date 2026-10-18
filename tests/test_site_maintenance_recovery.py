"""Badcase regressions: ordinary login, lost targets and native network rebinding."""

import importlib.util
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from doxagent.site_strategy.governance_models import (
    ChallengeTask,
    MaintenanceOperation,
    MaintenanceSession,
    OwnedBrowserTarget,
)
from doxagent.site_strategy.governance_repository import GovernanceRepository
from doxagent.site_strategy.isolation_migration import apply_migration, migration_plan
from doxagent.site_strategy.maintenance import MaintenanceController
from doxagent.site_strategy.repository import SiteStrategyRepository
from doxagent.site_strategy.schema import utc_now
from doxagent.site_strategy.seeds import bootstrap_seed
from doxagent.site_strategy.service import SiteStrategyService


@pytest.fixture
def setup(tmp_path):
    repo = SiteStrategyRepository(tmp_path / "registry.sqlite3")
    service = SiteStrategyService(repo, profile_root=tmp_path / "profiles")
    bootstrap_seed(repo, service)
    plan = migration_plan(repo)
    apply_migration(
        repo,
        plan,
        expected_revision=plan["expected_revision"],
        backup_path=tmp_path / "before.sqlite3",
    )
    store = GovernanceRepository(repo)
    scope = store.get_scope("source:reuters_site_search")
    target = store.save_target(
        OwnedBrowserTarget(
            identity_id=scope.primary_identity_id,
            browser_instance_id="old-instance",
            target_id="old-target",
            worker_epoch=1,
            state="LIVE",
        )
    )
    task = store.handoff_challenge(
        ChallengeTask(
            scope_id=scope.scope_id,
            identity_id=scope.primary_identity_id,
            site_id=scope.owner_site_id,
            purpose="BODY",
            operation_id="old-op",
            original_url="https://www.reuters.com/article/old",
            original_target_id=target.target_id,
            browser_instance_id=target.browser_instance_id,
            allocation_id=target.allocation_id,
            reason="challenge_required",
        )
    )
    store.save_challenge(task.model_copy(update={"state": "TARGET_LOST"}))
    target = store.get_target(target.allocation_id)
    store.save_target(target.model_copy(update={"state": "LOST"}))
    old = store.save_session(
        MaintenanceSession(
            kind="CHALLENGE",
            scope_id=scope.scope_id,
            identity_id=task.identity_id,
            site_id=task.site_id,
            task_id=task.task_id,
            target_id=target.target_id,
            browser_instance_id=target.browser_instance_id,
            allocation_id=target.allocation_id,
            control_token=task.task_id,
            state="RELEASED",
        )
    )
    yield repo, service, store, scope, task, old
    repo.close()


def operation(action, **kwargs):
    return MaintenanceOperation(
        action=action,
        idempotency_key=action,
        deadline_at=utc_now() + timedelta(seconds=40),
        **kwargs,
    )


async def test_viewer_conflict_is_actionable_and_release_preserves_original(setup, monkeypatch):
    repo, service, store, scope, task, old = setup
    active = store.save_session(old.model_copy(update={"state": "OPEN"}))
    controller = MaintenanceController(service)
    wsj = store.get_scope("publisher:wsj")
    opened = AsyncMock()
    monkeypatch.setattr(service, "open_identity_login", opened)
    with pytest.raises(ValueError, match="Viewer is in use by .*Later / Release Viewer"):
        await controller._dispatch(operation("OPEN_LOGIN", scope_id=wsj.scope_id))
    opened.assert_not_awaited()
    await controller._dispatch(operation("RELEASE_VIEWER", session_id=active.session_id))
    assert store.get_session(active.session_id).state == "RELEASED"
    assert store.get_target(old.allocation_id).target_id == old.target_id
    assert store.current_challenge(task.identity_id).task_id == task.task_id
    assert repo.get_profile(repo.get_identity(task.identity_id).profile_id).maintenance_session_id
    opened.return_value = {"login_token": "new-token", "target_id": "wsj-tab"}
    result = await controller._dispatch(operation("OPEN_LOGIN", scope_id=wsj.scope_id))
    assert store.get_session(result["session_id"]).state == "OPEN"


async def test_repeated_open_login_reuses_exact_session(setup, monkeypatch):
    _, service, store, scope, _, _ = setup
    session = store.save_session(
        MaintenanceSession(
            kind="LOGIN",
            scope_id=scope.scope_id,
            identity_id=scope.primary_identity_id,
            site_id=scope.owner_site_id,
            target_id="login-tab",
            control_token="login-token",
        )
    )
    recover = AsyncMock(return_value={"login_token": "login-token"})
    monkeypatch.setattr(service, "recover_identity_login", recover)
    result = await MaintenanceController(service)._dispatch(
        operation("OPEN_LOGIN", scope_id=scope.scope_id)
    )
    assert result["session_id"] == session.session_id
    recover.assert_awaited_once_with(
        session.site_id, session.identity_id, session.control_token, target_id="login-tab"
    )


def test_reuters_rendered_listing_is_normal_but_shell_and_challenge_are_not():
    from doxagent.content_enrichment.quality import inspect_html

    url = "https://www.reuters.com/site-search/?query=Micron"
    normal = (
        "<html><title>Search Results</title>"
        "<main>Search results for Micron: 0 results</main></html>"
    )
    assert inspect_html(normal, url, "").page_kind == "listing"
    assert inspect_html("<html><main></main></html>", url, "").page_kind != "listing"
    blocked = (
        "<html><title>Verify you are human</title><main>Search results for Micron</main></html>"
    )
    assert inspect_html(blocked, url, "").access_reason == "challenge_required"


async def test_original_challenge_clear_commits_even_when_business_checks_throw(setup, monkeypatch):
    from doxagent.site_strategy.runtime import RuntimeResponse

    _, service, store, scope, task, old = setup
    store.save_challenge(
        store.current_challenge(task.identity_id).model_copy(update={"state": "WAITING_HUMAN"})
    )
    monkeypatch.setattr(
        service.runtime,
        "inspect_challenge",
        AsyncMock(
            return_value=RuntimeResponse(
                0,
                "https://www.reuters.com/site-search/",
                {},
                body="<main>Search results for Micron: 0 results</main>",
                challenge_cleared=True,
            )
        ),
    )
    monkeypatch.setattr(
        service.runtime,
        "verify_crawler",
        AsyncMock(side_effect=RuntimeError("net::ERR_TUNNEL_CONNECTION_FAILED https://secret")),
    )
    monkeypatch.setattr(
        service.runtime,
        "verify_login",
        AsyncMock(side_effect=RuntimeError("net::ERR_CONNECTION_RESET https://secret")),
    )
    monkeypatch.setattr(service.runtime, "close_login", AsyncMock())
    monkeypatch.setattr(service.profile_use, "end_maintenance", AsyncMock())
    result = await MaintenanceController(service)._dispatch(
        operation(
            "VERIFY_CHALLENGE",
            session_id=old.session_id,
            verification_url="https://www.reuters.com/world/test-story-2026-10-19/",
        )
    )
    assert result["challenge"] == "CLEARED" and result["session_closed"]
    assert result["original_page_kind"] == "listing"
    assert result["crawler"] == result["body"] == "CONTENT_ERROR"
    assert result["crawler_reason"] == "net::ERR_TUNNEL_CONNECTION_FAILED"
    assert result["body_reason"] == "net::ERR_CONNECTION_RESET"
    assert result["authentication"] != "VALID"
    assert not store.current_challenge(task.identity_id)
    proof = service.repository.get_runtime(f"scope:{scope.scope_id}").combinations[task.identity_id]
    assert proof.capabilities["CRAWLER"]["state"] == "UNVERIFIED"
    assert proof.capabilities["BODY"]["state"] == "UNVERIFIED"


async def test_unknown_original_does_not_clear_challenge_on_network_failure(setup, monkeypatch):
    from doxagent.site_strategy.runtime import RuntimeResponse

    _, service, store, _, task, old = setup
    store.save_challenge(
        store.current_challenge(task.identity_id).model_copy(update={"state": "WAITING_HUMAN"})
    )
    monkeypatch.setattr(
        service.runtime,
        "inspect_challenge",
        AsyncMock(
            return_value=RuntimeResponse(
                0,
                "about:blank",
                {},
                body="<html></html>",
                challenge_cleared=False,
            )
        ),
    )
    monkeypatch.setattr(service.runtime, "verify_crawler", AsyncMock(side_effect=TimeoutError()))
    monkeypatch.setattr(service.runtime, "verify_login", AsyncMock(side_effect=TimeoutError()))
    closed = AsyncMock()
    monkeypatch.setattr(service.runtime, "close_login", closed)
    result = await MaintenanceController(service)._dispatch(
        operation("VERIFY_CHALLENGE", session_id=old.session_id)
    )
    assert result["challenge"] == "UNKNOWN" and not result["session_closed"]
    assert store.current_challenge(task.identity_id).state == "WAITING_HUMAN"
    closed.assert_not_awaited()


def test_verification_diagnostics_never_forward_exception_urls():
    from doxagent.site_strategy.verification import verification_failure_reason
    from doxagent.site_strategy.worker_manager import IdentityOperationError

    assert verification_failure_reason(RuntimeError("https://secret/token")) == "RuntimeError"
    assert (
        verification_failure_reason(IdentityOperationError("X", "net::ERR_NAME_NOT_RESOLVED"))
        == "net::ERR_NAME_NOT_RESOLVED"
    )


def test_ui_verification_message_separates_challenge_and_business_failures():
    spec = importlib.util.spec_from_file_location(
        "maintenance_ui_messages", Path("deploy/site-login-ui.py")
    )
    ui = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ui)
    text = ui.verification_message(
        {"challenge": "CLEARED", "crawler": "CONTENT_ERROR", "body": "NOT_TESTED"}
    )
    assert "challenge cleared" in text and "CONTENT_ERROR" in text
    assert "not yet recognized" in ui.verification_message({"challenge": "UNKNOWN"})


def test_independent_login_cannot_resolve_another_original_challenge(setup):
    from doxagent.site_strategy.verification import VerificationReport

    _, _, store, scope, task, _ = setup
    store.save_challenge(
        store.current_challenge(task.identity_id).model_copy(update={"state": "WAITING_HUMAN"})
    )
    login = store.save_session(
        MaintenanceSession(
            kind="LOGIN",
            scope_id=scope.scope_id,
            identity_id=task.identity_id,
            site_id=task.site_id,
            target_id="separate-login-tab",
            control_token="separate-login",
        )
    )
    store.commit_verification(
        scope,
        task.identity_id,
        VerificationReport(
            challenge="CLEARED",
            authentication="VALID",
            body="FULL",
        ),
        session=login,
    )
    assert store.current_challenge(task.identity_id).state == "WAITING_HUMAN"


async def test_challenge_reconnect_uses_durable_session_not_current_login_projection(
    setup, monkeypatch
):
    repo, service, store, scope, task, old = setup
    store.save_challenge(
        store.current_challenge(task.identity_id).model_copy(update={"state": "WAITING_HUMAN"})
    )
    profile = repo.get_profile(repo.get_identity(task.identity_id).profile_id)
    repo.save_profile(
        profile.model_copy(
            update={
                "maintenance_session_id": "separate-login",
                "challenge_url": None,
                "challenge_target_id": "separate-login-tab",
            }
        )
    )
    monkeypatch.setattr(
        service.runtime, "recover_login", AsyncMock(return_value={"login_token": old.control_token})
    )
    await service.recover_identity_login(
        task.site_id, task.identity_id, old.control_token, target_id=old.target_id
    )
    recovered = repo.get_profile(profile.profile_id)
    assert recovered.challenge_target_id == old.target_id
    assert recovered.maintenance_session_id == old.control_token
    assert recovered.challenge_url == task.original_url


def test_worker_resolves_released_challenge_binding_after_login_projection_changed(setup):
    from doxagent.site_strategy.worker_runtime import WorkerSiteAccessRuntime

    repo, _, _, _, task, old = setup
    profile = repo.get_profile(repo.get_identity(task.identity_id).profile_id)
    repo.save_profile(profile.model_copy(update={"maintenance_session_id": "another-login"}))
    runtime = WorkerSiteAccessRuntime.__new__(WorkerSiteAccessRuntime)
    runtime.repository, runtime._tokens = repo, {}
    site, identity, _ = runtime._binding(old.control_token)
    assert site == task.site_id and identity.identity_id == task.identity_id


@pytest.mark.parametrize("full", [True, False])
async def test_body_readiness_timeout_preserves_only_proven_full_document(setup, monkeypatch, full):
    from types import SimpleNamespace

    from doxagent.site_strategy import runtime as runtime_module
    from doxagent.site_strategy.schema import AccessRequest
    from doxagent.site_strategy.verification import validate_capture
    from tests.test_content_enrichment_pipeline import article

    repo, service, _, scope, _, _ = setup
    identity = repo.get_identity(scope.primary_identity_id)
    egress = repo.get_egress(identity.egress_id)
    url = "https://www.reuters.com/technology/complete-story-2026-10-19/"
    resolved = service.resolve(url)
    response = SimpleNamespace(status=200, all_headers=AsyncMock(return_value={}))
    page = SimpleNamespace(
        url=url,
        goto=AsyncMock(return_value=response),
        wait_for_function=AsyncMock(side_effect=TimeoutError()),
        content=AsyncMock(
            return_value=article()
            if full
            else "<html><title>Verify you are human</title><body>Captcha</body></html>"
        ),
    )
    # Special async methods are looked up on the class, not an instance namespace.
    class Lease:
        provenance = SimpleNamespace(instance_id="body-test-instance")
        browser_cdp = None

        async def __aenter__(self):
            return page

        async def __aexit__(self, *args):
            return None

    gate = SimpleNamespace(start=AsyncMock(), close=AsyncMock(), blocked=None)
    monkeypatch.setattr(runtime_module, "public_url", AsyncMock())
    monkeypatch.setattr(runtime_module, "_DocumentNavigationGate", lambda *args: gate)
    monkeypatch.setattr(service.runtime.browser_runtimes, "page", AsyncMock(return_value=Lease()))
    request = AccessRequest(
        operation_id="body-timeout",
        purpose="BODY",
        mode="BROWSER",
        url=url,
        recipe_parameters={"_maintenance_canary": True},
    )
    if full:
        result = await service.runtime._browser(request, resolved, identity, egress)
        assert validate_capture(result, resolved).body in {"FULL", "SHORT_FULL"}
    else:
        with pytest.raises(TimeoutError):
            await service.runtime._browser(request, resolved, identity, egress)
    gate.close.assert_awaited_once()


async def test_open_login_creates_new_login_instead_of_adopting_lost_challenge(setup, monkeypatch):
    repo, service, store, scope, task, old = setup
    opened = []

    async def open_login(profile, egress, url, *, token, identity=None, site_id=None):
        opened.append((url, token))
        assert profile.challenge_url is None
        assert token != old.control_token
        return {
            "login_token": token,
            "target_id": "new-target",
            "browser_instance_id": "new-instance",
        }

    monkeypatch.setattr(service.runtime, "open_login", open_login)
    adopt = AsyncMock(side_effect=AssertionError("normal login must not adopt challenge"))
    monkeypatch.setattr(service.runtime, "adopt_challenge", adopt)
    result = await MaintenanceController(service)._dispatch(
        operation("OPEN_LOGIN", scope_id=scope.scope_id)
    )
    session = store.get_session(result["session_id"])
    assert session.kind == "LOGIN" and session.target_id == "new-target"
    assert len(opened) == 1
    assert store.get_session(old.session_id).state == "CLOSED"
    assert store.current_challenge(task.identity_id).state == "TARGET_LOST"
    adopt.assert_not_awaited()


async def test_live_challenge_does_not_block_independent_login_or_get_discarded(setup, monkeypatch):
    repo, service, store, scope, task, old = setup
    store.save_challenge(
        store.current_challenge(task.identity_id).model_copy(update={"state": "WAITING_HUMAN"})
    )
    open_login = AsyncMock(return_value={"login_token": "ordinary-login", "target_id": "login-tab"})
    monkeypatch.setattr(service.runtime, "open_login", open_login)
    result = await MaintenanceController(service)._dispatch(
        operation("OPEN_LOGIN", scope_id=scope.scope_id)
    )
    assert store.get_session(result["session_id"]).kind == "LOGIN"
    assert store.current_challenge(task.identity_id).original_target_id == "old-target"
    assert store.get_session(old.session_id).state == "RELEASED"
    open_login.assert_awaited_once()
    # Closing the independent login must restore the pending Task reservation,
    # not close its target or make the identity business-ready.
    profile = repo.get_profile(repo.get_identity(task.identity_id).profile_id)
    await service.profile_use.end_maintenance(profile.profile_id, profile.maintenance_session_id)
    restored = repo.get_profile(profile.profile_id)
    assert restored.challenge_target_id == old.target_id
    assert restored.maintenance_session_id == old.control_token


async def test_login_action_cannot_resume_challenge_session(setup):
    _, service, _, _, _, old = setup
    with pytest.raises(ValueError, match="Open Challenge"):
        await MaintenanceController(service)._dispatch(
            operation("OPEN_LOGIN", session_id=old.session_id)
        )


async def test_failed_reopen_without_new_target_leaves_no_unfinished_empty_session(
    setup, monkeypatch
):
    _, service, store, _, task, _ = setup
    monkeypatch.setattr(service.runtime, "open_login", AsyncMock(side_effect=TimeoutError()))
    with pytest.raises(TimeoutError):
        await MaintenanceController(service)._dispatch(
            operation("REOPEN_LOST_CHALLENGE", task_id=task.task_id)
        )
    assert all(s.target_id for s in store.list_sessions())
    assert store.current_challenge(task.identity_id).task_id == task.task_id


def test_new_login_challenge_links_replacement_instead_of_reusing_lost_target(setup):
    _, _, store, scope, task, _ = setup
    target = store.save_target(
        OwnedBrowserTarget(
            identity_id=task.identity_id,
            browser_instance_id="new-instance",
            target_id="new-target",
            worker_epoch=2,
            state="LIVE",
        )
    )
    replacement = store.handoff_challenge(
        task.model_copy(
            update={
                "task_id": "new-task",
                "allocation_id": target.allocation_id,
                "browser_instance_id": target.browser_instance_id,
                "original_target_id": target.target_id,
                "state": "WAITING_HUMAN",
            }
        )
    )
    assert replacement.predecessor_task_id == task.task_id
    assert store.current_challenge(task.identity_id).task_id == "new-task"
    assert store.get_target(target.allocation_id).owner_ref == "new-task"


def test_network_guard_detects_recreated_owner_and_same_id_restarted_namespace():
    spec = importlib.util.spec_from_file_location(
        "network_guard", Path(__file__).parents[1] / "deploy/site-access-network-guard.py"
    )
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    site = {
        "Id": "site",
        "State": {"Running": True, "Pid": 1},
        "HostConfig": {"NetworkMode": "container:new"},
    }
    supervisor = {
        "Id": "new",
        "State": {"Running": True, "Pid": 2, "Health": {"Status": "healthy"}},
    }
    assert guard.inspect_binding([site, supervisor], lambda _: "same")["state"] == "READY"
    assert guard.inspect_binding([site, supervisor], lambda p: p)["state"] == "MISMATCH"
    site["HostConfig"]["NetworkMode"] = "container:old"
    assert guard.inspect_binding([site, supervisor], lambda _: "same")["state"] == "MISMATCH"
    supervisor["State"]["Health"]["Status"] = "starting"
    assert guard.inspect_binding([site, supervisor], lambda _: "same")["state"] == "DEFERRED"


async def test_readiness_fails_closed_on_wrong_supervisor_network(monkeypatch):
    from doxagent.site_strategy.network_binding import network_binding

    monkeypatch.setattr("doxagent.site_strategy.network_binding.os.readlink", lambda _: "actual")
    monkeypatch.setattr(
        "doxagent.site_strategy.network_binding.ChromeSupervisorClient._request",
        AsyncMock(return_value={"network_namespace": "old"}),
    )
    result = await network_binding("fixture.sock")
    assert not result["ready"] and result["reason"] == "supervisor_network_namespace_mismatch"


async def test_focus_failure_does_not_reclassify_working_viewer_as_unavailable():
    from types import SimpleNamespace

    from doxagent.site_strategy.runtime import SiteAccessRuntime

    cdp = SimpleNamespace(send=AsyncMock(side_effect=RuntimeError("focus temporarily unavailable")))
    await SiteAccessRuntime._focus_maintenance_target(cdp, "exact-login-target")
    cdp.send.assert_awaited_once_with("Target.activateTarget", {"targetId": "exact-login-target"})


async def test_healthy_delayed_rfb_banner_is_not_rejected_as_viewer_unavailable():
    import asyncio
    from types import SimpleNamespace

    from doxagent.site_strategy.chrome_supervisor import ChromeSupervisor

    async def peer(reader, writer):
        await asyncio.sleep(0.25)
        writer.write(b"RFB 003.008\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(peer, "127.0.0.1", 0)
    async with server:
        instance = SimpleNamespace(
            vnc=SimpleNamespace(returncode=None), vnc_port=server.sockets[0].getsockname()[1]
        )
        await ChromeSupervisor._ensure_viewer(SimpleNamespace(), instance)
