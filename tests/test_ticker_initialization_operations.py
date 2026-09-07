from datetime import UTC, datetime

import pytest

from doxagent.ticker_initialization import (
    InitializationRepository,
    InitializationWorker,
    NodeResult,
    NodeSpec,
)
from doxagent.ticker_initialization.schema import InitializationError


@pytest.mark.asyncio
async def test_adopt_and_explicit_invalidation_do_not_cascade_or_change_active_revision(tmp_path):
    repo = InitializationRepository(tmp_path / "control.db")
    run = repo.submit(
        "MU",
        datetime.now(UTC),
        [NodeSpec(key="a", block="D1"), NodeSpec(key="b", block="D2", dependencies=["a"])],
    )

    class Adapter:
        async def reconcile(self, context):
            return None

        async def execute(self, context):
            return NodeResult(artifacts={"version": 1})

    await InitializationWorker(repo, lambda _: Adapter()).run_once()
    second = repo.nodes(run.initialization_id)[1]
    repo.adopt(run.initialization_id, "a", NodeResult(artifacts={"version": 2}), reason="replace")
    assert repo.nodes(run.initialization_id)[1] == second
    assert repo.active_revision("MU") is None
    repo.invalidate(run.initialization_id, ["a"], reason="explicit selection")
    assert repo.nodes(run.initialization_id)[1] == second
    with pytest.raises(InitializationError, match="isolated rerun"):
        repo.resume(run.initialization_id, node_key="a", reason="must not reuse invalidated result")
    repo.adopt(
        run.initialization_id, "a", NodeResult(artifacts={"version": 3}), reason="replacement"
    )
    result = await InitializationWorker(repo, lambda _: Adapter()).run_once()
    assert result.status == "SUCCEEDED"
    assert repo.nodes(run.initialization_id)[1] == second


@pytest.mark.asyncio
async def test_duplicate_manual_operation_is_rejected(tmp_path):
    repo = InitializationRepository(tmp_path / "control.db")
    run = repo.submit("MU", datetime.now(UTC), [NodeSpec(key="a", block="D1")])
    for action in (
        lambda: repo.adopt(run.initialization_id, "a", NodeResult(), reason="replace"),
        lambda: repo.invalidate(run.initialization_id, ["a"], reason="invalidate"),
    ):
        with pytest.raises(InitializationError, match="DUPLICATE_ACTIVE"):
            action()
