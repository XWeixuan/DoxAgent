import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from doxagent.site_strategy.operation_gate import OperationGate
from doxagent.site_strategy.worker_manager import IdentityWorkerManager, WorkerHandle


async def test_two_identity_reads_overlap_and_cancelling_one_preserves_sibling():
    handle = WorkerHandle("identity", 1, SimpleNamespace(returncode=None, stdin=Mock()), "config")
    handle.process.stdin.is_closing.return_value = False
    manager = IdentityWorkerManager.__new__(IdentityWorkerManager)
    manager.workers = {"identity": handle}
    manager.ensure = AsyncMock(return_value=handle)
    manager.snapshot = Mock(return_value={})
    entered = set()
    both = asyncio.Event()
    finish = asyncio.Event()

    async def roundtrip(handle, action, payload, **kwargs):
        entered.add(payload["name"])
        if len(entered) == 2:
            both.set()
        await finish.wait()
        return payload["name"]

    manager._roundtrip = roundtrip
    identity = SimpleNamespace(identity_id="identity", access=SimpleNamespace(max_concurrency=2))
    a = asyncio.create_task(manager.command(identity, None, "execute", {"name": "a"}, timeout=2))
    b = asyncio.create_task(manager.command(identity, None, "execute", {"name": "b"}, timeout=2))
    await asyncio.wait_for(both.wait(), 1)
    a.cancel()
    await asyncio.gather(a, return_exceptions=True)
    finish.set()
    assert await b == "b"
    assert handle.operations.readers == 0


async def test_maintenance_writer_drains_reads_and_blocks_later_reads():
    gate = OperationGate()
    release = asyncio.Event()
    order = []

    async def read(name):
        async with gate.enter(read=True, limit=2):
            order.append(name)
            if name == "first":
                await release.wait()

    async def write():
        async with gate.enter(read=False):
            order.append("maintenance")

    first = asyncio.create_task(read("first"))
    await asyncio.sleep(0)
    writer = asyncio.create_task(write())
    await asyncio.sleep(0)
    later = asyncio.create_task(read("later"))
    await asyncio.sleep(0)
    assert order == ["first"]
    release.set()
    await asyncio.gather(first, writer, later)
    assert order == ["first", "maintenance", "later"]


async def test_admission_timeout_does_not_cancel_active_identity_page():
    handle = WorkerHandle("identity", 1, SimpleNamespace(returncode=None, stdin=Mock()), "config")
    handle.process.stdin.is_closing.return_value = False
    manager = IdentityWorkerManager.__new__(IdentityWorkerManager)
    manager.workers = {"identity": handle}
    manager.ensure = AsyncMock(return_value=handle)
    manager.snapshot = Mock(return_value={})
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def roundtrip(handle, action, payload, **kwargs):
        calls.append(payload["name"])
        entered.set()
        await release.wait()
        return payload["name"]

    manager._roundtrip = roundtrip
    identity = SimpleNamespace(identity_id="identity", access=SimpleNamespace(max_concurrency=1))
    running = asyncio.create_task(
        manager.command(identity, None, "execute", {"name": "running"}, timeout=2)
    )
    await entered.wait()
    import pytest

    with pytest.raises(TimeoutError):
        await manager.command(identity, None, "execute", {"name": "queued"}, timeout=0.03)
    release.set()
    assert await running == "running"
    assert calls == ["running"]


async def test_worker_snapshot_and_runtime_do_not_bleed_between_commands(tmp_path):
    from copy import deepcopy

    from doxagent.site_strategy.identity_worker import SnapshotRepository
    from doxagent.site_strategy.repository import SiteStrategyRepository
    from doxagent.site_strategy.seeds import bootstrap_seed
    from doxagent.site_strategy.service import SiteStrategyService

    repository = SiteStrategyRepository(tmp_path / "registry.db")
    service = SiteStrategyService(repository, profile_root=tmp_path / "profiles")
    bootstrap_seed(repository, service)
    identity = repository.list_active_identities()[0][1]
    manager = IdentityWorkerManager(repository, {"profile_root": str(tmp_path / "profiles")})
    snapshot = manager.snapshot(identity, repository.get_egress(identity.egress_id))
    local = SnapshotRepository(snapshot)
    entered = []
    ready = asyncio.Event()

    async def read(state, generation):
        own = deepcopy(snapshot)
        own["profile"]["auth_state"] = state
        local.update(own)
        local.runtime = local.runtime.model_copy(update={"generation": generation})
        entered.append(state)
        if len(entered) == 2:
            ready.set()
        await ready.wait()
        await asyncio.sleep(0)
        assert local.profile.auth_state.value == state
        assert local.runtime.generation == generation

    await asyncio.gather(read("VALID", 1), read("UNKNOWN", 2))
    assert local.profile.auth_state.value == snapshot["profile"]["auth_state"]
    assert local.runtime.generation == 0
