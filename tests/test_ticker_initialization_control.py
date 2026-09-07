from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from doxagent.ticker_initialization import (
    InitializationRepository,
    InitializationWorker,
    NodeContext,
    NodeResult,
    NodeSpec,
    RunStatus,
)
from doxagent.ticker_initialization.schema import InitializationError, LeaseLost, semantic_day

NOW = datetime(2026, 9, 5, tzinfo=UTC)


@pytest.fixture
def lease_clock(monkeypatch):
    instant = [1000.0]
    monkeypatch.setattr(
        "doxagent.ticker_initialization.repository.time",
        SimpleNamespace(time=lambda: instant[0]),
    )
    return instant


def plan() -> list[NodeSpec]:
    return [
        NodeSpec(key="d1", block="D1"),
        NodeSpec(key="cdecr", block="CDECR"),
        NodeSpec(key="o2", block="O2", dependencies=["d1", "cdecr"]),
        NodeSpec(key="d3.stage_a", block="D3", dependencies=["o2"]),
        NodeSpec(key="d3.compile", block="D3", dependencies=["d3.stage_a"]),
    ]


class Adapter:
    def __init__(self, failures: int = 0) -> None:
        self.failures = failures
        self.calls: list[str] = []
        self.outputs: dict[str, NodeResult] = {}

    async def reconcile(self, context: NodeContext) -> NodeResult | None:
        return self.outputs.get(context.node.key)

    async def execute(self, context: NodeContext) -> NodeResult:
        self.calls.append(context.node.key)
        if context.node.key == "d3.compile" and self.failures:
            self.failures -= 1
            raise RuntimeError("compile unavailable")
        result = NodeResult(artifacts={"version": 1}, quality_annotations=["PARTIAL", "DEGRADED"])
        self.outputs[context.node.key] = result
        return result


@pytest.mark.asyncio
async def test_retry_only_failed_compile_and_partial_never_blocks(tmp_path: Path) -> None:
    repo = InitializationRepository(tmp_path / "control.db")
    run = repo.submit("mu", NOW, plan())
    adapter = Adapter(failures=1)
    result = await InitializationWorker(repo, lambda _: adapter).run_once()
    assert result is not None and result.status == RunStatus.SUCCEEDED
    assert adapter.calls.count("d3.compile") == 2
    assert adapter.calls.count("d3.stage_a") == 1
    assert len(repo.attempts(run.initialization_id, "d3.compile")) == 2


@pytest.mark.asyncio
async def test_budget_survives_restart_and_only_manual_resume_refreshes_it(tmp_path: Path) -> None:
    path = tmp_path / "control.db"
    repo = InitializationRepository(path)
    run = repo.submit("MU", NOW, plan())
    adapter = Adapter(failures=2)
    result = await InitializationWorker(repo, lambda _: adapter).run_once()
    assert result is not None and result.status == RunStatus.FAILED
    assert result.manual_resume_required
    restarted = InitializationRepository(path)
    assert await InitializationWorker(restarted, lambda _: adapter).run_once() is None
    restarted.resume(run.initialization_id, reason="fixed compile", node_key="d3.compile")
    result = await InitializationWorker(restarted, lambda _: adapter).run_once()
    assert result is not None and result.status == RunStatus.SUCCEEDED
    assert adapter.calls.count("d1") == 1
    assert adapter.calls.count("d3.compile") == 3
    history = restarted.attempts(run.initialization_id, "d3.compile")
    assert [(a.generation, a.ordinal) for a in history] == [(1, 1), (1, 2), (2, 1)]


def test_duplicate_submit_is_invalid_without_mutation(tmp_path: Path) -> None:
    repo = InitializationRepository(tmp_path / "control.db")
    run = repo.submit("MU", NOW, plan())
    with pytest.raises(InitializationError, match="DUPLICATE_ACTIVE"):
        InitializationRepository(repo.path).submit("mu", NOW, plan())
    assert repo.get(run.initialization_id) == run


