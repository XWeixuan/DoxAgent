"""Small persistence facade for resumable stage tasks."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from cdecr.ports import CDECRRegistry


class BulkTaskLedger:
    def __init__(
        self, *, registry: CDECRRegistry, epoch_id: str, batch_enabled: bool = True
    ) -> None:
        self.registry = registry
        self.epoch_id = epoch_id
        self.batch_enabled = batch_enabled

    def start(
        self,
        *,
        stage: str,
        task_id: str,
        input_hash: str,
        snapshot_hash: str,
        component_id: str | None = None,
    ) -> None:
        self.registry.upsert_bulk_epoch_task(
            epoch_id=self.epoch_id,
            stage=stage,
            task_id=task_id,
            component_id=component_id,
            input_hash=input_hash,
            snapshot_hash=snapshot_hash,
            status="RUNNING",
        )

    def _write_many(self, records: Sequence[dict[str, Any]]) -> dict[str, int]:
        if not self.batch_enabled:
            for record in records:
                self.registry.upsert_bulk_epoch_task(epoch_id=self.epoch_id, **record)
            return {"rows": len(records), "transactions": len(records)}
        return self.registry.upsert_bulk_epoch_tasks(
            [{"epoch_id": self.epoch_id, **record} for record in records],
            chunk_size=512,
        )

    def start_many(self, tasks: Sequence[dict[str, Any]]) -> dict[str, int]:
        return self._write_many([{**task, "status": "RUNNING"} for task in tasks])

    def finish_many(self, tasks: Sequence[dict[str, Any]]) -> dict[str, int]:
        return self._write_many([{**task, "status": "SUCCEEDED"} for task in tasks])

    def fail_many(
        self,
        tasks: Sequence[dict[str, Any]],
        *,
        status: str = "FAILED_TERMINAL",
    ) -> dict[str, int]:
        if status not in {"FAILED_RETRYABLE", "FAILED_TERMINAL"}:
            raise ValueError("failure status must be FAILED_RETRYABLE or FAILED_TERMINAL")
        return self._write_many([{**task, "status": status} for task in tasks])

    def finish(
        self,
        *,
        stage: str,
        task_id: str,
        input_hash: str,
        snapshot_hash: str,
        decision_ref: dict[str, Any],
        component_id: str | None = None,
    ) -> None:
        self.registry.upsert_bulk_epoch_task(
            epoch_id=self.epoch_id,
            stage=stage,
            task_id=task_id,
            component_id=component_id,
            input_hash=input_hash,
            snapshot_hash=snapshot_hash,
            status="SUCCEEDED",
            decision_ref=decision_ref,
        )

    def fail(
        self,
        *,
        stage: str,
        task_id: str,
        input_hash: str,
        snapshot_hash: str,
        error_code: str,
        component_id: str | None = None,
        status: str = "FAILED_TERMINAL",
    ) -> None:
        self.registry.upsert_bulk_epoch_task(
            epoch_id=self.epoch_id,
            stage=stage,
            task_id=task_id,
            component_id=component_id,
            input_hash=input_hash,
            snapshot_hash=snapshot_hash,
            status=status,
            error_code=error_code,
        )

    def completed(self, stage: str) -> dict[str, dict[str, Any]]:
        return {
            str(item["task_id"]): item
            for item in self.registry.list_bulk_epoch_tasks(self.epoch_id, stage=stage)
            if item["status"] == "SUCCEEDED"
        }
