"""Short recoverable leases while bounded node calls are in flight."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from threading import Event, Thread


@contextmanager
def heartbeat(renew: Callable[[], None], *, interval: float = 20) -> Iterator[None]:
    stop = Event()

    def pulse() -> None:
        while not stop.wait(interval):
            try:
                renew()
            except Exception:
                # A lost lease must never be reacquired by its obsolete worker.
                return

    thread = Thread(target=pulse, daemon=True, name="runtime-lease-heartbeat")
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=1)
