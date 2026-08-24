"""Run one Codex Document 1 node repeatedly against a fixed Pilot fixture."""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from doxagent.codex_runtime.repository import InMemoryCodexRuntimeRepository
from doxagent.codex_runtime.schema import CodexD1Node
from doxagent.codex_worker.jobs import WorkerJobManager
from doxagent.codex_worker.local_client import (
    EmbeddedCodexWorkerClient,
    LocalWorkspaceClient,
)
from doxagent.codex_worker.sdk_runtime import OpenAICodexRuntime
from doxagent.codex_worker.workspace_store import LocalWorkspaceStore
from doxagent.model_usage.repository import SQLiteModelUsageRepository
from doxagent.settings import DoxAgentSettings
from doxagent.workflows.codex_document1.node_runner import CodexD1NodeRunner


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node", required=True, choices=[item.value for item in CodexD1Node])
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--iterations", type=int, default=2)
    parser.add_argument("--model", default="gpt-5.6-luna")
    parser.add_argument(
        "--effort",
        choices=("low", "medium", "high", "xhigh", "max"),
        default="high",
    )
    thread = parser.add_mutually_exclusive_group()
    thread.add_argument("--fresh-thread", dest="fresh_thread", action="store_true")
    thread.add_argument("--reuse-thread", dest="fresh_thread", action="store_false")
    parser.set_defaults(fresh_thread=True)
    parser.add_argument("--max-subagents", type=int, choices=(0, 1, 2), default=2)
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    return parser


async def _run(args: argparse.Namespace) -> dict[str, object]:
    if not 1 <= args.iterations <= 10:
        raise ValueError("iterations must be between 1 and 10")
    fixture = Path(args.fixture).resolve()
    manifest = json.loads((fixture / "case_manifest.json").read_text(encoding="utf-8"))
    node = CodexD1Node(args.node)
    if manifest["node"] != node.value:
        raise ValueError("fixture node does not match --node")
    source_attempt = str(manifest["attempt_id"])
    source_input = fixture / "attempts" / source_attempt / "input"
    context_outer = json.loads((source_input / "context.json").read_text(encoding="utf-8"))
    payload = context_outer["payload"]
    horizontal_path = source_input / "horizontal.json"
    horizontal = (
        json.loads(horizontal_path.read_text(encoding="utf-8"))
        if horizontal_path.is_file()
        else None
    )
    run_id = str(manifest["run_id"])
    ticker = str(manifest["ticker"])
    cutoff_at = datetime.fromisoformat(str(manifest["cutoff_at"]).replace("Z", "+00:00"))
    expected_input_sha = str(manifest["input_sha256"])
    loop_id = f"{node.value}-{datetime.now().strftime('%Y%m%dT%H%M%S')}-{uuid4().hex[:8]}"
    loop_root = fixture / "sdk_runs" / loop_id
    loop_root.mkdir(parents=True)
    settings = DoxAgentSettings()
    usage = SQLiteModelUsageRepository(fixture / "audit" / "model_usage.sqlite3")
    results: list[dict[str, object]] = []
    reusable_thread_id: str | None = None
    for iteration in range(1, args.iterations + 1):
        iteration_root = loop_root / f"iteration-{iteration:02d}"
        workspace_store = LocalWorkspaceStore(iteration_root / "workspaces")
        run_root = workspace_store.ensure_run(run_id)
        _copy_frozen_context(fixture, run_root)
        workspace = LocalWorkspaceClient(workspace_store)
        runtime = OpenAICodexRuntime(
            capability_secret=settings.codex_capability_secret,
            settings=settings,
        )
        manager = WorkerJobManager(runtime, workspace_store)
        worker = EmbeddedCodexWorkerClient(manager)
        repository = InMemoryCodexRuntimeRepository()
        runner = CodexD1NodeRunner(
            worker=worker,
            workspace=workspace,
            repository=repository,
            prompt_root=(
                Path(manifest["repo_root"])
                / "prompts"
                / "codex_v2"
                / "document1"
                / "compatibility"
                / "legacy_document1"
            ),
            model=args.model,
            effort=args.effort,
            timeout_seconds=args.timeout_seconds,
            max_subagents=args.max_subagents,
            usage_repository=usage,
        )
        attempt_id = f"{node.value}-loop-{iteration}-{uuid4().hex[:10]}"
        record: dict[str, object] = {
            "iteration": iteration,
            "attempt_id": attempt_id,
            "status": "failed",
            "input_sha256": None,
            "thread_id": None,
            "workspace": str(run_root),
        }
        try:
            result = await runner.run_attempt(
                run_id=run_id,
                ticker=ticker,
                cutoff_at=cutoff_at,
                node=node,
                attempt_id=attempt_id,
                attempt_number=iteration,
                payload=payload,
                horizontal=horizontal,
                thread_id=reusable_thread_id,
                fresh_thread=args.fresh_thread,
            )
            if result.seeded.input_sha256 != expected_input_sha:
                raise RuntimeError(
                    "fixture input SHA drifted: "
                    f"{result.seeded.input_sha256} != {expected_input_sha}"
                )
            record.update(
                {
                    "status": "succeeded",
                    "input_sha256": result.seeded.input_sha256,
                    "thread_id": result.worker_job.thread_id if result.worker_job else None,
                    "report_path": result.report.relative_path,
                }
            )
            if not args.fresh_thread and result.worker_job is not None:
                reusable_thread_id = result.worker_job.thread_id
        except Exception as exc:
            attempts = repository.list_attempts(run_id)
            failed = next((item for item in attempts if item.attempt_id == attempt_id), None)
            record.update(
                {
                    "input_sha256": failed.input_sha256 if failed else None,
                    "error_code": failed.error_code if failed else type(exc).__name__,
                    "error_message": str(exc)[:1000],
                }
            )
        results.append(record)
        (iteration_root / "iteration_summary.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    summary = {
        "schema_version": "codex-d1-node-loop-v1",
        "loop_id": loop_id,
        "node": node.value,
        "fixture": str(fixture),
        "expected_input_sha256": expected_input_sha,
        "fresh_thread": args.fresh_thread,
        "model": args.model,
        "effort": args.effort,
        "max_subagents": args.max_subagents,
        "iterations": results,
        "succeeded": sum(item["status"] == "succeeded" for item in results),
        "failed": sum(item["status"] == "failed" for item in results),
        "model_usage_sqlite": str(fixture / "audit" / "model_usage.sqlite3"),
    }
    (loop_root / "loop_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary


def _copy_frozen_context(fixture: Path, run_root: Path) -> None:
    for name in ("context", "artifacts"):
        source = fixture / name
        if source.is_dir():
            shutil.copytree(source, run_root / name, dirs_exist_ok=True)


def main() -> None:
    args = _parser().parse_args()
    print(json.dumps(asyncio.run(_run(args)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
