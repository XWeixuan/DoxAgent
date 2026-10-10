"""Resume missing MU D3 v2.1 Build waves from an immutable partial release.

The source release stays untouched. A successor D3 run inherits frozen inputs,
accepted tasks, Policy IDs, and workspaces, then dispatches only the unfinished
Build waves before using the ordinary Integration/publication path.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import shutil
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path

from doxagent.codex_runtime.client import HttpCodexWorkerClient
from doxagent.settings import DoxAgentSettings
from doxagent.workflows.codex_document3.schema_v21 import Agenda
from doxagent.workflows.codex_document3.service import build_document3_orchestrator
from doxagent.workflows.codex_document3.state_v21 import canonical
from scripts.ops.mu_v21_remote_initialize import TestPublishedDocumentStorage

SOURCE = "mu-v21-20261010-02-d3"
SUCCESSOR = "mu-v21-20261010-02-d3-resume1"
WORKSPACE_ROOT = Path("/data/mu-v21-test/workspaces")
DERIVED = {
    "affected_paths",
    "basis",
    "commit",
    "coverage",
    "final",
    "final_candidates",
    "final_schedule",
    "handoff",
    "integration_schedule",
    "main_results",
    "parse_issues",
    "pending_edit_targets",
    "pending_research_topics",
    "post_review_focus",
    "post_supplement_review",
    "post_supplement_schedule",
    "reference_count",
    "replacement_receipt",
    "review",
    "supplement_agenda",
    "supplement_dispatched",
    "supplement_hash",
    "supplement_paths",
    "supplement_results",
    "status",
}


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "prepare", "run"))
    return parser.parse_args()


def _load_source(db: sqlite3.Connection):
    row = db.execute(
        "SELECT identity,payload FROM codex_document3_v21_runs WHERE run_id=?", (SOURCE,)
    ).fetchone()
    if row is None:
        raise ValueError("source D3 run unavailable")
    identity, run = row[0], json.loads(row[1])
    if run.get("phase") != "STAGED" or run.get("status") != "PARTIAL":
        raise ValueError("source D3 run is not the expected immutable partial release")
    if run.get("commit", {}).get("version") != 21:
        raise ValueError("source D3 version changed")
    tasks = {
        key: json.loads(payload)
        for key, payload in db.execute(
            "SELECT task_key,payload FROM codex_document3_v21_tasks WHERE run_id=?", (SOURCE,)
        )
    }
    agenda = Agenda.model_validate(run["agenda"])
    unfinished = {
        key
        for key, task in tasks.items()
        if key.startswith("research:") and task["status"] in {"FAILED", "PARTIAL"}
    }
    if len(agenda.waves) != 105 or Counter(tasks[k]["status"] for k in unfinished) != {
        "FAILED": 72,
        "PARTIAL": 2,
    }:
        raise ValueError("source D3 Build checkpoint differs from the audited 74 waves")
    if (
        sum(k.startswith("research:") and t["status"] == "COMPLETED" for k, t in tasks.items())
        != 31
    ):
        raise ValueError("source D3 completed Build wave count changed")
    if any(
        k.startswith("research:") and t["status"] not in {"COMPLETED", "FAILED", "PARTIAL"}
        for k, t in tasks.items()
    ):
        raise ValueError("source D3 has an active Build wave")
    return identity, run, tasks, agenda, unfinished


def _successor_payload(source: dict) -> dict:
    run = copy.deepcopy(source)
    for key in DERIVED:
        run.pop(key, None)
    run["phase"] = "BUILD"
    run["missing"] = list(run.get("discovery_missing", []))
    run["diagnostics"] = []
    run["version_floor"] = max(run.get("version_floor", 0), 21)
    run["owner_workspaces"] = {
        owner: f"{SUCCESSOR}-{owner.lower()}" for owner in run["prepared"]["topology"]["owners"]
    }
    run["recovery_source"] = {
        "run_id": SOURCE,
        "staged_version": 21,
        "scope": "72 failed and 2 partial Build waves; completed tasks inherited",
        "new_model": "gpt-6-luna",
        "new_effort": "max",
    }
    return run


def _successor_task(key: str, source: dict) -> dict | None:
    if key.startswith("integration:"):
        return None
    task = copy.deepcopy(source)
    if key.startswith("research:") and task["status"] in {"FAILED", "PARTIAL"}:
        task.update(status="PENDING", attempt_count=0, max_attempts=1, fresh_session=True, error="")
        for field in (
            "request",
            "job",
            "acceptance",
            "context_generation",
            "task_path",
            "host_metrics",
            "host_spans",
        ):
            task.pop(field, None)
    task["persistence_run_id"] = SUCCESSOR
    if task.get("owner_slot"):
        task["workspace_run_id"] = f"{SUCCESSOR}-{task['owner_slot'].lower()}"
    return task


def _copy_workspaces(source_run: dict, tasks: dict) -> None:
    if not WORKSPACE_ROOT.is_dir() or WORKSPACE_ROOT.resolve() != Path(
        "/data/mu-v21-test/workspaces"
    ):
        raise ValueError("unexpected workspace root")
    master = WORKSPACE_ROOT / SOURCE
    if not master.is_dir():
        raise ValueError("source D3 master workspace missing")
    shutil.copytree(master, WORKSPACE_ROOT / SUCCESSOR, dirs_exist_ok=True)
    for owner in source_run["prepared"]["topology"]["owners"]:
        original = WORKSPACE_ROOT / f"{SOURCE}-{owner.lower()}" / "output"
        target = WORKSPACE_ROOT / f"{SUCCESSOR}-{owner.lower()}" / "output"
        if original.is_dir():
            shutil.copytree(original, target, dirs_exist_ok=True)
    for task in tasks.values():
        if task["status"] not in {"COMPLETED", "PARTIAL"}:
            continue
        for path, content in task.get("files", {}).items():
            snapshot = task["snapshots"][path]
            copied = (WORKSPACE_ROOT / SUCCESSOR / snapshot).read_text(encoding="utf-8")
            if copied != content:
                raise ValueError(f"inherited snapshot mismatch:{snapshot}")


def check(settings: DoxAgentSettings) -> dict:
    if (
        settings.codex_runtime_storage_mode != "sqlite"
        or not settings.codex_runtime_sqlite_path.endswith("/mu-v21-test/research.sqlite3")
    ):
        raise ValueError("refusing a non-test runtime database")
    with sqlite3.connect(settings.codex_runtime_sqlite_path) as db:
        _, run, tasks, agenda, unfinished = _load_source(db)
        successor = db.execute(
            "SELECT payload FROM codex_document3_v21_runs WHERE run_id=?", (SUCCESSOR,)
        ).fetchone()
    return {
        "source": SOURCE,
        "successor": SUCCESSOR,
        "waves": len(agenda.waves),
        "inherited_completed": 31,
        "unfinished": len(unfinished),
        "successor_exists": successor is not None,
        "source_status": run["status"],
    }


def prepare(settings: DoxAgentSettings) -> dict:
    status = check(settings)
    if status["successor_exists"]:
        return status
    with sqlite3.connect(settings.codex_runtime_sqlite_path) as db:
        identity, source, tasks, _, _ = _load_source(db)
        _copy_workspaces(source, tasks)
        db.execute("BEGIN IMMEDIATE")
        if db.execute(
            "SELECT 1 FROM codex_document3_v21_runs WHERE run_id=?", (SUCCESSOR,)
        ).fetchone():
            raise ValueError("successor was created concurrently")
        db.execute(
            "INSERT INTO codex_document3_v21_runs VALUES(?,?,?)",
            (SUCCESSOR, identity, canonical(_successor_payload(source))),
        )
        for key, value in tasks.items():
            inherited = _successor_task(key, value)
            if inherited is not None:
                db.execute(
                    "INSERT INTO codex_document3_v21_tasks VALUES(?,?,?)",
                    (SUCCESSOR, key, canonical(inherited)),
                )
        db.execute(
            "INSERT INTO codex_document3_v21_draft_ids "
            "SELECT ?,path,payload FROM codex_document3_v21_draft_ids WHERE run_id=?",
            (SUCCESSOR, SOURCE),
        )
    return check(settings)


async def run(settings: DoxAgentSettings) -> dict:
    if settings.codex_model != "gpt-6-luna" or settings.codex_reasoning_effort != "max":
        raise ValueError("generic model must be gpt-6-luna/max")
    if (
        settings.codex_d3_initialize_model != "gpt-6-luna"
        or settings.codex_d3_initialize_effort != "max"
    ):
        raise ValueError("D3 initialize model must be gpt-6-luna/max")
    if settings.codex_d3_max_concurrency != 4:
        raise ValueError("recovery uses the original D3 limit of four concurrent owner waves")
    if settings.codex_worker_base_url != "http://doxagent-mu-v21-worker:8791":
        raise ValueError("recovery requires isolated v2.1 Worker")
    status = check(settings)
    if not status["successor_exists"]:
        raise ValueError("prepare the successor checkpoint before dispatch")
    worker = HttpCodexWorkerClient(
        settings.codex_worker_base_url,
        settings.codex_worker_bearer_token,
        capability_secret=settings.codex_capability_secret,
    )
    try:
        orchestrator = build_document3_orchestrator(
            settings,
            worker=worker,
            orchestration_version="v2.1",
            published_storage=TestPublishedDocumentStorage(Path("/data/mu-v21-test/published")),
        )
        state = orchestrator.state
        frozen = state.run(SUCCESSOR)
        agenda = Agenda.model_validate(frozen["agenda"])
        await orchestrator._build(SUCCESSOR, agenda, stop_on_failed_wave=True)
        incomplete = {
            key: task["status"]
            for key, task in state.tasks(SUCCESSOR).items()
            if key.startswith("research:") and task["status"] != "COMPLETED"
        }
        if incomplete:
            raise RuntimeError(f"Build recovery incomplete:{incomplete}")
        result = await orchestrator.initialize(
            ticker="MU",
            run_id=SUCCESSOR,
            document2_run_id=f"{SOURCE.rsplit('-', 1)[0]}-d2",
            source_global_run_id=f"{SOURCE.rsplit('-', 1)[0]}-d1",
            event_library_version=1,
            as_of=datetime.fromisoformat(frozen["identity"]["as_of"]),
        )
        return result.model_dump(mode="json")
    finally:
        await worker.aclose()


def main() -> None:
    args = arguments()
    settings = DoxAgentSettings()
    if args.action == "check":
        result = check(settings)
    elif args.action == "prepare":
        result = prepare(settings)
    else:
        result = asyncio.run(run(settings))
    print(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
