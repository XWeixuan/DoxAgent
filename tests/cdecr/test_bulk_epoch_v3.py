from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from statistics import median
from time import perf_counter
from types import SimpleNamespace

import pytest

from cdecr.bulk_epoch.engine import BULK_STAGE_GRAPH_VERSION, BulkEpochEngine
from cdecr.bulk_epoch.executor import AsyncModelExecutor
from cdecr.bulk_epoch.indexes import MultiKeyBoundedIndex
from cdecr.bulk_epoch.package_stage import _constrained_components
from cdecr.bulk_epoch.writer import BulkWriter
from cdecr.config import CDECRSettings
from cdecr.contracts import (
    EventFamily,
    EventPackage,
    PackageAssignmentRelation,
    PackageFamily,
    PackageKind,
    PackageStatus,
    PackageTimeRange,
)
from cdecr.cross_document import CrossDocumentEngine, _balanced_request_batches
from cdecr.cross_document_contracts import (
    PackageAssignmentDecision,
    PackageCandidateAssessment,
    PackagePairBoundary,
)
from cdecr.field_coreference_contracts import (
    ATOMIC_OBJECT_FIELD_NAMESPACES,
    FieldNamespace,
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


def executor(m2: AsyncFakeStructured, m3: AsyncFakeStructured) -> AsyncModelExecutor:
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
            futures = [pool.submit(hub.complete, ModelTier.M2, request) for request in requests]
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


def test_async_executor_enforces_shared_provider_hard_limit() -> None:
    delegate = FakeStructured()
    async_client = AsyncFakeStructured(delegate, delay=0.03)
    hub = AsyncModelExecutor(
        clients={
            ModelTier.M2: async_client,
            ModelTier.M3: async_client,
            ModelTier.M4: async_client,
        },
        tier_limits={ModelTier.M2: 24, ModelTier.M3: 24, ModelTier.M4: 24},
        stage_limits={"atomic_coreference": 24},
        repair_limit=4,
        rates={
            ModelTier.M2: (1000.0, 24),
            ModelTier.M3: (1000.0, 24),
            ModelTier.M4: (1000.0, 24),
        },
        provider_target=8,
        provider_hard_limit=10,
        provider_start_rate=1000,
        provider_initial_burst=64,
    )
    request = StructuredModelRequest(
        system_prompt="Return JSON.",
        user_prompt="{}",
        json_schema={"type": "object"},
        metadata={"stage": "atomic_coreference", "priority": "normal"},
    )
    try:
        with ThreadPoolExecutor(max_workers=24) as pool:
            results = list(pool.map(lambda _: hub.complete(ModelTier.M3, request), range(24)))
        assert len(results) == 24
        assert hub.provider_snapshot().max_active == 10
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


def test_bulk_writer_has_bounded_queue_and_single_writer_telemetry() -> None:
    release = threading.Event()

    def commit(value: int) -> int:
        release.wait(timeout=2)
        return value

    with BulkWriter(low_watermark=1, high_watermark=2, hard_limit=4) as writer:
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [
                pool.submit(writer.run, lambda value=value: commit(value)) for value in range(4)
            ]
            deadline = perf_counter() + 1
            while writer.snapshot().max_queued < 2 and perf_counter() < deadline:
                threading.Event().wait(0.005)
            snapshot = writer.snapshot()
            assert snapshot.active_writers == 1
            assert snapshot.max_queued >= 2
            assert snapshot.high_watermark_hits >= 1
            release.set()
            assert sorted(future.result() for future in futures) == list(range(4))
    assert writer.snapshot().active_writers == 0
    assert writer.snapshot().completed == 4


def test_registry_serializes_process_local_transactions(tmp_path: Path) -> None:
    registry = SQLiteCDECRRegistry(tmp_path / "registry-writer-gate.sqlite3")
    registry.initialize()
    active = 0
    max_active = 0
    guard = threading.Lock()
    original = registry._connection

    @contextmanager
    def observed_connection():
        nonlocal active, max_active
        with original() as connection:
            with guard:
                active += 1
                max_active = max(max_active, active)
            try:
                threading.Event().wait(0.005)
                yield connection
            finally:
                with guard:
                    active -= 1

    registry._connection = observed_connection  # type: ignore[method-assign]
    with ThreadPoolExecutor(max_workers=16) as pool:
        rows = list(pool.map(lambda _: registry.count_model_calls(), range(64)))
    assert rows == [0] * 64
    assert max_active == 1


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
    # Model NOT_RELATED is a soft negative. It must not veto an independently
    # supported MEMBER path through another task.
    assert components == [["a", "b", "c"]]


def test_constrained_components_respects_only_structured_hard_negative() -> None:
    decisions = {
        "a": PackageAssignmentDecision(
            event_id="a",
            candidate_assessments=[
                PackageCandidateAssessment(
                    candidate_package_id="pb",
                    relation=PackageAssignmentRelation.MEMBER,
                    membership_relation="DISCLOSED_IN",
                    reason="same parent",
                )
            ],
            ranked_member_package_ids=["pb"],
            selected_member_package_id="pb",
            selection_reason="same parent",
        )
    }
    components = _constrained_components(
        ["a", "b"],
        decisions,
        {"pa": "a", "pb": "b"},
        hard_negative_pairs={frozenset(("a", "b"))},
    )
    assert components == [["a"], ["b"]]


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
    assert epochs["result"]["package_stage"]["n13_pair_local_applied_count"] == 0
    assert epochs["result"]["async_executor"]["provider"]["target"] == 100
    assert epochs["result"]["async_executor"]["provider"]["hard_limit"] == 160
    assert all(item["active_writers"] == 0 for item in epochs["result"]["bulk_writer"])
    for kind in (
        "manifest_v1",
        "field_plan_v1",
        "field_overlay_v1",
        "atomic_plan_v1",
        "atomic_late_plan_v1",
        "atomic_late_partition_v1",
        "atomic_partition_v1",
        "package_plan_v1",
        "package_wave_c_plan_v1",
        "package_wave_c_partition_v1",
        "package_partition_v1",
        "n13_pair_plan_v1",
        "n13_late_apply_plan_v1",
        "final_package_partition_v1",
    ):
        assert registry.get_bulk_epoch_artifact(str(epochs["epoch_id"]), kind) is not None
    n13_apply_plan = registry.get_bulk_epoch_artifact(
        str(epochs["epoch_id"]), "n13_late_apply_plan_v1"
    )
    assert n13_apply_plan is not None
    assert n13_apply_plan["payload"]["pair_local"] is False
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
    ) == (64, 128, 160, 96)
    assert settings.structured_provider_target_concurrency == 100
    assert settings.structured_provider_hard_concurrency == 160
    assert settings.document_workers == 120
    assert settings.document_block_concurrency == 24
    assert settings.dreamer_active_requests == 100
    assert settings.grounder_active_requests == 100
    assert settings.judge_active_requests == 64
    assert settings.field_active_requests == 100
    assert settings.n9_active_requests == 72
    assert settings.n9_escalation_active_requests == 32
    assert settings.n9_late_active_requests == 24
    assert settings.n12_active_requests == 48
    assert settings.package_wave_c_active_requests == 32
    assert settings.n13_active_requests == 96
    assert settings.n13_planner_version == "indexed_v2"
    assert settings.item_repair_active_requests == 16
    assert settings.structured_request_start_interval_seconds == 0.0
    assert settings.atomic_late_task_cap == 48
    assert settings.package_wave_c_pair_cap == 64
    assert settings.late_max_spoke_members == 4
    assert settings.late_max_spokes_per_hub == 4
    assert not settings.n13_pair_local_apply


