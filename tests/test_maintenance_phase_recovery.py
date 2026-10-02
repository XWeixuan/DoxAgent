from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from doxagent.codex_runtime.errors import InfrastructureRecoveryExhausted, StructuredOutputInvalid
from doxagent.persistent_runtime_v2.journal import MaintenancePhaseFailure, RuntimeJournal
from doxagent.persistent_runtime_v2.maintenance import RuntimeMaintenance
from tests.test_runtime_orchestration_recovery import maintenance_fixture


def _journal(tmp_path):
    now = [datetime(2026, 10, 2, 6, tzinfo=UTC)]
    journal = RuntimeJournal(tmp_path / "runtime.db", clock=lambda: now[0])
    journal.put_task("daily:MU:2026-10-01", "MU", "MAINTENANCE", {})
    return journal, now


def _fail(journal, now, phase, *, scope="phase", retryable=True):
    task = journal.claim("daily:MU:2026-10-01")
    assert task
    journal.fail(task, MaintenancePhaseFailure(
        "confirmed worker failure", phase=phase, code="TEST_FAILURE",
        scope=scope, retryable=retryable,
    ))
    row = journal.get_task(task["id"])
    now[0] = datetime.fromisoformat(row["due_at"]) + timedelta(seconds=1)
    return row


def test_startup_and_later_o2_phases_do_not_share_retry_budget(tmp_path):
    journal, now = _journal(tmp_path)
    row = _fail(journal, now, "o2-known-index-map", scope="infrastructure")
    assert row["status"] == "PENDING"
    # Restart the scheduler: counts must survive and the edit timeout gets a retry.
    journal = RuntimeJournal(journal.path, clock=lambda: now[0])
    row = _fail(journal, now, "o2-incremental-edit")
    assert row["failures"] == 2 and row["status"] == "PENDING"
    row = _fail(journal, now, "o2-reference-review")
    assert row["failures"] == 3 and row["status"] == "PENDING"
    journal.finish(journal.claim(row["id"]))
    assert journal.get_task(row["id"])["status"] == "SUCCEEDED"


@pytest.mark.parametrize(("scope", "maximum"), [("phase", 2), ("infrastructure", 3)])
def test_same_phase_failure_is_bounded_and_explicit_resume_resets_budget(tmp_path, scope, maximum):
    journal, now = _journal(tmp_path)
    for count in range(1, maximum + 1):
        row = _fail(journal, now, "o2-known-index-map", scope=scope)
        assert row["status"] == ("FAILED" if count == maximum else "PENDING")
    assert row["receipt"]["last_phase_failure"]["count"] == maximum
    assert journal.claim(row["id"]) is None
    journal.resume(row["id"], "confirmed infrastructure repair")
    resumed = journal.get_task(row["id"])
    assert resumed["generation"] == 2 and resumed["failures"] == 0
    assert "phase_failures" not in resumed["receipt"]


def test_uncertain_or_quarantined_job_requires_reconciliation(tmp_path):
    journal, now = _journal(tmp_path)
    assert _fail(journal, now, "o2-incremental-edit", retryable=False)["status"] == "FAILED"


