import asyncio
import time
from types import SimpleNamespace

import pytest

from doxagent.site_strategy.governance_repository import GovernanceRepository
from doxagent.site_strategy.worker_runtime import WorkerRuntimeFacade


@pytest.mark.asyncio
async def test_idle_inventory_does_not_block_independent_requests(monkeypatch):
    entered = asyncio.Event()
    progressed = []

    def slow_sessions(self):
        time.sleep(0.12)
        return []

    monkeypatch.setattr(GovernanceRepository, 'list_sessions', slow_sessions)
    monkeypatch.setattr(GovernanceRepository, 'list_scopes', lambda self: [])
    runtime = SimpleNamespace(
        repository=SimpleNamespace(list_active_identities=lambda: []),
        workers=SimpleNamespace(sample_resources=lambda: None, status=lambda: {}),
    )
    facade = WorkerRuntimeFacade(runtime)

    async def independent_request():
        await entered.wait()
        await asyncio.sleep(0.02)
        progressed.append(True)

    task = asyncio.create_task(independent_request())
    entered.set()
    assert await facade.close_idle() == 0
    assert progressed, 'registry inventory must yield to physical request callbacks'
    await task