def test_wave_c_source_has_no_full_pair_scan() -> None:
    root = Path(__file__).parents[2]
    source = (root / "src" / "cdecr" / "bulk_epoch" / "late_stage.py").read_text(encoding="utf-8")
    assert "MultiKeyBoundedIndex" in source
    assert "for right_id in ids[index + 1 :]" not in source
    assert "scheduler_edge_cap" in source


def test_n13_balances_one_tail_wave_without_losing_pair_coverage() -> None:
    items = list(range(300))
    batches = _balanced_request_batches(
        items,
        nominal_size=12,
        active_requests=24,
        max_balanced_size=13,
    )
    assert len(batches) == 24
    assert max(map(len, batches)) == 13
    assert [item for batch in batches for item in batch] == items


def test_n13_batch_balancing_does_not_expand_other_shapes() -> None:
    items = list(range(316))
    batches = _balanced_request_batches(
        items,
        nominal_size=12,
        active_requests=24,
        max_balanced_size=13,
    )
    assert len(batches) == 27
    assert max(map(len, batches)) == 12
    assert [item for batch in batches for item in batch] == items


def test_package_pair_boundary_compact_protocol_and_hard_scope() -> None:
    boundary = PackagePairBoundary(
        shared_artifact_ids=["A"],
        shared_source_member=True,
        instrument_conflict=True,
        object_scope_difference=True,
        left_member_count=1,
        right_member_count=2,
    )
    assert boundary.hard_blocked
    assert boundary.compact_signals() == {
        "same": ["artifact", "source"],
        "diff": ["instrument", "object_scope"],
    }
    assert boundary.compact_signals(include_object_scope=False) == {
        "same": ["artifact", "source"],
        "diff": ["instrument"],
    }
    object_only = boundary.model_copy(
        update={
            "shared_artifact_ids": [],
            "shared_source_member": False,
            "instrument_conflict": False,
        }
    )
    assert not object_only.hard_blocked


