import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from doxagent.codex_runtime.schema import CodexMonitoringO4Node
from doxagent.message_bus_v2.service import MessageBusV2Service
from doxagent.settings import DoxAgentSettings
from doxagent.ticker_initialization import InitializationRepository, InitializationWorker, NodeSpec
from doxagent.ticker_initialization.configuration import CandidateConfiguration
from doxagent.ticker_initialization.o4_adapter import O4InitializationAdapter
from doxagent.ticker_initialization.service import NodeContext
from doxagent.workflows.codex_monitoring_o4.orchestrator import MonitoringO4Orchestrator
from doxagent.workflows.codex_monitoring_o4.repository import MonitoringO4Repository
from doxagent.workflows.codex_monitoring_o4.schema import DeliveryItemStatus
from tests.test_codex_monitoring_o4 import _FakeRunner, _policy


def setup(tmp_path: Path, runner):
    control = InitializationRepository(tmp_path / "control.db")
    for filename, value in (("policy", _policy()), ("d2", {"shells": []})):
        (tmp_path / f"{filename}.json").write_text(json.dumps(value), encoding="utf-8")
    run = control.submit(
        "MU",
        datetime.now(UTC),
        [
            NodeSpec(
                key="o4.configure",
                block="O4",
                inputs={
                    "policy_set_path": str(tmp_path / "policy.json"),
                    "document2_path": str(tmp_path / "d2.json"),
                },
            ),
            NodeSpec(key="o4.deliver", block="O4", dependencies=["o4.configure"]),
            NodeSpec(key="o4.register", block="REGISTER", dependencies=["o4.deliver"]),
        ],
    )
    candidate = CandidateConfiguration(tmp_path / "bus.db", run.initialization_id, "MU")
    candidate.prepare()

    def factory(context):
        target = CandidateConfiguration(tmp_path / "bus.db", context.run.initialization_id, "MU")
        target.prepare(snapshot_from=candidate.candidate.path)
        repository = MonitoringO4Repository(target.candidate.path.with_name("o4.sqlite3"))
        orchestrator = MonitoringO4Orchestrator(
            repository=repository,
            runner=runner,
            message_bus=MessageBusV2Service(target.candidate),
            message_bus_enabled=True,
        )

        async def close():
            repository.close()

        return SimpleNamespace(
            repository=repository,
            orchestrator=orchestrator,
            message_bus=orchestrator.message_bus,
            close=close,
        )

    adapter = O4InitializationAdapter(DoxAgentSettings(_env_file=None), runtime_factory=factory)
    return control, run, candidate, adapter


@pytest.mark.asyncio
@pytest.mark.parametrize("node", ["o4.configure", "o4.deliver", "o4.register"])
async def test_rerun_imports_frozen_candidate_without_running_successors(tmp_path, node):
    runner = _FakeRunner(new_crawler=True, delivery_status=DeliveryItemStatus.COMPLETED)
    control, run, candidate, adapter = setup(tmp_path, runner)
    assert (await InitializationWorker(control, lambda _: adapter).run_once()).status == "SUCCEEDED"
    original_requests = len(runner.requests)
    rerun = control.rerun(run.initialization_id, node_key=node, reason="operator-selected rerun")
    result = await InitializationWorker(control, lambda _: adapter).run_once()
    assert result.status == "SUCCEEDED", result.error
    assert [n.key for n in control.nodes(rerun.initialization_id)] == [node]
    assert len(runner.requests) == original_requests + (0 if node == "o4.register" else 1)
    assert candidate.live.get_ticker_state("MU") is None


@pytest.mark.asyncio
async def test_deliver_retries_without_rerunning_configure_and_keeps_candidate_unpollable(
    tmp_path: Path,
):
    class Runner(_FakeRunner):
        failures = 1

        async def run(self, request):
            if request.node is CodexMonitoringO4Node.DELIVER and self.failures:
                self.failures -= 1
                self.requests.append(request)
                raise RuntimeError("offline interruption")
            return await super().run(request)

    runner = Runner(
        new_crawler=True, delivery_status=DeliveryItemStatus.HUMAN_INTERVENTION_REQUIRED
    )
    control, run, candidate, adapter = setup(tmp_path, runner)
    result = await InitializationWorker(control, lambda _: adapter).run_once()
    assert result.status == "SUCCEEDED"
    assert [r.node for r in runner.requests].count(CodexMonitoringO4Node.CONFIGURE) == 1
    assert [r.node for r in runner.requests].count(CodexMonitoringO4Node.DELIVER) == 2
    assert len(control.attempts(run.initialization_id, "o4.deliver")) == 2
    assert candidate.live.get_ticker_state("MU") is None
    assert candidate.candidate.get_ticker_state("MU") is None


@pytest.mark.asyncio
async def test_unfinished_settlement_exhausts_budget_and_manual_resume_only_retries_deliver(
    tmp_path: Path,
):
    runner = _FakeRunner(new_crawler=True, delivery_status=DeliveryItemStatus.IN_PROGRESS)
    control, run, candidate, adapter = setup(tmp_path, runner)
    worker = InitializationWorker(control, lambda _: adapter)
    assert (await worker.run_once()).status == "FAILED"
    assert len(control.attempts(run.initialization_id, "o4.deliver")) == 2
    assert await worker.run_once() is None
    runner.delivery_status = DeliveryItemStatus.HUMAN_INTERVENTION_REQUIRED
    control.resume(run.initialization_id, reason="offline operator recovery", node_key="o4.deliver")
    assert (await worker.run_once()).status == "SUCCEEDED"
    assert [r.node for r in runner.requests].count(CodexMonitoringO4Node.CONFIGURE) == 1
    assert candidate.live.get_ticker_state("MU") is None


@pytest.mark.asyncio
async def test_child_completed_before_parent_receipt_is_reconciled_without_model_call(
    tmp_path: Path,
):
    runner = _FakeRunner(new_crawler=False, delivery_status=DeliveryItemStatus.COMPLETED)
    control, run, _, adapter = setup(tmp_path, runner)
    lease = control.claim("crashing-worker", lease_seconds=60)
    node = control.begin(lease, "o4.configure", {"workflow": "V2"})
    await adapter.execute(NodeContext(control, lease, node))
    control.heartbeat(lease, lease_seconds=-1)
    assert (await InitializationWorker(control, lambda _: adapter).run_once()).status == "SUCCEEDED"
    assert len(runner.requests) == 1
    assert len(control.attempts(run.initialization_id, "o4.configure")) == 1
