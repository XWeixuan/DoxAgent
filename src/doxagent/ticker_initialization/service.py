"""Dependency scheduling with durable per-node retry and recovery contracts."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, Protocol
from uuid import uuid4

from .repository import InitializationRepository
from .schema import Lease, LeaseLost, NodeRecord, NodeResult, RunRecord


class NodeAdapter(Protocol):
    async def reconcile(self, context: NodeContext) -> NodeResult | None:
        """Recover committed output or await an existing job; None means no usable result."""
        ...

    async def execute(self, context: NodeContext) -> NodeResult: ...


class NodeContext:
    def __init__(
        self,
        repository: InitializationRepository,
        lease: Lease,
        node: NodeRecord,
    ) -> None:
        self.repository = repository
        self.lease = lease
        self.node = node
        self.run = repository.get(lease.initialization_id)

    def checkpoint(self, **values: Any) -> None:
        self.repository.receipt(self.lease, self.node.key, values)

    def dependency(self, key: str) -> NodeResult:
        frozen = self.node.receipt.get("dependency_results", {})
        if key in frozen:
            return NodeResult.model_validate(frozen[key])
        replay = self.node.inputs.get("_replay_dependencies", {})
        if key in replay:
            return NodeResult.model_validate(replay[key])
        node = next(n for n in self.repository.nodes(self.run.initialization_id) if n.key == key)
        if node.result is None:
            raise ValueError(f"dependency {key} has no result")
        return node.result


class InitializationWorker:
    def __init__(
        self,
        repository: InitializationRepository,
        adapter: Callable[[NodeRecord], NodeAdapter],
        *,
        execution_version: dict[str, str] | None = None,
        lease_seconds: float = 60,
    ) -> None:
        self.repository = repository
        self.adapter = adapter
        from .provenance import execution_version as local_version

        self.execution_version = execution_version or local_version()
        self.lease_seconds = lease_seconds
        self.owner = uuid4().hex

    async def run_once(self) -> RunRecord | None:
        lease = self.repository.claim(self.owner, lease_seconds=self.lease_seconds)
        if lease is None:
            return None
        work = asyncio.create_task(self._drive(lease))
        heartbeat = asyncio.create_task(self._heartbeat(lease))
        try:
            done, _ = await asyncio.wait([work, heartbeat], return_when=asyncio.FIRST_COMPLETED)
            if heartbeat in done:
                await heartbeat
                raise LeaseLost("heartbeat stopped")
            return await work
        finally:
            for task in (work, heartbeat):
                if not task.done():
                    task.cancel()
            await asyncio.gather(work, heartbeat, return_exceptions=True)

    async def _heartbeat(self, lease: Lease) -> None:
        while True:
            await asyncio.sleep(self.lease_seconds / 3)
            self.repository.heartbeat(lease, lease_seconds=self.lease_seconds)

    async def _drive(self, lease: Lease) -> RunRecord:
        while True:
            all_nodes = self.repository.nodes(lease.initialization_id)
            nodes = [n for n in all_nodes if not n.inputs.get("managed_by")]
            complete = {n.key for n in nodes if n.status == "SUCCEEDED"}
            if len(complete) == len(nodes):
                return self.repository.finish(lease)
            exhausted = [n.key for n in nodes if n.status == "FAILED" and n.ordinal >= 2]
            recovering = [n for n in nodes if n.status == "RUNNING"]
            if exhausted and not recovering:
                for completed_node in nodes:
                    if (
                        completed_node.key == "activation.commit"
                        and completed_node.status == "SUCCEEDED"
                    ):
                        compensator = getattr(self.adapter(completed_node), "compensate", None)
                        if compensator is not None:
                            await compensator(NodeContext(self.repository, lease, completed_node))
                return self.repository.finish(
                    lease, error="retry budget exhausted: " + ", ".join(exhausted)
                )
            ready = [n for n in nodes if n.key not in complete and set(n.dependencies) <= complete]
            if exhausted:
                ready = recovering
            if not ready:
                return self.repository.finish(lease, error="no runnable nodes")
            # Persist each result independently; do not cancel a healthy sibling on failure.
            results = await asyncio.gather(
                *(self._execute(lease, n) for n in ready),
                return_exceptions=True,
            )
            for result in results:
                if isinstance(result, BaseException):
                    raise result

    async def _execute(self, lease: Lease, previous: NodeRecord) -> None:
        node = self.repository.begin(lease, previous.key, self.execution_version)
        context = NodeContext(self.repository, lease, node)
        try:
            adapter = self.adapter(node)
            from .substeps import execution_scope

            with execution_scope(context):
                recovered = await adapter.reconcile(context)
            if recovered is not None:
                self.repository.complete(lease, node.key, recovered)
                return
            if previous.status == "RUNNING":
                # Unknown completion after a crash is charged before a replacement dispatch.
                self.repository.fail(
                    lease, node.key, "interrupted execution has no recoverable output"
                )
                return
            visited_failures: set[tuple[str, str | None]] = set()
            while True:
                try:
                    with execution_scope(context):
                        result = await adapter.execute(context)
                    break
                except LeaseLost:
                    raise
                except Exception:
                    retryable = {
                        (child.key, child.execution_id)
                        for child in self.repository.nodes(lease.initialization_id)
                        if child.inputs.get("managed_by") == node.key
                        and child.status == "FAILED"
                        and child.ordinal < 2
                    }
                    if not retryable - visited_failures:
                        raise
                    # Continue the same container attempt. Only the failed child
                    # spends its remaining retry; no automatic generation reset.
                    visited_failures.update(retryable)
            self.repository.complete(lease, node.key, result)
        except LeaseLost:
            raise
        except Exception as exc:
            self.repository.fail(lease, node.key, f"{type(exc).__name__}: {exc}"[:4000])
