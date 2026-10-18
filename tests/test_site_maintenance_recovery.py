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


async def test_live_challenge_is_not_silently_discarded_or_adopted_by_login(setup, monkeypatch):
    _, service, store, scope, task, _ = setup
    store.save_challenge(
        store.current_challenge(task.identity_id).model_copy(update={"state": "WAITING_HUMAN"})
    )
    open_login = AsyncMock()
    monkeypatch.setattr(service.runtime, "open_login", open_login)
    with pytest.raises(RuntimeError, match="active_challenge_use_challenge_maintenance"):
        await MaintenanceController(service)._dispatch(
            operation("OPEN_LOGIN", scope_id=scope.scope_id)
        )
    assert store.current_challenge(task.identity_id).original_target_id == "old-target"
    open_login.assert_not_awaited()


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
