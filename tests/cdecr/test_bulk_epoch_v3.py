from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from time import perf_counter

from cdecr.bulk_epoch.executor import AsyncModelExecutor
from cdecr.bulk_epoch.writer import BulkWriter
from cdecr.models import ModelTier
from cdecr.ports import StructuredModelRequest, StructuredModelResult
from cdecr.registry import SQLiteCDECRRegistry
from cdecr.scheduler import CDECRScheduler, StructuredProviderGate
from tests.cdecr.test_cross_document import (
    FakeStructured,
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
            "parent_induction": 24,
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


def test_async_bulk_gate_is_isolated_from_document_scheduler() -> None:
    gate = StructuredProviderGate(target=4, hard_limit=4, start_rate=1000, burst=64)
    scheduler = CDECRScheduler(
        m1_limit=8,
        structured_provider_target=4,
        structured_provider_hard_limit=4,
        structured_provider_start_rate=1000,
        structured_provider_initial_burst=64,
        provider_gate=gate,
        max_retries=0,
    )
    async_client = AsyncFakeStructured(FakeStructured(), delay=0.05)
    hub = AsyncModelExecutor(
        clients={
            ModelTier.M2: async_client,
            ModelTier.M3: async_client,
            ModelTier.M4: async_client,
        },
        tier_limits={ModelTier.M2: 8, ModelTier.M3: 8, ModelTier.M4: 8},
        stage_limits={"atomic_coreference": 8},
        repair_limit=4,
        rates={tier: (1000.0, 8) for tier in (ModelTier.M2, ModelTier.M3, ModelTier.M4)},
        provider_target=4,
        provider_hard_limit=4,
        provider_start_rate=1000,
        provider_initial_burst=64,
        max_retries=0,
    )
    request = StructuredModelRequest(
        system_prompt="Return JSON.",
        user_prompt="{}",
        json_schema={"type": "object"},
        metadata={"stage": "atomic_coreference"},
    )
    release = threading.Event()

    def sync_call() -> int:
        return scheduler.run(ModelTier.M2, lambda: (release.wait(timeout=2), 1)[1])[0]

    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(sync_call) for _ in range(4)]
            futures.extend(pool.submit(hub.complete, ModelTier.M2, request) for _ in range(4))
            deadline = perf_counter() + 1
            while gate.snapshot().active < 4 and perf_counter() < deadline:
                threading.Event().wait(0.005)
            assert gate.snapshot().active == 4
            deadline = perf_counter() + 1
            while hub.provider_snapshot().max_active < 4 and perf_counter() < deadline:
                threading.Event().wait(0.005)
            assert hub.provider_snapshot().max_active == 4
            assert hub.provider_stage_snapshots()["atomic_coreference"]["limit"] == 4
            release.set()
            assert len([future.result() for future in futures]) == 8
    finally:
        hub.close()


def test_async_executor_records_physical_attempt_telemetry() -> None:
    async_client = AsyncFakeStructured(FakeStructured(), delay=0.0)
    hub = AsyncModelExecutor(
        clients={tier: async_client for tier in (ModelTier.M2, ModelTier.M3, ModelTier.M4)},
        tier_limits={tier: 2 for tier in (ModelTier.M2, ModelTier.M3, ModelTier.M4)},
        stage_limits={"field_coreference": 2},
        repair_limit=1,
        rates={tier: (1000.0, 2) for tier in (ModelTier.M2, ModelTier.M3, ModelTier.M4)},
        provider_target=2,
        provider_hard_limit=2,
        provider_start_rate=1000,
        provider_initial_burst=8,
        max_retries=0,
    )
    try:
        hub.complete(
            ModelTier.M2,
            StructuredModelRequest(
                system_prompt="Return JSON.",
                user_prompt="{}",
                json_schema={"type": "object"},
                metadata={"stage": "field_coreference"},
            ),
        )
        attempts = hub.attempt_telemetry()
        assert len(attempts) == 1
        assert attempts[0].attempt_index == 1
        assert attempts[0].stage == "field_coreference"
        assert attempts[0].status == "SUCCEEDED"
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
