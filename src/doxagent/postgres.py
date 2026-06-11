"""Shared psycopg connection helpers for Supabase/PgBouncer compatibility."""

import time
from typing import Any


def connect_postgres(
    psycopg: Any,
    database_url: str,
    *,
    max_attempts: int = 3,
    retry_delay_seconds: float = 0.4,
    **kwargs: Any,
) -> Any:
    """Open a psycopg3 connection without server-side prepared statements.

    Supabase's transaction pooler runs through PgBouncer. psycopg3's default
    prepared statement threshold can collide with pooled server sessions and
    raise DuplicatePreparedStatement for generated names such as "_pg3_0".
    The pooler can also close new connections transiently, so OperationalError
    is retried a few times before surfacing to the workflow.
    """

    kwargs.setdefault("prepare_threshold", None)
    attempts = max(1, max_attempts)
    operational_error = getattr(psycopg, "OperationalError", None)
    if operational_error is None:
        errors = getattr(psycopg, "errors", None)
        operational_error = getattr(errors, "OperationalError", Exception)
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return psycopg.connect(database_url, **kwargs)
        except operational_error as exc:  # type: ignore[misc]
            last_error = exc
            if attempt >= attempts - 1:
                break
            time.sleep(retry_delay_seconds * (attempt + 1))
    if last_error is not None:
        raise last_error
    raise RuntimeError("Postgres connection failed without an error.")
