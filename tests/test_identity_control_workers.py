"""Real subprocess boundaries without contacting publishers or launching Chrome."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from doxagent.site_strategy.repository import SiteStrategyRepository
from doxagent.site_strategy.schema import BrowserIdentitySpec
from doxagent.site_strategy.seeds import bootstrap_seed
from doxagent.site_strategy.service import SiteStrategyService
from doxagent.site_strategy.worker_manager import (
    IdentityOperationError,
    IdentityWorkerManager,
    WorkerHandle,
)


@pytest.mark.asyncio
async def test_queued_command_re_resolves_retired_worker_without_replaying_request():
    def handle(epoch):
        process = SimpleNamespace(returncode=None, stdin=Mock())
        process.stdin.is_closing.return_value = False
        return WorkerHandle("investing", epoch, process, "same-config")

    old, current = handle(1), handle(2)
    manager = IdentityWorkerManager.__new__(IdentityWorkerManager)
    manager.workers = {"investing": old}
    manager.ensure = AsyncMock(side_effect=[old, current])
    manager.snapshot = Mock(return_value={})
    manager._roundtrip = AsyncMock(return_value={"ready": True})
    await old.operation_lock.acquire()
    pending = asyncio.create_task(manager.command(
        SimpleNamespace(identity_id="investing"), None, "execute", {}, timeout=1
    ))
    await asyncio.sleep(0)
    manager.workers["investing"] = current
    old.process.stdin.is_closing.return_value = True
    old.operation_lock.release()
    assert await pending == {"ready": True}
    assert manager.ensure.await_count == 2
    assert manager._roundtrip.await_args.args[0] is current
    manager._roundtrip.assert_awaited_once()
    assert not old.operation_lock.locked() and not current.operation_lock.locked()


@pytest.mark.asyncio
async def test_closed_worker_pipe_is_typed_but_unrelated_runtime_error_is_not_hidden():
    process = SimpleNamespace(stdin=Mock())
    handle = WorkerHandle("investing", 1, process, "config")
    manager = IdentityWorkerManager.__new__(IdentityWorkerManager)
    process.stdin.is_closing.return_value = True
    with pytest.raises(IdentityOperationError, match="identity_worker_disconnected"):
        await manager._write(handle, {"action": "execute"})
    process.stdin.write.assert_not_called()
    process.stdin.is_closing.return_value = False
    process.stdin.write.side_effect = RuntimeError("unrelated bug")
    with pytest.raises(RuntimeError, match="unrelated bug"):
        await manager._write(handle, {"action": "execute"})


@pytest.mark.asyncio
async def test_real_workers_are_identity_local_and_owner_stays_responsive(tmp_path):
    repository = SiteStrategyRepository(tmp_path / "registry.sqlite3")
    service = SiteStrategyService(repository, profile_root=tmp_path / "profiles")
    bootstrap_seed(repository, service)
    identities = [identity for _, identity in repository.list_active_identities()][:2]
    manager = IdentityWorkerManager(repository, {"profile_root": str(tmp_path / "profiles")})
    try:
        handles = await asyncio.gather(
            *(
                manager.ensure(identity, repository.get_egress(identity.egress_id))
                for identity in identities
            )
        )
        assert handles[0].process.pid != handles[1].process.pid
        assert len(manager.status()) == 2
        result = await manager.command(
            identities[1], repository.get_egress(identities[1].egress_id), "status", {}, timeout=10
        )
        assert result["identity_id"] == identities[1].identity_id
        await manager.retire(identities[0].identity_id, graceful=False)
        assert manager.status()[identities[1].identity_id]["alive"]
        restarted = await manager.ensure(
            identities[0], repository.get_egress(identities[0].egress_id)
        )
        assert restarted.epoch == 2
        assert restarted.process.pid != handles[0].process.pid
        assert manager.status()[identities[1].identity_id]["epoch"] == 1
    finally:
        await manager.close()
        repository.close()


def test_snapshot_repository_has_no_database_connection(tmp_path):
    from doxagent.site_strategy.identity_worker import SnapshotRepository
    from doxagent.site_strategy.schema import BrowserProfile, ProxyEgress

    identity = BrowserIdentitySpec(identity_id="a", profile_id="a", egress_id="direct")
    snapshot = SnapshotRepository(
        {
            "identity": identity.model_dump(mode="json"),
            "profile": BrowserProfile(
                profile_id="a", site_id="a", bound_egress_id="direct", directory_key="a"
            ).model_dump(mode="json"),
            "egress": ProxyEgress(
                egress_id="direct",
                node_ref="DIRECT",
                node_fingerprint="direct",
                listener_port=7890,
                endpoint="http://127.0.0.1:7890",
            ).model_dump(mode="json"),
            "strategies": [],
        }
    )
    assert not hasattr(snapshot, "_connection")
    assert not hasattr(snapshot, "path")
    assert snapshot.get_identity("other") is None