@pytest.mark.asyncio
async def test_completed_external_output_is_adopted_after_lost_receipt(
    tmp_path: Path, lease_clock
) -> None:
    repo = InitializationRepository(tmp_path / "control.db")
    run = repo.submit("MU", NOW, [NodeSpec(key="d1", block="D1")])
    lease = repo.claim("crashed-worker", lease_seconds=0.02)
    assert lease
    node = repo.begin(lease, "d1", {})
    adapter = Adapter()
    await adapter.execute(NodeContext(repo, lease, node))
    lease_clock[0] += 0.03
    result = await InitializationWorker(repo, lambda _: adapter).run_once()
    assert result is not None and result.status == RunStatus.SUCCEEDED
    assert adapter.calls == ["d1"]
    assert len(repo.attempts(run.initialization_id, "d1")) == 1
    with pytest.raises(LeaseLost):
        repo.complete(lease, "d1", NodeResult())


@pytest.mark.asyncio
async def test_unknown_crashed_attempt_counts_toward_retry_budget(
    tmp_path: Path, lease_clock
) -> None:
    repo = InitializationRepository(tmp_path / "control.db")
    run = repo.submit("MU", NOW, [NodeSpec(key="d1", block="D1")])
    lease = repo.claim("crashed-worker", lease_seconds=0.02)
    assert lease
    repo.begin(lease, "d1", {})
    lease_clock[0] += 0.03
    adapter = Adapter()
    result = await InitializationWorker(repo, lambda _: adapter).run_once()
    assert result is not None and result.status == RunStatus.SUCCEEDED
    assert adapter.calls == ["d1"]
    assert len(repo.attempts(run.initialization_id, "d1")) == 2


@pytest.mark.asyncio
async def test_parallel_success_is_persisted_before_sibling_finishes(tmp_path: Path) -> None:
    repo = InitializationRepository(tmp_path / "control.db")
    run = repo.submit("MU", NOW, plan())
    signal = asyncio.Event()

    class Parallel(Adapter):
        async def execute(self, context: NodeContext) -> NodeResult:
            if context.node.key == "cdecr":
                await signal.wait()
                assert repo.nodes(run.initialization_id)[0].status == "SUCCEEDED"
            value = await super().execute(context)
            if context.node.key == "d1":
                signal.set()
            return value

    adapter = Parallel()
    result = await InitializationWorker(repo, lambda _: adapter).run_once()
    assert result is not None and result.status == RunStatus.SUCCEEDED


def test_outbox_coalesces_and_old_ack_does_not_remove_new_summary(tmp_path: Path) -> None:
    repo = InitializationRepository(tmp_path / "control.db")
    run = repo.submit("MU", NOW, plan())
    seq = repo.outbox()[0]["state_seq"]
    lease = repo.claim("worker")
    assert lease
    repo.begin(lease, "d1", {"prompt": "local-only"})
    repo.receipt(lease, "d1", {"large_payload": "X" * 100000})
    repo.acknowledge_summary(run.initialization_id, seq)
    summary = repo.outbox()[0]
    assert "large_payload" not in str(summary)
    assert len(str(summary)) < 8192
    repo.acknowledge_summary(run.initialization_id, summary["state_seq"])
    assert repo.outbox() == []


def test_activation_is_immutable_and_idempotent(tmp_path: Path) -> None:
    repo = InitializationRepository(tmp_path / "control.db")
    repo.submit("MU", NOW, plan())
    lease = repo.claim("worker")
    assert lease
    repo.stage_revision(lease, "rev1", {"d1": "artifact1"})
    repo.activate(lease, "rev1")
    repo.activate(lease, "rev1")
    with pytest.raises(InitializationError, match="immutable"):
        repo.stage_revision(lease, "rev1", {"d1": "artifact2"})
    active = repo.active_revision("MU")
    assert active and active["artifacts"] == {"d1": "artifact1"}


@pytest.mark.asyncio
async def test_manual_rerun_selects_one_node_and_freezes_external_dependencies(
    tmp_path: Path,
) -> None:
    repo = InitializationRepository(tmp_path / "control.db")
    original = repo.submit("MU", NOW, plan())
    adapter = Adapter()
    await InitializationWorker(repo, lambda _: adapter).run_once()
    rerun = repo.rerun(original.initialization_id, node_key="d3.compile", reason="operator repair")
    assert [n.key for n in repo.nodes(rerun.initialization_id)] == ["d3.compile"]

    class Replay(Adapter):
        async def execute(self, context: NodeContext) -> NodeResult:
            assert context.dependency("d3.stage_a").artifacts == {"version": 1}
            return await super().execute(context)

    replay = Replay()
    result = await InitializationWorker(repo, lambda _: replay).run_once()
    assert result and result.status == RunStatus.SUCCEEDED
    assert replay.calls == ["d3.compile"]
    assert repo.get(original.initialization_id).status == RunStatus.SUCCEEDED
    assert all(n.generation == 1 for n in repo.nodes(original.initialization_id))


