"""One process-local writer actor for short BULK_EPOCH commit operations."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future
from queue import Queue
from threading import Thread
from typing import TypeVar

_T = TypeVar("_T")


class BulkWriter:
    def __init__(self) -> None:
        self._queue: Queue[tuple[Callable[[], object] | None, Future[object] | None]] = Queue()
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
                try:
                    future.set_result(operation())
                except BaseException as exc:
                    future.set_exception(exc)
            finally:
                self._queue.task_done()

    def run(self, operation: Callable[[], _T]) -> _T:
        future: Future[object] = Future()
        self._queue.put((operation, future))
        return future.result()  # type: ignore[return-value]

    def close(self) -> None:
        self._queue.put((None, None))
        self._thread.join(timeout=15)

    def __enter__(self) -> BulkWriter:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
