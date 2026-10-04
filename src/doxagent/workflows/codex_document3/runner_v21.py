"""Owner-isolated V21 transport. Business instructions come only from assets."""

from __future__ import annotations

import asyncio
from pathlib import Path

from doxagent.codex_runtime.errors import ImmutableWorkspacePath
from doxagent.codex_runtime.schema import (
    CODEX_DOCUMENT3_WORKFLOW_VERSION,
    AttemptStatus,
    CodexD3AgentRole,
    CodexD3Node,
    NodeAttempt,
    ResearchLane,
    ThreadRecord,
    utc_now,
)
from doxagent.codex_worker.schema import WorkerRunRequest

from .schema import strict_json_schema
from .schema_v21 import WORK_SCHEMAS, TechnicalReceipt
from .state_v21 import canonical, digest
from .validation_v21 import safe_path

NODES = {
    "discovery": CodexD3Node.O3_DISCOVERY,
    "planning": CodexD3Node.O3_PLANNING,
    "build": CodexD3Node.O3_BUILD,
    "integration": CodexD3Node.O3_INTEGRATION,
    "maintain": CodexD3Node.O3_MAINTAIN,
}
TERMINAL = {"COMPLETED", "PARTIAL", "FAILED", "CANCELLED"}


class AssetDependencyError(ValueError):
    pass


