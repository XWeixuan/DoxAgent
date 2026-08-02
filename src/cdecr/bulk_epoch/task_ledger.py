"""Small persistence facade for resumable stage tasks."""

from __future__ import annotations

from typing import Any

from cdecr.ports import CDECRRegistry


class BulkTaskLedger:
    def __init__(self, *, registry: CDECRRegistry, epoch_id: str) -> None:
        self.registry = registry
        self.epoch_id = epoch_id

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
    ) -> None:
        self.registry.upsert_bulk_epoch_task(
            epoch_id=self.epoch_id,
            stage=stage,
            task_id=task_id,
            component_id=component_id,
            input_hash=input_hash,
            snapshot_hash=snapshot_hash,
            status="FAILED",
            error_code=error_code,
        )

    def completed(self, stage: str) -> dict[str, dict[str, Any]]:
        return {
            str(item["task_id"]): item
            for item in self.registry.list_bulk_epoch_tasks(self.epoch_id, stage=stage)
            if item["status"] == "SUCCEEDED"
        }
