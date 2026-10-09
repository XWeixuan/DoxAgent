"""Assistant CLI: isolated D3 local SDK start/continue/report, no app task launch."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import secrets
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from dotenv import load_dotenv

from doxagent.codex_worker.sdk_runtime import OpenAICodexRuntime
from doxagent.codex_worker.workspace_store import LocalWorkspaceStore
from doxagent.pilot.document3_sdk import Document3PilotWorker, build_document3_delivery
from doxagent.pilot.sdk_runner import execution_lock, read_json, write_json
from doxagent.settings import DoxAgentSettings
from doxagent.workflows.codex_document3.orchestrator_v21 import PilotPhasePaused
from doxagent.workflows.codex_document3.service import build_document3_orchestrator
from doxagent.workflows.codex_document3.state_v21 import canonical, digest
from doxagent.workflows.codex_document3.validation_v21 import safe_path


class PilotPublishedCapture:
    """Capture legacy publication bytes locally; never publish to external storage."""

    def __init__(self, root):
        self.root = root

    async def put(self, path, content, content_type):
        target = self.root / safe_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


def seed_database(source: Path, destination: Path):
    source = source.resolve(strict=True)
    if source == destination.resolve():
        raise ValueError("Pilot database must be isolated from source")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as upstream:
        with sqlite3.connect(destination) as local:
            upstream.backup(local)


def seed_pilot_document2(database: Path, completion_path: Path, run_id: str, global_run_id: str):
    """Expose one accepted D2 Pilot Shell as an explicitly isolated test fixture."""
    from doxagent.codex_runtime.repository import SQLiteCodexRuntimeRepository
    from doxagent.codex_runtime.schema import ArtifactRef, PublishedDocument
    from doxagent.workflows.codex_document2.schema import (
        Document2Bundle,
        Document2DocumentV21,
        Document2HandoffV1,
        ExpectationShellV21,
    )

    repository = SQLiteCodexRuntimeRepository(str(database))
    global_bundle = repository.get_bundle(global_run_id)
    if global_bundle is None or global_bundle.status != "published":
        raise ValueError("Pilot Global source is not a published snapshot")
    raw = completion_path.read_bytes()
    completion = json.loads(raw)
    shell = ExpectationShellV21.model_validate(completion["canonical_shell"])
    if not shell.units:
        raise ValueError("Pilot D2 Shell has no Units")
    receipt = completion_path.parent.parent / "audit" / "pilot_sdk_receipt.json"
    if not receipt.is_file() or read_json(receipt).get("status") != "completed":
        raise ValueError("D2 Pilot Shell does not have a completed SDK receipt")
    published_at = datetime.fromisoformat(
        read_json(receipt)["research_finished_at"].replace("Z", "+00:00")
    )
    document = Document2DocumentV21.model_validate(
        {
            "document2_run_id": run_id,
            "ticker": "MU",
            "as_of": published_at,
            "source_global_run_id": global_run_id,
            "input_manifest": {
                "global_research": {"status": "AVAILABLE", "source_run_id": global_run_id},
                "narrative_research": {"status": "ABSENT"},
                "event_library": {"status": "ABSENT"},
            },
            "shells": [shell.model_dump(mode="json")],
            "shell_outcomes": [{"shell": shell.name, "status": "completed"}],
        }
    )
    content = document.model_dump_json(indent=2)
    data = content.encode("utf8")
    sha256 = hashlib.sha256(data).hexdigest()
    artifact_id = f"pilot-d2-{sha256[:24]}"
    reference = ArtifactRef(
        workflow_version="codex_document2_v1",
        research_lane="document2",
        artifact_id=artifact_id,
        run_id=run_id,
        node="d2_publish",
        attempt_id="pilot-source-adapter",
        kind="bundle",
        relative_path="artifacts/document2/pilot-adapted/document2.json",
        sha256=sha256,
        size_bytes=len(data),
        content_type="application/json",
        published=True,
    )
    handoff = Document2HandoffV1(
        run_id=run_id,
        ticker="MU",
        source_global_run_id=global_run_id,
        document2_artifact_id=artifact_id,
        publication_state="PARTIAL",
        citation_status="UNAVAILABLE",
        published_at=published_at,
    )
    repository.save_artifact(reference)
    repository.save_published_document(
        PublishedDocument(
            artifact_id=artifact_id,
            run_id=run_id,
            artifact_kind="bundle",
            sha256=sha256,
            size_bytes=len(data),
            content_type="application/json",
            content_text=content,
            published_at=published_at,
        )
    )
    repository.save_bundle(
        Document2Bundle(
            run_id=run_id,
            ticker="MU",
            source_global_run_id=global_run_id,
            status="published",
            publication_state="PARTIAL",
            citation_status="UNAVAILABLE",
            artifacts={"document2": reference},
            handoff=handoff,
            published_at=published_at,
        )
    )
    return {
        "kind": "pilot_only_d2_snapshot",
        "source_completion": str(completion_path.resolve()),
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "shells": 1,
        "units": len(shell.units),
        "adapted_document_sha256": sha256,
        "run_id": run_id,
    }


def pilot_audit_failed(root: Path) -> bool:
    return any(
        read_json(path).get("review_error")
        for path in (root / "workspaces").glob("*/attempts/*/audit/pilot_sdk_receipt.json")
    )


def retry_failed_planning(root: Path, state, run_id: str) -> None:
    """Re-arm failed Planning tasks without repeating accepted earlier batches."""
    run = state.run(run_id)
    tasks = state.tasks(run_id)
    planning = {key: value for key, value in tasks.items() if key.startswith("planning:")}
    failed = {key: value for key, value in planning.items() if value["status"] == "FAILED"}
    if (
        not run
        or run.get("phase") != "BUILD"
        or "basis" in run
        or not failed
        or any(item["status"] not in {"FAILED", "COMPLETED"} for item in planning.values())
        or any(key.startswith(("research:", "supplement:", "integration:")) for key in tasks)
    ):
        raise ValueError(
            "Planning retry requires only failed Planning tasks and no downstream work"
        )
    if any(
        not any(
            marker in item.get("error", "")
            for marker in ("invalid transport", "context is immutable")
        )
        for item in failed.values()
    ):
        raise ValueError("Planning retry requires a verified admission or stale-context error")
    if any(
        item.get("planning_recovery_used") or item["attempt_count"] >= 4 for item in failed.values()
    ):
        raise ValueError("Planning recovery budget already used")
    recovery_root = root / "pilot_recovery"
    next_number = len(list(recovery_root.glob("planning_retry_*.json"))) + 1
    recovery_receipt = {
        "run_id": run_id,
        "checkpoint_sha256": digest(run),
        "phase": run["phase"],
        "failed_tasks": {
            key: {
                "status": item["status"],
                "attempt_count": item["attempt_count"],
                "error": item.get("error"),
            }
            for key, item in failed.items()
        },
        "isolated_files": [],
    }
    # Only failed task-local derived namespaces may be isolated; immutable shared
    # source and accepted outputs are retained. New contexts use a new generation.
    moves = []
    for key, item in failed.items():
        workspace = (
            root / "workspaces" / item.get("workspace_run_id", f"{run_id}-global")
        ).resolve()
        allowed_root = (root / "workspaces").resolve()
        if not workspace.is_relative_to(allowed_root):
            raise ValueError("Planning recovery workspace escapes pilot root")
        task_hash = digest(key)[:24]
        task_path = item.get("task_path", f"context/document3/v21/tasks/{task_hash}.json")
        for relative in (
            task_path,
            f"context/document3/v21/borrowed/{task_hash}",
            f"context/document3/v21/context_index/{task_hash}",
            f"context/document3/v21/context_index/tasks/{Path(task_path).stem}",
        ):
            target = (workspace / relative).resolve()
            if not target.is_relative_to(workspace):
                raise ValueError("Planning recovery path escapes workspace")
            if target.exists():
                files = sorted(target.rglob("*")) if target.is_dir() else [target]
                recovery_receipt["isolated_files"].extend(
                    {
                        "workspace": workspace.name,
                        "path": p.relative_to(workspace).as_posix(),
                        "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                    }
                    for p in files
                    if p.is_file()
                )
                destination = (
                    recovery_root / f"planning_context_{next_number}" / task_hash / relative
                )
                moves.append((target, destination))
    write_json(recovery_root / f"planning_retry_{next_number}.json", recovery_receipt)
    for target, destination in moves:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(target), str(destination))
    recovery_receipt["isolation_complete"] = True
    write_json(recovery_root / f"planning_retry_{next_number}.json", recovery_receipt)
    run = dict(run)
    run.pop("agenda", None)
    run["phase"] = "PLANNING"
    run["missing"] = list(run.get("discovery_missing", []))
    with state.lock, state.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            "UPDATE codex_document3_v21_runs SET payload=? WHERE run_id=?",
            (canonical(run), run_id),
        )
        for key, old in failed.items():
            item = dict(old)
            item.update(
                status="PENDING",
                max_attempts=4,
                planning_recovery_used=True,
                context_generation=max(old["attempt_count"], old.get("context_generation", 0)) + 1,
                error="",
                files={},
                snapshots={},
            )
            item.pop("request", None)
            item.pop("job", None)
            db.execute(
                "UPDATE codex_document3_v21_tasks SET payload=? WHERE run_id=? AND task_key=?",
                (canonical(item), run_id, key),
            )


async def import_global_files(settings, worker, kwargs, source_root, preparer):
    from doxagent.codex_runtime.repository import SQLiteCodexRuntimeRepository

    repository = SQLiteCodexRuntimeRepository(settings.codex_runtime_sqlite_path)
    run_id = kwargs.get("source_global_run_id")
    if not run_id and kwargs.get("document2_run_id"):
        bundle = repository.get_bundle(kwargs["document2_run_id"])
        run_id = getattr(bundle, "source_global_run_id", None)
        if not run_id and bundle and bundle.handoff:
            published = repository.get_published_document(
                kwargs["document2_run_id"], bundle.handoff.document2_artifact_id
            )
            if published:
                raw = json.loads(await preparer._read_document(published))
                run_id = raw.get("source_global_run_id")
    if not run_id:
        return
    bundle = repository.get_bundle(run_id)
    if bundle is None:
        raise ValueError("Global source missing from source snapshot")
    refs = [*getattr(bundle, "reports", {}).values(), *repository.list_artifacts(run_id, limit=500)]
    source = LocalWorkspaceStore(source_root)
    for reference in refs:
        try:
            content = source.read_text(run_id, reference.relative_path)
        except FileNotFoundError:
            published = repository.get_published_document(run_id, reference.artifact_id)
            if published is None or published.content_text is None:
                continue  # preparer will report unavailable source records
            content = SimpleNamespace(
                content=published.content_text,
                sha256=hashlib.sha256(published.content_text.encode()).hexdigest(),
            )
        if content.sha256 != reference.sha256:
            raise ValueError("Global source checksum mismatch")
        worker.local.write_text(run_id, reference.relative_path, content.content)


async def execute(args):
    root = args.pilot_root.resolve()
    if args.command == "report":
        return build_document3_delivery(root)
    if args.runtime_env_file:
        load_dotenv(args.runtime_env_file, override=True)
    root.mkdir(parents=True, exist_ok=True)
    with execution_lock(root / ".driver.lock"):
        request_path = root / "pilot_request.json"
        if args.command == "start":
            if request_path.exists():
                raise ValueError("Pilot already exists; use continue or a new root")
            request = read_json(args.request)
            if request.get("orchestration_version", "v2.1") not in {"v2", "v2.1"}:
                raise ValueError("unsupported D3 version")
            if request.get("mode") not in {"initialize", "maintain"}:
                raise ValueError("request mode must be initialize or maintain")
            if not request.get("kwargs", {}).get("run_id"):
                raise ValueError("Pilot requires an explicit run_id")
            if args.source_snapshot_db:
                seed_database(args.source_snapshot_db, root / "runtime.sqlite")
            if getattr(args, "d2_pilot_completion", None):
                kwargs = request["kwargs"]
                if (
                    kwargs.get("ticker", "").upper() != "MU"
                    or not kwargs.get("source_global_run_id")
                    or not kwargs.get("document2_run_id")
                ):
                    raise ValueError("Pilot D2 adapter requires MU and explicit D2/Global run IDs")
                provenance = seed_pilot_document2(
                    root / "runtime.sqlite",
                    args.d2_pilot_completion,
                    kwargs["document2_run_id"],
                    kwargs["source_global_run_id"],
                )
                write_json(root / "pilot_source_provenance.json", provenance)
            request["pilot_options"] = {
                "model": args.model,
                "effort": args.effort,
                "source_workspaces": str(args.source_workspaces.resolve())
                if args.source_workspaces
                else None,
            }
            write_json(request_path, request)
        request = read_json(request_path)
        options = request.get("pilot_options", {})
        model = args.model or options.get("model")
        effort = args.effort or options.get("effort")
        secret = root / ".capability-secret"
        if not secret.exists():
            secret.write_text(secrets.token_hex(32), encoding="utf8")
        base = DoxAgentSettings()
        settings = base.model_copy(
            update={
                "codex_document3_enabled": True,
                "codex_worker_bearer_token": secrets.token_hex(24),
                "codex_capability_secret": secret.read_text("utf8"),
                "codex_runtime_storage_mode": "sqlite",
                "codex_runtime_sqlite_path": str(root / "runtime.sqlite"),
                "codex_workspace_root": str(root / "workspaces"),
                "codex_model": model or base.codex_model,
                "codex_d3_initialize_model": model or base.codex_d3_initialize_model,
                "codex_reasoning_effort": effort or base.codex_reasoning_effort,
                "codex_d3_initialize_effort": effort or base.codex_d3_initialize_effort,
            }
        )
        runtime = OpenAICodexRuntime(
            settings=settings,
            capability_secret=settings.codex_capability_secret,
            reasoning_summary="detailed",
            preserve_requested_model=True,
        )
        worker = Document3PilotWorker(LocalWorkspaceStore(root / "workspaces"), runtime)
        kwargs = dict(request["kwargs"])
        if "as_of" in kwargs:
            kwargs["as_of"] = datetime.fromisoformat(kwargs["as_of"])
        if getattr(args, "retry_failed_planning", False):
            if args.command != "continue":
                raise ValueError("Planning retry requires continue")
            from doxagent.workflows.codex_document3.state_v21 import StateV21

            retry_failed_planning(root, StateV21(root / "runtime.sqlite"), kwargs["run_id"])
        status = {"status": "interrupted"}
        try:
            orchestrator = build_document3_orchestrator(
                settings,
                worker=worker,
                orchestration_version=request.get("orchestration_version", "v2.1"),
                node_assets=request.get("node_assets"),
            )
            if hasattr(orchestrator, "_published_storage"):
                orchestrator._published_storage = PilotPublishedCapture(root / "published")
            if (
                getattr(args, "stop_after", None)
                and request.get("orchestration_version", "v2.1") == "v2.1"
            ):
                orchestrator.pilot_stop_after_phase = args.stop_after
            if options.get("source_workspaces") and not (root / ".sources-imported").exists():
                source_root = Path(options["source_workspaces"])
                if source_root.resolve() == (root / "workspaces").resolve():
                    raise ValueError("Pilot source workspaces must be separate")
                preparer = (
                    orchestrator.preparer.legacy
                    if hasattr(orchestrator, "preparer")
                    else orchestrator._inputs
                )
                await import_global_files(settings, worker, kwargs, source_root, preparer)
                (root / ".sources-imported").touch()
            result = await getattr(orchestrator, request["mode"])(**kwargs)
            status = {
                "status": "completed_with_audit_errors"
                if worker.pilot_failed or pilot_audit_failed(root)
                else "completed",
                "result": result.model_dump(mode="json"),
            }
        except PilotPhasePaused as exc:
            status = {"status": "paused", "completed_phase": exc.phase, "run_id": exc.run_id}
        except Exception as exc:
            status = {"status": "failed", "error": str(exc)}
            raise
        finally:
            write_json(root / "pilot_result.json", status)
            delivery = build_document3_delivery(root)
            await runtime.close()
        return delivery


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("start", "continue", "report"):
        command = commands.add_parser(name)
        command.add_argument("--pilot-root", type=Path, required=True)
        if name == "report":
            continue
        command.add_argument("--runtime-env-file", type=Path)
        command.add_argument("--model")
        command.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"])
        command.add_argument("--stop-after", choices=["discovery", "planning", "build"])
        if name == "continue":
            command.add_argument("--retry-failed-planning", action="store_true")
        if name == "start":
            command.add_argument("--request", type=Path, required=True)
            command.add_argument("--source-snapshot-db", type=Path)
            command.add_argument("--source-workspaces", type=Path)
            command.add_argument("--d2-pilot-completion", type=Path)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(execute(args)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
