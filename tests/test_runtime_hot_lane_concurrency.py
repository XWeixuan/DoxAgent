"""Exercise admitted-case limits and actual model-entry concurrency without LLM I/O."""

import logging
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from threading import Condition, Event, Thread
from threading import enumerate as threads
from time import perf_counter

import pytest

from doxagent.persistent_runtime_v2.coordinator import RuntimeCoordinator
from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from doxagent.persistent_runtime_v2.repository import SQLitePersistentRuntimeV2Repository
from doxagent.persistent_runtime_v2.service import PersistentRuntimeV2Service
from doxagent.persistent_runtime_v2.transport import RuntimeResponsesError
from tests.test_persistent_runtime_v2 import (
    _FakeKnownEvents,
    _FakePolicies,
    _FakeResponses,
    _source,
)


def source(ticker, number):
    value = _source()
    return value.model_copy(
        update={
            "source_message_id": f"{ticker}-{number}",
            "snapshot": value.snapshot.model_copy(update={"ticker": ticker}),
        }
    )


def runtime(tmp_path, responses):
    return PersistentRuntimeV2Service(
        repository=SQLitePersistentRuntimeV2Repository(tmp_path / "runtime.sqlite"),
        responses=responses,
        known_events=_FakeKnownEvents(),
        policies=_FakePolicies(),
        dispatch_effects=False,
        retry_delays_seconds=(0, 0),
    )


class BlockedResponses(_FakeResponses):
    def __init__(self, tickers):
        super().__init__()
        self.gates = {ticker: Event() for ticker in tickers}
        self.entered = Counter()
        self.condition = Condition()

    def complete(self, request):
        if request.metadata["round"] == "R1":
            ticker = request.payload["source_message"]["ticker"]
            if ticker in self.gates:
                with self.condition:
                    self.entered[ticker, request.metadata["lane"]] += 1
                    self.condition.notify_all()
                if not self.gates[ticker].wait(15):
                    raise AssertionError("test gate was not released")
        return super().complete(request)

    def wait_for(self, ticker, count=6):
        with self.condition:
            return self.condition.wait_for(
                lambda: self.entered[ticker, "W1"] == count and self.entered[ticker, "W2"] == count,
                timeout=10,
            )

    def release(self):
        for gate in self.gates.values():
            gate.set()


def test_two_tickers_enter_six_w1_and_six_w2_each(tmp_path, monkeypatch):
    created = Counter()
    creation_lock = Condition()

    def executor(*args, **kwargs):
        prefix = kwargs.get("thread_name_prefix", "")
        if prefix.startswith("prv2-hot-"):
            with creation_lock:
                created[prefix] += 1
        return ThreadPoolExecutor(*args, **kwargs)

    monkeypatch.setattr("doxagent.persistent_runtime_v2.service.ThreadPoolExecutor", executor)
    responses = BlockedResponses(["MU", "INTC"])
    service = runtime(tmp_path, responses)
    callers = ThreadPoolExecutor(max_workers=12)
    try:
        futures = [
            callers.submit(service.execute_message, source(ticker, number))
            for ticker in ["MU", "INTC"]
            for number in range(6)
        ]
        assert responses.wait_for("MU"), responses.entered
        assert responses.wait_for("INTC"), responses.entered
        assert created == {"prv2-hot-MU": 1, "prv2-hot-INTC": 1}
        assert all(pool._max_workers == 12 for pool in service._hot_executors.values())
        responses.release()
        assert all(f.result(timeout=10).technical_status == "OK" for f in futures)
    finally:
        responses.release()
        callers.shutdown(wait=True)
        service.close()


def test_saturated_ticker_does_not_block_other_ticker(tmp_path):
    responses = BlockedResponses(["MU"])
    service = runtime(tmp_path, responses)
    callers = ThreadPoolExecutor(max_workers=7)
    try:
        slow = [callers.submit(service.execute_message, source("MU", n)) for n in range(6)]
        assert responses.wait_for("MU"), responses.entered
        fast = callers.submit(service.execute_message, source("INTC", 0))
        assert fast.result(timeout=10).technical_status == "OK"
        assert all(not future.done() for future in slow)
    finally:
        responses.release()
        callers.shutdown(wait=True)
        service.close()


def test_journal_admits_six_per_ticker_and_releases_capacity(tmp_path):
    journal = RuntimeJournal(tmp_path / "runtime.sqlite")
    for ticker in ["MU", "INTC"]:
        for number in range(7):
            journal.put_task(f"{ticker}-{number}", ticker, "CASE", {})
    for ticker in ["MU", "INTC"]:
        leases = [journal.claim(f"{ticker}-{number}") for number in range(6)]
        assert all(leases)
        assert journal.claim(f"{ticker}-6") is None
        journal.finish(leases[0])
        assert journal.claim(f"{ticker}-6")
    for number in range(2):
        journal.put_task(f"sweep-{number}", "MU", "SWEEP", {})
    assert journal.claim("sweep-0")
    assert journal.claim("sweep-1") is None


