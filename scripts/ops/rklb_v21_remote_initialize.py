"""Run formal RKLB D1/D2/D3 initialization without the ticker-init CDECR/O2/O4 DAG.

Use only with a separate SQLite runtime copy. Each stage has a stable run ID and
can be invoked again after a failure to resume its native workflow checkpoint.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path

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


class TestPublishedDocumentStorage:
    """Keep oversized test publications inside the isolated RKLB test volume."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _path(self, path: str) -> Path:
        target = (self.root / path).resolve()
        if not target.is_relative_to(self.root):
            raise ValueError("published document path escapes the RKLB test root")
        return target

    async def put(self, path: str, content: bytes, content_type: str) -> None:
        target = self._path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    async def get(self, path: str) -> bytes:
        return self._path(path).read_bytes()


class O0Boundary(BaseException):
    """Intentional operator stop, preserving the completed O0 checkpoint."""

    def __init__(self, count: int, status: str) -> None:
        self.count = count
        self.status = status


class GatedDocument2Orchestrator(CodexDocument2Orchestrator):
    def __init__(self, *, stop_after_o0: bool, **kwargs) -> None:
        super().__init__(**kwargs)
        self.stop_after_o0 = stop_after_o0

    async def _construct_shells(self, **kwargs):
        result = await super()._construct_shells(**kwargs)
        count = len(result[0].shells)
        # The parent persisted final seeds before this boundary. No O1 is dispatched.
        if not 3 <= count <= 6:
            raise O0Boundary(count, "shell_count_rejected")
        if self.stop_after_o0:
            raise O0Boundary(count, "awaiting_shell_count_release")
        return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["d1", "d2-o0", "d2", "d3"])
    parser.add_argument("--run-prefix", required=True)
    parser.add_argument("--as-of", required=True, type=datetime.fromisoformat)
    parser.add_argument("--event-library-version", type=int, required=True)
    return parser.parse_args()


async def run(args: argparse.Namespace) -> dict:
    if args.as_of.tzinfo is None:
        raise ValueError("--as-of must include a timezone")
    settings = DoxAgentSettings()
    settings.codex_model_provider = settings.codex_model_provider or None
    if settings.codex_runtime_storage_mode != "sqlite":
        raise ValueError("formal test requires isolated SQLite runtime storage")
    if not settings.codex_runtime_sqlite_path.endswith("/rklb-v21-test/research.sqlite3"):
        raise ValueError("refusing to use a non-test runtime database")
    if not settings.codex_worker_bearer_token or not settings.codex_capability_secret:
        raise ValueError("Codex Worker credentials are missing")
    repository = SQLiteCodexRuntimeRepository(settings.codex_runtime_sqlite_path)
    published_storage = TestPublishedDocumentStorage(Path("/data/rklb-v21-test/published"))
    run_ids = {stage: f"{args.run_prefix}-{stage}" for stage in ("d1", "d2", "d3")}
    reader = PublishedEventLibraryReader(settings.event_library_root or "", market="US")
    event = reader.reference_view("RKLB", version=args.event_library_version)
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
                max_attempts=2,
                max_subagents=settings.codex_max_subagents,
            )
            result = await orchestrator.run(
                GlobalResearchRunRequest(
                    run_id=run_ids["d1"],
                    ticker="RKLB",
                    cutoff_at=args.as_of,
                    research_brief="Initialize the ticker's Global Research context.",
                )
            )
            return {"stage": "d1", "run_id": run_ids["d1"], "status": result.status}
        d1 = repository.get_bundle(run_ids["d1"])
        if d1 is None or d1.status != "published":
            raise ValueError("published D1 handoff is required")
        if args.stage in ("d2-o0", "d2"):
            tools = default_real_tool_registry(settings)
            orchestrator = GatedDocument2Orchestrator(
                stop_after_o0=args.stage == "d2-o0",
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
                published_storage=published_storage,
                model=settings.codex_model,
                model_provider=settings.codex_model_provider,
                effort=settings.codex_reasoning_effort,
                timeout_seconds=settings.codex_node_timeout_seconds,
                max_attempts=2,
                max_subagents=settings.codex_max_subagents,
            )
            result = await orchestrator.run(
                Document2RunRequest(
                    run_id=run_ids["d2"],
                    ticker="RKLB",
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
            settings,
            worker=worker,
            orchestration_version="v2.1",
            published_storage=published_storage,
        )
        result = await orchestrator.initialize(
            ticker="RKLB",
            run_id=run_ids["d3"],
            document2_run_id=run_ids["d2"],
            source_global_run_id=run_ids["d1"],
            event_library_version=event.version,
            as_of=args.as_of,
        )
        return result.model_dump(mode="json")
    except O0Boundary as boundary:
        return {
            "stage": "d2-o0",
            "run_id": run_ids["d2"],
            "status": boundary.status,
            "shell_count": boundary.count,
        }
    finally:
        await worker.aclose()


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run(parse_args())), ensure_ascii=False, default=str))

