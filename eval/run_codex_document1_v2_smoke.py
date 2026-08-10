"""Low-cost real Codex D1 v2 functional smoke.

The smoke intentionally skips live horizontal provider collection. It exercises the
real Docker worker, Codex SDK turns, Data MCP citation path, node hand-offs, hybrid
checkpoints, one injected retry, publication, and the final Document 1 artifact.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from uuid import uuid4

from doxagent.codex_runtime.client import HttpCodexWorkerClient
from doxagent.codex_runtime.repository import (
    HybridCodexRuntimeRepository,
    PostgresCodexRuntimeRepository,
    SQLiteCodexRuntimeRepository,
)
from doxagent.codex_runtime.schema import AttemptStatus, CodexD1Node
from doxagent.codex_worker.schema import WorkerJob, WorkerRunRequest
from doxagent.horizontal_collection.collector import HorizontalCollector
from doxagent.horizontal_collection.compiler import HorizontalStateCompiler
from doxagent.horizontal_collection.schema import (
    CollectionObservation,
    HorizontalCollectionBundle,
    HorizontalCollectionManifest,
)
from doxagent.settings import DoxAgentSettings
from doxagent.workflows.codex_document1.orchestrator import CodexDocument1Orchestrator
from doxagent.workflows.codex_document1.schema import Document1V2RunRequest


class SmokeHorizontalCollector(HorizontalCollector):
    """Return a valid empty manifest so the smoke does not probe every provider."""

    def __init__(self) -> None:
        pass

    def collect(
        self, *, run_id: str, ticker: str
    ) -> tuple[HorizontalCollectionManifest, tuple[CollectionObservation, ...]]:
        return (
            HorizontalCollectionManifest(
                run_id=run_id,
                ticker=ticker,
                metric_registry_version="smoke-v1",
                target_registry_version="smoke-v1",
                target_results=(),
            ),
            (),
        )


class SmokeHorizontalCompiler(HorizontalStateCompiler):
    def __init__(self) -> None:
        pass

    def compile(
        self,
        *,
        ticker: str,
        manifest: HorizontalCollectionManifest,
        observations: tuple[CollectionObservation, ...],
    ) -> HorizontalCollectionBundle:
        del ticker
        return HorizontalCollectionBundle(
            manifest=manifest,
            observations=observations,
            state_values=(),
        )


class RetryOnceWorker:
    """Inject one zero-cost worker failure before delegating the retry."""

    def __init__(self, delegate: HttpCodexWorkerClient, retry_node: CodexD1Node) -> None:
        self._delegate = delegate
        self._retry_node = retry_node
        self.injected = False

    async def run(self, request: WorkerRunRequest) -> WorkerJob:
        if request.node is self._retry_node and not self.injected:
            self.injected = True
            return WorkerJob(
                job_id=f"smoke-retry-{uuid4().hex}",
                run_id=request.run_id,
                attempt_id=request.attempt_id,
                status="failed",
                error_code="SMOKE_INJECTED_RETRY",
                error_message="synthetic zero-cost retry probe",
            )
        return await self._delegate.run(request)

    async def cancel(self, job_id: str) -> WorkerJob | None:
        return await self._delegate.cancel(job_id)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ticker", default="NVDA")
    parser.add_argument("--company-name", default="NVIDIA Corporation")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--model", default="gpt-5.6-luna")
    parser.add_argument(
        "--effort",
        choices=("low", "medium", "high", "xhigh", "max"),
        default="low",
    )
    parser.add_argument("--retry-node", choices=[node.value for node in CodexD1Node], default="c2")
    parser.add_argument("--max-subagents", type=int, choices=(0, 1, 2), default=1)
    return parser


async def _run(args: argparse.Namespace) -> dict[str, object]:
    settings = DoxAgentSettings()
    if not settings.codex_worker_bearer_token or not settings.codex_capability_secret:
        raise RuntimeError("persistent Codex worker bearer/capability secrets are required")

    run_id = args.run_id or f"codex-smoke-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    local_repository = SQLiteCodexRuntimeRepository(settings.codex_runtime_sqlite_path)
    if settings.codex_runtime_storage_mode == "hybrid":
        if not settings.codex_remote_runtime_storage_enabled or not settings.database_url:
            raise RuntimeError("hybrid smoke requires remote storage opt-in and DB URL")
        repository = HybridCodexRuntimeRepository(
            local=local_repository,
            remote=PostgresCodexRuntimeRepository(
                settings.database_url,
                evidence_repository=local_repository,
            ),
            mirror_remote_runtime_locally=settings.codex_hybrid_local_mirror_enabled,
        )
    else:
        repository = local_repository
    client = HttpCodexWorkerClient(
        settings.codex_worker_base_url,
        settings.codex_worker_bearer_token,
        capability_secret=settings.codex_capability_secret,
        poll_seconds=0.25,
    )
    retry_node = CodexD1Node(args.retry_node)
    worker = RetryOnceWorker(client, retry_node)
    orchestrator = CodexDocument1Orchestrator(
        worker=worker,
        workspace=client,
        repository=repository,
        horizontal_collector=SmokeHorizontalCollector(),
        horizontal_compiler=SmokeHorizontalCompiler(),
        model=args.model,
        effort=args.effort,
        timeout_seconds=900,
        max_attempts=2,
        max_subagents=args.max_subagents,
    )
    try:
        bundle = await orchestrator.run(
            Document1V2RunRequest(
                run_id=run_id,
                ticker=args.ticker.upper(),
                company_name=args.company_name,
                research_brief=(
                    "Functional smoke only; keep every section concise. C1 must call Data MCP "
                    "sec_issuer_filings once for one recent 10-Q or 10-K and cite at least one "
                    "returned O# observation in report_markdown. Do not perform broad research. "
                    "C4 finalization must return at least one minimal entity relation and one "
                    "minimal future node so the public schema path is exercised."
                ),
                base_context={
                    "smoke_mode": True,
                    "quality_acceptance": False,
                    "horizontal_collection": "intentionally empty",
                },
            )
        )
        checkpoint = repository.get_checkpoint(run_id)
        attempts = repository.list_attempts(run_id)
        inventory = await client.inventory(run_id)
        final_ref = bundle.reports.get("document1")
        if bundle.status != "published" or bundle.handoff is None or final_ref is None:
            raise RuntimeError("smoke did not publish a complete Document 1 bundle")
        if checkpoint is None or CodexD1Node.PUBLISH not in checkpoint.completed_nodes:
            raise RuntimeError("publish checkpoint was not persisted")
        if checkpoint.failed_nodes:
            raise RuntimeError(f"checkpoint retained failed nodes: {checkpoint.failed_nodes}")
        retry_attempts = [attempt for attempt in attempts if attempt.node is retry_node]
        if [attempt.status for attempt in retry_attempts] != [
            AttemptStatus.FAILED,
            AttemptStatus.SUCCEEDED,
        ]:
            raise RuntimeError("injected retry did not persist failed then succeeded attempts")
        manifest = bundle.citation_manifest
        if (
            manifest is None
            or not manifest.entries
            or not any(entry.resolved for entry in manifest.entries)
        ):
            raise RuntimeError("full smoke produced no resolved citation")
        inventory_paths = {item.relative_path for item in inventory.files}
        required_paths = {
            final_ref.relative_path,
            "artifacts/document1/citation_manifest.json",
        }
        if not required_paths.issubset(inventory_paths):
            raise RuntimeError("published inventory is missing final artifacts")
        return {
            "run_id": run_id,
            "status": bundle.status,
            "model": args.model,
            "effort": args.effort,
            "subagent_limit": args.max_subagents,
            "retry_node": retry_node.value,
            "attempt_count": len(attempts),
            "completed_nodes": [node.value for node in checkpoint.completed_nodes],
            "citation_entries": len(manifest.entries),
            "resolved_citations": sum(entry.resolved for entry in manifest.entries),
            "unresolved_citations": sum(not entry.resolved for entry in manifest.entries),
            "citation_warnings": manifest.warnings,
            "artifact_count": len(bundle.reports),
            "workspace_file_count": len(inventory.files),
            "document1_path": final_ref.relative_path,
        }
    finally:
        await client.aclose()


def main() -> None:
    args = _parser().parse_args()
    print(json.dumps(asyncio.run(_run(args)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
