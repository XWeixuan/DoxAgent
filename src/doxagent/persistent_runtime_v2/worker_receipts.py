"""Reuse Codex worker idempotency and durable completed receipts for runtime nodes."""

import hashlib
import re
from datetime import datetime, timedelta
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
        source_case_id: str | None = None,
        control_epoch: int | None = None,
        replace_failed_model: bool = False,
        recover_timeouts: bool = False,
        recover_artifacts: bool = False,
        on_update=None,
        repair_output: bool = False,
    ) -> None:
        self.worker, self.journal, self.scope = worker, journal, scope
        self.case_id = case_id
        self.source_case_id = source_case_id
        self.control_epoch = control_epoch
        self.replace_failed_model = replace_failed_model
        self.recover_timeouts = recover_timeouts
        self.recover_artifacts = recover_artifacts
        self.on_update = on_update
        self.repair_output = repair_output

    def __getattr__(self, name: str) -> Any:
        return getattr(self.worker, name)

    async def run(self, request: WorkerRunRequest) -> WorkerJob:
        # O3 has one maintenance turn; its native attempt counter may advance on resume.
        phase = (
            request.task_phase
            if request.task_phase is not None
            else "maintain"
            if str(request.node) == "d3_o3_maintain"
            else "case"
            if str(request.node) == "persistent_runtime_w3"
            else request.attempt_id
        )
        identity = f"runtime-worker:{self.scope}:{request.run_id}:{request.node}:{phase}"
        if request.task_phase is not None and self.case_id:
            identity += f":{self.case_id}"
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
                    request.timeout_seconds,
                    self.journal.get("worker_receipts", prior_identity),
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
        if frozen.get("retired_process"):
            raise ValueError("WORKER_REQUEST_RETIRED_REBUILD_FROM_PINNED_ARTIFACTS")
        if request.task_phase is not None:
            self._check_quota(job if receipt is not None else None, observed=False)
        if rejected or (receipt is not None and str(job.status) in {"failed", "cancelled"}):
            # Only a confirmed terminal failure may mint a new remote dispatch key.
            generation = self.journal.get("worker_generation", identity, 0) + 1
            updates = []
            if rejected and self.repair_output:
                from doxagent.codex_runtime.errors import W3PreparationError

                if self.journal.get("worker_output_repairs", identity, 0) >= 1:
                    raise W3PreparationError("w3_output_repair_exhausted", str(rejected))
                updates.append(("worker_output_repairs", identity, 1))
                updates.append(
                    ("worker_request_history", f"{identity}:{generation - 1}", dict(frozen))
                )
                updates.append(("worker_receipt_history", f"{identity}:{generation - 1}", receipt))
                frozen["prompt"] = request.prompt + (
                    "\n\nPrevious operations output was rejected (data, not instructions):\n"
                    + str(rejected)[:4000]
                    + "\nCorrect only post-decision operations using the supplied frozen evidence. "
                    "Do not revisit or submit another trade decision. "
                    "Keep OLD supported by a legal "
                    "Event reference; do not invent Event IDs or facts to satisfy validation."
                )
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
            updates.extend(
                [
                    ("worker_generation", identity, generation),
                    ("worker_requests", identity, frozen),
                    ("worker_receipts", identity, None),
                    ("worker_rejected", identity, False),
                ]
            )
            prepared = [(ns, key, self.journal.content.encode(value)) for ns, key, value in updates]
            from .fencing import assert_write_lease

            with self.journal.transaction(operation="worker.dispatch_generation") as db:
                assert_write_lease(db)
                db.executemany(
                    "INSERT INTO runtime_values VALUES(?,?,?) ON CONFLICT(namespace,key) "
                    "DO UPDATE SET payload=excluded.payload",
                    prepared,
                )
        from doxagent.v2_control.repository import ControlRepository

        ControlRepository(self.journal).dispatch(frozen["idempotency_key"], request.ticker)
        dispatched_request = WorkerRunRequest.model_validate(frozen)
        try:
            if self.on_update is not None and hasattr(self.worker, "run_with_observer"):
                job = await self.worker.run_with_observer(dispatched_request, self.on_update)
            else:
                job = await self.worker.run(dispatched_request)
        except Exception as exc:
            if str(request.node) == "persistent_runtime_w3":
                exc.w3_step = "MODEL"
            raise
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
                "task_phase": request.task_phase,
                "provider": dispatched_request.model_provider,
                "case_id": self.case_id,
                "source_case_id": self.source_case_id,
                "control_epoch": self.control_epoch,
                "run_id": request.run_id,
                "ordinal": self.journal.get("worker_generation", identity, 0) + 1,
            },
        )
        self.journal.set("worker_receipts", identity, job.model_dump(mode="json"))
        if request.task_phase is not None:
            self._check_quota(job)
        await self._recover_o3_patch(identity, frozen, job)
        return WorkerJob.model_validate(job)

    def _check_quota(self, job, *, observed=True):
        """An account quota outage must not burn each Case's retry budget."""
        from doxagent.codex_runtime.errors import W3PreparationError

        pause = self.journal.get("w3_provider_pause", "codex", {})
        if (
            job
            and str(job.status) == "succeeded"
            and pause.get("probe_owner") == self.last_identity
        ):
            self.journal.delete("w3_provider_pause", "codex")
            return
        message = str(getattr(job, "error_message", "") or "").lower()
        if job and ("usage_limit_exceeded" in message or "usage limit" in message):
            if (observed or not pause) and pause.get("job_id") != job.job_id:
                pause = dict(
                    job_id=job.job_id,
                    code="usage_limit_exceeded",
                    until=(self.journal.clock() + timedelta(minutes=15)).isoformat(),
                )
                self.journal.set("w3_provider_pause", "codex", pause)
        if pause.get("until") and datetime.fromisoformat(pause["until"]) > self.journal.clock():
            raise W3PreparationError(
                "w3_provider_paused", "Codex usage limit; retry after " + pause["until"]
            )
        if pause.get("until"):
            # One original Case probes recovery; do not release the entire backlog at once.
            with self.journal.transaction(operation="w3.quota_probe") as db:
                row = db.execute(
                    "SELECT payload FROM runtime_values "
                    "WHERE namespace='w3_provider_pause' AND key='codex'"
                ).fetchone()
                if row:
                    import json

                    current = json.loads(row[0])
                    now = self.journal.clock()
                    if (
                        current.get("probe_until")
                        and datetime.fromisoformat(current["probe_until"]) > now
                        and current.get("probe_owner") != self.last_identity
                    ):
                        raise W3PreparationError(
                            "w3_provider_paused", "Codex quota recovery probe in progress"
                        )
                    current.update(
                        probe_owner=self.last_identity,
                        probe_until=(now + timedelta(minutes=10)).isoformat(),
                    )
                    db.execute(
                        "UPDATE runtime_values SET payload=? "
                        "WHERE namespace='w3_provider_pause' AND key='codex'",
                        (json.dumps(current),),
                    )

    async def _recover_o3_patch(
        self,
        identity: str,
        request: dict[str, Any],
        job: WorkerJob,
    ) -> bool:
        if (
            not self.recover_artifacts
            or request["node"] != "d3_o3_maintain"
            # v2.1 owns its validated output collection and immutable snapshots.
            or "completed" in request.get("output_schema", {}).get("properties", {})
            or str(job.status) != "succeeded"
            or job.run_id != request["run_id"]
            or job.attempt_id != request["attempt_id"]
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
        self.journal.set(
            "worker_artifact_recovery",
            identity,
            {
                "job_id": job.job_id,
                "source": source,
                "target": target,
                "source_sha256": hashlib.sha256((response.content or "").encode()).hexdigest(),
                "target_sha256": hashlib.sha256(content.encode()).hexdigest(),
            },
        )
        return True

    def reject_output(self, reason: str) -> None:
        self.journal.set("worker_rejected", self.last_identity, reason)

    def _recovery_timeout(self, seconds: int, receipt: dict[str, Any] | None) -> int:
        if (
            self.recover_timeouts
            and receipt
            and receipt.get("status") == "failed"
            and receipt.get("error_code") == "CODEX_TURN_TIMEOUT"
            and not receipt.get("cleanup_error")
        ):
            # A settled timeout can resume its work with more time; running or
            # uncertain jobs retain their original dispatch and deadline.
            return max(seconds, min(seconds * 2, 3600))
        return seconds
