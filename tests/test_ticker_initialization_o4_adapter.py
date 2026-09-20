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


def setup(
    tmp_path: Path,
    runner,
    *,
    delivery_enabled: bool = True,
    include_delivery_node: bool = True,
):
    control = InitializationRepository(tmp_path / "control.db")
    for filename, value in (("policy", _policy()), ("d2", {"shells": []})):
        (tmp_path / f"{filename}.json").write_text(json.dumps(value), encoding="utf-8")
    plan = [
        NodeSpec(
            key="o4.configure",
            block="O4",
            inputs={
                "policy_set_path": str(tmp_path / "policy.json"),
                "document2_path": str(tmp_path / "d2.json"),
            },
        )
    ]
    terminal = "o4.configure"
    if include_delivery_node:
        plan.append(NodeSpec(key="o4.deliver", block="O4", dependencies=["o4.configure"]))
        terminal = "o4.deliver"
    plan.append(NodeSpec(key="o4.register", block="REGISTER", dependencies=[terminal]))
    run = control.submit(
        "MU",
        datetime.now(UTC),
        plan,
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
            initialization_delivery_enabled=delivery_enabled,
        )

        async def close():
            repository.close()

        return SimpleNamespace(
            repository=repository,
            orchestrator=orchestrator,
            message_bus=orchestrator.message_bus,
            close=close,
        )

    settings = DoxAgentSettings(_env_file=None).model_copy(
        update={"ticker_initialization_o4_delivery_enabled": delivery_enabled}
    )
    adapter = O4InitializationAdapter(settings, runtime_factory=factory)
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
async def test_unfinished_settlement_defers_need_without_repeating_configure(
    tmp_path: Path,
):
    runner = _FakeRunner(new_crawler=True, delivery_status=DeliveryItemStatus.IN_PROGRESS)
    control, run, candidate, adapter = setup(tmp_path, runner)
    worker = InitializationWorker(control, lambda _: adapter)
    assert (await worker.run_once()).status == "SUCCEEDED"
    assert len(control.attempts(run.initialization_id, "o4.deliver")) == 1
    assert await worker.run_once() is None
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


@pytest.mark.asyncio
async def test_frozen_default_path_keeps_crawler_need_without_dispatching_deliver(tmp_path):
    runner = _FakeRunner(new_crawler=True, delivery_status=DeliveryItemStatus.COMPLETED)
    control, run, candidate, adapter = setup(
        tmp_path,
        runner,
        delivery_enabled=False,
        include_delivery_node=False,
    )

    result = await InitializationWorker(control, lambda _: adapter).run_once()

    assert result.status == "SUCCEEDED", result.error
    assert [request.node for request in runner.requests] == [CodexMonitoringO4Node.CONFIGURE]
    nodes = {node.key: node for node in control.nodes(run.initialization_id)}
    assert set(nodes) == {"o4.configure", "o4.register"}
    assert nodes["o4.configure"].result is not None
    assert nodes["o4.configure"].result.quality_annotations == ["CRAWLER_DELIVERY_FROZEN"]
    assert nodes["o4.register"].result is not None
    configuration = nodes["o4.register"].result.artifacts["monitoring_configuration"]
    assert configuration["crawler_delivery_state"] == "FROZEN"
    assert configuration["pending_crawler_need_count"] == 1
    repository = MonitoringO4Repository(candidate.candidate.path.with_name("o4.sqlite3"))
    try:
        requests = repository.list_requests(ticker="MU")
        assert [request.node for request in requests] == [CodexMonitoringO4Node.CONFIGURE]
        plan = repository.get_plan(
            nodes["o4.configure"].result.artifacts["plan_id"],
            nodes["o4.configure"].result.artifacts["plan_version"],
        )
        assert plan is not None
        assert plan.source_needs[0].resolution.value == "NEW_CRAWLER_REQUIRED"
        assert repository.get_delivery_checkpoint(plan.plan_id, plan.plan_version) is None
    finally:
        repository.close()
    assert candidate.live.get_ticker_state("MU") is None


@pytest.mark.asyncio
async def test_frozen_path_still_requires_a_real_usable_binding(tmp_path):
    runner = _FakeRunner(new_crawler=True, delivery_status=DeliveryItemStatus.COMPLETED)
    control, run, candidate, adapter = setup(
        tmp_path,
        runner,
        delivery_enabled=False,
        include_delivery_node=False,
    )
    for binding in candidate.candidate.list_bindings(ticker="MU"):
        candidate.candidate.save_binding(binding.model_copy(update={"enabled": False}))

    result = await InitializationWorker(control, lambda _: adapter).run_once()

    assert result.status == "FAILED"
    nodes = {node.key: node for node in control.nodes(run.initialization_id)}
    assert nodes["o4.configure"].status == "SUCCEEDED"
    assert nodes["o4.register"].status == "FAILED"
    assert "no usable registered monitoring binding" in (nodes["o4.register"].error or "")
    assert [request.node for request in runner.requests] == [CodexMonitoringO4Node.CONFIGURE]


@pytest.mark.asyncio
async def test_frozen_legacy_plan_bypasses_deliver_without_claiming_delivery_success(tmp_path):
    runner = _FakeRunner(new_crawler=True, delivery_status=DeliveryItemStatus.COMPLETED)
    control, run, _, adapter = setup(
        tmp_path,
        runner,
        delivery_enabled=False,
        include_delivery_node=True,
    )

    result = await InitializationWorker(control, lambda _: adapter).run_once()

    assert result.status == "SUCCEEDED", result.error
    assert [request.node for request in runner.requests] == [CodexMonitoringO4Node.CONFIGURE]
    nodes = {node.key: node for node in control.nodes(run.initialization_id)}
    assert nodes["o4.deliver"].result is not None
    assert nodes["o4.deliver"].result.quality_annotations == [
        "CRAWLER_DELIVERY_FROZEN_NOT_EXECUTED"
    ]
    configuration = nodes["o4.register"].result.artifacts["monitoring_configuration"]
    assert configuration["crawler_delivery_state"] == "FROZEN"
    assert configuration["pending_crawler_need_count"] == 1
