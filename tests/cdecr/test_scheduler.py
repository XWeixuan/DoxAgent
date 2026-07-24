from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
from time import monotonic, sleep

from cdecr.scheduler import ConcurrencyLane


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
