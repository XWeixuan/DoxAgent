import sqlite3
from datetime import UTC, datetime

import pytest

from doxagent.ticker_initialization import (
    InitializationRepository,
    InitializationWorker,
    NodeContext,
    NodeSpec,
)
from doxagent.ticker_initialization.substeps import execution_scope, settle_stage
from tests.test_ticker_initialization_substeps import Output, Stages, Step


@pytest.mark.asyncio
async def test_crash_between_response_and_stage_confirmation_reuses_receipt(tmp_path):
    repo = InitializationRepository(tmp_path / "control.db")
    run = repo.submit("MU", datetime.now(UTC), [NodeSpec(key="d3", block="D3")])
    lease = repo.claim("old")
    parent = repo.begin(lease, "d3", {})
    context = NodeContext(repo, lease, parent)
    stages = Stages({})
    with execution_scope(context):
        context.checkpoint(started=True)
        await stages.turn(node=Step.CALIBRATE, output_model=Output)
    assert repo.nodes(run.initialization_id)[1].status == "RUNNING"
    with sqlite3.connect(repo.path) as db:
        db.execute("UPDATE ticker_operations SET lease_until=0")
    result = await InitializationWorker(repo, lambda _: stages).run_once()
    assert result.status == "SUCCEEDED"
    assert stages.calls == list(Step)


@pytest.mark.asyncio
async def test_semantic_failure_consumes_stage_budget_not_cached_response(tmp_path):
    class SemanticStages(Stages):
        failures_left = {step: 1 for step in Step}

        async def execute(self, context):
            context.checkpoint(started=True)
            for node in Step:
                await self.turn(node=node, output_model=Output)
                if self.failures_left[node]:
                    self.failures_left[node] -= 1
                    settle_stage(node.value, error="missing stage output")
                    raise ValueError("missing stage output")
                settle_stage(node.value)
            from doxagent.ticker_initialization import NodeResult

            return NodeResult()

    repo = InitializationRepository(tmp_path / "control.db")
    repo.submit("MU", datetime.now(UTC), [NodeSpec(key="d3", block="D3")])
    stages = SemanticStages({})
    result = await InitializationWorker(repo, lambda _: stages).run_once()
    assert result.status == "SUCCEEDED", result.error
    assert all(stages.calls.count(step) == 2 for step in Step)
