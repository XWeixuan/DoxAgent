"""Test helpers for asynchronous Codex run lifecycle and published body checks."""

from __future__ import annotations

import asyncio
import hashlib
from uuid import uuid4

from doxagent.codex_runtime.client import WorkspaceClient
from doxagent.codex_runtime.published_storage import (
    PublishedDocumentStorage,
)
from doxagent.codex_runtime.repository import (
    CodexRuntimeRepository,
)
from doxagent.codex_runtime.schema import ResearchLane, utc_now
from doxagent.workflows.codex_document1.orchestrator import CodexDocument1Orchestrator
from doxagent.workflows.codex_document1.schema import Document1V2RunRequest
from doxagent.workflows.codex_document2.orchestrator import CodexDocument2Orchestrator
from doxagent.workflows.codex_document2.schema import (
    Document2Bundle,
    Document2RunRequest,
    StartDocument2Request,
)
from doxagent.workflows.codex_global_research import (
    CodexGlobalResearchOrchestrator,
    GlobalResearchRunRequest,
)
from doxagent.workflows.codex_market_situation import (
    CodexMarketSituationOrchestrator,
    MarketSituationRunRequest,
)


class CodexDocument1RunService:
    def __init__(
        self,
        *,
        orchestrator: CodexDocument1Orchestrator,
        repository: CodexRuntimeRepository,
        workspace: WorkspaceClient,
        published_storage: PublishedDocumentStorage | None = None,
    ) -> None:
        self._orchestrator = orchestrator
        self._repository = repository
        self._workspace = workspace
        self._published_storage = published_storage
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._errors: dict[str, str] = {}

    async def start(self, request: Document1V2RunRequest) -> dict[str, object]:
        existing = self._tasks.get(request.run_id)
        if existing is not None and not existing.done():
            raise ValueError("run is already active")
        self._errors.pop(request.run_id, None)
        task = asyncio.create_task(self._run(request), name=f"codex-d1:{request.run_id}")
        self._tasks[request.run_id] = task
        return {"run_id": request.run_id, "ticker": request.ticker, "status": "queued"}

    async def _run(self, request: Document1V2RunRequest) -> None:
        try:
            await self._orchestrator.run(request)
        except asyncio.CancelledError:
            checkpoint = self._repository.get_checkpoint(request.run_id)
            if checkpoint:
                checkpoint.cancelled = True
                checkpoint.updated_at = utc_now()
                self._repository.save_checkpoint(checkpoint)
            raise
        except Exception as exc:
            self._errors[request.run_id] = str(exc)

    def list_runs(
        self, ticker: str | None = None, cursor: str | None = None, limit: int = 20
    ) -> list[dict[str, object]]:
        return [
            item.model_dump(mode="json")
            for item in self._repository.list_run_summaries(ticker, cursor, limit)
        ]

    def get(self, run_id: str) -> dict[str, object] | None:
        bundle = self._repository.get_bundle(run_id)
        if bundle:
            return bundle.model_dump(mode="json", by_alias=True)
        checkpoint = self._repository.get_checkpoint(run_id)
        error = self._errors.get(run_id)
        if checkpoint:
            return {
                "run_id": run_id,
                "ticker": checkpoint.ticker,
                "workflow_version": checkpoint.workflow_version,
                "status": (
                    "cancelled" if checkpoint.cancelled else "failed" if error else "running"
                ),
                "error": error,
                "checkpoint": checkpoint.model_dump(mode="json"),
            }
        return None

    async def cancel(self, run_id: str) -> dict[str, object] | None:
        task = self._tasks.get(run_id)
        if task is None or task.done():
            return None
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return {"run_id": run_id, "status": "cancelled"}

    async def artifact(self, run_id: str, artifact_id: str) -> dict[str, object] | None:
        artifact = self._repository.get_artifact(run_id, artifact_id)
        if artifact is None or not artifact.published:
            return None
        published = self._repository.get_published_document(run_id, artifact_id)
        if published is None:
            raise RuntimeError("published document body is unavailable")
        if published.content_text is None:
            if self._published_storage is None or published.storage_path is None:
                raise RuntimeError(
                    "published document is stored externally but no Storage reader is configured"
                )
            content_bytes = await self._published_storage.get(published.storage_path)
            if len(content_bytes) != published.size_bytes:
                raise RuntimeError("published Storage document size mismatch")
            if hashlib.sha256(content_bytes).hexdigest() != published.sha256:
                raise RuntimeError("published Storage document checksum mismatch")
            content = content_bytes.decode("utf-8")
        else:
            content = published.content_text
        return {
            "artifact": artifact.model_dump(mode="json"),
            "content": content,
            "etag": artifact.sha256,
        }

    def events(self, run_id: str, after: int = -1, limit: int = 100) -> list[dict[str, object]]:
        return [
            item.model_dump(mode="json")
            for item in self._repository.list_events(run_id, after, limit)
        ]

    @staticmethod
    def _summary(bundle: dict[str, object]) -> dict[str, object]:
        return {
            key: bundle.get(key)
            for key in (
                "run_id",
                "ticker",
                "workflow_version",
                "status",
                "created_at",
                "published_at",
            )
        }


