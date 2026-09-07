import asyncio
import sqlite3
from collections import Counter
from datetime import UTC, datetime

import pytest

from doxagent.ticker_initialization import (
    InitializationRepository,
    InitializationWorker,
    NodeResult,
)
from doxagent.ticker_initialization.catalog import default_plan


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_key", [n.key for n in default_plan()])
async def test_every_default_stage_exhausts_once_then_exact_manual_resume(tmp_path, failed_key):
    repo = InitializationRepository(tmp_path / "control.db")
    run = repo.submit("MU", datetime.now(UTC), default_plan())
    calls = Counter()
    repaired = False

    class Adapter:
        async def reconcile(self, context):
            return None

        async def execute(self, context):
            calls[context.node.key] += 1
            if context.node.key == failed_key and not repaired:
                raise ValueError("offline deterministic failure")
            if context.node.key == "activation.commit":
                identity = context.run.initialization_id + "-activation"
                repo.stage_revision(context.lease, identity, {})
                repo.activate(context.lease, identity)
            return NodeResult(quality_annotations=["PARTIAL", "DEGRADED"])

    result = await InitializationWorker(repo, lambda _: Adapter()).run_once()
    assert result.status == "FAILED"
    assert calls[failed_key] == 2
    completed = {n.key for n in repo.nodes(run.initialization_id) if n.status == "SUCCEEDED"}
    before = dict(calls)
    repaired = True
    repo.resume(run.initialization_id, node_key=failed_key, reason="offline repair")
    result = await InitializationWorker(repo, lambda _: Adapter()).run_once()
    assert result.status == "SUCCEEDED", result.error
    assert calls[failed_key] == 3
    assert all(calls[key] == before[key] for key in completed)


@pytest.mark.asyncio
@pytest.mark.parametrize("crash_key", [n.key for n in default_plan()])
async def test_every_default_stage_recovers_completed_side_effect_without_redispatch(
    tmp_path, crash_key
):
    repo = InitializationRepository(tmp_path / "control.db")
    repo.submit("MU", datetime.now(UTC), default_plan())
    calls = Counter()

    class Adapter:
        async def reconcile(self, context):
            return NodeResult() if context.node.receipt.get("external_committed") else None

        async def execute(self, context):
            calls[context.node.key] += 1
            context.checkpoint(external_committed=True)
            if context.node.key == crash_key:
                raise asyncio.CancelledError()
            return NodeResult()

    with pytest.raises(asyncio.CancelledError):
        await InitializationWorker(repo, lambda _: Adapter()).run_once()
    with sqlite3.connect(repo.path) as db:
        db.execute("UPDATE ticker_operations SET lease_until=0")
    result = await InitializationWorker(repo, lambda _: Adapter()).run_once()
    assert result.status == "SUCCEEDED", result.error
    assert set(calls) == {n.key for n in default_plan()}
    assert all(count == 1 for count in calls.values())