def test_package_pair_boundary_extracts_only_complete_market_conflicts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = object.__new__(CrossDocumentEngine)

    def package(package_id: str, *, artifact: str | None = None) -> EventPackage:
        return EventPackage(
            package_id=package_id,
            package_kind=PackageKind.BOUNDED,
            package_family=PackageFamily.OTHER,
            canonical_title=package_id,
            anchor_entities=[],
            anchor_artifact_id=artifact,
            time_range=PackageTimeRange(),
            member_event_ids=[f"event-{package_id}"],
            canonical_summary=package_id,
            status=PackageStatus.UNKNOWN,
        )

    left = package("left")
    right = package("right")
    instruments = {"left": {"STOCK_A"}, "right": {"INDEX_B"}}
    objects = {"left": {"PRODUCT_A"}, "right": {"PRODUCT_B"}}

    def trusted_fields(item: EventPackage, namespaces: set[FieldNamespace]) -> set[str]:
        if FieldNamespace.PARTICIPANT_INSTRUMENT in namespaces:
            return instruments[item.package_id]
        if namespaces.intersection(ATOMIC_OBJECT_FIELD_NAMESPACES):
            return objects[item.package_id]
        return set()

    monkeypatch.setattr(engine, "_trusted_package_anchor_ids", lambda item: set())
    monkeypatch.setattr(engine, "_trusted_package_field_ids", trusted_fields)
    monkeypatch.setattr(engine, "_package_market_measures", lambda item: {item.package_id})
    monkeypatch.setattr(engine, "_package_trading_sessions", lambda item: {item.package_id})
    monkeypatch.setattr(engine, "_package_is_pure_market", lambda _item: True)
    monkeypatch.setattr(
        engine,
        "_package_member_events",
        lambda _item: [SimpleNamespace(event_family=EventFamily.MARKET_MOVEMENT)],
    )

    boundary = engine._package_pair_boundary(left, right, [])
    assert boundary.instrument_conflict
    assert boundary.market_measure_conflict
    assert boundary.session_boundary
    assert boundary.object_scope_difference
    assert boundary.hard_blocked

    instruments["right"] = set()
    boundary_with_missing_instrument = engine._package_pair_boundary(left, right, [])
    assert not boundary_with_missing_instrument.instrument_conflict


