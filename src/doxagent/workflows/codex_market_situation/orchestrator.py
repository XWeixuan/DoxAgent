"""Independent C2/O4 Market Situation Research DAG."""

from __future__ import annotations

import asyncio
from typing import Any

from doxagent.codex_runtime.schema import (
    ArtifactKind,
    ArtifactRef,
    CodexD1Node,
    MarketSituationBundle,
    MarketSituationHandoffV1,
    ResearchLane,
    WorkflowCheckpoint,
)
from doxagent.horizontal_collection.context import render_horizontal_context
from doxagent.workflows.codex_document1.orchestrator import CodexDocument1Orchestrator
from doxagent.workflows.codex_document1.schema import NodeOutput
from doxagent.workflows.codex_market_situation.assembler import assemble_market_situation
from doxagent.workflows.codex_market_situation.schema import MarketSituationRunRequest


class CodexMarketSituationOrchestrator(CodexDocument1Orchestrator):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(
            **kwargs,
            prompt_root="codex_assets/market_situation_v1",
            workflow_version="codex_market_situation_v1",
            research_lane=ResearchLane.MARKET_SITUATION_RESEARCH,
        )

    async def run(  # type: ignore[override]
        self, request: MarketSituationRunRequest
    ) -> MarketSituationBundle:
        existing = self._repository.get_bundle(request.run_id)
        if isinstance(existing, MarketSituationBundle) and existing.status == "published":
            return existing
        checkpoint = self._repository.get_checkpoint(request.run_id) or WorkflowCheckpoint(
            workflow_version=request.workflow_version,
            research_lane=request.research_lane,
            ticker=request.ticker,
            run_id=request.run_id,
        )
        if (
            checkpoint.workflow_version != request.workflow_version
            or checkpoint.research_lane is not request.research_lane
        ):
            raise ValueError("run_id belongs to a different workflow or research lane")
        self._recover_stale_attempts(request.run_id, checkpoint)
        self._repository.save_checkpoint(checkpoint)
        await self._event(
            request.run_id,
            "workflow.started",
            {"ticker": request.ticker, "research_lane": request.research_lane.value},
        )
        horizontal = await self._collect_or_restore_horizontal(request, checkpoint)
        base_context: dict[str, object] = {
            "ticker": request.ticker,
            "company_name": request.company_name,
            "research_brief": request.research_brief,
            "base_context": request.base_context,
        }
        outputs: dict[CodexD1Node, NodeOutput] = {}
        reports: dict[str, ArtifactRef] = {}
        specs = {
            CodexD1Node.C2: (
                dict(base_context),
                render_horizontal_context(horizontal, role="c2"),
            ),
            CodexD1Node.O4: (
                dict(base_context),
                render_horizontal_context(horizontal, role="o4"),
            ),
        }
        results = await asyncio.gather(
            *(
                self._execute_or_partial(
                    request, node, context, checkpoint, horizontal=horizontal_input
                )
                for node, (context, horizontal_input) in specs.items()
            )
        )
        for node, (output, reference) in zip(specs, results, strict=True):
            outputs[node] = output
            if reference:
                reports[node.value] = reference

        normalized = self._normalize_candidates(
            outputs,
            reports,
            horizontal,
            nodes=(CodexD1Node.C2, CodexD1Node.O4),
        )
        await self._write_normalized(request.run_id, normalized)
        self._complete_checkpoint(checkpoint, CodexD1Node.AGENT_NORMALIZATION)
        if checkpoint.failed_nodes:
            failed = [node.value for node in checkpoint.failed_nodes]
            await self._event(request.run_id, "workflow.failed", {"failed_nodes": failed})
            raise RuntimeError("Market Situation publish blocked by: " + ", ".join(failed))

        citation_nodes = (CodexD1Node.C2, CodexD1Node.O4)
        node_manifests = []
        for node in citation_nodes:
            reference = reports[node.value]
            manifest = self._repository.get_citation_manifest(
                request.run_id, reference.artifact_id
            )
            if manifest is None:
                raise RuntimeError(f"citation manifest missing for {node.value}")
            node_manifests.append(manifest)
        citation_plan = self._citations.plan_aggregate(node_manifests)
        aggregate_outputs = dict(outputs)
        for node in citation_nodes:
            reference = reports[node.value]
            aggregate_outputs[node] = outputs[node].model_copy(
                update={
                    "report_markdown": citation_plan.rewrite(
                        attempt_id=reference.attempt_id,
                        markdown=outputs[node].report_markdown,
                    )
                }
            )
        document = assemble_market_situation(request.ticker, aggregate_outputs)
        final_ref = await self._write_final_document(
            request.run_id,
            document,
            relative_path="artifacts/market_situation/market_situation_v1.md",
        )
        reports["market_situation"] = final_ref
        citation_manifest = self._citations.commit_aggregate(
            run_id=request.run_id,
            artifact_id=final_ref.artifact_id,
            plan=citation_plan,
        )
        citation_ref = await self._write_json_artifact(
            run_id=request.run_id,
            node=CodexD1Node.ASSEMBLE,
            attempt_id=final_ref.attempt_id,
            relative_path="artifacts/market_situation/citation_manifest.json",
            content=citation_manifest.model_dump_json(indent=2),
            kind=ArtifactKind.MANIFEST,
        )
        self._complete_checkpoint(checkpoint, CodexD1Node.ASSEMBLE)
        published_at, published = await self._publish_references(
            request.run_id, [*reports.values(), citation_ref]
        )
        by_id = {item.artifact_id: item for item in published}
        reports = {name: by_id[item.artifact_id] for name, item in reports.items()}
        citation_ref = by_id[citation_ref.artifact_id]
        final_ref = reports["market_situation"]
        handoff = MarketSituationHandoffV1(
            run_id=request.run_id,
            ticker=request.ticker,
            document_artifact_id=final_ref.artifact_id,
            citation_manifest_artifact_id=citation_ref.artifact_id,
            published_at=published_at,
        )
        bundle = MarketSituationBundle(
            run_id=request.run_id,
            ticker=request.ticker,
            status="published",
            reports=reports,
            citation_manifest=citation_manifest,
            handoff=handoff,
            published_at=published_at,
        )
        self._repository.save_bundle(bundle)
        self._complete_checkpoint(checkpoint, CodexD1Node.PUBLISH)
        self._repository.mark_run_published(request.run_id, published_at)
        await self._event(
            request.run_id,
            "workflow.published",
            {"artifact_id": final_ref.artifact_id, "research_lane": request.research_lane.value},
        )
        return bundle
