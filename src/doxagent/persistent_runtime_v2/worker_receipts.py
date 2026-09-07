"""Reuse Codex worker idempotency and durable completed receipts for runtime nodes."""

from typing import Any

from doxagent.codex_worker.schema import WorkerJob, WorkerRunRequest

from .journal import RuntimeJournal, digest


class ReceiptWorker:
    def __init__(self, worker: Any, journal: RuntimeJournal, scope: str) -> None:
        self.worker, self.journal, self.scope = worker, journal, scope

    def __getattr__(self, name: str) -> Any:
        return getattr(self.worker, name)

    async def run(self, request: WorkerRunRequest) -> WorkerJob:
        # O3 has one maintenance turn; its native attempt counter may advance on resume.
        phase = (
            "maintain"
            if str(request.node) == "d3_o3_maintain"
            else "case"
            if str(request.node) == "persistent_runtime_w3"
            else request.attempt_id
        )
        identity = f"runtime-worker:{self.scope}:{request.run_id}:{request.node}:{phase}"
        self.last_identity = identity
        rejected = self.journal.get("worker_rejected", identity, False)
        frozen = self.journal.get("worker_requests", identity)
        if frozen is None:
            frozen = request.model_copy(update={"idempotency_key": digest(identity)}).model_dump(
                mode="json"
            )
            self.journal.set("worker_requests", identity, frozen)
        receipt = self.journal.get("worker_receipts", identity)
        if receipt is not None:
            job = WorkerJob.model_validate(receipt)
            if not rejected and str(job.status) in {"SUCCEEDED", "completed", "succeeded"}:
                return WorkerJob.model_validate(job)
        if rejected or (receipt is not None and str(job.status) in {"failed", "cancelled"}):
            # Only a confirmed terminal failure may mint a new remote dispatch key.
            generation = self.journal.get("worker_generation", identity, 0) + 1
            self.journal.set("worker_generation", identity, generation)
            frozen["idempotency_key"] = digest([identity, generation])
            self.journal.set("worker_requests", identity, frozen)
            self.journal.set("worker_receipts", identity, None)
            self.journal.set("worker_rejected", identity, False)
        job = await self.worker.run(WorkerRunRequest.model_validate(frozen))
        self.journal.set("worker_receipts", identity, job.model_dump(mode="json"))
        return WorkerJob.model_validate(job)

    def reject_output(self, reason: str) -> None:
        self.journal.set("worker_rejected", self.last_identity, reason)
