"""Small, content-free boundary receipts. Timing never changes business outcomes."""

from __future__ import annotations

import inspect
import json
import logging
import sqlite3
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from threading import Lock
from uuid import uuid4

_current = ContextVar("w3_boundary_timing", default=None)
_parent = ContextVar("w3_boundary_timing_parent", default=None)
_log = logging.getLogger(__name__)


class BoundaryTrace:
    def __init__(self, **identity):
        self.identity = identity
        self.started_at = datetime.now(UTC).isoformat()
        self.started = time.perf_counter_ns()
        self.events = []
        self.totals = {}
        self.lock = Lock()
        self.sequence = 0
        self.active = {}
        self.progress = None

    @contextmanager
    def activate(self):
        token = _current.set(self)
        parent = _parent.set(None)
        try:
            yield self
        finally:
            _current.reset(token)
            _parent.reset(parent)

    @contextmanager
    def span(self, name, **counts):
        started = time.perf_counter_ns()
        with self.lock:
            self.sequence += 1
            identity = self.sequence
        parent = _parent.get()
        with self.lock:
            self.active[identity] = dict(
                span_id=identity,
                parent_span_id=parent,
                name=name,
                offset_ms=round((started - self.started) / 1e6, 3),
                **counts,
            )
        self._checkpoint(name)
        token = _parent.set(identity)
        error = None
        try:
            yield
        except BaseException as exc:
            error = type(exc).__name__
            raise
        finally:
            _parent.reset(token)
            end = time.perf_counter_ns()
            event = dict(
                span_id=identity,
                parent_span_id=parent,
                name=name,
                offset_ms=round((started - self.started) / 1e6, 3),
                elapsed_ms=round((end - started) / 1e6, 3),
                error_type=error,
                **counts,
            )
            with self.lock:
                self.active.pop(identity, None)
                total = self.totals.setdefault(name, dict(count=0, elapsed_ms=0, errors=0))
                total["count"] += 1
                total["elapsed_ms"] = round(total["elapsed_ms"] + event["elapsed_ms"], 3)
                total["errors"] += int(error is not None)
                for key in ("bytes", "files"):
                    if key in counts:
                        total[key] = total.get(key, 0) + counts[key]
                high_volume = name.startswith("journal.") or name == "workspace.write_http"
                if len(self.events) < 256 and (not high_volume or total["count"] <= 3):
                    self.events.append(event)
            self._checkpoint(name)

    def _checkpoint(self, name):
        if self.progress and name.startswith("lifecycle."):
            try:
                self.progress(self.snapshot())
            except Exception as exc:
                _log.warning("W3 timing progress unavailable: %s", type(exc).__name__)

    def snapshot(self):
        with self.lock:
            return dict(
                schema_version="w3-boundary-timing-v1",
                **self.identity,
                started_at=self.started_at,
                elapsed_ms=round((time.perf_counter_ns() - self.started) / 1e6, 3),
                events=list(self.events),
                totals={k: dict(v) for k, v in self.totals.items()},
                active_spans=list(self.active.values()),
                events_truncated=self.sequence > len(self.events),
            )


@contextmanager
def span(name, **counts):
    trace = _current.get()
    if trace is None:
        yield
    else:
        with trace.span(name, **counts):
            yield


def observed(name):
    def decorate(function):
        if inspect.iscoroutinefunction(function):

            @wraps(function)
            async def asynchronous(*args, **kwargs):
                with span(name):
                    return await function(*args, **kwargs)

            return asynchronous

        @wraps(function)
        def synchronous(*args, **kwargs):
            with span(name):
                return function(*args, **kwargs)

        return synchronous

    return decorate


def snapshot_current():
    trace = _current.get()
    return trace.snapshot() if trace else None


def save_trace(trace, directory):
    """One bounded receipt per attempt, outside the business transaction."""
    if directory is None:
        return
    try:
        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        (path / (uuid4().hex + ".json")).write_text(
            json.dumps(trace.snapshot(), separators=(",", ":")),
            encoding="utf-8",
        )
    except Exception as exc:
        _log.warning("W3 timing sink unavailable: %s", type(exc).__name__)


@contextmanager
def file_trace(directory, **identity):
    trace = BoundaryTrace(**identity)
    if directory is not None:
        try:
            path = Path(directory)
            path.mkdir(parents=True, exist_ok=True)
            progress = path / (uuid4().hex + ".progress.json")
            trace.progress = lambda value: progress.write_text(json.dumps(value), encoding="utf-8")
        except Exception as exc:
            _log.warning("W3 timing sink unavailable: %s", type(exc).__name__)
    with trace.activate():
        try:
            yield trace
        finally:
            save_trace(trace, directory)


async def measured(name, awaitable):
    with span(name):
        return await awaitable


@contextmanager
def effect_trace(effect, journal, **dispatch_timing):
    trace = BoundaryTrace(
        case_id=effect.case_id,
        effect_id=effect.effect_id,
        phase=str(effect.effect_type),
        lease_token=effect.lease_token,
        effect_created_at=effect.created_at.isoformat(),
        effect_available_at=effect.available_at.isoformat(),
        effect_attempt_count=effect.attempt_count,
        effect_age_at_start_ms=max(
            0, round((datetime.now(UTC) - effect.created_at).total_seconds() * 1000, 3)
        ),
        **dispatch_timing,
    )
    with trace.activate():
        try:
            yield trace
        finally:
            receipt = trace.snapshot()
            try:
                key = f"{effect.effect_id}:{effect.lease_token}"
                if hasattr(journal, "path"):
                    # Diagnostics must not inherit the runtime's 30s business lock wait.
                    db = sqlite3.connect(journal.path, timeout=0.01)
                    try:
                        with db:
                            db.execute(
                                "INSERT INTO runtime_values(namespace,key,payload) VALUES(?,?,?) "
                                "ON CONFLICT(namespace,key) DO UPDATE SET payload=excluded.payload",
                                ("w3_boundary_timing", key, json.dumps(receipt)),
                            )
                    finally:
                        db.close()
                else:
                    journal.set("w3_boundary_timing", key, receipt)
            except Exception as exc:
                _log.warning("W3 timing sink unavailable: %s", type(exc).__name__)
                if hasattr(journal, "path"):
                    save_trace(trace, Path(journal.path).parent / "boundary-timing")
            _log.info("w3_boundary_timing %s", json.dumps(receipt, separators=(",", ":")))