@pytest.mark.asyncio
async def test_cloud_offline_leaves_local_success_and_coalesced_retry(tmp_path: Path) -> None:
    from doxagent.ticker_initialization.sync import flush_summaries

    repo = InitializationRepository(tmp_path / "control.db")
    run = repo.submit("MU", NOW, plan())
    adapter = Adapter()
    await InitializationWorker(repo, lambda _: adapter).run_once()

    class Offline:
        async def upsert(self, summary: dict[str, object]) -> None:
            raise ConnectionError("offline")

    assert await flush_summaries(repo, Offline()) == 0
    assert repo.get(run.initialization_id).status == RunStatus.SUCCEEDED
    assert len(repo.outbox()) == 1


@pytest.mark.asyncio
async def test_dynamic_plan_expansion_does_not_reexecute_completed_node(tmp_path: Path) -> None:
    repo = InitializationRepository(tmp_path / "control.db")
    run = repo.submit("MU", NOW, [NodeSpec(key="survey", block="O2")])

    class Dynamic(Adapter):
        async def execute(self, context: NodeContext) -> NodeResult:
            if context.node.key == "survey":
                context.repository.expand(
                    context.lease,
                    [
                        NodeSpec(key="wave:1", block="O2", dependencies=["survey"]),
                        NodeSpec(key="wave:2", block="O2", dependencies=["wave:1"]),
                    ],
                )
            return await super().execute(context)

    adapter = Dynamic()
    result = await InitializationWorker(repo, lambda _: adapter).run_once()
    assert result and result.status == RunStatus.SUCCEEDED
    assert adapter.calls == ["survey", "wave:1", "wave:2"]
    assert len(repo.nodes(run.initialization_id)) == 3


def test_cli_submit_duplicate_and_read_only_status(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    from doxagent.ticker_initialization.cli import main

    # Use an existing source file as a plan fixture without running any adapter.
    plan_path = Path(__file__).parent / "fixtures" / "ticker_initialization_plan.json"
    common = ["--database", str(tmp_path / "control.db")]
    submit = [
        *common,
        "submit",
        "--ticker",
        "MU",
        "--research-cutoff-at",
        NOW.isoformat(),
        "--plan",
        str(plan_path),
    ]
    assert main(submit) == 0
    import json

    run_id = json.loads(capsys.readouterr().out)["initialization_id"]
    assert main(submit) == 2
    assert "DUPLICATE_ACTIVE_INITIALIZATION" in capsys.readouterr().out
    assert main([*common, "status", "--initialization-id", run_id]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "QUEUED"


def test_bus_cursor_recovers_legacy_partial_commit_without_seeking_again(tmp_path: Path) -> None:
    from doxagent.message_bus_v2.repository import MessageBusV2Repository
    from doxagent.message_bus_v2.schema import TickerMonitoringState

    repo = MessageBusV2Repository(tmp_path / "bus.db")
    try:
        repo.save_ticker_state(TickerMonitoringState(ticker="MU", profile_version=1))
        # Simulate the old implementation crashing after offset commit, before marker write.
        repo.commit_consumer_offset("runtime", "MU", 12)
        assert repo.initialize_runtime_cursor("runtime", "MU") == 12
        state = repo.get_ticker_state("MU")
        assert state is not None and state.runtime_cursor_initialized
        repo.commit_consumer_offset("runtime", "MU", 15)
        assert repo.initialize_runtime_cursor("runtime", "MU") == 15
    finally:
        repo.close()


@pytest.mark.parametrize(
    ("at", "day"),
    [
        ("2026-03-08T06:59:59+00:00", "2026-03-07"),
        ("2026-03-08T07:00:00+00:00", "2026-03-08"),
        ("2026-11-01T06:59:59+00:00", "2026-10-31"),
        ("2026-11-01T07:00:00+00:00", "2026-11-01"),
    ],
)
def test_semantic_day_dst(at: str, day: str) -> None:
    assert semantic_day(datetime.fromisoformat(at)) == day
