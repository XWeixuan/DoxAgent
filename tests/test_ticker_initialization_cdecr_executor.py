from __future__ import annotations

from datetime import UTC, datetime

import pytest

from doxagent.settings import DoxAgentSettings
from doxagent.ticker_initialization.cdecr_executor import CDECRExecutionWorker
from doxagent.ticker_initialization.provenance import execution_version
from doxagent.ticker_initialization.repository import InitializationRepository
from doxagent.ticker_initialization.schema import NodeResult, NodeSpec


def _queued_dispatch(repository: InitializationRepository) -> tuple[str, str]:
    run = repository.submit(
        "MU",
        datetime(2026, 9, 21, tzinfo=UTC),
        [NodeSpec(key="cdecr", block="CDECR")],
    )
    lease = repository.claim("initialization-controller")
    assert lease is not None
    node = repository.begin(lease, "cdecr", execution_version())
    dispatch_id = repository.enqueue_cdecr_dispatch(
        lease, node.key, execution_identity="production"
    )
    repository.release_lease(lease)
    return run.initialization_id, dispatch_id


def test_cdecr_dispatch_atomically_takes_over_and_returns_workflow_lease(tmp_path) -> None:
    repository = InitializationRepository(tmp_path / "initialization.sqlite3")
    initialization_id, dispatch_id = _queued_dispatch(repository)

    assert repository.claim("ordinary-worker") is None
    claimed = repository.claim_cdecr_dispatch("cdecr-worker", "production")
    assert claimed is not None
    dispatch, lease = claimed
    assert dispatch["dispatch_id"] == dispatch_id
    assert dispatch["input_ref"].endswith(f":cdecr:{dispatch['execution_id']}")
    assert len(dispatch["input_hash"]) == 64
    repository.heartbeat_cdecr_dispatch(lease, dispatch_id, int(dispatch["generation"]))
    repository.complete(
        lease,
        "cdecr",
        NodeResult(artifacts={"cdecr": {"status": "FINALIZED"}}),
    )
    repository.settle_cdecr_dispatch(
        lease,
        dispatch_id,
        int(dispatch["generation"]),
        status="SUCCEEDED",
        result_ref=f"initialization-node:{initialization_id}:cdecr",
    )

    returned = repository.claim("ordinary-worker")
    assert returned is not None
    assert returned.initialization_id == initialization_id
    assert repository.cdecr_dispatch(dispatch_id)["status"] == "SUCCEEDED"


def test_cdecr_dispatch_is_only_claimed_by_matching_code_identity(tmp_path) -> None:
    repository = InitializationRepository(tmp_path / "initialization.sqlite3")
    _queued_dispatch(repository)

    assert (
        repository.claim_cdecr_dispatch(
            "wrong-build",
            "production",
            execution_version={"code_revision": "different"},
        )
        is None
    )
    assert (
        repository.claim_cdecr_dispatch(
            "matching-build",
            "production",
            execution_version=execution_version(),
        )
        is not None
    )


@pytest.mark.asyncio
async def test_cdecr_executor_settles_original_node_without_a_second_attempt(
    tmp_path, monkeypatch
) -> None:
    repository = InitializationRepository(tmp_path / "initialization.sqlite3")
    initialization_id, dispatch_id = _queued_dispatch(repository)

    from doxagent.ticker_initialization import research_adapter

    async def execute(_self, _context):
        return NodeResult(artifacts={"cdecr": {"status": "FINALIZED"}})

    async def after_complete(_self, _context, _result):
        return None

    monkeypatch.setattr(research_adapter.ResearchInitializationAdapter, "execute", execute)
    monkeypatch.setattr(
        research_adapter.ResearchInitializationAdapter, "after_complete", after_complete
    )
    settings = DoxAgentSettings().model_copy(
        update={"ticker_initialization_control_path": repository.path}
    )
    worker = CDECRExecutionWorker(repository, settings, identity="production")

    assert worker.settings.cdecr_execution_mode == "LOCAL_ONLY"
    assert await worker.run_once()
    node = next(item for item in repository.nodes(initialization_id) if item.key == "cdecr")
    assert node.status == "SUCCEEDED"
    assert node.ordinal == 1
    assert repository.cdecr_dispatch(dispatch_id)["status"] == "SUCCEEDED"
