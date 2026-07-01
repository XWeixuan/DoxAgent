"""Shared psycopg connection helpers for Supabase/PgBouncer compatibility."""

import json
import os
import sys
import time
import traceback
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar
from urllib.parse import urlparse

T = TypeVar("T")

_POOLER_GUARD_OPTION_PARTS = (
    "-c statement_timeout=120000",
    "-c lock_timeout=30000",
    "-c idle_in_transaction_session_timeout=120000",
)
_POOLER_GUARD_OPTIONS = " ".join(_POOLER_GUARD_OPTION_PARTS)
_LOCAL_FAILURE_DIR = Path(".tmp/supabase_write_failures")
_LOCAL_PAYLOAD_DIR = Path(".tmp/supabase_payload_logs")
_STOP_RETRY_MARKERS = (
    "read-only transaction",
    "read only transaction",
    "cannot execute",
    "no space left on device",
    "pgrst000",
    "econnrefused",
    "echeckouttimeout",
)


def postgres_operational_error(psycopg: Any) -> type[BaseException]:
    operational_error = getattr(psycopg, "OperationalError", None)
    if operational_error is None:
        errors = getattr(psycopg, "errors", None)
        operational_error = getattr(errors, "OperationalError", Exception)
    return operational_error


def postgres_database_error(psycopg: Any) -> type[BaseException]:
    database_error = getattr(psycopg, "Error", None)
    if database_error is None:
        database_error = postgres_operational_error(psycopg)
    return database_error


def connect_postgres(
    psycopg: Any,
    database_url: str,
    *,
    max_attempts: int = 5,
    retry_delay_seconds: float = 0.8,
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
    kwargs.setdefault("connect_timeout", 15)
    kwargs["options"] = _postgres_options_with_pooler_guards(kwargs.get("options"))
    attempts = max(1, max_attempts)
    operational_error = postgres_operational_error(psycopg)
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return psycopg.connect(database_url, **kwargs)
        except operational_error as exc:  # type: ignore[misc]
            last_error = exc
            if should_stop_high_frequency_retry(exc):
                break
            if attempt >= attempts - 1:
                break
            time.sleep(retry_delay_seconds * (attempt + 1))
    if last_error is not None:
        raise last_error
    raise RuntimeError("Postgres connection failed without an error.")


def postgres_endpoint_kind(database_url: str) -> str:
    """Return a non-secret label for the configured Postgres endpoint."""

    parsed = urlparse(database_url)
    host = parsed.hostname or ""
    port = parsed.port
    if "pooler.supabase.com" in host and port == 6543:
        return "transaction_pooler_6543"
    if "pooler.supabase.com" in host and port == 5432:
        return "session_pooler_5432"
    if "supabase.co" in host and port == 5432:
        return "direct_5432"
    if port is not None:
        return f"postgres_{port}"
    return "unknown"


def postgres_sqlstate(exc: BaseException) -> str | None:
    for attr in ("sqlstate", "pgcode"):
        value = getattr(exc, attr, None)
        if value:
            return str(value)
    return None


def should_stop_high_frequency_retry(exc: BaseException) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in _STOP_RETRY_MARKERS)


def estimate_json_payload_bytes(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))


def record_postgres_failure(
    exc: BaseException,
    *,
    database_url: str,
    operation: str,
    table: str | None = None,
    payload_bytes: int | None = None,
    read_only_status: dict[str, Any] | None = None,
) -> None:
    """Persist a local diagnostic record without leaking database credentials."""

    try:
        _LOCAL_FAILURE_DIR.mkdir(parents=True, exist_ok=True)
        path = _LOCAL_FAILURE_DIR / f"{datetime.now(UTC).date().isoformat()}.jsonl"
        record = {
            "recorded_at": datetime.now(UTC).isoformat(),
            "operation": operation,
            "table": table,
            "payload_bytes": payload_bytes,
            "endpoint_kind": postgres_endpoint_kind(database_url),
            "sqlstate": postgres_sqlstate(exc),
            "error_type": exc.__class__.__name__,
            "error_message": str(exc)[:1000],
            "read_only_status": read_only_status or {},
            "stack_trace": "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            )[-4000:],
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        print(
            "[postgres-failure] "
            + json.dumps(record, ensure_ascii=False, default=str),
            file=sys.stderr,
        )
    except OSError:
        if os.getenv("DOXAGENT_RAISE_POSTGRES_DIAGNOSTIC_ERRORS") == "1":
            raise


def record_postgres_payload(
    *,
    operation: str,
    table: str,
    run_id: str | None,
    payload_bytes: int,
    item_count: int | None = None,
) -> None:
    """Record high-risk Supabase payload sizes in a local JSONL log."""

    try:
        _LOCAL_PAYLOAD_DIR.mkdir(parents=True, exist_ok=True)
        path = _LOCAL_PAYLOAD_DIR / f"{datetime.now(UTC).date().isoformat()}.jsonl"
        record = {
            "recorded_at": datetime.now(UTC).isoformat(),
            "operation": operation,
            "table": table,
            "run_id": run_id,
            "payload_bytes": payload_bytes,
            "item_count": item_count,
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except OSError:
        if os.getenv("DOXAGENT_RAISE_POSTGRES_DIAGNOSTIC_ERRORS") == "1":
            raise


def _postgres_options_with_pooler_guards(options: Any) -> str:
    if options is None:
        return _POOLER_GUARD_OPTIONS
    option_text = str(options).strip()
    if not option_text:
        return _POOLER_GUARD_OPTIONS
    missing_guards = [
        guard_option
        for guard_option in _POOLER_GUARD_OPTION_PARTS
        if guard_option.split("=", maxsplit=1)[0].removeprefix("-c ") not in option_text
    ]
    if not missing_guards:
        return option_text
    return f"{option_text} {' '.join(missing_guards)}"


def retry_postgres_operation(
    psycopg: Any,
    operation: Callable[[], T],
    *,
    max_attempts: int = 3,
    retry_delay_seconds: float = 0.8,
) -> T:
    """Retry a whole Postgres operation after mid-query pooler disconnects."""

    attempts = max(1, max_attempts)
    operational_error = postgres_operational_error(psycopg)
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return operation()
        except operational_error as exc:  # type: ignore[misc]
            last_error = exc
            if should_stop_high_frequency_retry(exc):
                break
            if attempt >= attempts - 1:
                break
            time.sleep(retry_delay_seconds * (attempt + 1))
    if last_error is not None:
        raise last_error
    raise RuntimeError("Postgres operation failed without an error.")
