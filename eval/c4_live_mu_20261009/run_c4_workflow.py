"""Run the real Global Research C4 node chain against frozen MU D1 inputs.

This is a local workflow-node run, not the separate Pilot case machinery. C1/C3/C5
are supplied from the same frozen D1 asset set used by D2/D3 v2.1 validation.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import secrets
from datetime import UTC, datetime
from pathlib import Path

from doxagent.codex_runtime.repository import SQLiteCodexRuntimeRepository
from doxagent.codex_runtime.schema import CodexD1Node, ResearchLane, WorkflowCheckpoint
from doxagent.codex_worker.jobs import WorkerJobManager
from doxagent.codex_worker.local_client import EmbeddedCodexWorkerClient, LocalWorkspaceClient
from doxagent.codex_worker.schema import WorkerRunRequest
from doxagent.codex_worker.sdk_runtime import OpenAICodexRuntime
from doxagent.codex_worker.workspace_store import LocalWorkspaceStore
from doxagent.workflows.codex_document1.attempt_bundle import AttemptBundleSeeder
from doxagent.workflows.codex_document1.schema import NodeOutput
from doxagent.workflows.codex_global_research.orchestrator import CodexGlobalResearchOrchestrator
from doxagent.workflows.codex_global_research.schema import GlobalResearchRunRequest

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
SOURCE = Path(
    "D:/DoxAgentPilot/cases/document2/_sources/init-mu-1f3e9130ae304b01a7fdf2991a35e028-d1"
)
RUN_ID = "c4-mu-local-20261009-04"
NODES = (
    CodexD1Node.C4_PRE_SCAN,
    CodexD1Node.C4F_FUTURE_NODES,
    CodexD1Node.C4E_FORMAL_SCAN,
    CodexD1Node.C4E_NETWORK_BUILD,
)
SKILLS = {
    CodexD1Node.C4_PRE_SCAN: "c4e_pre_scan.md",
    CodexD1Node.C4F_FUTURE_NODES: "c4f_future_node.md",
    CodexD1Node.C4E_FORMAL_SCAN: "c4e_formal-scan.md",
    CodexD1Node.C4E_NETWORK_BUILD: "c4e_network-build.md",
}
SCHEMAS = {
    CodexD1Node.C4_PRE_SCAN: "c4e_relations.schema.json",
    CodexD1Node.C4F_FUTURE_NODES: "c4f_future_nodes.schema.json",
    CodexD1Node.C4E_FORMAL_SCAN: "c4e_relations.schema.json",
    CodexD1Node.C4E_NETWORK_BUILD: "c4e_network-build.schema.json",
}


class FreshSdkRuntime:
    """Mirror production C4 capsule release while preserving the exact test model."""

    def __init__(self) -> None:
        self.secret = secrets.token_urlsafe(48)
        self.active: dict[str, OpenAICodexRuntime] = {}
        self.receipt_callback = None

    def _new(self) -> OpenAICodexRuntime:
        return OpenAICodexRuntime(
            capability_secret=self.secret,
            preserve_requested_model=True,
        )

    async def probe(self) -> dict[str, object]:
        runtime = self._new()
        try:
            return await runtime.probe()
        finally:
            await runtime.close()

    async def start(self, request: WorkerRunRequest, cwd: Path):
        runtime = self._new()
        self.active[request.attempt_id] = runtime
        try:
            return await runtime.start(request, cwd)
        except BaseException:
            self.active.pop(request.attempt_id, None)
            await runtime.close()
            raise

    async def release(self, request: WorkerRunRequest) -> None:
        runtime = self.active.pop(request.attempt_id, None)
        if runtime is not None:
            await runtime.close()

    async def close(self) -> None:
        for runtime in list(self.active.values()):
            await runtime.close()
        self.active.clear()


def save_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def log(message: str) -> None:
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    print(f"{stamp} {message}", flush=True)


def historical_report(name: str) -> str:
    # Frozen O-aliases belong to the September run and are not valid in this run.
    text = (SOURCE / name).read_text(encoding="utf-8")
    return re.sub(r"【cite:O[1-9]\d*】", "[历史引用需本轮核验]", text)


async def verify_workspace_bundle(workspace: LocalWorkspaceClient) -> dict[str, object]:
    seeder = AttemptBundleSeeder(workspace, REPO / "prompts/codex_v2/document1/global_research")
    checked: dict[str, object] = {}
    for node in NODES:
        definition = seeder.node_definition(node)
        expected_skill = f"skills/c4/{SKILLS[node]}"
        assert definition["required_skills"] == [expected_skill]
        assert definition["schema"] == f"schemas/{SCHEMAS[node]}"
        seeded = await seeder.seed(
            run_id=RUN_ID,
            node=node,
            attempt_id=f"preflight-{node.value}",
            context_payload={"ticker": "MU", "preflight": True},
            horizontal=None,
            workflow_version="codex_global_research_v1",
            research_lane=ResearchLane.GLOBAL_RESEARCH,
        )
        task = json.loads((await workspace.read_text(RUN_ID, seeded.task_path)).content)
        skill = (await workspace.read_text(RUN_ID, task["required_skills"][0])).content
        prompt = (
            await workspace.read_text(RUN_ID, f"attempts/preflight-{node.value}/input/task.md")
        ).content
        schema = json.loads((await workspace.read_text(RUN_ID, task["output_schema_path"])).content)
        assert skill == (REPO / "prompts/codex_v2/document1" / expected_skill).read_text(
            encoding="utf-8"
        )
        assert prompt == (REPO / "prompts/codex_v2/document1" / definition["agent"]).read_text(
            encoding="utf-8"
        )
        assert schema == seeder.output_schema(node)
        assert task["required_skills"] == [
            f"attempts/preflight-{node.value}/input/{expected_skill}"
        ]
        if node is CodexD1Node.C4E_NETWORK_BUILD:
            assert schema["type"] == "string" and task["response_format"] == "json_string"
            assert task["markdown_output_path"] and task["structured_output_path"] is None
        else:
            expected_product = (
                "future_nodes" if node is CodexD1Node.C4F_FUTURE_NODES else "entity_relations"
            )
            assert expected_product in schema["required"]
            assert schema["additionalProperties"] is False
            assert schema["properties"]["report_markdown"]["const"] == ""
        checked[node.value] = {
            "prompt": definition["agent"],
            "skill": expected_skill,
            "schema": definition["schema"],
            "input_sha256": seeded.input_sha256,
        }
    return checked


async def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    assert SOURCE.is_dir(), SOURCE
    assert Path.cwd().resolve() == REPO.resolve(), "run from repository root"
    workspace_store = LocalWorkspaceStore(ROOT / "workspaces")
    workspace = LocalWorkspaceClient(workspace_store)
    preflight = await verify_workspace_bundle(workspace)
    source_files = {}
    for name in ("c1.md", "c3.md", "c5.md", "horizontal.json"):
        data = (SOURCE / name).read_bytes()
        source_files[name] = {
            "path": str(SOURCE / name),
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    summary: dict[str, object] = {
        "run_id": RUN_ID,
        "ticker": "MU",
        "workflow_version": "codex_global_research_v1",
        "scope": (
            "C4 stages only, using production workflow node runner "
            "and embedded real Codex Worker"
        ),
        "model": "gpt-6-sol",
        "effort": "high",
        "cutoff_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "source_files": source_files,
        "workspace_preflight": preflight,
        "stages": {},
    }
    save_json(ROOT / "run_summary.json", summary)
    log("Workspace prompt/skill/schema injection verified for all four C4 nodes")

    runtime = FreshSdkRuntime()
    manager = WorkerJobManager(runtime, workspace_store, subagents=0, startup_concurrency=1)
    worker = EmbeddedCodexWorkerClient(manager, poll_seconds=5)
    repository = SQLiteCodexRuntimeRepository(ROOT / "c4_runtime.sqlite3")
    owner = CodexGlobalResearchOrchestrator(
        worker=worker,
        workspace=workspace,
        repository=repository,
        horizontal_collector=None,
        horizontal_compiler=None,
        model="gpt-6-sol",
        effort="high",
        timeout_seconds=3600,
        max_attempts=2,
        max_subagents=0,
    )
    cutoff_at = datetime.fromisoformat(str(summary["cutoff_at"]))
    request = GlobalResearchRunRequest(
        run_id=RUN_ID,
        ticker="MU",
        company_name="Micron Technology, Inc.",
        research_brief="研究 MU 的未来事件入口、外部实体关系及深度网络；所有事实截至本轮 cutoff。",
        base_context={
            "asset_origin": "same frozen Document1 MU assets used by D2/D3 v2.1",
            "asset_capture": (
                "September 2026; historical citation aliases invalidated before import"
            ),
            "source_files": source_files,
        },
        cutoff_at=cutoff_at,
    )
    checkpoint = repository.get_checkpoint(RUN_ID) or WorkflowCheckpoint(
        workflow_version=request.workflow_version,
        research_lane=request.research_lane,
        ticker=request.ticker,
        run_id=RUN_ID,
    )
    repository.save_checkpoint(checkpoint)
    historical = {
        "c1_report": NodeOutput(
            status="completed", report_markdown=historical_report("c1.md")
        ).model_dump(mode="json", by_alias=True),
        "c3_report": NodeOutput(
            status="completed", report_markdown=historical_report("c3.md")
        ).model_dump(mode="json", by_alias=True),
        "c5_report": NodeOutput(
            status="completed", report_markdown=historical_report("c5.md")
        ).model_dump(mode="json", by_alias=True),
    }
    horizontal = json.loads((SOURCE / "horizontal.json").read_text(encoding="utf-8"))

    def record(node: CodexD1Node, output: NodeOutput, reference: object) -> None:
        stage = {
            "status": output.status,
            "report_ref": reference.model_dump(mode="json") if reference else None,
            "relations": len(output.entity_relations),
            "future_nodes": len(output.future_nodes),
            "network_chars": len(output.report_markdown),
            "warnings": output.warnings,
            "attempts": [
                item.model_dump(mode="json")
                for item in repository.list_attempts(RUN_ID, limit=100)
                if item.node is node
            ],
        }
        summary["stages"][node.value] = stage
        save_json(ROOT / "run_summary.json", summary)
        save_json(
            ROOT / "outputs" / f"{node.value}.json", output.model_dump(mode="json", by_alias=True)
        )
        if node is CodexD1Node.C4E_NETWORK_BUILD:
            (ROOT / "outputs" / "c4e_network_build.md").write_text(
                output.report_markdown, encoding="utf-8"
            )
        log(
            f"{node.value} {output.status} relations={stage['relations']} "
            f"future={stage['future_nodes']} network_chars={stage['network_chars']}"
        )

    try:
        probe = await runtime.probe()
        summary["model_probe"] = {
            "authenticated": probe.get("authenticated"),
            "model_list_includes_requested": "gpt-6-sol" in probe.get("models", []),
        }
        save_json(ROOT / "run_summary.json", summary)
        log(
            f"Model probe authenticated={probe.get('authenticated')} "
            f"gpt-6-sol listed={summary['model_probe']['model_list_includes_requested']}"
        )
        if not probe.get("authenticated"):
            raise RuntimeError("local Codex SDK account is not authenticated")

        pre_payload = {
            "ticker": request.ticker,
            "company_name": request.company_name,
            "research_brief": request.research_brief,
            "base_context": request.base_context,
            "task": "pre-scan initial entity relations only",
        }
        log("Starting real C4e pre-scan")
        pre, pre_ref = await owner._execute_or_partial(
            request, CodexD1Node.C4_PRE_SCAN, pre_payload, checkpoint
        )
        record(CodexD1Node.C4_PRE_SCAN, pre, pre_ref)
        context = {
            "ticker": request.ticker,
            "company_name": request.company_name,
            "research_brief": request.research_brief,
            "base_context": request.base_context,
            "c4_pre_scan": owner._handoff_output(pre, pre_ref),
            **historical,
            "horizontal_collection": horizontal,
            "agent_observations": [],
            "upstream_artifacts": [],
            "historical_upstream_warning": (
                "C1/C3/C5 and horizontal assets are frozen September MU D1 outputs. "
                "Their old O aliases were invalidated; verify new facts and sources "
                "in this attempt."
            ),
        }
        log("Starting real C4f Future Node")
        future, future_ref = await owner._execute_or_partial(
            request,
            CodexD1Node.C4F_FUTURE_NODES,
            {**context, "output_contract": "return Future Nodes only; do not modify Entity Map"},
            checkpoint,
        )
        record(CodexD1Node.C4F_FUTURE_NODES, future, future_ref)
        log("Starting real C4e formal-scan")
        formal, formal_ref = await owner._execute_after_c4_stage(
            request,
            CodexD1Node.C4E_FORMAL_SCAN,
            {
                **context,
                "future_nodes": owner._handoff_output(future, future_ref),
                "output_contract": (
                    "return a complete Entity Map snapshot, not a delta; no Future Nodes"
                ),
            },
            checkpoint,
            predecessor=future_ref,
        )
        record(CodexD1Node.C4E_FORMAL_SCAN, formal, formal_ref)
        log("Starting real C4e network-build")
        network, network_ref = await owner._execute_after_c4_stage(
            request,
            CodexD1Node.C4E_NETWORK_BUILD,
            {
                **context,
                "future_nodes": owner._handoff_output(future, future_ref),
                "c4e_formal_scan": owner._handoff_output(formal, formal_ref),
                "output_contract": (
                    "continue deep network research, including web search and "
                    "new discoveries; return complete Markdown as a JSON string"
                ),
            },
            checkpoint,
            predecessor=formal_ref,
        )
        record(CodexD1Node.C4E_NETWORK_BUILD, network, network_ref)
        summary["completed_at"] = datetime.now(UTC).isoformat(timespec="seconds")
        summary["thread_ids"] = sorted(
            {
                item.thread_id
                for item in repository.list_attempts(RUN_ID, limit=100)
                if item.node in NODES and item.thread_id
            }
        )
        summary["checkpoint"] = repository.get_checkpoint(RUN_ID).model_dump(mode="json")
        save_json(ROOT / "run_summary.json", summary)
        log(f"C4 chain finished, thread_ids={summary['thread_ids']}")
    except BaseException as exc:
        summary["fatal_error"] = f"{type(exc).__name__}: {exc}"
        save_json(ROOT / "run_summary.json", summary)
        raise
    finally:
        await manager.close()


if __name__ == "__main__":
    asyncio.run(main())
