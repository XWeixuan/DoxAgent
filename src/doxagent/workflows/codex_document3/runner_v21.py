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

from .assets_v21 import ATLAS_NAMES, ATLAS_ROOT
from .materials_v21 import attach_material_indexes, reference_path
from .schema import strict_json_schema
from .schema_v21 import WORK_SCHEMAS, PolicySetV3, TechnicalReceipt
from .state_v21 import canonical, digest
from .timing_v21 import HostTiming
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
        self.shell_concurrency = legacy.shell_concurrency
        self.index_cache = {}

    def frozen_assets(self, mode):
        required = [
            "role",
            "common",
            *(
                ["discovery", "discovery_open", "planning", "build", "integration", *ATLAS_NAMES]
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
        async with lock, self.shell_concurrency.slot(ticker):
            timing = HostTiming()
            timing.step("state_load")
            record = self.state.task(run_id, key)
            run = self.state.run(run_id)
            research_owners = run["prepared"]["topology"].get("research_owners", {})
            profile = run["prepared"]["topology"].get("owner_profiles", {}).get(owner, {})
            is_open = profile.get("kind") == "open" or owner == "OPEN"
            permitted = (
                (owner == "GLOBAL" and phase in {"planning", "integration"})
                or (owner in research_owners and phase in {"discovery", "build"})
                or (owner == "MAINTAIN" and phase == "maintain")
            )
            if not permitted:
                record.update(
                    status="FAILED",
                    error=f"owner/phase routing error:{owner}:{phase}",
                    files={},
                    snapshots={},
                )
                self.state.save_task(run_id, key, record)
                return record
            if record["status"] in TERMINAL:
                for path, content in record.get("files", {}).items():
                    snapshot = record["snapshots"][path]
                    accepted = (await self.workspace.read_text(run_id, snapshot)).content
                    if accepted != content:
                        raise ValueError(f"accepted task snapshot integrity error:{key}:{path}")
                    draft = self.state.get_draft(run_id, path)
                    await self.workspace.write_text(
                        run_id, path, canonical(draft["policy"]) if draft else accepted
                    )
                return record
            owner_id = f"{run_id}-{owner.lower()}"
            timing.step("material_resolve")
            frozen = {}
            mapping = []
            original = run["prepared"]["files"]
            for ref, content in inputs.items():
                path = ref if ref in original else reference_path(ref, content)
                frozen[path] = content
                mapping.append({"ref": ref, "local_path": path})
            timing.step("history_restore")
            drafts = self.state.drafts(run_id)
            # Rebuild owner history from host authority when a physical session is lost.
            # Previous accepted outputs are protected unless this turn declares that path.
            for previous in self.state.tasks(run_id).values():
                if previous.get("workspace_run_id") != owner_id:
                    continue
                for path, content in previous.get("files", {}).items():
                    if any(path == p or (p.endswith("/") and path.startswith(p)) for p in outputs):
                        continue
                    accepted = drafts.get(path)
                    if accepted:
                        content = canonical(accepted["policy"])
                    frozen[path] = content
            for name, asset in run["assets"].items():
                if name in ATLAS_NAMES:
                    if owner == "OPEN_EVENT" and phase in {"discovery", "build"}:
                        path = f"{ATLAS_ROOT}/{ATLAS_NAMES[name]}"
                        frozen[path] = asset["content"]
                        mapping.append({"ref": path, "local_path": path})
                    continue
                if name in {"role", "common", phase} or (
                    name == "discovery_open" and phase == "discovery" and is_open
                ):
                    filename = {
                        "discovery": "initialize_discovery.md",
                        "discovery_open": "initialize_discovery_open.md",
                    }.get(name, f"{name}.md")
                    path = (
                        "AGENTS.md"
                        if name == "role"
                        else f"context/document3/v21/assets/{filename}"
                    )
                    frozen[path] = asset["content"]
            for name, model in WORK_SCHEMAS.items():
                frozen[f"context/document3/v21/schemas/{name}.json"] = canonical(
                    model.model_json_schema()
                )
            frozen["context/document3/v21/schemas/policy_set.json"] = canonical(
                PolicySetV3.model_json_schema()
            )
            frozen["context/document3/v21/schemas/receipt.json"] = canonical(
                strict_json_schema(TechnicalReceipt.model_json_schema())
            )
            timing.step("context_index")
            metadata = {entry["path"]: entry for entry in run["prepared"]["manifest"]}
            atlas_profile = None
            if owner == "OPEN_EVENT" and phase in {"discovery", "build"}:
                atlas_profile = {"sector_code": "L1-01", "materials": []}
                for name, filename in ATLAS_NAMES.items():
                    path = f"{ATLAS_ROOT}/{filename}"
                    metadata[path] = {
                        "source_kind": "static_reference",
                        "sha256": run["assets"][name]["sha256"],
                    }
                    atlas_profile["materials"].append({"ref": path, **metadata[path]})
            generation = record.setdefault(
                "context_generation",
                record.get("attempt_count", 0) + (record["status"] != "RUNNING"),
            )
            task_identity = digest(f"r{run.get('orchestration_revision', 2)}:{key}:{generation}")[
                :24
            ]
            navigation = attach_material_indexes(
                frozen,
                mapping,
                metadata,
                task_root=f"context/document3/v21/context_index/tasks/{task_identity}",
                cache=self.index_cache,
            )
            task_path = f"context/document3/v21/tasks/{task_identity}.json"
            record["task_path"] = task_path
            frozen[task_path] = canonical(
                {
                    **task,
                    **({"atlas_profile": atlas_profile} if atlas_profile else {}),
                    "ticker": ticker,
                    "as_of": as_of.isoformat(),
                    "node": phase,
                    "owner": owner,
                    "output_paths": outputs,
                    "schemas": {
                        name: f"context/document3/v21/schemas/{name}.json"
                        for name in [*WORK_SCHEMAS, "policy_set", "receipt"]
                    },
                    "read_mapping": mapping,
                    "context_reading": navigation,
                }
            )
            timing.step("input_inventory")
            history_prefixes = list(
                dict.fromkeys(
                    path.rsplit("/", 1)[0] + "/" for path in frozen if path.startswith("output/")
                )
            )
            inventory_prefixes = list(
                dict.fromkeys(["context/", "AGENTS.md", *history_prefixes, *outputs])
            )
            inventory = await self.workspace.inventory(owner_id, prefixes=inventory_prefixes)
            known = {f.relative_path: f.sha256 for f in inventory.files}
            metrics = {
                "input_refs": len(mapping),
                "input_files": len(frozen),
                "context_index_pages": sum("/context_index/" in p for p in frozen),
                "draft_ledger_queries": 1,
                "inventory_files_before": len(inventory.files),
                "hash_bytes_before": sum(f.size_bytes for f in inventory.files),
                "materialized_files": 0,
                "reused_files": 0,
            }
            timing.step("materialize")
            missing_index = []
            try:
                for path, content in frozen.items():
                    if known.get(path) != digest(content):
                        metrics["materialized_files"] += 1
                        try:
                            await self.workspace.write_text(owner_id, path, content)
                        except (ValueError, OSError):
                            if "/context_index/" not in path:
                                raise
                            record.setdefault("warnings", []).append("context_index_unavailable")
                            missing_index.append(path)
                    else:
                        metrics["reused_files"] += 1
                for path in missing_index:
                    frozen.pop(path, None)
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
            max_attempts = record.get("max_attempts", 2)
            while record.get("attempt_count", 0) < max_attempts or record["status"] == "RUNNING":
                # A crash retries the same durable request, not another task-budget unit.
                if record["status"] != "RUNNING":
                    record = self.state.claim(run_id, key, max_attempts=max_attempts)
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
                        f"Read task file {task_path}, role AGENTS.md, common and "
                        f"{'initialize_discovery' if phase == 'discovery' else phase} assets "
                        "under context/document3/v21/assets/. Write only the declared output "
                        "paths. Use read_mapping to read canonical refs. "
                        + (
                            "For OPEN Discovery, also read initialize_discovery_open.md. "
                            if phase == "discovery" and is_open
                            else ""
                        )
                        + f"Optional UTF-8 navigation: {navigation.get('overview_path', '')}. "
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
                    task_key=key,
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
                timing.step("worker_wait")
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
                timing.step("output_inventory")
                inventory = await self.workspace.inventory(
                    owner_id, prefixes=list(dict.fromkeys([*inventory_prefixes, *outputs]))
                )
                metrics["inventory_files_after"] = len(inventory.files)
                metrics["hash_bytes_after"] = sum(f.size_bytes for f in inventory.files)
                actual = {f.relative_path: f.sha256 for f in inventory.files}
                timing.step("protected_check")
                protected = all(
                    actual.get(path) == digest(content) for path, content in frozen.items()
                )
                timing.step("acceptance")
                files = {}
                if protected:
                    for item in inventory.files:
                        path = safe_path(item.relative_path)
                        if any(
                            path == p or (p.endswith("/") and path.startswith(p)) for p in outputs
                        ):
                            if error and known.get(path) == item.sha256:
                                continue
                            text = (await self.workspace.read_text(owner_id, path)).content
                            if text is not None:
                                files[path] = text
                else:
                    error = "read-only task input modified"
                if (
                    files
                    or (job and job.status == "succeeded")
                    or attempt >= max_attempts
                    or not protected
                ):
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
                        host_spans=timing.finish(),
                        host_metrics=metrics,
                        task_path=task_path,
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
