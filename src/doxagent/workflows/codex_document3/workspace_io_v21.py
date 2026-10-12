"""Bounded, durable workspace recovery, separate from the research attempt budget."""

from __future__ import annotations

import asyncio
import inspect
import time
from contextvars import ContextVar

import httpx

from doxagent.codex_runtime.errors import InfrastructureRecoveryExhausted
from doxagent.codex_worker.schema import WorkspaceInventory

from .state_v21 import digest


def transient(error):
    """Classify actual transport/status causes, never wrapper text or retryable flags."""
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, httpx.HTTPStatusError):
            return error.response.status_code in {502, 503, 504}
        if isinstance(error, httpx.TransportError):
            return True
        error = error.__cause__
    return False


class D3WorkspaceIO:
    def __init__(self, workspace, state, *, clock=time.time, sleep=asyncio.sleep):
        self.http_view = hasattr(workspace, "workspace_view")
        self.raw = workspace.workspace_view(transport_attempts=1) if self.http_view else workspace
        self.state, self.clock, self.sleep = state, clock, sleep
        self.scope = ContextVar("d3_workspace_scope")
        self.locks = {}
        self.active = {}

    def bind(self, run_id):
        self.scope.set(run_id)

    @property
    def snapshot(self):
        # Preserve the durable wrapper's existing opt-out for in-process test facades.
        return self._snapshot if hasattr(self.raw, "snapshot") else None

    async def _snapshot(self, run_id, snapshot_id):
        return await self._recover(
            "snapshot",
            run_id,
            snapshot_id,
            lambda remaining: self._invoke(
                self.raw.snapshot, run_id, snapshot_id, remaining=remaining
            ),
        )

    async def fork_snapshot(self, run_id, snapshot_id, destination_run_id):
        return await self._recover(
            "fork",
            run_id,
            [snapshot_id, destination_run_id],
            lambda remaining: self._invoke(
                self.raw.fork_snapshot, run_id, snapshot_id, destination_run_id, remaining=remaining
            ),
        )

    async def _invoke(self, method, *args, remaining, **kwargs):
        if self.http_view:
            kwargs["timeout_limit"] = remaining
        value = method(*args, **kwargs)
        return await value if inspect.isawaitable(value) else value

    async def _recover(self, operation, run_id, identity, call):
        scope = self.scope.get(run_id)
        key = digest([run_id, operation, identity])
        if scope not in self.active:
            self.active[scope] = (
                (self.state.run(scope) or {}).get("workspace_io_recovery", {}).get("active", {})
            )
        active = self.active[scope].get(key)
        started = self.clock()
        while True:
            remaining = active["deadline"] - self.clock() if active else 900
            if remaining <= 0:
                raise InfrastructureRecoveryExhausted(
                    f"D3 workspace {operation} recovery exhausted; "
                    "resume the same run after service recovery"
                )
            timer = asyncio.timeout(remaining)
            try:
                # Total operation (including write confirmation) must fit the window.
                async with timer:
                    result = await call(remaining)
            except Exception as exc:
                if isinstance(exc, TimeoutError) and timer.expired():
                    if not active:
                        active = {
                            "first_failure": started,
                            "deadline": started + 900,
                            "attempts": 1,
                            "operation": operation,
                            "workspace": run_id,
                            "error_type": type(exc).__name__,
                        }
                        self.state.io_recovery(scope, key, active)
                        self.active[scope][key] = active
                    raise InfrastructureRecoveryExhausted(
                        f"D3 workspace {operation} recovery exhausted"
                    ) from exc
                if not transient(exc):
                    raise
                now = self.clock()
                active = active or {
                    "first_failure": started,
                    "deadline": started + 900,
                    "attempts": 0,
                }
                active.update(
                    attempts=active["attempts"] + 1,
                    operation=operation,
                    workspace=run_id,
                    error_type=type(exc.__cause__ or exc).__name__,
                    elapsed=now - active["first_failure"],
                )
                cause = exc.__cause__ or exc
                if isinstance(cause, httpx.HTTPStatusError):
                    active["http_status"] = cause.response.status_code
                self.state.io_recovery(scope, key, active)
                self.active[scope][key] = active
                delay = (2, 5, 10, 20)[active["attempts"] - 1] if active["attempts"] <= 4 else 30
                await self.sleep(min(delay, max(0, active["deadline"] - self.clock())))
            else:
                if active:
                    self.state.io_recovery(
                        scope,
                        key,
                        {"elapsed": self.clock() - active["first_failure"]},
                        success=True,
                    )
                    self.active[scope].pop(key, None)
                return result

    async def read_text(self, run_id, path):
        return await self._recover(
            "read",
            run_id,
            path,
            lambda remaining: self._invoke(self.raw.read_text, run_id, path, remaining=remaining),
        )

    async def write_text(self, run_id, path, content):
        lock = self.locks.setdefault((run_id, path), asyncio.Lock())
        async with lock:
            uncertain = False

            async def write(remaining):
                nonlocal uncertain
                if uncertain:
                    try:
                        current = await self._invoke(
                            self.raw.read_text, run_id, path, remaining=remaining
                        )
                    except FileNotFoundError:
                        pass
                    else:
                        if current.content == content:
                            return current
                        # Store still enforces immutable conflicts; mutable writes retain semantics.
                uncertain = True
                return await self._invoke(
                    self.raw.write_text, run_id, path, content, remaining=remaining
                )

            return await self._recover("write", run_id, [path, digest(content)], write)

    async def inventory_query(self, run_id, *, paths=(), prefixes=()):
        paths, prefixes = sorted(set(paths)), sorted(set(prefixes))
        chunks = [(paths[i : i + 512], []) for i in range(0, len(paths), 512)]
        chunks += [([], prefixes[i : i + 32]) for i in range(0, len(prefixes), 32)]
        files = {}
        for selected, directories in chunks:

            async def query(remaining, selected=selected, directories=directories):
                if hasattr(self.raw, "inventory_query"):
                    return await self._invoke(
                        self.raw.inventory_query,
                        run_id,
                        paths=selected,
                        prefixes=directories,
                        remaining=remaining,
                    )
                # Compatibility for in-process workspace facades, never full-run fallback.
                return await self._invoke(
                    self.raw.inventory,
                    run_id,
                    prefixes=[*selected, *directories],
                    remaining=remaining,
                )

            result = await self._recover("inventory_query", run_id, [selected, directories], query)
            files.update((item.relative_path, item) for item in result.files)
        return WorkspaceInventory(run_id=run_id, files=[files[p] for p in sorted(files)])

    async def publish(self, run_id, paths, *, release_only=True):
        async def publish(remaining):
            kwargs = (
                {"release_only": release_only}
                if "release_only" in inspect.signature(self.raw.publish).parameters
                or any(
                    p.kind == inspect.Parameter.VAR_KEYWORD
                    for p in inspect.signature(self.raw.publish).parameters.values()
                )
                or self.http_view
                else {}
            )
            return await self._invoke(
                self.raw.publish, run_id, paths, remaining=remaining, **kwargs
            )

        return await self._recover("publish", run_id, paths, publish)