class RunnerV21:
    def __init__(self, legacy, state, node_assets):
        self.legacy, self.state = legacy, state
        self.workspace = legacy.workspace
        self.assets = dict(node_assets or {})
        self.locks = {}
        self.semaphore = asyncio.Semaphore(3)

    def frozen_assets(self, mode):
        required = [
            "role",
            "common",
            *(
                ["discovery", "planning", "build", "integration"]
                if mode == "initialize"
                else ["maintain"]
            ),
        ]
        result = {}
        for key in required:
            value = self.assets.get(key)
            if not value or not Path(value).is_file():
                raise AssetDependencyError(
                    f"V21 node_assets dependency missing: {key}; "
                    "supply an existing external asset file"
                )
            text = Path(value).read_bytes().decode("utf8")
            result[key] = {"source": str(value), "sha256": digest(text), "content": text}
        return result

    async def turn(self, *, run_id, ticker, owner, phase, key, as_of, inputs, outputs, task):
        lock = self.locks.setdefault((run_id, owner), asyncio.Lock())
        async with lock, self.semaphore:
            record = self.state.task(run_id, key)
            if record["status"] in TERMINAL:
                for path, content in record.get("files", {}).items():
                    snapshot = record["snapshots"][path]
                    accepted = (await self.workspace.read_text(run_id, snapshot)).content
                    if accepted != content:
                        raise ValueError(f"accepted task snapshot integrity error:{key}:{path}")
                    await self.workspace.write_text(run_id, path, accepted)
                return record
            owner_id = f"{run_id}-{owner.lower()}"
            run = self.state.run(run_id)
            frozen = {}
            mapping = []
            original = run["prepared"]["files"]
            for ref, content in inputs.items():
                path = (
                    ref
                    if ref in original
                    else f"context/document3/v21/borrowed/{digest(key)[:24]}/{digest(ref)[:24]}.txt"
                )
                frozen[path] = content
                mapping.append({"ref": ref, "local_path": path})
            # Rebuild owner history from host authority when a physical session is lost.
            # Previous accepted outputs are protected unless this turn declares that path.
            for previous in self.state.tasks(run_id).values():
                if previous.get("workspace_run_id") != owner_id:
                    continue
                for path, content in previous.get("files", {}).items():
                    if any(path == p or (p.endswith("/") and path.startswith(p)) for p in outputs):
                        continue
                    accepted = self.state.drafts(run_id).get(path)
                    if accepted:
                        content = canonical(accepted["policy"])
                    frozen[path] = content
            for name, asset in run["assets"].items():
                if name in {"role", "common", phase}:
                    path = (
                        "AGENTS.md" if name == "role" else f"context/document3/v21/assets/{name}.md"
                    )
                    frozen[path] = asset["content"]
            for name, model in WORK_SCHEMAS.items():
                frozen[f"context/document3/v21/schemas/{name}.json"] = canonical(
                    model.model_json_schema()
                )
            task_path = f"context/document3/v21/tasks/{digest(key)[:24]}.json"
            frozen[task_path] = canonical(
                {
                    **task,
                    "node": phase,
                    "owner": owner,
                    "output_paths": outputs,
                    "read_mapping": mapping,
                }
            )
            known = {
                f.relative_path: f.sha256 for f in (await self.workspace.inventory(owner_id)).files
            }
            try:
                for path, content in frozen.items():
                    if known.get(path) != digest(content):
                        await self.workspace.write_text(owner_id, path, content)
            except (ValueError, OSError, ImmutableWorkspacePath) as exc:
                record.update(
                    status="FAILED",
                    error=f"owner input integrity:{str(exc)[:800]}",
                    workspace_run_id=owner_id,
                    persistence_run_id=run_id,
                    files={},
                    snapshots={},
                )
                self.state.save_task(run_id, key, record)
                return record
            while record.get("attempt_count", 0) < 2 or record["status"] == "RUNNING":
                # A crash retries the same durable request, not another task-budget unit.
                if record["status"] != "RUNNING":
                    record = self.state.claim(run_id, key)
                    if record is None:
                        return self.state.task(run_id, key)
                attempt = record["attempt_count"]
                runtime = self.legacy._runtime_repository
                thread = (
                    runtime.get_thread(owner_id, CodexD3AgentRole.O3.value) if runtime else None
                )
                request = WorkerRunRequest(
                    workflow_version=CODEX_DOCUMENT3_WORKFLOW_VERSION,
                    research_lane=ResearchLane.DOCUMENT3,
                    run_id=owner_id,
                    ticker=ticker,
                    node=NODES[phase],
                    agent_role=CodexD3AgentRole.O3,
                    attempt_id=f"v21-{digest(run_id + ':' + key)[:16]}-{attempt}",
                    idempotency_key=f"{run_id}:{key}:{attempt}",
                    cutoff_at=as_of,
                    prompt=(
                        f"Read task file {task_path}, role AGENTS.md, common and {phase} assets "
                        "under context/document3/v21/assets/. Write only the declared output "
                        "paths. Use read_mapping to read canonical refs. "
                        "Return the technical receipt."
                    ),
                    output_schema=strict_json_schema(TechnicalReceipt.model_json_schema()),
                    thread_id=None
                    if record.get("fresh_session")
                    else (thread.thread_id if thread else None),
                    model=self.legacy._model
                    if phase == "maintain"
                    else self.legacy._initialize_model,
                    model_provider=self.legacy._model_provider,
                    effort=self.legacy._effort
                    if phase == "maintain"
                    else self.legacy._initialize_effort,
                    timeout_seconds=self.legacy._timeout_seconds,
                    allow_subagents=False,
                    max_subagents=0,
                    data_mcp_enabled=phase != "planning",
                )
                if record.get("request"):
                    request = WorkerRunRequest.model_validate(record["request"])
                record.update(
                    workspace_run_id=owner_id,
                    persistence_run_id=run_id,
                    phase=phase,
                    round=task.get("round", "main"),
                    owner_slot=owner,
                    ordinal=task.get("ordinal", 0),
                    request=request.model_dump(mode="json"),
                )
                self.state.save_task(run_id, key, record)
                audit = NodeAttempt(
                    workflow_version=CODEX_DOCUMENT3_WORKFLOW_VERSION,
                    research_lane=ResearchLane.DOCUMENT3,
                    run_id=run_id,
                    ticker=ticker,
                    cutoff_at=as_of,
                    node=NODES[phase],
                    attempt_id=request.attempt_id,
                    attempt_number=attempt,
                    thread_id=request.thread_id,
                    status=AttemptStatus.RUNNING,
                    started_at=utc_now(),
                )
                if runtime:
                    runtime.save_attempt(audit)
                error = ""
                job = None
                try:
                    # V21 owns the budget; avoid outer DurableWorker multiplication.
                    worker = getattr(self.legacy._worker, "worker", self.legacy._worker)
                    if record.get("job") and record["job"]["status"] not in {"queued", "running"}:
                        from doxagent.codex_worker.schema import WorkerJob

                        job = WorkerJob.model_validate(record["job"])
                    else:
                        job = await worker.run(request)
                    record["job"] = job.model_dump(mode="json")
                    if job.thread_id and runtime:
                        runtime.save_thread(
                            ThreadRecord(
                                workflow_version=CODEX_DOCUMENT3_WORKFLOW_VERSION,
                                research_lane=ResearchLane.DOCUMENT3,
                                ticker=ticker,
                                run_id=owner_id,
                                agent_role=CodexD3AgentRole.O3,
                                thread_id=job.thread_id,
                                model=request.model or "",
                                model_provider=request.model_provider,
                            )
                        )
                    error = (
                        (job.error_message or job.error_code or job.status)
                        if job.status != "succeeded"
                        else ""
                    )
                    self.state.save_task(run_id, key, record)
                except asyncio.CancelledError:
                    self.state.save_task(run_id, key, record)
                    raise
                except Exception as exc:
                    error = str(exc)[:1000]
                if runtime:
                    runtime.save_attempt(
                        audit.model_copy(
                            update={
                                "status": AttemptStatus.FAILED
                                if error
                                else AttemptStatus.SUCCEEDED,
                                "completed_at": utc_now(),
                                "error_message": error or None,
                                "thread_id": job.thread_id if job else request.thread_id,
                            }
                        )
                    )
                inventory = await self.workspace.inventory(owner_id)
                actual = {f.relative_path: f.sha256 for f in inventory.files}
                protected = all(
                    actual.get(path) == digest(content) for path, content in frozen.items()
                )
                files = {}
                if protected:
                    for item in inventory.files:
                        path = safe_path(item.relative_path)
                        if any(
                            path == p or (p.endswith("/") and path.startswith(p)) for p in outputs
                        ):
                            text = (await self.workspace.read_text(owner_id, path)).content
                            if text is not None:
                                files[path] = text
                else:
                    error = "read-only task input modified"
                if files or (job and job.status == "succeeded") or attempt >= 2 or not protected:
                    snapshots = {}
                    for path, content in files.items():
                        snapshot = (
                            f"artifacts/document3/checkpoints/{digest(key)[:24]}/"
                            f"{digest(path)[:24]}.txt"
                        )
                        await self.workspace.write_text(run_id, snapshot, content)
                        await self.workspace.write_text(run_id, path, content)
                        snapshots[path] = snapshot
                    record.update(
                        files=files,
                        snapshots=snapshots,
                        status="PARTIAL" if error and files else "FAILED" if error else "COMPLETED",
                        error=error,
                    )
                    self.state.save_task(run_id, key, record)
                    return record
                record.update(status="PENDING", error=error, fresh_session=True)
                record.pop("request", None)
                record.pop("job", None)
                self.state.save_task(run_id, key, record)
            record.update(status="FAILED", error="technical budget exhausted")
            self.state.save_task(run_id, key, record)
            return record
