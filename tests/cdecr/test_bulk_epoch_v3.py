from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from statistics import median
from time import perf_counter

import pytest

from cdecr.bulk_epoch.engine import BULK_STAGE_GRAPH_VERSION, BulkEpochEngine
from cdecr.bulk_epoch.executor import AsyncModelExecutor
from cdecr.bulk_epoch.indexes import MultiKeyBoundedIndex
from cdecr.bulk_epoch.package_stage import _constrained_components
from cdecr.bulk_epoch.writer import BulkWriter
from cdecr.config import CDECRSettings
from cdecr.contracts import PackageAssignmentRelation
from cdecr.cross_document import CrossDocumentEngine
from cdecr.cross_document_contracts import (
    PackageAssignmentDecision,
    PackageCandidateAssessment,
)
from cdecr.models import ModelTier
from cdecr.ports import StructuredModelRequest, StructuredModelResult
from cdecr.registry import SQLiteCDECRRegistry
from tests.cdecr.test_cross_document import (
    FakeEmbedding,
    FakeStructured,
    add,
    market_mention,
    metric_mention,
    source,
)


class AsyncFakeStructured:
    def __init__(self, delegate: FakeStructured, *, delay: float = 0.0) -> None:
        self.delegate = delegate
        self.model = "fake-structured"
        self.delay = delay
        self.active = 0
        self.max_active = 0
        self.lock = threading.Lock()

    async def acomplete(self, request: StructuredModelRequest) -> StructuredModelResult:
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if request.system_prompt == "Return JSON.":
                return StructuredModelResult(
                    payload={"ok": True},
                    input_tokens=3,
                    output_tokens=2,
                    latency_ms=20,
                    model=self.model,
                )
            return self.delegate.complete(request)
        finally:
            with self.lock:
                self.active -= 1


def executor(
    m2: AsyncFakeStructured, m3: AsyncFakeStructured
) -> AsyncModelExecutor:
    return AsyncModelExecutor(
        clients={ModelTier.M2: m2, ModelTier.M3: m3, ModelTier.M4: m3},
        tier_limits={ModelTier.M2: 48, ModelTier.M3: 48, ModelTier.M4: 16},
        stage_limits={
            "field_coreference": 32,
            "atomic_coreference": 24,
            "atomic_coreference_escalation": 16,
            "package_assignment": 24,
            "package_merge": 24,
        },
        repair_limit=8,
        rates={
            ModelTier.M2: (1000.0, 48),
            ModelTier.M3: (1000.0, 48),
            ModelTier.M4: (1000.0, 16),
        },
    )


def test_async_executor_fixed_lane_and_local_failure_isolation() -> None:
    delegate = FakeStructured()
    async_client = AsyncFakeStructured(delegate, delay=0.02)
    hub = executor(async_client, async_client)
    request = StructuredModelRequest(
        system_prompt="Return JSON.",
        user_prompt="{}",
        json_schema={"type": "object"},
        metadata={"stage": "atomic_coreference", "priority": "normal"},
    )
    try:
        with ThreadPoolExecutor(max_workers=16) as pool:
            results = list(pool.map(lambda _: hub.complete(ModelTier.M2, request), range(16)))
        assert len(results) == 16
        assert async_client.max_active >= 8
        assert all(item.status == "SUCCEEDED" for item in hub.telemetry())
    finally:
        hub.close()


def test_async_executor_failure_does_not_cancel_other_requests() -> None:
    class OneFailure(AsyncFakeStructured):
        async def acomplete(self, request: StructuredModelRequest) -> StructuredModelResult:
            if request.user_prompt == "fail":
                raise ValueError("isolated failure")
            return await super().acomplete(request)

    async_client = OneFailure(FakeStructured(), delay=0.01)
    hub = executor(async_client, async_client)
    requests = [
        StructuredModelRequest(
            system_prompt="Return JSON.",
            user_prompt="fail" if index == 5 else "{}",
            json_schema={"type": "object"},
            metadata={"stage": "atomic_coreference", "priority": "normal"},
        )
        for index in range(12)
    ]
    try:
        with ThreadPoolExecutor(max_workers=12) as pool:
            futures = [
                pool.submit(hub.complete, ModelTier.M2, request) for request in requests
            ]
            succeeded = 0
            failed = 0
            for future in futures:
                try:
                    future.result()
                    succeeded += 1
                except ValueError:
                    failed += 1
        assert (succeeded, failed) == (11, 1)
        assert sum(item.status == "FAILED" for item in hub.telemetry()) == 1
    finally:
        hub.close()


def test_bulk_writer_serializes_concurrent_commits() -> None:
    actor_threads: list[int] = []

    def commit(value: int) -> int:
        actor_threads.append(threading.get_ident())
        return value

    with BulkWriter() as writer, ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda value: writer.run(lambda: commit(value)), range(20)))
    assert sorted(results) == list(range(20))
    assert len(set(actor_threads)) == 1


def test_n12_reducer_does_not_cross_explicit_not_related_edge() -> None:
    decisions = {
        "a": PackageAssignmentDecision(
            event_id="a",
            candidate_assessments=[
                PackageCandidateAssessment(
                    candidate_package_id="pb",
                    relation=PackageAssignmentRelation.MEMBER,
                    membership_relation="DISCLOSED_IN",
                    reason="same parent",
                ),
                PackageCandidateAssessment(
                    candidate_package_id="pc",
                    relation=PackageAssignmentRelation.NOT_RELATED,
                    reason="different parent",
                ),
            ],
            ranked_member_package_ids=["pb"],
            selected_member_package_id="pb",
            selection_reason="same parent",
        ),
        "b": PackageAssignmentDecision(
            event_id="b",
            candidate_assessments=[
                PackageCandidateAssessment(
                    candidate_package_id="pc",
                    relation=PackageAssignmentRelation.MEMBER,
                    membership_relation="DISCLOSED_IN",
                    reason="same parent",
                )
            ],
            ranked_member_package_ids=["pc"],
            selected_member_package_id="pc",
            selection_reason="same parent",
        ),
    }
    components = _constrained_components(
        ["a", "b", "c"], decisions, {"pa": "a", "pb": "b", "pc": "c"}
    )
    assert components == [["a", "b"], ["c"]]


