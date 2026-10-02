"""Per-query cooperative deadline, private to the query process context."""
from contextvars import ContextVar
import time

deadline = ContextVar("v2_query_deadline", default=None)

class QueryDeadlineExceeded(TimeoutError):
    pass

def interrupted():
    until = deadline.get()
    return until is not None and time.monotonic() >= until

def check_deadline():
    if interrupted():
        raise QueryDeadlineExceeded()

frozen_proofs = ContextVar("v2_frozen_proofs", default=None)

request_views = ContextVar("v2_request_views", default=None)
