from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
from time import monotonic, sleep

from cdecr.models import ModelTier
from cdecr.scheduler import CDECRScheduler, ConcurrencyLane


def test_concurrency_lane_enforces_limit_and_records_queue_wait() -> None:
    lane = ConcurrencyLane("m2", 2)
    release = Event()
    lock = Lock()
    active = 0
    max_active = 0

    def operation() -> int:
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        release.wait(timeout=2)
        with lock:
            active -= 1
        return 1

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(lane.run, operation) for _ in range(4)]
        deadline = monotonic() + 1
        while lane.snapshot().active < 2 and monotonic() < deadline:
            sleep(0.005)
        assert lane.snapshot().active == 2
        release.set()
        assert [future.result()[0] for future in futures] == [1, 1, 1, 1]

    snapshot = lane.snapshot()
    assert max_active == 2
    assert snapshot.max_active == 2
    assert snapshot.completed == 4
    assert snapshot.total_queue_wait_ms >= 0


def test_scheduler_pressure_does_not_multiply_shrink_tier_lane() -> None:
    scheduler = CDECRScheduler(
        m1_limit=2,
        m2_limit=8,
        m3_limit=8,
        m4_limit=4,
        structured_start_interval_seconds=0,
        structured_provider_start_rate=1000,
        structured_provider_initial_burst=128,
    )

    class RateLimited(RuntimeError):
        status_code = 429

    try:
        scheduler.run(ModelTier.M3, lambda: (_ for _ in ()).throw(RateLimited()))
    except RateLimited:
        pass
    else:  # pragma: no cover
        raise AssertionError("rate limit should propagate")

    assert scheduler.snapshot()["m3"].limit == 8
    assert scheduler.snapshot()["m2"].limit == 8
    assert scheduler.provider_snapshot().limit < scheduler.provider_snapshot().hard_limit
    for _ in range(10):
        scheduler.run(ModelTier.M3, lambda: 1)
    assert scheduler.snapshot()["m3"].limit == 8


def test_scheduler_provider_hard_limit_is_shared_across_tiers() -> None:
    scheduler = CDECRScheduler(
        m2_limit=12,
        m3_limit=12,
        m4_limit=12,
        structured_provider_target=6,
        structured_provider_hard_limit=8,
        structured_provider_start_rate=1000,
        structured_provider_initial_burst=64,
    )
    release = Event()

    def operation() -> int:
        release.wait(timeout=2)
        return 1

    tiers = [ModelTier.M2, ModelTier.M3, ModelTier.M4] * 4
    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = [executor.submit(scheduler.run, tier, operation) for tier in tiers]
        deadline = monotonic() + 1
        while scheduler.provider_snapshot().active < 8 and monotonic() < deadline:
            sleep(0.005)
        assert scheduler.provider_snapshot().active == 8
        release.set()
        assert len([future.result() for future in futures]) == 12
    assert scheduler.provider_snapshot().max_active == 8


def test_scheduler_m1_bypasses_structured_provider_gate() -> None:
    scheduler = CDECRScheduler(
        m1_limit=4,
        structured_provider_target=1,
        structured_provider_hard_limit=1,
        structured_provider_start_rate=1000,
        structured_provider_initial_burst=8,
    )
    release = Event()
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [
            executor.submit(
                scheduler.run,
                ModelTier.M1,
                lambda: (release.wait(timeout=2), 1)[1],
            )
            for _ in range(4)
        ]
        deadline = monotonic() + 1
        while scheduler.snapshot()["m1"].active < 4 and monotonic() < deadline:
            sleep(0.005)
        assert scheduler.snapshot()["m1"].active == 4
        assert scheduler.provider_snapshot().active == 0
        release.set()
        assert len([future.result() for future in futures]) == 4