@pytest.mark.asyncio
@pytest.mark.parametrize(("message", "retryable", "scope"), [
    ("required MCP servers failed to initialize: data: timed out handshaking", True,
     "infrastructure"),
    ("owned process cleanup failed; slot quarantined", False, "phase"),
])
async def test_maintenance_preserves_nested_infrastructure_failure_scope(
    tmp_path, message, retryable, scope,
):
    runtime, journal, settings, _ = maintenance_fixture(tmp_path)

    class O2:
        def __init__(self, durable):
            self.durable = durable

        async def run(self, **kwargs):
            self.durable.last_identity = "test-o2-worker"
            journal.set("worker_requests", "test-o2-worker", {
                "attempt_id": "o2-known-index-map-retry-001",
            })
            try:
                raise InfrastructureRecoveryExhausted(message)
            except InfrastructureRecoveryExhausted as exc:
                raise StructuredOutputInvalid(f"invalid O2 run result: {exc}") from exc

    maintain = RuntimeMaintenance(
        settings, runtime, journal, worker_factory=lambda: SimpleNamespace(),
        o2_factory=lambda _repository, durable: O2(durable),
    )
    try:
        with pytest.raises(MaintenancePhaseFailure) as caught:
            await maintain(journal.claim("maintain"))
        error = caught.value
        assert error.phase == "o2-known-index-map"
        assert error.code == "WORKER_INFRA_RECOVERY_EXHAUSTED"
        assert error.retryable == retryable and error.scope == scope
        assert isinstance(error.__cause__, StructuredOutputInvalid)
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_data_mcp_cold_start_budget_applies_to_actual_sdk_config(tmp_path, monkeypatch):
    from doxagent.codex_runtime.schema import CodexAgentRole, CodexD1Node
    from doxagent.codex_worker.schema import WorkerRunRequest
    from doxagent.codex_worker.sdk_runtime import OpenAICodexRuntime
    from tests.test_codex_runtime_v2 import _AsyncSdkClient

    sdk = _AsyncSdkClient()
    monkeypatch.setattr("doxagent.codex_worker.sdk_runtime.AsyncCodex", lambda *args, **kwargs: sdk)
    runtime = OpenAICodexRuntime(capability_secret="s" * 32, container_isolated=True)
    root = tmp_path / "cold-start"
    root.mkdir()
    await runtime.start(WorkerRunRequest(
        run_id="cold-start", ticker="MU", node=CodexD1Node.C1,
        agent_role=CodexAgentRole.C1, attempt_id="attempt-1", cutoff_at=datetime.now(UTC),
        prompt="Use authorized data tools.", output_schema={"type": "object"},
        data_mcp_enabled=True,
    ), root)
    assert sdk.thread_start_kwargs["config"]["mcp_servers.data.required"] is True
    assert sdk.thread_start_kwargs["config"]["mcp_servers.data.startup_timeout_sec"] == 60


