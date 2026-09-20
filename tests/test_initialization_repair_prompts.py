from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from doxagent.codex_runtime.schema import CodexAgentRole, CodexD1Node
from doxagent.codex_worker.schema import WorkerJob, WorkerRunRequest
from doxagent.initialization_repair.repair_prompts import PromptOverride, write_override
from doxagent.initialization_repair.repository import RepairRepository
from doxagent.initialization_repair.schema import RoundStatus
from doxagent.ticker_initialization import InitializationRepository, NodeSpec
from doxagent.ticker_initialization.service import NodeContext
from doxagent.ticker_initialization.substeps import DurableWorker, _step


class Worker:
    def __init__(self) -> None:
        self.requests: list[WorkerRunRequest] = []

    async def run(self, request: WorkerRunRequest) -> WorkerJob:
        self.requests.append(request)
        return WorkerJob(
            job_id=f"job-{len(self.requests)}",
            run_id=request.run_id,
            attempt_id=request.attempt_id,
            status="succeeded",
            final_response="{}",
        )

    async def cancel(self, job_id: str):
        return None


def request() -> WorkerRunRequest:
    return WorkerRunRequest(
        run_id="run",
        ticker="MU",
        node=CodexD1Node.C1,
        agent_role=CodexAgentRole.C1,
        attempt_id="attempt",
        prompt="base prompt",
        output_schema={},
    )


def routed_child(tmp_path):
    repository = InitializationRepository(tmp_path / "control.sqlite3")
    run = repository.submit("MU", datetime.now(UTC), [NodeSpec(key="d1", block="D1")])
    lease = repository.claim("original")
    assert lease is not None
    repository.begin(lease, "d1", {})
    repository.fail(lease, "d1", "broken")
    failed = repository.finish(lease, error="broken")
    repairs = RepairRepository(repository)
    incident = repairs.open_incident(run.initialization_id, expected_state_seq=failed.state_seq)
    repair_round = repairs.start_round(incident.incident_id, failed_state_seq=failed.state_seq)
    repairs.update_round(repair_round.round_id, status=RoundStatus.VERIFIED, image_id="image")
    repository.resume_for_repair(
        run.initialization_id,
        incident_id=incident.incident_id,
        round_id=repair_round.round_id,
        control_epoch=1,
        control_operation_id="control-1",
    )
    repair_lease = repository.claim_repair(
        run.initialization_id,
        incident.incident_id,
        repair_round.round_id,
        "repair",
    )
    assert repair_lease is not None
    repository.expand(
        repair_lease,
        [NodeSpec(key="d1.c1", block="D1", inputs={"managed_by": "d1"})],
    )
    child = repository.begin(repair_lease, "d1.c1", {})
    return repository, repair_lease, child, incident


@pytest.mark.asyncio
async def test_supplement_is_applied_once_before_frozen_request(tmp_path, monkeypatch):
    repository, lease, child, incident = routed_child(tmp_path)
    override_root = tmp_path / "overrides"
    override_path = override_root / f"{incident.incident_id}.json"
    write_override(
        override_path,
        incident_id=incident.incident_id,
        overrides=[PromptOverride(node_key="d1.c1", supplement="repair one", reason="test")],
    )
    monkeypatch.setattr(
        "doxagent.initialization_repair.repair_prompts.default_override_root",
        lambda: override_root,
    )
    worker = Worker()
    durable = DurableWorker(worker)  # type: ignore[arg-type]
    context = NodeContext(repository, lease, child)
    token = _step.set(context)
    try:
        await durable.run(request())
    finally:
        _step.reset(token)
    write_override(
        override_path,
        incident_id=incident.incident_id,
        overrides=[PromptOverride(node_key="d1.c1", supplement="repair two", reason="changed")],
    )
    current = next(
        node for node in repository.nodes(lease.initialization_id) if node.key == "d1.c1"
    )
    token = _step.set(NodeContext(repository, lease, current))
    try:
        await durable.run(request())
    finally:
        _step.reset(token)
    assert len(worker.requests) == 2
    assert worker.requests[0].prompt == worker.requests[1].prompt
    assert "repair one" in worker.requests[0].prompt
    assert "repair two" not in worker.requests[1].prompt
    assert current.receipt["repair_prompt"]["incident_id"] == incident.incident_id


def test_override_file_has_no_business_input_mutation_fields(tmp_path):
    path = tmp_path / "override.json"
    write_override(
        path,
        incident_id="incident",
        overrides=[PromptOverride(node_key="d3.shell", supplement="instruction", reason="root")],
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert set(payload) == {"incident_id", "overrides"}
    assert set(payload["overrides"][0]) == {"node_key", "supplement", "reason"}
