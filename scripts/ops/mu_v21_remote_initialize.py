"""Run formal MU D1/D2/D3 initialization without the ticker-init CDECR/O2/O4 DAG.

Use only with a separate SQLite runtime copy. Each stage has a stable run ID and
can be invoked again after a failure to resume its native workflow checkpoint.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime

from doxagent.codex_runtime.client import HttpCodexWorkerClient
from doxagent.codex_runtime.repository import SQLiteCodexRuntimeRepository
from doxagent.codex_runtime.schema import ResearchLane
from doxagent.event_library.provider import PublishedEventLibraryReader
from doxagent.horizontal_collection.collector import HorizontalCollector
from doxagent.horizontal_collection.compiler import HorizontalStateCompiler
from doxagent.horizontal_collection.registry import (
    collection_target_registry_for_lane,
    default_metric_registry,
)
from doxagent.settings import DoxAgentSettings
from doxagent.tools.factory import default_real_tool_registry
from doxagent.workflows.codex_document2.inputs import (
    DoxAtlasNarrativeReportProvider,
    PublishedEventLibraryProvider,
)
from doxagent.workflows.codex_document2.orchestrator import CodexDocument2Orchestrator
from doxagent.workflows.codex_document2.schema import Document2RunRequest
from doxagent.workflows.codex_document3.service import build_document3_orchestrator
from doxagent.workflows.codex_global_research import (
    CodexGlobalResearchOrchestrator,
    GlobalResearchRunRequest,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["d1", "d2", "d3"])
    parser.add_argument("--run-prefix", required=True)
    parser.add_argument("--as-of", required=True, type=datetime.fromisoformat)
    parser.add_argument("--event-library-version", type=int, required=True)
    return parser.parse_args()


async def run(args: argparse.Namespace) -> dict:
    if args.as_of.tzinfo is None:
        raise ValueError("--as-of must include a timezone")
    settings = DoxAgentSettings()
    if settings.codex_runtime_storage_mode != "sqlite":
        raise ValueError("formal test requires isolated SQLite runtime storage")
    if not settings.codex_runtime_sqlite_path.endswith("/mu-v21-test/research.sqlite3"):
        raise ValueError("refusing to use a non-test runtime database")
    if not settings.codex_worker_bearer_token or not settings.codex_capability_secret:
        raise ValueError("Codex Worker credentials are missing")
    repository = SQLiteCodexRuntimeRepository(settings.codex_runtime_sqlite_path)
    run_ids = {stage: f"{args.run_prefix}-{stage}" for stage in ("d1", "d2", "d3")}
    reader = PublishedEventLibraryReader(settings.event_library_root or "", market="US")
    event = reader.reference_view("MU", version=args.event_library_version)
    if event is None or event.published_at is None or event.published_at > args.as_of:
        raise ValueError("the pinned Published Event Library is unavailable at this cutoff")
    worker = HttpCodexWorkerClient(
        settings.codex_worker_base_url,
        settings.codex_worker_bearer_token,
        capability_secret=settings.codex_capability_secret,
    )
    try:
        if args.stage == "d1":
            metrics = default_metric_registry()
            targets = collection_target_registry_for_lane(ResearchLane.GLOBAL_RESEARCH)
            tools = default_real_tool_registry(settings)
            orchestrator = CodexGlobalResearchOrchestrator(
                worker=worker,
                workspace=worker,
                repository=repository,
                horizontal_collector=HorizontalCollector(
                    tools=tools, metrics=metrics, targets=targets
                ),
                horizontal_compiler=HorizontalStateCompiler(metrics=metrics, targets=targets),
                model=settings.codex_model,
                model_provider=settings.codex_model_provider,
                effort=settings.codex_reasoning_effort,
                timeout_seconds=settings.codex_node_timeout_seconds,
                max_attempts=1,
                max_subagents=settings.codex_max_subagents,
            )
            result = await orchestrator.run(
                GlobalResearchRunRequest(
                    run_id=run_ids["d1"],
                    ticker="MU",
                    cutoff_at=args.as_of,
                    research_brief="Initialize the ticker's Global Research context.",
                )
            )
            return {"stage": "d1", "run_id": run_ids["d1"], "status": result.status}
        d1 = repository.get_bundle(run_ids["d1"])
        if d1 is None or d1.status != "published":
            raise ValueError("published D1 handoff is required")
        if args.stage == "d2":
            tools = default_real_tool_registry(settings)
            orchestrator = CodexDocument2Orchestrator(
                max_shell_concurrency=settings.codex_d2_max_concurrency,
                worker=worker,
                workspace=worker,
                repository=repository,
                narrative_provider=DoxAtlasNarrativeReportProvider(tools),
                event_library_provider=PublishedEventLibraryProvider(
                    reader,
                    pinned_version=event.version,
                    pinned_sha256=event.sha256,
                    pinned_published_at=event.published_at,
                ),
                model=settings.codex_model,
                model_provider=settings.codex_model_provider,
                effort=settings.codex_reasoning_effort,
                timeout_seconds=settings.codex_node_timeout_seconds,
                max_attempts=1,
                max_subagents=settings.codex_max_subagents,
            )
            result = await orchestrator.run(
                Document2RunRequest(
                    run_id=run_ids["d2"],
                    ticker="MU",
                    source_global_run_id=run_ids["d1"],
                    as_of=args.as_of,
                    document_schema_version="document2.v2.1",
                    initialization_id=args.run_prefix,
                    reuse_published_partial=True,
                )
            )
            return {
                "stage": "d2",
                "run_id": run_ids["d2"],
                "status": result.status,
                "publication_state": str(result.publication_state),
            }
        d2 = repository.get_bundle(run_ids["d2"])
        if d2 is None or d2.status != "published" or not d2.handoff:
            raise ValueError("published D2 handoff is required")
        orchestrator = build_document3_orchestrator(
            settings, worker=worker, orchestration_version="v2.1"
        )
        result = await orchestrator.initialize(
            ticker="MU",
            run_id=run_ids["d3"],
            document2_run_id=run_ids["d2"],
            source_global_run_id=run_ids["d1"],
            event_library_version=event.version,
            as_of=args.as_of,
        )
        return result.model_dump(mode="json")
    finally:
        await worker.aclose()


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run(parse_args())), ensure_ascii=False, default=str))