ResearchRunRequest = GlobalResearchRunRequest | MarketSituationRunRequest | Document2RunRequest


class CodexResearchLaneService:
    def __init__(
        self,
        *,
        global_orchestrator: CodexGlobalResearchOrchestrator,
        market_orchestrator: CodexMarketSituationOrchestrator,
        document2_orchestrator: CodexDocument2Orchestrator | None = None,
        repository: CodexRuntimeRepository,
        workspace: WorkspaceClient,
        published_storage: PublishedDocumentStorage | None = None,
    ) -> None:
        self._global = global_orchestrator
        self._market = market_orchestrator
        self._document2 = document2_orchestrator
        self._repository = repository
        self._workspace = workspace
        self._published_storage = published_storage
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._requests: dict[str, ResearchRunRequest] = {}
        self._errors: dict[str, str] = {}

    async def start(self, request: ResearchRunRequest) -> dict[str, object]:
        existing = self._tasks.get(request.run_id)
        if existing is not None and not existing.done():
            raise ValueError("run is already active")
        checkpoint = self._repository.get_checkpoint(request.run_id)
        if checkpoint is not None and checkpoint.research_lane is not request.research_lane:
            raise ValueError("run_id belongs to another research lane")
        self._errors.pop(request.run_id, None)
        self._requests[request.run_id] = request
        task_name = f"codex-research:{request.research_lane.value}:{request.run_id}"
        task = asyncio.create_task(self._run(request), name=task_name)
        self._tasks[request.run_id] = task
        return {
            "run_id": request.run_id,
            "ticker": request.ticker,
            "research_lane": request.research_lane.value,
            "workflow_version": request.workflow_version,
            "status": "queued",
        }

    async def _run(self, request: ResearchRunRequest) -> None:
        try:
            if isinstance(request, GlobalResearchRunRequest):
                # D2 is launched by the total initialization coordinator only after
                # O2 has published and pinned an Event Library version/hash/timestamp.
                await self._global.run(request)
            elif isinstance(request, MarketSituationRunRequest):
                await self._market.run(request)
            elif self._document2 is not None:
                await self._document2.run(request)
            else:
                raise RuntimeError("Document2 orchestrator is unavailable")
        except asyncio.CancelledError:
            checkpoint = self._repository.get_checkpoint(request.run_id)
            if checkpoint:
                checkpoint.cancelled = True
                checkpoint.updated_at = utc_now()
                self._repository.save_checkpoint(checkpoint)
            raise
        except Exception as exc:
            self._errors[request.run_id] = str(exc)

    async def start_document2(self, request: StartDocument2Request) -> dict[str, object]:
        if self._document2 is None:
            raise ValueError("Document2 runtime is unavailable")
        source = self._repository.get_bundle(request.source_global_run_id)
        if source is None or source.workflow_version != "codex_global_research_v1":
            raise ValueError("source_global_run_id must reference Global Research")
        if source.status != "published" or source.published_at is None:
            raise ValueError("source Global Research run is not published")
        run_id = request.run_id or _document2_run_id(
            request.source_global_run_id,
            force_new=request.force_new,
        )
        existing = self._repository.get_bundle(run_id)
        if (
            isinstance(existing, Document2Bundle)
            and existing.status == "published"
            and existing.publication_state == "COMPLETE"
        ):
            return {
                "run_id": run_id,
                "ticker": existing.ticker,
                "research_lane": ResearchLane.DOCUMENT2.value,
                "workflow_version": existing.workflow_version,
                "status": "published",
                "source_global_run_id": existing.source_global_run_id,
            }
        active = self._tasks.get(run_id)
        if active is not None and not active.done():
            return {
                "run_id": run_id,
                "ticker": source.ticker,
                "research_lane": ResearchLane.DOCUMENT2.value,
                "workflow_version": "codex_document2_v1",
                "status": "running",
                "source_global_run_id": request.source_global_run_id,
            }
        payload = Document2RunRequest(
            run_id=run_id,
            source_global_run_id=request.source_global_run_id,
            ticker=source.ticker,
            as_of=request.as_of or source.published_at,
            force_new=request.force_new,
        )
        result = await self.start(payload)
        result["source_global_run_id"] = request.source_global_run_id
        return result

    def list_runs(
        self,
        *,
        lane: ResearchLane | None,
        ticker: str | None,
        cursor: str | None,
        limit: int,
    ) -> list[dict[str, object]]:
        return [
            item.model_dump(mode="json")
            for item in self._repository.list_run_summaries(ticker, cursor, limit, lane)
        ]

    def get(self, run_id: str) -> dict[str, object] | None:
        bundle = self._repository.get_bundle(run_id)
        if bundle:
            data = bundle.model_dump(mode="json", by_alias=True)
            error = self._errors.get(run_id)
            if error is not None:
                data["status"] = "failed"
                data["error"] = error
            return data
        checkpoint = self._repository.get_checkpoint(run_id)
        if checkpoint:
            return {
                "run_id": run_id,
                "ticker": checkpoint.ticker,
                "workflow_version": checkpoint.workflow_version,
                "research_lane": checkpoint.research_lane.value,
                "status": (
                    "cancelled"
                    if checkpoint.cancelled
                    else "failed"
                    if run_id in self._errors
                    else "running"
                ),
                "error": self._errors.get(run_id),
                "checkpoint": checkpoint.model_dump(mode="json"),
            }
        return None

    async def cancel(self, run_id: str) -> dict[str, object] | None:
        task = self._tasks.get(run_id)
        if task is None or task.done():
            return None
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return {"run_id": run_id, "status": "cancelled"}

    async def retry(
        self,
        run_id: str,
        request: ResearchRunRequest | None = None,
    ) -> dict[str, object] | None:
        request = request or self._requests.get(run_id)
        if request is None:
            return None
        return await self.start(request)

    def events(self, run_id: str, after: int, limit: int) -> list[dict[str, object]]:
        return [
            item.model_dump(mode="json")
            for item in self._repository.list_events(run_id, after, limit)
        ]

    async def artifact(self, run_id: str, artifact_id: str) -> dict[str, object] | None:
        artifact = self._repository.get_artifact(run_id, artifact_id)
        if artifact is None or not artifact.published:
            return None
        published = self._repository.get_published_document(run_id, artifact_id)
        if published is None:
            raise RuntimeError("published document body is unavailable")
        if published.content_text is not None:
            content = published.content_text
        else:
            if self._published_storage is None or published.storage_path is None:
                raise RuntimeError("external published document Storage is unavailable")
            raw = await self._published_storage.get(published.storage_path)
            checksum_mismatch = hashlib.sha256(raw).hexdigest() != published.sha256
            if len(raw) != published.size_bytes or checksum_mismatch:
                raise RuntimeError("published Storage document checksum mismatch")
            content = raw.decode("utf-8")
        return {
            "artifact": artifact.model_dump(mode="json"),
            "content": content,
            "etag": artifact.sha256,
        }


def _document2_run_id(source_global_run_id: str, *, force_new: bool) -> str:
    digest = hashlib.sha256(source_global_run_id.encode("utf-8")).hexdigest()[:24]
    suffix = f"-{uuid4().hex[:10]}" if force_new else ""
    return f"d2-{digest}{suffix}"