def test_same_earnings_artifact_different_child_scope_is_not_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = object.__new__(CrossDocumentEngine)
    packages = [
        EventPackage(
            package_id=value,
            package_kind=PackageKind.BOUNDED,
            package_family=PackageFamily.EARNINGS_DISCLOSURE,
            canonical_title=value,
            anchor_entities=["COMPANY_MU"],
            anchor_artifact_id="EARNINGS_RELEASE_MU_Q3",
            time_range=PackageTimeRange(),
            member_event_ids=[f"event-{value}"],
            canonical_summary=value,
            status=PackageStatus.UNKNOWN,
        )
        for value in ("revenue", "eps")
    ]
    monkeypatch.setattr(
        engine, "_trusted_package_anchor_ids", lambda _item: {"EARNINGS_RELEASE_MU_Q3"}
    )
    monkeypatch.setattr(
        engine,
        "_trusted_package_field_ids",
        lambda item, namespaces: (
            {item.package_id.upper()}
            if namespaces.intersection(ATOMIC_OBJECT_FIELD_NAMESPACES)
            else set()
        ),
    )
    monkeypatch.setattr(engine, "_package_market_measures", lambda _item: set())
    monkeypatch.setattr(engine, "_package_trading_sessions", lambda _item: set())
    monkeypatch.setattr(engine, "_package_is_pure_market", lambda _item: False)
    monkeypatch.setattr(
        engine,
        "_package_member_events",
        lambda _item: [SimpleNamespace(event_family=EventFamily.FINANCIAL_PERFORMANCE)],
    )

    boundary = engine._package_pair_boundary(packages[0], packages[1], [])
    assert boundary.shared_artifact_ids == ["EARNINGS_RELEASE_MU_Q3"]
    assert boundary.object_scope_difference
    assert not boundary.hard_blocked


def test_wave_c_uses_unique_dictionary_cards_and_shared_boundary() -> None:
    root = Path(__file__).parents[2]
    source = (root / "src" / "cdecr" / "bulk_epoch" / "late_stage.py").read_text(encoding="utf-8")
    assert '"l": short_by_full[left.package_id]' in source
    assert '"r": short_by_full[right.package_id]' in source
    assert 'pair["boundary"] = compact_boundary' in source
    assert "boundary.compact_signals(include_object_scope=False)" in source
    assert 'f"p{index}l"' not in source


def test_wave_c_restores_shared_anchor_m0_and_parent_membership_prompt() -> None:
    root = Path(__file__).parents[2]
    source = (root / "src" / "cdecr" / "bulk_epoch" / "late_stage.py").read_text(encoding="utf-8")
    assert "if boundary.shared_artifact_ids or boundary.shared_anchor_ids:" in source
    assert "shared_clean_anchor" not in source
    assert "Judge shared parent membership, not Atomic equality." in source
    assert "shared topic, source, or entity alone is insufficient." in source


def test_n13_pair_local_apply_defaults_off_without_late_wall_admission_control() -> None:
    root = Path(__file__).parents[2]
    source = (root / "src" / "cdecr" / "bulk_epoch" / "engine.py").read_text(encoding="utf-8")
    settings = CDECRSettings(_env_file=None)
    assert not settings.n13_pair_local_apply
    assert "n13_pair_local_apply: bool = False" in source
    assert "self.core.n13_pair_local_apply = self.n13_pair_local_apply" in source
    assert 'self.n13_pair_local_apply and n13_budget["admitted"]' not in source


def test_missing_candidate_recovery_prompt_requires_exact_coverage_and_atomic_splitting() -> None:
    root = Path(__file__).parents[2]
    source = (root / "src" / "cdecr" / "single_document.py").read_text(encoding="utf-8")
    assert source.count("Return every supplied candidate exactly once.") == 2
    assert source.count("split ") >= 2
    assert source.count("independent ") >= 2
    assert source.count("reject an umbrella exhausted by recovered children") == 2


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
