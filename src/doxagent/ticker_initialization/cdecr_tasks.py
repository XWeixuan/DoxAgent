"""Mirror native Bulk Epoch task boundaries without replacing its recovery engine."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import Any

from .schema import BudgetExhausted, NodeSpec
from .service import NodeContext


class NativeTaskObserver:
    def __init__(self, context: NodeContext) -> None:
        self.context = context
        self.epochs: set[str] = set()

    def __call__(self, epoch: str, status: str, rows: Sequence[dict[str, Any]]) -> None:
        if not rows:
            return
        self.epochs.add(epoch)
        parent = self.context
        repo, lease = parent.repository, parent.lease
        specs = [
            NodeSpec(
                key=parent.node.key
                + ".native:"
                + str(row["stage"])
                + ":"
                + hashlib.sha256(f"{epoch}:{row['task_id']}".encode()).hexdigest()[:24],
                block="CDECR",
                inputs={
                    "managed_by": parent.node.key,
                    "kind": "cdecr_native",
                    "epoch_id": epoch,
                    "stage": row["stage"],
                    "task_id": row["task_id"],
                },
            )
            for row in rows
        ]
        repo.expand(lease, specs)
        nodes = {node.key: node for node in repo.nodes(parent.run.initialization_id)}
        if status == "RUNNING":
            # Check the whole batch before reserving any siblings. Native deferred
            # retry and parent re-traversal share this same two-attempt budget.
            for spec in specs:
                node = nodes[spec.key]
                if node.ordinal >= 2 and node.status != "SUCCEEDED":
                    raise BudgetExhausted(spec.key)
            for spec in specs:
                node = nodes[spec.key]
                if node.status == "RUNNING":
                    repo.fail(lease, node.key, "interrupted native task")
                repo.begin(lease, spec.key, parent.node.execution_version)
        elif status == "SUCCEEDED":
            for spec in specs:
                repo.reconcile_native_success(lease, spec.key)
        else:
            for spec in specs:
                if nodes[spec.key].status == "RUNNING":
                    repo.fail(lease, spec.key, "native task " + status)

    def reconcile(self, registry: Any) -> None:
        # Native writes can commit before the controller receipt. This also handles
        # deterministic tasks and degraded-but-usable native terminal decisions.
        epochs = self.epochs | {
            str(node.inputs["epoch_id"])
            for node in self.context.repository.nodes(self.context.run.initialization_id)
            if node.inputs.get("kind") == "cdecr_native"
        }
        for epoch in epochs:
            rows = registry.list_bulk_epoch_tasks(epoch)
            for status in {str(row["status"]) for row in rows} - {"RUNNING"}:
                self(epoch, status, [row for row in rows if row["status"] == status])