def test_bulk_epoch_v3_is_only_bulk_path_and_reuses_finalized_epoch(
    tmp_path: Path,
) -> None:
    registry = SQLiteCDECRRegistry(tmp_path / "bulk-v3.sqlite3")
    registry.initialize()
    add(registry, source("MSG-1"), metric_mention("MSG-1"))
    add(registry, source("MSG-2"), metric_mention("MSG-2"))
    add(registry, source("MSG-3"), market_mention("MSG-3"))
    m2_delegate = FakeStructured()
    m3_delegate = FakeStructured()
    m2 = AsyncFakeStructured(m2_delegate)
    m3 = AsyncFakeStructured(m3_delegate)
    hub = executor(m2, m3)
    core = CrossDocumentEngine(
        registry=registry,
        embedding_client=FakeEmbedding(),
        m2_client=hub.client(ModelTier.M2),
        m3_client=hub.client(ModelTier.M3),
        hard_cannot_link_mode="shadow",
        n12_wire_protocol="on",
        n13_wire_protocol="on",
        n9_active_requests=24,
        n12_active_requests=24,
        n13_active_requests=24,
    )
    bulk = BulkEpochEngine(
        registry=registry,
        core=core,
        executor=hub,
        field_active_requests=32,
    )
    core.process = pytest.fail  # type: ignore[method-assign]
    try:
        first = bulk.process_batch(["MSG-3", "MSG-1", "MSG-2"])
        calls_before = registry.count_model_calls()
        second = bulk.process_batch(["MSG-1", "MSG-2", "MSG-3"])
    finally:
        bulk.close()
    assert [result.message_id for result in first] == ["MSG-1", "MSG-2", "MSG-3"]
    assert all(result.status.value == "SUCCEEDED" for result in first)
    assert all(result.reused for result in second)
    assert registry.count_model_calls() == calls_before
    epochs = registry.get_bulk_epoch(first[0].run_id.rsplit(":", 1)[0])
    assert epochs is not None
    assert epochs["status"] == "FINALIZED"
    assert epochs["orchestrator_version"] == BULK_STAGE_GRAPH_VERSION
    for kind in (
        "manifest_v1",
        "field_plan_v1",
        "field_overlay_v1",
        "atomic_plan_v1",
        "atomic_partition_v1",
        "package_plan_v1",
        "package_partition_v1",
        "n13_pair_plan_v1",
        "final_package_partition_v1",
    ):
        assert registry.get_bulk_epoch_artifact(str(epochs["epoch_id"]), kind) is not None
    tasks = registry.list_bulk_epoch_tasks(str(epochs["epoch_id"]))
    assert tasks
    assert {str(item["status"]) for item in tasks}.issubset({"SUCCEEDED", "FAILED"})


def test_stage_graph_capacity_defaults_are_fixed() -> None:
    settings = CDECRSettings(_env_file=None)
    assert (
        settings.scheduler_m1_concurrency,
        settings.scheduler_m2_concurrency,
        settings.scheduler_m3_concurrency,
        settings.scheduler_m4_concurrency,
    ) == (32, 48, 48, 16)
    assert settings.document_concurrency == 24
    assert settings.structured_request_start_interval_seconds == 0.0


def test_removed_bulk_component_path_is_absent() -> None:
    root = Path(__file__).parents[2]
    source = (root / "src" / "cdecr" / "cross_document.py").read_text(encoding="utf-8")
    config = (root / "src" / "cdecr" / "config.py").read_text(encoding="utf-8")
    for symbol in (
        "_bulk_document_components",
        "_finalize_bulk_epoch_packages",
        "bulk_atomic_component_workers",
        "bulk_package_component_workers",
        "defer_package_merge",
    ):
        assert symbol not in source
        assert symbol not in config


@pytest.mark.parametrize("shape", ["giant", "distributed"])
def test_bounded_planner_scale_is_linear(shape: str) -> None:
    measurements: list[tuple[int, int, int, float]] = []
    for size in (30, 60, 120, 240):
        samples: list[float] = []
        final_edges = 0
        final_bytes = 0
        for _ in range(9):
            started = perf_counter()
            for _repeat in range(25):
                index = MultiKeyBoundedIndex(bucket_limit=12)
                keys_by_id: dict[str, list[str]] = {}
                for item in range(size):
                    identity = f"item-{item:04d}"
                    keys = (
                        ["issuer:giant", f"period:{item % 4}"]
                        if shape == "giant"
                        else [f"issuer:{item // 4}", f"period:{item // 8}"]
                    )
                    keys_by_id[identity] = keys
                    index.add(identity, keys)
                payload = {
                    identity: index.query(keys, limit=12, exclude=identity)
                    for identity, keys in keys_by_id.items()
                }
            samples.append((perf_counter() - started) / 25)
            final_edges = index.stats().emitted_edges
            final_bytes = sum(
                len(identity) + sum(len(value) for value in candidates)
                for identity, candidates in payload.items()
            )
        measurements.append((size, final_edges, final_bytes, median(samples)))
        assert final_edges <= 12 * size
    for left, right in zip(measurements, measurements[1:], strict=False):
        assert right[1] / max(1, left[1]) <= 2.2
        assert right[2] / max(1, left[2]) <= 2.2
        assert right[3] / max(1e-9, left[3]) <= 2.3
