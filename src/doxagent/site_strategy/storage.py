"""Small, read-only capacity guard and SQLite transaction diagnostics."""

from __future__ import annotations

import os
import shutil
import sqlite3
import time
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
_READS=ThreadPoolExecutor(max_workers=4,thread_name_prefix="site-read")


async def registry_io(function, *args, **kwargs):
    """Cancelable bounded reads; actual owner mutations drain their commit."""
    name=getattr(function,"__name__","")
    readonly=(name.startswith(("get_","list_","resolve")) and not name.startswith("get_or_create")) or name in {
        "snapshot","candidate_status","effective_candidates","_resolve_request","_shared_key","_cache_key",
        "_profile","_auth_state","_identity_for","_purpose_combinations","_materialize_combination"}
    if readonly:
        owner=getattr(function,"__self__",None)
        repository=getattr(owner,"repository",owner)
        import contextvars
        context=contextvars.copy_context()
        def read():
            if hasattr(repository,"read_scope"):
                with repository.read_scope():return function(*args,**kwargs)
            return function(*args,**kwargs)
        return await asyncio.get_running_loop().run_in_executor(_READS,context.run,read)
    work=asyncio.create_task(asyncio.to_thread(function,*args,**kwargs))
    try:
        return await asyncio.shield(work)
    except asyncio.CancelledError:
        while not work.done():
            try:await asyncio.shield(work)
            except asyncio.CancelledError:continue
            except Exception:break
        await asyncio.gather(work,return_exceptions=True)
        raise


def sqlite_primary_code(exc: BaseException) -> int | None:
    code = getattr(exc, "sqlite_errorcode", None)
    return code & 0xFF if isinstance(code, int) else None


def storage_failure(exc: BaseException) -> bool:
    return sqlite_primary_code(exc) in {
        sqlite3.SQLITE_FULL,
        sqlite3.SQLITE_IOERR,
        sqlite3.SQLITE_CORRUPT,
        sqlite3.SQLITE_BUSY,
        sqlite3.SQLITE_LOCKED,
    }


class StorageMonitor:
    def __init__(self, path: Path) -> None:
        self.path = path.parent
        self.warning_bytes = int(os.getenv("SITE_STORAGE_WARNING_BYTES", str(20 * 1024**3)))
        self.degraded_bytes = int(os.getenv("SITE_STORAGE_DEGRADED_BYTES", str(5 * 1024**3)))
        self.critical_bytes = int(os.getenv("SITE_STORAGE_CRITICAL_BYTES", str(1024**3)))
        self.last_commit_at: str | None = None
        self.last_error: dict[str, object] | None = None
        self._error_until = 0.0
        self._error_available: int | None = None
        self._sampled = float("-inf")
        self._available = 0

    def committed(self) -> None:
        self.last_commit_at = datetime.now(UTC).isoformat()
        self._error_until = 0.0

    def failed(self, exc: sqlite3.Error) -> None:
        code = sqlite_primary_code(exc)
        self.last_error = {
            "code": code,
            "name": getattr(exc, "sqlite_errorname", "SQLITE_ERROR"),
            "at": datetime.now(UTC).isoformat(),
        }
        if code in {sqlite3.SQLITE_FULL, sqlite3.SQLITE_IOERR, sqlite3.SQLITE_CORRUPT}:
            self._sampled = float("-inf")
            self._error_available = self.diagnostics()["available_bytes"]
            self._error_until = time.monotonic() + 60

    def diagnostics(self) -> dict[str, object]:
        now = time.monotonic()
        if now - self._sampled >= 2:
            self._available = shutil.disk_usage(self.path).free
            self._sampled = now
        # Capacity recovered after a full filesystem: no restart or write probe needed.
        if (
            self.last_error
            and self.last_error["code"] == sqlite3.SQLITE_FULL
            and self._error_available is not None
            and self._available > self._error_available + self.critical_bytes
        ):
            self._error_until = 0.0
        unavailable = self._available < self.critical_bytes or now < self._error_until
        state = (
            "critical"
            if unavailable
            else "degraded"
            if self._available < self.degraded_bytes
            else "warning"
            if self._available < self.warning_bytes
            else "ready"
        )
        return {
            "state": state,
            "unavailable": unavailable,
            "available_bytes": self._available,
            "last_commit_at": self.last_commit_at,
            "last_error": self.last_error,
        }
