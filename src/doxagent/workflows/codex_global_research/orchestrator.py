"""Global Research with a shared C4 thread and independent C4 products."""

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
            {**base_context, "task": "pre-scan initial entity relations only"},
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

        missing = [
            node.value
            for node in (CodexD1Node.C1, CodexD1Node.C3, CodexD1Node.C5)
            if node.value not in reports or not outputs[node].report_markdown.strip()
        ]
        if missing:
            await self._event(request.run_id, "workflow.failed", {"failed_nodes": missing})
            raise RuntimeError("Global Research publish blocked by: " + ", ".join(missing))

        research_context = {
            **base_context,
            "c4_pre_scan": self._handoff_output(c4_pre, c4_pre_ref),
            "c1_report": self._handoff_output(outputs[CodexD1Node.C1], reports.get("c1")),
            "c3_report": self._handoff_output(outputs[CodexD1Node.C3], reports.get("c3")),
            "c5_report": self._handoff_output(c5, c5_ref),
            "horizontal_collection": horizontal.model_dump(mode="json"),
            "agent_observations": self._observation_handoffs(normalized),
            "upstream_artifacts": [
                item.model_dump(mode="json")
                for item in self._repository.list_artifacts(request.run_id)
                if item.attempt_id in {reference.attempt_id for reference in reports.values()}
            ],
        }
        c4_future, c4_future_ref = await self._execute_or_partial(
            request,
            CodexD1Node.C4F_FUTURE_NODES,
            {
                **research_context,
                "output_contract": "return Future Nodes only; do not modify Entity Map",
            },
            checkpoint,
        )
        if c4_future_ref:
            reports[CodexD1Node.C4F_FUTURE_NODES.value] = c4_future_ref
        c4_formal, c4_formal_ref = await self._execute_after_c4_stage(
            request,
            CodexD1Node.C4E_FORMAL_SCAN,
            {
                **research_context,
                "future_nodes": self._handoff_output(c4_future, c4_future_ref),
                "output_contract": (
                    "return a complete Entity Map snapshot, not a delta; no Future Nodes"
                ),
            },
            checkpoint,
            predecessor=c4_future_ref,
        )
        if c4_formal_ref:
            reports[CodexD1Node.C4E_FORMAL_SCAN.value] = c4_formal_ref
        c4_network, c4_network_ref = await self._execute_after_c4_stage(
            request,
            CodexD1Node.C4E_NETWORK_BUILD,
            {
                **research_context,
                "future_nodes": self._handoff_output(c4_future, c4_future_ref),
                "c4e_formal_scan": self._handoff_output(c4_formal, c4_formal_ref),
                "output_contract": (
                    "continue deep network research, including web search and new discoveries; "
                    "return the complete Markdown Network Research Report as a JSON string"
                ),
            },
            checkpoint,
            predecessor=c4_formal_ref,
        )
        if c4_network_ref:
            reports[CodexD1Node.C4E_NETWORK_BUILD.value] = c4_network_ref
        c4_product_status = {
            name: ("failed" if reference is None else "available" if content else "empty")
            for name, reference, content in (
                ("future_nodes", c4_future_ref, c4_future.future_nodes),
                ("entity_relations", c4_formal_ref, c4_formal.entity_relations),
                ("entity_network_report", c4_network_ref, c4_network.report_markdown.strip()),
            )
        }

        if checkpoint.failed_nodes or any(
            value != "available" for value in c4_product_status.values()
        ):
            await self._event(
                request.run_id,
                "workflow.partial",
                {
                    "optional_failed_nodes": [n.value for n in checkpoint.failed_nodes],
                    "c4_product_status": c4_product_status,
                },
            )

        citation_nodes = (CodexD1Node.C1, CodexD1Node.C3, CodexD1Node.C5)
        node_manifests = []
        for node in citation_nodes:
            reference = reports[node.value]
            manifest = self._repository.get_citation_manifest(request.run_id, reference.artifact_id)
            if manifest is None:
                manifest = await self._node_runner._promote_attempt_citations(
                    run_id=request.run_id,
                    attempt_id=reference.attempt_id,
                    artifact_id=reference.artifact_id,
                    anchor=node.value,
                    markdown=outputs[node].model_dump_json(by_alias=True),
                )
            node_manifests.append(manifest)
        citation_plan = self._citations.plan_aggregate(node_manifests, allow_unresolved=True)
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
        citation_manifest = self._repository.get_citation_manifest(
            request.run_id, final_ref.artifact_id
        ) or self._citations.commit_aggregate(
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
            entity_relations=c4_formal.entity_relations,
            future_nodes=c4_future.future_nodes,
            entity_network_report=c4_network.report_markdown,
            c4_product_status=c4_product_status,
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

    async def _execute_after_c4_stage(
        self,
        request: GlobalResearchRunRequest,
        node: CodexD1Node,
        payload: dict[str, object],
        checkpoint: WorkflowCheckpoint,
        *,
        predecessor: ArtifactRef | None,
    ) -> tuple[NodeOutput, ArtifactRef | None]:
        if predecessor is not None:
            return await self._execute_or_partial(request, node, payload, checkpoint)
        # An empty validated snapshot is complete; a failed predecessor is not.
        # Do not run a stage whose required upstream product was never accepted.
        self._fail_checkpoint(checkpoint, node)
        warning = "preceding C4 stage failed; required upstream product unavailable"
        await self._event(request.run_id, "node.skipped", {"node": node.value, "reason": warning})
        return NodeOutput(status="failed", warnings=[warning]), None
