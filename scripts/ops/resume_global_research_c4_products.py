"""Resume only missing C4 products of an already published Global Research run.

The original C1/C3/C5, horizontal collection, and document handoff remain frozen.
This is for a partial publication whose C4 products failed before acceptance.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import datetime

from doxagent.codex_runtime.client import HttpCodexWorkerClient
from doxagent.codex_runtime.repository import SQLiteCodexRuntimeRepository
from doxagent.codex_runtime.schema import (
    ArtifactKind,
    CodexD1Node,
    GlobalResearchBundle,
    NormalizedAgentObservation,
    ResearchLane,
)
from doxagent.horizontal_collection.collector import HorizontalCollector
from doxagent.horizontal_collection.compiler import HorizontalStateCompiler
from doxagent.horizontal_collection.registry import (
    collection_target_registry_for_lane,
    default_metric_registry,
)
from doxagent.horizontal_collection.schema import HorizontalCollectionBundle
from doxagent.settings import DoxAgentSettings
from doxagent.tools.factory import default_real_tool_registry
from doxagent.workflows.codex_document1.schema import NodeOutput
from doxagent.workflows.codex_global_research import (
    CodexGlobalResearchOrchestrator,
    GlobalResearchRunRequest,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--as-of", required=True, type=datetime.fromisoformat)
    parser.add_argument("--check", action="store_true")
    return parser.parse_args()


async def _read_verified(worker, run_id, reference) -> str:
    file = await worker.read_text(run_id, reference.relative_path)
    content = file.content or ""
    if hashlib.sha256(content.encode("utf-8")).hexdigest() != reference.sha256:
        raise ValueError(f"source artifact checksum mismatch: {reference.relative_path}")
    return content


async def _run(args: argparse.Namespace) -> dict:
    if args.as_of.tzinfo is None:
        raise ValueError("--as-of must include a timezone")
    settings = DoxAgentSettings()
    if not settings.codex_runtime_sqlite_path.endswith("/mu-v21-test/research.sqlite3"):
        raise ValueError("refusing to mutate a non-test runtime database")
    if "doxagent-mu-v21-worker" not in settings.codex_worker_base_url:
        raise ValueError("C4 resume requires the isolated v2.1 Worker")
    if (settings.codex_model, settings.codex_reasoning_effort) != ("gpt-6-sol", "high"):
        raise ValueError("C4 resume requires gpt-6-sol / high")
    repository = SQLiteCodexRuntimeRepository(settings.codex_runtime_sqlite_path)
    bundle = repository.get_bundle(args.run_id)
    checkpoint = repository.get_checkpoint(args.run_id)
    if not isinstance(bundle, GlobalResearchBundle) or bundle.status != "published":
        raise ValueError("the original published Global Research bundle is required")
    if checkpoint is None or checkpoint.current_nodes:
        raise ValueError("the original run must be quiescent")
    if any(
        node not in checkpoint.completed_nodes
        for node in (
            CodexD1Node.PROGRAM_COLLECTION,
            CodexD1Node.C4_PRE_SCAN,
            CodexD1Node.C1,
            CodexD1Node.C3,
            CodexD1Node.C5,
            CodexD1Node.PUBLISH,
        )
    ):
        raise ValueError("required upstream nodes were not completed")
    request = GlobalResearchRunRequest(
        run_id=args.run_id,
        ticker=bundle.ticker,
        cutoff_at=args.as_of,
        research_brief="Initialize the ticker's Global Research context.",
    )
    worker = HttpCodexWorkerClient(
        settings.codex_worker_base_url,
        settings.codex_worker_bearer_token,
        capability_secret=settings.codex_capability_secret,
    )
    try:
        metrics = default_metric_registry()
        targets = collection_target_registry_for_lane(ResearchLane.GLOBAL_RESEARCH)
        tools = default_real_tool_registry(settings)
        orchestrator = CodexGlobalResearchOrchestrator(
            worker=worker,
            workspace=worker,
            repository=repository,
            horizontal_collector=HorizontalCollector(tools=tools, metrics=metrics, targets=targets),
            horizontal_compiler=HorizontalStateCompiler(metrics=metrics, targets=targets),
            model=settings.codex_model,
            model_provider=settings.codex_model_provider,
            effort=settings.codex_reasoning_effort,
            timeout_seconds=settings.codex_node_timeout_seconds,
            max_attempts=1,
            max_subagents=settings.codex_max_subagents,
        )
        artifacts = repository.list_artifacts(args.run_id, limit=500)

        async def output_for(reference):
            completion = next(
                (
                    item
                    for item in artifacts
                    if item.attempt_id == reference.attempt_id
                    and item.kind is ArtifactKind.STRUCTURED_COMPLETION
                ),
                None,
            )
            if completion is None:
                raise ValueError(f"missing completion: {reference.relative_path}")
            await _read_verified(worker, args.run_id, reference)
            return NodeOutput.model_validate_json(
                await _read_verified(worker, args.run_id, completion)
            )

        reports = {name: bundle.reports[name] for name in ("c4_pre_scan", "c1", "c3", "c5")}
        outputs = {name: await output_for(reference) for name, reference in reports.items()}
        horizontal_ref = next(
            (
                item
                for item in artifacts
                if item.node is CodexD1Node.PROGRAM_COLLECTION
                and item.kind is ArtifactKind.BUNDLE
                and item.relative_path.startswith("context/ca/hc-")
            ),
            None,
        )
        normalized_ref = next(
            (
                item
                for item in artifacts
                if item.node is CodexD1Node.AGENT_NORMALIZATION
                and item.kind is ArtifactKind.CONTEXT
                and item.relative_path.startswith("context/ca/ao-")
            ),
            None,
        )
        if horizontal_ref is None or normalized_ref is None:
            raise ValueError("horizontal or normalized context artifact is missing")
        horizontal = HorizontalCollectionBundle.model_validate_json(
            await _read_verified(worker, args.run_id, horizontal_ref)
        )
        normalized = [
            NormalizedAgentObservation.model_validate(item)
            for item in json.loads(await _read_verified(worker, args.run_id, normalized_ref))
        ]
        base_context = {
            "ticker": bundle.ticker,
            "company_name": None,
            "research_brief": request.research_brief,
            "base_context": {},
        }
        context = {
            **base_context,
            "c4_pre_scan": orchestrator._handoff_output(
                outputs["c4_pre_scan"], reports["c4_pre_scan"]
            ),
            "c1_report": orchestrator._handoff_output(outputs["c1"], reports["c1"]),
            "c3_report": orchestrator._handoff_output(outputs["c3"], reports["c3"]),
            "c5_report": orchestrator._handoff_output(outputs["c5"], reports["c5"]),
            "horizontal_collection": horizontal.model_dump(mode="json"),
            "agent_observations": orchestrator._observation_handoffs(normalized),
            "upstream_artifacts": [
                item.model_dump(mode="json")
                for item in artifacts
                if item.attempt_id in {ref.attempt_id for ref in reports.values()}
            ],
        }
        if args.check:
            return {
                "run_id": args.run_id,
                "source_nodes": list(reports),
                "horizontal_state_values": len(horizontal.state_values),
                "normalized_observations": len(normalized),
                "c4_product_status": bundle.c4_product_status,
                "model": settings.codex_model,
                "effort": settings.codex_reasoning_effort,
            }

        async def resume(node, payload):
            completed = (
                next(
                    (
                        item
                        for item in reversed(artifacts)
                        if item.node is node and item.kind is ArtifactKind.REPORT
                    ),
                    None,
                )
                if node in checkpoint.completed_nodes
                else None
            )
            if completed is not None:
                return await output_for(completed), completed
            output, reference = await orchestrator._execute_or_partial(
                request, node, payload, checkpoint
            )
            if reference is None:
                raise RuntimeError(f"{node.value} failed; inspect its checkpoint and attempt")
            artifacts[:] = repository.list_artifacts(args.run_id, limit=500)
            return output, reference

        future, future_ref = await resume(
            CodexD1Node.C4F_FUTURE_NODES,
            {**context, "output_contract": "return Future Nodes only; do not modify Entity Map"},
        )
        formal, formal_ref = await resume(
            CodexD1Node.C4E_FORMAL_SCAN,
            {
                **context,
                "future_nodes": orchestrator._handoff_output(future, future_ref),
                "output_contract": (
                    "return a complete Entity Map snapshot, not a delta; no Future Nodes"
                ),
            },
        )
        network, network_ref = await resume(
            CodexD1Node.C4E_NETWORK_BUILD,
            {
                **context,
                "future_nodes": orchestrator._handoff_output(future, future_ref),
                "c4e_formal_scan": orchestrator._handoff_output(formal, formal_ref),
                "output_contract": (
                    "continue deep network research, including web search and new discoveries; "
                    "return the complete Markdown Network Research Report as a JSON string"
                ),
            },
        )
        status = {
            "future_nodes": "available" if future.future_nodes else "empty",
            "entity_relations": "available" if formal.entity_relations else "empty",
            "entity_network_report": "available" if network.report_markdown.strip() else "empty",
        }
        _, published = await orchestrator._publish_references(
            args.run_id, [future_ref, formal_ref, network_ref]
        )
        repaired = GlobalResearchBundle.model_validate(
            bundle.model_copy(
                update={
                    "reports": {
                        **bundle.reports,
                        **dict(
                            zip(
                                ("c4f_future_nodes", "c4e_formal_scan", "c4e_network_build"),
                                published,
                                strict=True,
                            )
                        ),
                    },
                    "future_nodes": future.future_nodes,
                    "entity_relations": formal.entity_relations,
                    "entity_network_report": network.report_markdown,
                    "c4_product_status": status,
                }
            ).model_dump(mode="json")
        )
        repository.save_bundle(repaired)
        await orchestrator._event(
            args.run_id, "workflow.c4_products_repaired", {"c4_product_status": status}
        )
        return {"run_id": args.run_id, "status": repaired.status, "c4_product_status": status}
    finally:
        await worker.aclose()


if __name__ == "__main__":
    print(json.dumps(asyncio.run(_run(_arguments())), ensure_ascii=False))
