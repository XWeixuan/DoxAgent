import asyncio
from pathlib import Path

import pytest

from doxagent.codex_runtime.schema import CodexAgentRole, CodexD1Node
from doxagent.codex_worker.jobs import WorkerJobManager
from doxagent.codex_worker.schema import WorkerRunRequest
from doxagent.codex_worker.sdk_runtime import WorkerTurnResult
from doxagent.codex_worker.workspace_store import LocalWorkspaceStore


class Runtime:
    def __init__(self) -> None:
        self.calls = 0
        self.release = asyncio.Event()

    async def start(self, request, cwd):
        self.calls += 1
        return self

    async def run(self):
        await self.release.wait()
        return WorkerTurnResult("thread", "turn", "completed", "{}")


@pytest.mark.asyncio
async def test_duplicate_dispatch_and_terminal_restart_reconcile(tmp_path: Path) -> None:
    runtime = Runtime()
    store = LocalWorkspaceStore(tmp_path)
    manager = WorkerJobManager(runtime, store)
    request = WorkerRunRequest(
        run_id="run",
        ticker="MU",
        node=CodexD1Node.C1,
        agent_role=CodexAgentRole.C1,
        attempt_id="attempt",
        prompt="offline",
        output_schema={},
        idempotency_key="node:1:1",
    )
    first, second = await asyncio.gather(manager.submit(request), manager.submit(request))
    assert first.job_id == second.job_id
    assert runtime.calls == 1
    with pytest.raises(ValueError, match="different worker request"):
        await manager.submit(request.model_copy(update={"prompt": "changed"}))
    runtime.release.set()
    await manager._tasks[first.job_id]
    restarted = WorkerJobManager(runtime, store)
    recovered = await restarted.submit(request)
    assert recovered.job_id == first.job_id
    assert recovered.status == "succeeded"
    assert runtime.calls == 1
    retry = await restarted.submit(request.model_copy(update={"idempotency_key": "node:1:2"}))
    await restarted._tasks[retry.job_id]
    assert runtime.calls == 2
