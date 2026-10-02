"""Reuse Codex worker idempotency and durable completed receipts for runtime nodes."""

import hashlib
import re
from typing import Any

from doxagent.codex_runtime.models import codex_execution_model
from doxagent.codex_worker.schema import WorkerJob, WorkerRunRequest

from .journal import RuntimeJournal, digest


class ReceiptWorker:
    def __init__(
        self,
        worker: Any,
        journal: RuntimeJournal,
        scope: str,
        *,
        case_id: str | None = None,
        control_epoch: int | None = None,
        replace_failed_model: bool = False,
        recover_timeouts: bool = False,
        recover_artifacts: bool = False,
    ) -> None:
        self.worker, self.journal, self.scope = worker, journal, scope
        self.case_id = case_id
        self.control_epoch = control_epoch
        self.replace_failed_model = replace_failed_model
        self.recover_timeouts = recover_timeouts
        self.recover_artifacts = recover_artifacts

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
            match = re.fullmatch(r"(.+)-retry-(\d+)", phase)
            if match:
                base, ordinal = match.group(1), int(match.group(2))
                previous = base if ordinal == 1 else f"{base}-retry-{ordinal - 1:03d}"
                prior_identity = (
                    f"runtime-worker:{self.scope}:{request.run_id}:{request.node}:{previous}"
                )
                timeout = self._recovery_timeout(
                    request.timeout_seconds, self.journal.get("worker_receipts", prior_identity),
                )
                request = request.model_copy(update={"timeout_seconds": timeout})
            frozen = request.model_copy(update={"idempotency_key": digest(identity)}).model_dump(
                mode="json"
            )
            self.journal.set("worker_requests", identity, frozen)
        receipt = self.journal.get("worker_receipts", identity)
        if receipt is not None:
            job = WorkerJob.model_validate(receipt)
            if str(job.status) in {"SUCCEEDED", "completed", "succeeded"}:
                recovered = await self._recover_o3_patch(identity, frozen, job)
                if not rejected or recovered:
                    if recovered:
                        self.journal.set("worker_rejected", identity, False)
                    return WorkerJob.model_validate(job)
        if rejected or (receipt is not None and str(job.status) in {"failed", "cancelled"}):
            # Only a confirmed terminal failure may mint a new remote dispatch key.
            generation = self.journal.get("worker_generation", identity, 0) + 1
            self.journal.set("worker_generation", identity, generation)
            if (
                self.replace_failed_model
                and receipt is not None
                and str(job.status) == "failed"
                and frozen.get("model") != request.model
            ):
                # Explicit recovery may change the dispatch model, not the frozen
                # Case context, prompt, output schema, or reasoning effort.
                frozen["model"] = request.model
                frozen["attempt_id"] = request.attempt_id
                frozen["thread_id"] = request.thread_id
            frozen["idempotency_key"] = digest([identity, generation])
            frozen["timeout_seconds"] = self._recovery_timeout(frozen["timeout_seconds"], receipt)
            self.journal.set("worker_requests", identity, frozen)
            self.journal.set("worker_receipts", identity, None)
            self.journal.set("worker_rejected", identity, False)
        from doxagent.v2_control.repository import ControlRepository

        ControlRepository(self.journal).dispatch(frozen["idempotency_key"], request.ticker)
        dispatched_request = WorkerRunRequest.model_validate(frozen)
        job = await self.worker.run(dispatched_request)
        self.journal.set(
            "worker_invocations",
            job.job_id,
            {
                "job": job.model_dump(mode="json"),
                "ticker": request.ticker,
                "node": str(request.node),
                "model": codex_execution_model(
                    dispatched_request.model, dispatched_request.model_provider
                ),
                "requested_model": dispatched_request.model,
                "provider": dispatched_request.model_provider,
                "case_id": self.case_id,
                "control_epoch": self.control_epoch,
                "run_id": request.run_id,
                "ordinal": self.journal.get("worker_generation", identity, 0) + 1,
            },
        )
        self.journal.set("worker_receipts", identity, job.model_dump(mode="json"))
        await self._recover_o3_patch(identity, frozen, job)
        return WorkerJob.model_validate(job)

    async def _recover_o3_patch(
        self, identity: str, request: dict[str, Any], job: WorkerJob,
    ) -> bool:
        if (
            not self.recover_artifacts or request["node"] != "d3_o3_maintain"
            or str(job.status) != "succeeded"
            or job.run_id != request["run_id"] or job.attempt_id != request["attempt_id"]
        ):
            return False
        target = "output/work/policy_patch.json"
        try:
            await self.worker.read_text(request["run_id"], target)
            return False
        except FileNotFoundError:
            pass
        # Recover only this settled dispatch's schema-valid patch. The O3
        # orchestrator still validates versions, semantics and write boundaries.
        source = f"attempts/{request['attempt_id']}/{target}"
        try:
            response = await self.worker.read_text(request["run_id"], source)
        except FileNotFoundError:
            return False
        from pydantic import ValidationError

        from doxagent.workflows.codex_document3.schema import PolicyPatchSet

        try:
            patch = PolicyPatchSet.model_validate_json(response.content or "")
        except ValidationError:
            return False
        content = patch.model_dump_json(indent=2)
        await self.worker.write_text(request["run_id"], target, content)
        self.journal.set("worker_artifact_recovery", identity, {
            "job_id": job.job_id, "source": source, "target": target,
            "source_sha256": hashlib.sha256((response.content or "").encode()).hexdigest(),
            "target_sha256": hashlib.sha256(content.encode()).hexdigest(),
        })
        return True

    def reject_output(self, reason: str) -> None:
        self.journal.set("worker_rejected", self.last_identity, reason)

    def _recovery_timeout(self, seconds: int, receipt: dict[str, Any] | None) -> int:
        if (
            self.recover_timeouts and receipt and receipt.get("status") == "failed"
            and receipt.get("error_code") == "CODEX_TURN_TIMEOUT"
            and not receipt.get("cleanup_error")
        ):
            # A settled timeout can resume its work with more time; running or
            # uncertain jobs retain their original dispatch and deadline.
            return max(seconds, min(seconds * 2, 3600))
        return seconds
