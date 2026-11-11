"""One request interval on the existing round receipt; no independent writer."""

from contextvars import ContextVar
from datetime import UTC, datetime

current = ContextVar("runtime_model_interval", default=None)


def start():
    value = current.get()
    if value is not None:
        value["start"] = datetime.now(UTC).isoformat()


def stop():
    value = current.get()
    if value is not None and value.get("start") and not value.get("end"):
        value["end"] = datetime.now(UTC).isoformat()