def test_failure_and_retry_preserve_error_and_other_ticker_progress(tmp_path, caplog):
    class FailingResponses(_FakeResponses):
        attempts = 0

        def complete(self, request):
            if (
                request.metadata["lane"] == "W1"
                and request.metadata["round"] == "R1"
                and request.payload["source_message"]["ticker"] == "MU"
            ):
                self.attempts += 1
                raise RuntimeResponsesError("provider_timeout", "test outage", retryable=True)
            return super().complete(request)

    responses = FailingResponses()
    service = runtime(tmp_path, responses)
    try:
        with caplog.at_level(logging.INFO, logger="doxagent.persistent_runtime_v2.service"):
            failed = service.execute_message(source("MU", 0))
            healthy = service.execute_message(source("INTC", 0))
        assert failed.status == "FAILED" and failed.error_code == "provider_timeout"
        assert responses.attempts == 2
        assert healthy.technical_status == "OK"
        records = [r for r in caplog.records if r.msg.startswith("runtime_hot_lane")]
        assert any(r.ticker == "MU" and r.lane == "W1" and r.status == "FAILED" for r in records)
        assert any(r.ticker == "INTC" and r.status == "SUCCEEDED" for r in records)
        assert all(r.queue_wait_ms >= 0 and r.lane_elapsed_ms >= 0 for r in records)
        assert all("Customer completed" not in r.getMessage() for r in records)
    finally:
        service.close()


def test_close_waits_for_coordinator_case_to_submit_before_closing_lane_pools(
    tmp_path, monkeypatch
):
    service = runtime(tmp_path, _FakeResponses())
    coordinator = RuntimeCoordinator(service, RuntimeJournal(tmp_path / "journal.sqlite"))
    service.coordinator = coordinator
    entered, release, closing = Event(), Event(), Event()
    original_close = coordinator.close

    def coordinator_close():
        closing.set()
        original_close()

    monkeypatch.setattr(coordinator, "close", coordinator_close)

    def in_flight_case():
        entered.set()
        assert release.wait(10)
        # Claimed before shutdown, but execute_message is entered during draining.
        return service.execute_message(source("MU", 0))

    # Freeze a valid Case before exercising the shutdown/late-submission race.
    case = service.execute_message(source("MU", 0))
    from doxagent.persistent_runtime_v2.schema import RuntimeCaseStatus

    case = case.model_copy(update={"status": RuntimeCaseStatus.RUNNING})
    service.repository.save_case(case)
    future = coordinator._realtime.submit(in_flight_case)
    assert entered.wait(5)
    closer = Thread(target=service.close)
    try:
        closer.start()
        assert closing.wait(5)
        assert service._draining and not service._closed and not future.done()
        with service._hot_executors_lock:
            assert not service._hot_executors_closed
        release.set()
        future.result(timeout=10)
        closer.join(10)
        assert not closer.is_alive()
        service.close()
        with pytest.raises(RuntimeError, match="closed"):
            service._submit_hot_lanes(case, None, None)
        assert not any(t.name.startswith("prv2-hot-") for t in threads())
    finally:
        release.set()
        closer.join(10)
        service.close()


def test_lane_queue_time_and_original_exception_survive_broken_diagnostics(tmp_path, monkeypatch):
    service = runtime(tmp_path, _FakeResponses())
    try:
        case = service.execute_message(source("MU", 0))

        def broken_log(*args, **kwargs):
            raise RuntimeError("broken diagnostics")

        monkeypatch.setattr("doxagent.persistent_runtime_v2.service._LOGGER.info", broken_log)
        assert service._run_hot_lane(case, "W1", perf_counter(), lambda: 42) == 42
        failure = ValueError("original error")

        def fail():
            raise failure

        with pytest.raises(ValueError) as caught:
            service._run_hot_lane(case, "W1", perf_counter(), fail)
        assert caught.value is failure
    finally:
        service.close()


def test_scheduler_entrypoint_enables_lane_diagnostics(tmp_path, monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace

    from doxagent.runtime_scheduler import v2

    logger = logging.getLogger("doxagent.persistent_runtime_v2.service")
    previous_level = logger.level
    calls = []
    fake_runtime = SimpleNamespace(close=lambda: calls.append("closed"))
    scheduler = SimpleNamespace(
        run_due_once=lambda **kwargs: calls.append("tick"),
        runtime_v2_service=fake_runtime,
    )
    monkeypatch.setattr("sys.argv", ["scheduler", "--once"])
    monkeypatch.setattr(v2, "build", lambda settings: scheduler)
    monkeypatch.setattr(
        v2,
        "DoxAgentSettings",
        lambda: SimpleNamespace(
            runtime_scheduler_sqlite_path=tmp_path / "scheduler.sqlite",
        ),
    )
    monkeypatch.setattr(v2, "WriterLock", lambda path: nullcontext())
    # Do not alter the test process's signal handlers.
    monkeypatch.setattr(v2.signal, "signal", lambda *args: None)
    try:
        v2.main()
        assert logger.getEffectiveLevel() == logging.INFO
        assert calls == ["tick", "closed"]
    finally:
        logger.setLevel(previous_level)
