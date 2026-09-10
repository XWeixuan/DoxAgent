"""Explicit single internal workflow rerun in a pre-node workspace fork.

Only the selected runner is invoked. No parent workflow, publication, downstream
invalidation or activation is implicit in this operation.
"""

from __future__ import annotations

import hashlib
from typing import Any

from doxagent.codex_runtime.client import HttpCodexWorkerClient
from doxagent.codex_runtime.repository import SQLiteCodexRuntimeRepository
from doxagent.settings import DoxAgentSettings

from .invocation import decode
from .schema import NodeResult
from .service import NodeContext
from .substeps import _codec, settle_stage


class InternalNodeAdapter:
    def __init__(self, settings: DoxAgentSettings) -> None:
        self.settings = settings.model_copy(
            update={
                "codex_runtime_storage_mode": "sqlite",
                "codex_published_storage_url": None,
                "codex_published_storage_secret_key": None,
            }
        )

    async def reconcile(self, context: NodeContext) -> NodeResult | None:
        return await self.execute(context)

    async def execute(self, context: NodeContext) -> NodeResult:
        settings = self.settings
        worker = HttpCodexWorkerClient(
            settings.codex_worker_base_url,
            settings.codex_worker_bearer_token or "",
            capability_secret=settings.codex_capability_secret,
        )
        try:
            return await self._run(context, worker)
        finally:
            await worker.aclose()

    async def _run(self, context: NodeContext, worker: HttpCodexWorkerClient) -> NodeResult:
        snapshot = context.node.inputs["_snapshot"]
        kind = snapshot["kind"]
        run_id = "rerun-" + hashlib.sha256(context.run.initialization_id.encode()).hexdigest()[:24]
        await worker.fork_snapshot(snapshot["run_id"], snapshot["snapshot_id"], run_id)
        context.checkpoint(child_run_id=run_id)
        arguments = decode(context.node.inputs["_selected_invocation"])
        for field in ("run_id", "persistence_run_id", "workspace_run_id"):
            if field in arguments:
                arguments[field] = run_id
        if "thread_id" in arguments:
            arguments["thread_id"] = None
        for field in ("request", "checkpoint"):
            if field in arguments:
                arguments[field] = arguments[field].model_copy(update={"run_id": run_id})
        owner, method = self._runner(kind, worker, context)
        if kind == "d3":
            await owner._agent.prepare_node_contracts(
                run_id=run_id, node=arguments["node"], output_model=arguments["output_model"]
            )
            baseline = await owner._workspace_snapshot(run_id)
        result = await method(**arguments)
        warnings: list[str] = []
        if kind == "d3":
            try:
                findings = await owner._assert_agent_write_boundary(
                    run_id, initialize=True, baseline=baseline
                )
                await owner._verify_input_manifest(run_id)
                prepared = await self._d3_inputs(owner, run_id)
                if arguments["node"].value == "d3_o3_trigger_calibration":
                    findings.extend(
                        await owner._validate_stage_a_checkpoint(
                            run_id, prepared, require_pending_worklist=True
                        )
                    )
                else:
                    findings.extend(await owner._validate_compile_checkpoint(run_id, prepared))
                warnings = [str(f.code) for f in findings]
                settle_stage(arguments["node"].value)
            except Exception as exc:
                settle_stage(arguments["node"].value, error=str(exc))
                raise
        return NodeResult(
            artifacts={
                "workspace_run_id": run_id,
                "source_node": context.node.inputs["_source_node"],
                "return": _codec(kind, arguments).dump_python(
                    result, mode="json", serialize_as_any=True
                ),
            },
            quality_annotations=warnings,
        )

    def _runner(
        self, kind: str, worker: HttpCodexWorkerClient, context: NodeContext
    ) -> tuple[Any, Any]:
        settings = self.settings
        repository = SQLiteCodexRuntimeRepository(settings.codex_runtime_sqlite_path)
        common: dict[str, Any] = dict(
            worker=worker,
            workspace=worker,
            model=settings.codex_model,
            model_provider=settings.codex_model_provider,
            effort=settings.codex_reasoning_effort,
            timeout_seconds=settings.codex_node_timeout_seconds,
        )
        if kind in {"d1", "d1_assemble", "d1_publish"}:
            from doxagent.codex_runtime.schema import ResearchLane
            from doxagent.horizontal_collection.collector import HorizontalCollector
            from doxagent.horizontal_collection.compiler import HorizontalStateCompiler
            from doxagent.horizontal_collection.registry import (
                collection_target_registry_for_lane,
                default_metric_registry,
            )
            from doxagent.tools.factory import default_real_tool_registry
            from doxagent.workflows.codex_global_research import CodexGlobalResearchOrchestrator

            metrics = default_metric_registry()
            targets = collection_target_registry_for_lane(ResearchLane.GLOBAL_RESEARCH)
            owner = CodexGlobalResearchOrchestrator(
                **common,
                repository=repository,
                horizontal_collector=HorizontalCollector(
                    tools=default_real_tool_registry(settings), metrics=metrics, targets=targets
                ),
                horizontal_compiler=HorizontalStateCompiler(metrics=metrics, targets=targets),
                max_attempts=1,
            )
            return owner, {
                "d1": owner._execute_node,
                "d1_assemble": owner._write_final_document,
                "d1_publish": owner._publish_references,
            }[kind]
        if kind == "d2":
            from doxagent.workflows.codex_document2.runner import Document2TurnRunner

            runner = Document2TurnRunner(
                **common, repository=repository, max_subagents=settings.codex_max_subagents
            )
            return runner, runner.run
        if kind == "d3":
            from doxagent.workflows.codex_document3.service import build_document3_orchestrator

            orchestrator = build_document3_orchestrator(settings, worker=worker)
            return orchestrator, orchestrator._agent._run_with_resume
        if kind in {"o2", "o2_repair"}:
            from doxagent.event_library.repository import EventLibraryRepository
            from doxagent.event_library.service import EventLibraryService
            from doxagent.workflows.codex_event_library.remote_runner import (
                RemoteEventLibraryInitializer,
            )

            runner_o2 = RemoteEventLibraryInitializer(
                **common,
                service=EventLibraryService(
                    EventLibraryRepository(
                        context.repository.path.parent / "isolated" / context.run.initialization_id
                    )
                ),
                local_workspace_root=context.repository.path.parent / "isolated",
            )
            return runner_o2, (
                runner_o2._execute_phase if kind == "o2" else runner_o2._execute_repair
            )
        raise ValueError(f"unsupported internal workflow: {kind}")

    @staticmethod
    async def _d3_inputs(owner: Any, run_id: str) -> Any:
        from doxagent.workflows.codex_document2.schema import Document2Document
        from doxagent.workflows.codex_document3.inputs import PreparedDocument3Inputs
        from doxagent.workflows.codex_document3.schema import Document3InitializeTask, PolicySet

        task = await owner._read_json(
            run_id, "context/document3/task.json", Document3InitializeTask
        )
        document = await owner._read_json(
            run_id, "context/document3/document2.json", Document2Document
        )
        previous = await owner._agent.workspace.read_text(
            run_id, "context/document3/previous_policy_set.json"
        )
        reference = await owner._agent.workspace.read_text(
            run_id, "context/document3/reference_event_view.md"
        )
        return PreparedDocument3Inputs(
            ticker=task.ticker,
            document2=document,
            document2_ref=task.document2_ref,
            expected_gap_refs=[
                (s.shell_id, u.expectation_id, g.gap_id)
                for s in document.shells
                for u in s.units
                for g in u.potential_gaps
            ],
            failed_shells=task.failed_shells,
            event_library_ref=task.event_library_ref,
            reference_view=reference.content or "",
            previous_policy_set=None
            if (previous.content or "null").strip() == "null"
            else PolicySet.model_validate_json(previous.content or ""),
            warnings=task.warnings,
        )