@pytest.mark.asyncio
@pytest.mark.parametrize(("recover", "code", "expected"), [
    (True, "CODEX_TURN_TIMEOUT", 3600),
    (False, "CODEX_TURN_TIMEOUT", 1800),
    (True, "CODEX_TURN_FAILED", 1800),
])
async def test_confirmed_timeout_recovers_native_o2_phase_with_bounded_deadline(
    tmp_path, recover, code, expected,
):
    from doxagent.codex_runtime.schema import (
        CODEX_EVENT_LIBRARY_WORKFLOW_VERSION,
        CodexEventLibraryAgentRole,
        CodexEventLibraryNode,
        ResearchLane,
    )
    from doxagent.codex_worker.schema import WorkerJob, WorkerRunRequest
    from doxagent.persistent_runtime_v2.worker_receipts import ReceiptWorker

    class Worker:
        def __init__(self):
            self.requests = []

        async def run(self, request):
            self.requests.append(request)
            return WorkerJob(
                job_id=f"job-{len(self.requests)}", run_id=request.run_id,
                attempt_id=request.attempt_id, status="failed",
                error_code=code,
            )

    journal = RuntimeJournal(tmp_path / "runtime.db")
    worker = Worker()
    durable = ReceiptWorker(worker, journal, "maintenance", recover_timeouts=recover)
    request = WorkerRunRequest(
        workflow_version=CODEX_EVENT_LIBRARY_WORKFLOW_VERSION,
        research_lane=ResearchLane.EVENT_LIBRARY, run_id="maintain-mu-o2", ticker="MU",
        node=CodexEventLibraryNode.O2_MAINTAIN, agent_role=CodexEventLibraryAgentRole.O2,
        attempt_id="o2-incremental-edit", cutoff_at=datetime.now(UTC),
        prompt="Immutable editing context", output_schema={"type": "object"}, effort="max",
    )
    await durable.run(request)
    await durable.run(request.model_copy(update={"attempt_id": "o2-incremental-edit-retry-001"}))
    retry = worker.requests[1]
    assert retry.timeout_seconds == expected
    assert retry.prompt == request.prompt and retry.effort == request.effort
    assert retry.output_schema == request.output_schema
    assert retry.idempotency_key != worker.requests[0].idempotency_key
    # Same failed dispatch also gets a fresh key, with a 60-minute upper bound.
    await durable.run(request.model_copy(update={"attempt_id": "o2-incremental-edit-retry-001"}))
    assert worker.requests[2].timeout_seconds == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(("cached", "valid", "canonical", "status", "enabled", "copied"), [
    (False, True, False, "succeeded", True, True),
    (True, True, False, "succeeded", True, True),
    (False, False, False, "succeeded", True, False),
    (False, True, True, "succeeded", True, False),
    (False, True, False, "failed", True, False),
    (False, True, False, "succeeded", False, False),
])
async def test_o3_recovers_only_settled_schema_valid_attempt_patch(
    tmp_path, cached, valid, canonical, status, enabled, copied,
):
    import json

    from doxagent.codex_runtime.schema import (
        CODEX_DOCUMENT3_WORKFLOW_VERSION,
        CodexD3AgentRole,
        CodexD3Node,
        ResearchLane,
    )
    from doxagent.codex_worker.schema import WorkerJob, WorkerRunRequest
    from doxagent.persistent_runtime_v2.worker_receipts import ReceiptWorker

    request = WorkerRunRequest(
        workflow_version=CODEX_DOCUMENT3_WORKFLOW_VERSION, research_lane=ResearchLane.DOCUMENT3,
        run_id="mu-o3", ticker="MU", node=CodexD3Node.O3_MAINTAIN,
        agent_role=CodexD3AgentRole.O3, attempt_id="d3_o3_maintain-01",
        cutoff_at=datetime.now(UTC), prompt="frozen maintenance", output_schema={"type": "object"},
    )
    patch = json.dumps({
        "base_policy_set_version": 7,
        "event_library_ref": {
            "contract_version": "v1", "ticker": "MU", "version": 26,
            "sha256": "a" * 64, "published_at": datetime.now(UTC).isoformat(),
        },
    }) if valid else "{}"
    target = "output/work/policy_patch.json"
    source = "attempts/d3_o3_maintain-01/" + target
    job = WorkerJob(
        job_id="settled-job", run_id=request.run_id, attempt_id=request.attempt_id, status=status,
    )

    class Worker:
        def __init__(self):
            self.files = {source: patch, **({target: "existing canonical"} if canonical else {})}
            self.reads, self.writes, self.calls = [], [], 0

        async def read_text(self, run_id, path):
            self.reads.append(path)
            if path not in self.files:
                raise FileNotFoundError(path)
            return SimpleNamespace(content=self.files[path])

        async def write_text(self, run_id, path, content):
            self.writes.append(path)
            self.files[path] = content

        async def run(self, request):
            self.calls += 1
            return job

    worker = Worker()
    journal = RuntimeJournal(tmp_path / "runtime.db")
    durable = ReceiptWorker(worker, journal, "maintenance", recover_artifacts=enabled)
    identity = f"runtime-worker:maintenance:{request.run_id}:{request.node}:maintain"
    if cached:
        journal.set("worker_requests", identity, request.model_dump(mode="json"))
        journal.set("worker_receipts", identity, job.model_dump(mode="json"))
        request = request.model_copy(update={"attempt_id": "d3_o3_maintain-02"})
    await durable.run(request)
    assert worker.writes == ([target] if copied else [])
    assert worker.calls == (0 if cached else 1)
    audit = journal.get("worker_artifact_recovery", identity)
    if copied:
        assert audit["job_id"] == job.job_id and audit["source"] == source
        assert len(audit["source_sha256"]) == 64
        assert json.loads(worker.files[target])["base_policy_set_version"] == 7
    else:
        assert audit is None
