"""Local-first bridges to the existing research workflows (no Dashboard/API dependency)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from doxagent.cdecr_integration.contracts import O2UpstreamContextManifest
from doxagent.cdecr_integration.coordinator import TickerCDECRPipelineCoordinator
from doxagent.cdecr_integration.historical_loader import (
    BenzingaHistoricalNewsProvider,
    FinnhubHistoricalNewsProvider,
    HistoricalNewsProvider,
)
from doxagent.cdecr_integration.runtime_factory import build_cdecr_workflow_runner
from doxagent.codex_runtime.client import HttpCodexWorkerClient
from doxagent.codex_runtime.repository import SQLiteCodexRuntimeRepository
from doxagent.codex_runtime.schema import GlobalResearchBundle, ResearchLane
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
from doxagent.workflows.codex_document2.schema import Document2Bundle, Document2RunRequest
from doxagent.workflows.codex_document3.service import build_document3_orchestrator
from doxagent.workflows.codex_event_library.remote_runner import RemoteEventLibraryInitializer
from doxagent.workflows.codex_global_research import (
    CodexGlobalResearchOrchestrator,
    GlobalResearchRunRequest,
)

from .schema import NodeResult
from .service import NodeContext


class ResearchInitializationAdapter:
    def __init__(self, settings: DoxAgentSettings) -> None:
        # Full documents, checkpoint traffic and prompts never enter a remote mirror.
        self.settings = settings.model_copy(
            update={
                "codex_runtime_storage_mode": "sqlite",
                "codex_published_storage_url": None,
                "codex_published_storage_secret_key": None,
            }
        )
        self.repository = SQLiteCodexRuntimeRepository(settings.codex_runtime_sqlite_path)
        self._providers: list[HistoricalNewsProvider] = []

    async def reconcile(self, context: NodeContext) -> NodeResult | None:
        if context.node.receipt.get("child_run_id"):
            # Existing orchestrators recover their own frozen checkpoints; durable
            # internal turns reattach HTTP jobs instead of issuing new dispatches.
            return await self.execute(context)
        return None

    async def execute(self, context: NodeContext) -> NodeResult:
        settings = self.settings
        if not settings.codex_worker_bearer_token or not settings.codex_capability_secret:
            raise ValueError("initialization requires configured Codex Worker credentials")
        if not settings.event_library_root:
            raise ValueError("initialization requires a local event_library_root")
        worker = HttpCodexWorkerClient(
            settings.codex_worker_base_url,
            settings.codex_worker_bearer_token,
            capability_secret=settings.codex_capability_secret,
        )
        child_id = f"{context.run.initialization_id}-{context.node.key}"
        context.checkpoint(child_run_id=child_id)
        try:
            if context.node.key == "d1":
                return await self._d1(context, worker, child_id)
            if context.node.key in {"cdecr", "o2"}:
                return await self._events(context, worker)
            if context.node.key == "d2":
                return await self._d2(context, worker, child_id)
            if context.node.key == "d3":
                return await self._d3(context, worker, child_id)
            raise ValueError(f"unsupported research node: {context.node.key}")
        finally:
            await worker.aclose()
            for provider in self._providers:
                close = getattr(provider, "close", None)
                if close is not None:
                    close()

    async def _d1(
        self, context: NodeContext, worker: HttpCodexWorkerClient, child_id: str
    ) -> NodeResult:
        metrics = default_metric_registry()
        targets = collection_target_registry_for_lane(ResearchLane.GLOBAL_RESEARCH)
        tools = default_real_tool_registry(self.settings)
        orchestrator = CodexGlobalResearchOrchestrator(
            worker=worker,
            workspace=worker,
            repository=self.repository,
            horizontal_collector=HorizontalCollector(tools=tools, metrics=metrics, targets=targets),
            horizontal_compiler=HorizontalStateCompiler(metrics=metrics, targets=targets),
            model=self.settings.codex_model,
            model_provider=self.settings.codex_model_provider,
            effort=self.settings.codex_reasoning_effort,
            timeout_seconds=self.settings.codex_node_timeout_seconds,
            max_attempts=1,
            max_subagents=self.settings.codex_max_subagents,
        )
        bundle = await orchestrator.run(
            GlobalResearchRunRequest(
                run_id=child_id,
                ticker=context.run.ticker,
                cutoff_at=context.run.research_cutoff_at,
                research_brief=context.node.inputs.get(
                    "research_brief", "Initialize the ticker's Global Research context."
                ),
                company_name=context.node.inputs.get("company_name"),
            )
        )
        if bundle.status != "published":
            raise ValueError("D1 has no usable published handoff")
        return NodeResult(artifacts={"document1": {"run_id": child_id}})

    async def _events(self, context: NodeContext, worker: HttpCodexWorkerClient) -> NodeResult:
        settings = self.settings
        source_id = context.run.initialization_id
        root = context.repository.path.parent / "workspaces" / source_id
        from doxagent.cdecr_integration.contracts import TickerPipelineResult

        pipeline = (
            TickerPipelineResult.model_validate(context.dependency("cdecr").artifacts["cdecr"])
            if context.node.key == "o2"
            else None
        )
        providers: list[HistoricalNewsProvider] = []
        self._providers = providers
        if settings.finnhub_api_key:
            providers.append(FinnhubHistoricalNewsProvider(settings))
        if settings.benzinga_api_key:
            providers.append(BenzingaHistoricalNewsProvider(settings))
        from .cdecr_process import execute_cdecr

        coordinator = TickerCDECRPipelineCoordinator(
            registry_root=(
                Path(pipeline.job.registry_path).parents[2] if pipeline else root / "registry"
            ),
            state_root=root / "state",
            run_namespace=source_id + "-",
            event_library_root=settings.event_library_root or "",
            providers=providers,
            runtime_factory=build_cdecr_workflow_runner,
            cdecr_executor=lambda binding, messages, cutoff: execute_cdecr(
                context, binding, messages, cutoff
            ),
            o2_factory=lambda service: RemoteEventLibraryInitializer(
                worker=worker,
                workspace=worker,
                service=service,
                local_workspace_root=root / "o2",
                model=settings.codex_model,
                model_provider=settings.codex_model_provider,
                effort=settings.codex_reasoning_effort,
                timeout_seconds=settings.codex_node_timeout_seconds,
            ),
        )
        kwargs: dict[str, Any] = {
            "market": "US",
            "ticker": context.run.ticker,
            "as_of": context.run.research_cutoff_at,
            "export_dir": root / "exports",
        }
        if context.node.key == "cdecr":
            result = await coordinator.prepare_runtime_through_delta(**kwargs)
            if result.job.stage.value == "FINALIZED_NOOP":
                return NodeResult(
                    artifacts={"cdecr": result.model_dump(mode="json")},
                    quality_annotations=["CDECR_NOOP"],
                )
            if not result.job.runtime_snapshot_id or not result.delta_batch_id:
                raise ValueError("CDECR did not produce a frozen snapshot and delta batch")
            return NodeResult(artifacts={"cdecr": result.model_dump(mode="json")})
        assert pipeline is not None
        if pipeline.job.stage.value == "FINALIZED_NOOP":
            from doxagent.event_library.repository import EventLibraryRepository

            empty_version = EventLibraryRepository(
                pipeline.job.event_library_path
            ).ensure_empty_publication(context.run.ticker)
            return NodeResult(
                artifacts={"event_library": {"version": empty_version}},
                quality_annotations=["O2_NOOP"],
            )
        if context.node.inputs.get("_source_initialization"):
            # New O2 workspace/job state; the completed CDECR Registry stays frozen.
            pipeline = pipeline.model_copy(
                update={
                    "job": pipeline.job.model_copy(
                        update={
                            "o2_run_id": source_id + "-o2",
                            "published_library_version": None,
                            "thread_id": None,
                        }
                    )
                }
            )
        if coordinator.jobs.get(pipeline.job.job_id) is None:
            coordinator.jobs.save(pipeline.job)
        if (
            not pipeline.job.epoch_id
            or not pipeline.job.runtime_snapshot_id
            or not pipeline.delta_batch_id
        ):
            raise ValueError("CDECR handoff is missing its frozen snapshot references")
        d1_id = context.dependency("d1").artifacts["document1"]["run_id"]
        bundle = self.repository.get_bundle(d1_id)
        if (
            not isinstance(bundle, GlobalResearchBundle)
            or not bundle.citation_manifest
            or not bundle.published_at
        ):
            raise ValueError("D1 context is unavailable for O2")
        manifest = O2UpstreamContextManifest(
            ticker=context.run.ticker,
            unified_as_of=context.run.research_cutoff_at,
            d1_run_id=d1_id,
            d1_published_at=bundle.published_at,
            research_artifacts={
                k: bundle.reports[k].model_dump(mode="json") for k in ("c1", "c3", "c5")
            },
            entity_relations=[x.model_dump(mode="json") for x in bundle.entity_relations],
            future_nodes=[x.model_dump(mode="json") for x in bundle.future_nodes],
            citation_manifest=bundle.citation_manifest.model_dump(mode="json"),
            cdecr_epoch_id=pipeline.job.epoch_id,
            runtime_snapshot_id=pipeline.job.runtime_snapshot_id,
            delta_batch_id=pipeline.delta_batch_id,
        )
        result = await coordinator.run_o2_with_upstream_context(
            **kwargs,
            upstream_context_manifest=manifest.model_dump(mode="json"),
            resume_finalized_only=True,
            prepared_pipeline=pipeline,
        )
        version = result.published_library_version
        annotations = []
        if not version:
            from doxagent.event_library.repository import EventLibraryRepository

            # Only reached after a completed NOOP, never after an execution error.
            version = EventLibraryRepository(
                pipeline.job.event_library_path
            ).ensure_empty_publication(context.run.ticker)
            annotations.append("O2_NOOP")
        return NodeResult(
            artifacts={"event_library": {"version": version}}, quality_annotations=annotations
        )

    async def _d2(
        self, context: NodeContext, worker: HttpCodexWorkerClient, child_id: str
    ) -> NodeResult:
        version = context.dependency("o2").artifacts["event_library"]["version"]
        reader = PublishedEventLibraryReader(self.settings.event_library_root or "", market="US")
        tools = default_real_tool_registry(self.settings)
        orchestrator = CodexDocument2Orchestrator(
            worker=worker,
            workspace=worker,
            repository=self.repository,
            narrative_provider=DoxAtlasNarrativeReportProvider(tools),
            event_library_provider=PublishedEventLibraryProvider(reader, pinned_version=version),
            model=self.settings.codex_model,
            model_provider=self.settings.codex_model_provider,
            effort=self.settings.codex_reasoning_effort,
            timeout_seconds=self.settings.codex_node_timeout_seconds,
            max_attempts=1,
            max_subagents=self.settings.codex_max_subagents,
        )
        bundle = await orchestrator.run(
            Document2RunRequest(
                run_id=child_id,
                ticker=context.run.ticker,
                source_global_run_id=context.dependency("d1").artifacts["document1"]["run_id"],
                as_of=context.run.research_cutoff_at,
                reuse_published_partial=True,
            )
        )
        if (
            not isinstance(bundle, Document2Bundle)
            or bundle.status != "published"
            or not bundle.handoff
        ):
            raise ValueError("D2 has no published handoff")
        published = self.repository.get_published_document(
            child_id, bundle.handoff.document2_artifact_id
        )
        if published is None or published.content_text is None:
            raise ValueError("D2 published body is not available locally")
        path = self._artifact(context, "document2.json", published.content_text)
        return NodeResult(
            artifacts={"document2": {"run_id": child_id}, "document2_path": str(path)}
        )

    async def _d3(
        self, context: NodeContext, worker: HttpCodexWorkerClient, child_id: str
    ) -> NodeResult:
        from doxagent.workflows.codex_document3.repository import SQLiteDocument3PolicyRepository

        policies = SQLiteDocument3PolicyRepository(self.settings.codex_runtime_sqlite_path)
        policy = policies.reserved_policy(context.run.ticker, child_id)
        if policy is None:
            orchestrator = build_document3_orchestrator(self.settings, worker=worker)
            result = await orchestrator.initialize(
                ticker=context.run.ticker,
                run_id=child_id,
                document2_run_id=context.dependency("d2").artifacts["document2"]["run_id"],
                event_library_version=context.dependency("o2").artifacts["event_library"][
                    "version"
                ],
                cutoff_at=context.run.research_cutoff_at,
                enqueue_o4=False,
                candidate_publication=True,
            )
            if result.policy_set_version is None:
                raise ValueError("D3 has no usable Policy Set")
            policy = policies.get_version(context.run.ticker, result.policy_set_version)
        if policy is None:
            raise ValueError("D3 Policy Set is not available locally")
        path = self._artifact(context, "policy_set.json", policy.model_dump_json())
        return NodeResult(
            artifacts={
                "document3": {"run_id": child_id, "version": policy.policy_set_version},
                "policy_set_path": str(path),
                "document2_path": context.dependency("d2").artifacts["document2_path"],
            }
        )

    @staticmethod
    def _artifact(context: NodeContext, filename: str, text: str) -> Path:
        path = (
            context.repository.path.parent / "artifacts" / context.run.initialization_id / filename
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if json.loads(path.read_text(encoding="utf-8")) != json.loads(text):
                raise ValueError("immutable initialization artifact conflicts with existing body")
        else:
            import os

            temporary = path.with_suffix(f".{context.node.execution_id}.tmp")
            with temporary.open("w", encoding="utf-8", newline="") as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(path)
        return path
