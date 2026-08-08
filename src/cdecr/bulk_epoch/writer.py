"""One process-local writer actor for short BULK_EPOCH commit operations."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass
from queue import Queue
from threading import Lock, Thread
from time import perf_counter
from typing import TypeVar

_T = TypeVar("_T")


@dataclass(frozen=True)
class BulkWriterSnapshot:
    queued: int
    max_queued: int
    active_writers: int
    completed: int
    max_commit_ms: int
    high_watermark_hits: int


class BulkWriter:
    def __init__(
        self,
        *,
        low_watermark: int = 1000,
        high_watermark: int = 5000,
        hard_limit: int = 10000,
    ) -> None:
        if not 1 <= low_watermark <= high_watermark <= hard_limit:
            raise ValueError("writer queue watermarks must satisfy 1 <= low <= high <= hard")
        self.low_watermark = low_watermark
        self.high_watermark = high_watermark
        self.hard_limit = hard_limit
        self._queue: Queue[tuple[Callable[[], object] | None, Future[object] | None]] = Queue(
            maxsize=hard_limit
        )
        self._metrics_lock = Lock()
        self._max_queued = 0
        self._active_writers = 0
        self._completed = 0
        self._max_commit_ms = 0
        self._high_watermark_hits = 0
        self._pending_lock = Lock()
        self._pending: set[Future[object]] = set()
        self._thread = Thread(target=self._run, name="cdecr-bulk-writer", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while True:
            operation, future = self._queue.get()
            try:
                if operation is None:
                    return
                assert future is not None
                if not future.set_running_or_notify_cancel():
                    continue
                started = perf_counter()
                with self._metrics_lock:
                    self._active_writers += 1
                try:
                    future.set_result(operation())
                except BaseException as exc:
                    future.set_exception(exc)
                finally:
                    elapsed_ms = round((perf_counter() - started) * 1000)
                    with self._metrics_lock:
                        self._active_writers -= 1
                        self._completed += 1
                        self._max_commit_ms = max(self._max_commit_ms, elapsed_ms)
            finally:
                self._queue.task_done()

    def submit(self, operation: Callable[[], _T]) -> Future[_T]:
        future: Future[object] = Future()
        with self._pending_lock:
            self._pending.add(future)
        future.add_done_callback(self._discard_pending)
        self._queue.put((operation, future))
        queued = self._queue.qsize()
        with self._metrics_lock:
            self._max_queued = max(self._max_queued, queued)
            if queued >= self.high_watermark:
                self._high_watermark_hits += 1
        return future  # type: ignore[return-value]

    def _discard_pending(self, future: Future[object]) -> None:
        with self._pending_lock:
            self._pending.discard(future)

    def run(self, operation: Callable[[], _T]) -> _T:
        return self.submit(operation).result()

    def barrier(self) -> None:
        """Wait for all writes submitted before the barrier."""

        with self._pending_lock:
            pending = tuple(self._pending)
        for future in pending:
            future.result()

    def snapshot(self) -> BulkWriterSnapshot:
        with self._metrics_lock:
            return BulkWriterSnapshot(
                queued=self._queue.qsize(),
                max_queued=self._max_queued,
                active_writers=self._active_writers,
                completed=self._completed,
                max_commit_ms=self._max_commit_ms,
                high_watermark_hits=self._high_watermark_hits,
            )

    def close(self) -> None:
        self.barrier()
        self._queue.put((None, None))
        self._thread.join(timeout=15)

    def __enter__(self) -> BulkWriter:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
