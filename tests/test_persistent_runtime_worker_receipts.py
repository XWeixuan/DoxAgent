"""A failed durable W3 receipt may be explicitly recovered with a new model."""

from datetime import UTC, datetime

import pytest

from doxagent.codex_runtime.schema import (
    CODEX_PERSISTENT_RUNTIME_W3_WORKFLOW_VERSION,
    CodexPersistentRuntimeAgentRole,
    CodexPersistentRuntimeNode,
    ResearchLane,
)
from doxagent.codex_worker.schema import WorkerJob, WorkerRunRequest
from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from doxagent.persistent_runtime_v2.worker_receipts import ReceiptWorker


class _Worker:
    def __init__(self) -> None:
        self.requests: list[WorkerRunRequest] = []

    async def run(self, request: WorkerRunRequest) -> WorkerJob:
        self.requests.append(request)
        return WorkerJob(
            job_id=f"job-{len(self.requests)}",
            run_id=request.run_id,
            attempt_id=request.attempt_id,
            status="failed" if len(self.requests) == 1 else "succeeded",
        )


def _request(model: str, attempt_id: str) -> WorkerRunRequest:
    return WorkerRunRequest(
        workflow_version=CODEX_PERSISTENT_RUNTIME_W3_WORKFLOW_VERSION,
        research_lane=ResearchLane.PERSISTENT_RUNTIME,
        run_id="persistent-runtime-w3-mu-main",
        ticker="MU",
        node=CodexPersistentRuntimeNode.W3,
        agent_role=CodexPersistentRuntimeAgentRole.W3,
        attempt_id=attempt_id,
        cutoff_at=datetime.now(UTC),
        prompt="Frozen W3 prompt",
        output_schema={"type": "object"},
        model=model,
        effort="max",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("replace_failed_model", "expected_model"),
    [(False, "gpt-6-luna"), (True, "gpt-5.6-luna")],
)
async def test_failed_receipt_model_refresh_requires_explicit_override(
    tmp_path, replace_failed_model: bool, expected_model: str
) -> None:
    journal = RuntimeJournal(tmp_path / "runtime.sqlite3")
    worker = _Worker()
    receipt = ReceiptWorker(
        worker,
        journal,
        "w3-case",
        case_id="case-1",
        replace_failed_model=replace_failed_model,
    )
    await receipt.run(_request("gpt-6-luna", "w3-01"))
    await receipt.run(_request("gpt-5.6-luna", "w3-02"))

    first, second = worker.requests
    assert first.model == "gpt-6-luna"
    assert second.model == expected_model
    assert second.effort == "max"
    assert second.prompt == first.prompt
    assert second.output_schema == first.output_schema
    assert second.idempotency_key != first.idempotency_key
    assert second.attempt_id == ("w3-02" if replace_failed_model else "w3-01")
    assert journal.get("worker_invocations", "job-1")["model"] == "gpt-6-luna"
    assert journal.get("worker_invocations", "job-2")["model"] == expected_model
