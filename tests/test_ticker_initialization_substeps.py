from datetime import UTC, datetime
from enum import StrEnum

import pytest
from pydantic import BaseModel

from doxagent.ticker_initialization import (
    InitializationRepository,
    InitializationWorker,
    NodeResult,
    NodeSpec,
)
from doxagent.ticker_initialization.substeps import durable, settle_stage


class Step(StrEnum):
    CALIBRATE = "calibrate"
    COMPILE = "compile"
    REVIEW = "review"


class Output(BaseModel):
    usable: bool = True


class Stages:
    def __init__(self, failures):
        self.failures = failures
        self.calls = []

    @durable("d3")
    async def turn(self, *, node, output_model, max_attempts=2):
        assert max_attempts == 1
        self.calls.append(node)
        if self.failures.get(node, 0):
            self.failures[node] -= 1
            raise ValueError("offline turn failure")
        return output_model(), "thread"

    async def reconcile(self, context):
        # Real adapters traverse the same persisted workspace on recovery.
        if context.node.status == "RUNNING" and context.node.receipt.get("started"):
            return await self.execute(context)
        return None

    async def execute(self, context):
        context.checkpoint(started=True)
        for node in Step:
            await self.turn(node=node, output_model=Output, max_attempts=2)
            settle_stage(node.value)
        return NodeResult()


@pytest.mark.asyncio
async def test_internal_budget_no_parent_multiplication_and_manual_resume(tmp_path):
    repo = InitializationRepository(tmp_path / "control.db")
    run = repo.submit("MU", datetime.now(UTC), [NodeSpec(key="d3", block="D3")])
    stages = Stages({Step.COMPILE: 2})
    result = await InitializationWorker(repo, lambda _: stages).run_once()
    assert result.status == "FAILED"
    assert stages.calls == [Step.CALIBRATE, Step.COMPILE, Step.COMPILE]
    repo.resume(run.initialization_id, reason="fixed", node_key="d3.compile")
    result = await InitializationWorker(repo, lambda _: stages).run_once()
    assert result.status == "SUCCEEDED"
    assert stages.calls == [Step.CALIBRATE, Step.COMPILE, Step.COMPILE, Step.COMPILE, Step.REVIEW]


@pytest.mark.asyncio
async def test_each_internal_node_has_own_retry_not_shared_parent_budget(tmp_path):
    repo = InitializationRepository(tmp_path / "control.db")
    repo.submit("MU", datetime.now(UTC), [NodeSpec(key="d3", block="D3")])
    stages = Stages({step: 1 for step in Step})
    result = await InitializationWorker(repo, lambda _: stages).run_once()
    assert result.status == "SUCCEEDED"
    assert all(stages.calls.count(step) == 2 for step in Step)
