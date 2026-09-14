"""Hard-bounded resource settings; retention cannot weaken the public contract."""
import os
from dataclasses import dataclass


def integer(name, default, low, high):
    value = int(os.environ.get("DOXAGENT_V2_DB_" + name, default))
    if not low <= value <= high:
        raise ValueError("invalid DOXAGENT_V2_DB_" + name)
    return value


@dataclass(frozen=True)
class Limits:
    query_workers: int = 2
    query_queue: int = 16
    stream_workers: int = 1
    query_seconds: int = 2
    detail_seconds: int = 5
    mvcc_hours: int = 24
    diagnostic_days: int = 30

    @classmethod
    def load(cls):
        return cls(
            query_workers=integer("QUERY_WORKERS", 2, 1, 4),
            query_queue=integer("QUERY_QUEUE", 16, 1, 64),
            stream_workers=integer("STREAM_WORKERS", 1, 1, 2),
            query_seconds=integer("QUERY_SECONDS", 2, 1, 2),
            detail_seconds=integer("DETAIL_SECONDS", 5, 2, 5),
            mvcc_hours=integer("MVCC_HOURS", 24, 24, 168),
            diagnostic_days=integer("DIAGNOSTIC_DAYS", 30, 30, 365),
        )
