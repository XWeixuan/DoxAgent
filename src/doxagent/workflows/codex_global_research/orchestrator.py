"""C4 pre-scan -> C1/C3 -> C5 -> C4 enrichment Global Research DAG."""

from __future__ import annotations

import asyncio
from typing import Any

from doxagent.codex_runtime.schema import (
    ArtifactKind,
    ArtifactRef,
    CodexD1Node,
    GlobalResearchBundle,
    GlobalResearchHandoffV1,
    ResearchLane,
    WorkflowCheckpoint,
)
from doxagent.horizontal_collection.context import render_horizontal_context
from doxagent.workflows.codex_document1.orchestrator import CodexDocument1Orchestrator
from doxagent.workflows.codex_document1.schema import NodeOutput
from doxagent.workflows.codex_global_research.assembler import assemble_global_research
from doxagent.workflows.codex_global_research.schema import GlobalResearchRunRequest


class CodexGlobalResearchOrchestrator(CodexDocument1Orchestrator):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(
            **kwargs,
            prompt_root="prompts/codex_v2/document1/global_research",
            workflow_version="codex_global_research_v1",
            research_lane=ResearchLane.GLOBAL_RESEARCH,
        )

    async def run(  # type: ignore[override]
        self, request: GlobalResearchRunRequest
    ) -> GlobalResearchBundle:
        existing = self._repository.get_bundle(request.run_id)
        if isinstance(existing, GlobalResearchBundle) and existing.status == "published":
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

        c4_pre, c4_pre_ref = await self._execute_or_partial(
            request,
            CodexD1Node.C4_PRE_SCAN,
            {**base_context, "task": "pre-scan entity relations and preliminary future nodes"},
            checkpoint,
        )
        outputs[CodexD1Node.C4_PRE_SCAN] = c4_pre
        if c4_pre_ref:
            reports[CodexD1Node.C4_PRE_SCAN.value] = c4_pre_ref
        relation_only_pre = c4_pre.model_copy(update={"future_nodes": []})

        specs = {
            CodexD1Node.C1: (
                {
                    **base_context,
                    "c4_pre_scan": self._handoff_output(relation_only_pre, c4_pre_ref),
                },
                render_horizontal_context(horizontal, role="c1"),
            ),
            CodexD1Node.C3: (
                {
                    **base_context,
                    "c4_pre_scan": self._handoff_output(relation_only_pre, c4_pre_ref),
                },
                render_horizontal_context(horizontal, role="c3"),
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
            nodes=(CodexD1Node.C1, CodexD1Node.C3),
        )
        await self._write_normalized(request.run_id, normalized)
        self._complete_checkpoint(checkpoint, CodexD1Node.AGENT_NORMALIZATION)

        c5, c5_ref = await self._execute_or_partial(
            request,
            CodexD1Node.C5,
            {
                **base_context,
                "c1": self._handoff_output(outputs[CodexD1Node.C1], reports.get("c1")),
                "c3": self._handoff_output(outputs[CodexD1Node.C3], reports.get("c3")),
                "agent_observations": self._observation_handoffs(normalized),
            },
            checkpoint,
            horizontal=render_horizontal_context(horizontal, role="c5"),
        )
        outputs[CodexD1Node.C5] = c5
        if c5_ref:
            reports[CodexD1Node.C5.value] = c5_ref

        c4_enriched, c4_enriched_ref = await self._execute_or_partial(
            request,
            CodexD1Node.C4_ENRICHMENT,
            {
                **base_context,
                "c4_pre_scan": self._handoff_output(c4_pre, c4_pre_ref),
                "c1_report": self._handoff_output(outputs[CodexD1Node.C1], reports.get("c1")),
                "c3_report": self._handoff_output(outputs[CodexD1Node.C3], reports.get("c3")),
                "c5_report": self._handoff_output(c5, c5_ref),
                "agent_observations": self._observation_handoffs(normalized),
                "output_contract": "return the complete merged C4 snapshot",
            },
            checkpoint,
        )
        outputs[CodexD1Node.C4_ENRICHMENT] = c4_enriched
        if c4_enriched_ref:
            reports[CodexD1Node.C4_ENRICHMENT.value] = c4_enriched_ref

        if checkpoint.failed_nodes:
            failed = [node.value for node in checkpoint.failed_nodes]
            await self._event(request.run_id, "workflow.failed", {"failed_nodes": failed})
            raise RuntimeError("Global Research publish blocked by: " + ", ".join(failed))

        citation_nodes = (CodexD1Node.C1, CodexD1Node.C3, CodexD1Node.C5)
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
        document = assemble_global_research(request.ticker, aggregate_outputs)
        final_ref = await self._write_final_document(
            request.run_id,
            document,
            relative_path="artifacts/global_research/global_research_v1.md",
        )
        reports["global_research"] = final_ref
        citation_manifest = self._citations.commit_aggregate(
            run_id=request.run_id,
            artifact_id=final_ref.artifact_id,
            plan=citation_plan,
        )
        citation_ref = await self._write_json_artifact(
            run_id=request.run_id,
            node=CodexD1Node.ASSEMBLE,
            attempt_id=final_ref.attempt_id,
            relative_path="artifacts/global_research/citation_manifest.json",
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
        final_ref = reports["global_research"]
        handoff = GlobalResearchHandoffV1(
            run_id=request.run_id,
            ticker=request.ticker,
            document_artifact_id=final_ref.artifact_id,
            citation_manifest_artifact_id=citation_ref.artifact_id,
            published_at=published_at,
        )
        bundle = GlobalResearchBundle(
            run_id=request.run_id,
            ticker=request.ticker,
            status="published",
            reports=reports,
            entity_relations=c4_enriched.entity_relations,
            future_nodes=c4_enriched.future_nodes,
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
