"""Behavioral coverage for shared-thread, independently published C4 products."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from doxagent.codex_runtime.errors import StructuredOutputInvalid
from doxagent.codex_runtime.repository import (
    InMemoryCodexRuntimeRepository,
    PostgresCodexRuntimeRepository,
)
from doxagent.codex_runtime.schema import (
    ArtifactKind,
    AttemptStatus,
    CodexAgentRole,
    CodexD1Node,
    GlobalResearchBundle,
    ResearchLane,
)
from doxagent.codex_worker.local_client import LocalWorkspaceClient
from doxagent.codex_worker.schema import WorkerRunRequest
from doxagent.codex_worker.workspace_store import LocalWorkspaceStore
from doxagent.data_runtime.policy import DataToolPolicyRegistry
from doxagent.observations.models import PersistedObservation
from doxagent.observations.pack import render_observation_block
from doxagent.pilot.case_builder import (
    PilotCaseRequest,
    _apply_manual_upstream,
    _load_manual_upstream,
    _pilot_identity,
)
from doxagent.pilot.templates import render_task
from doxagent.workflows.codex_document1.attempt_bundle import AttemptBundleSeeder
from doxagent.workflows.codex_document1.recovery import recover_node_output
from doxagent.workflows.codex_global_research import (
    CodexGlobalResearchOrchestrator,
    GlobalResearchRunRequest,
)
from tests.fixtures.codex_document1 import _empty_horizontal, _FakeWorker

C4_STAGES = (
    CodexD1Node.C4_PRE_SCAN,
    CodexD1Node.C4F_FUTURE_NODES,
    CodexD1Node.C4E_FORMAL_SCAN,
    CodexD1Node.C4E_NETWORK_BUILD,
)
ASSET_ROOT = Path("prompts/codex_v2/document1")


def runtime(tmp_path, worker_type=_FakeWorker, *, max_attempts=1, orchestrator_type=None):
    repository = InMemoryCodexRuntimeRepository()
    workspace = LocalWorkspaceClient(LocalWorkspaceStore(tmp_path / "workspaces"))
    worker = worker_type(workspace)
    collector, compiler = _empty_horizontal()
    orchestrator = (orchestrator_type or CodexGlobalResearchOrchestrator)(
        worker=worker,
        workspace=workspace,
        repository=repository,
        horizontal_collector=collector,
        horizontal_compiler=compiler,
        model="test",
        max_attempts=max_attempts,
    )
    return orchestrator, worker, workspace, repository


@pytest.mark.asyncio
async def test_stage_prompts_schemas_full_context_and_artifacts_are_independent(tmp_path):
    orchestrator, worker, workspace, repository = runtime(tmp_path)
    bundle = await orchestrator.run(
        GlobalResearchRunRequest(run_id="split", ticker="NVDA", research_brief="test")
    )
    asset_pairs = {
        CodexD1Node.C4_PRE_SCAN: ("c4e.md", "c4e_pre_scan.md"),
        CodexD1Node.C4F_FUTURE_NODES: ("c4f.md", "c4f_future_node.md"),
        CodexD1Node.C4E_FORMAL_SCAN: ("c4e.md", "c4e_formal-scan.md"),
        CodexD1Node.C4E_NETWORK_BUILD: ("c4e.md", "c4e_network-build.md"),
    }
    for request in (item for item in worker.requests if item.node in C4_STAGES):
        base = f"attempts/{request.attempt_id}/input"
        task = json.loads((await workspace.read_text("split", f"{base}/task.json")).content)
        agent, skill = asset_pairs[request.node]
        assert (await workspace.read_text("split", f"{base}/task.md")).content == (
            ASSET_ROOT / "agents" / agent
        ).read_text(encoding="utf-8")
        assert len(task["required_skills"]) == 1
        assert (await workspace.read_text("split", task["required_skills"][0])).content == (
            ASSET_ROOT / "skills/c4" / skill
        ).read_text(encoding="utf-8")
        assert request.agent_role is CodexAgentRole.C4
        assert request.thread_id == (
            None if request.node is C4_STAGES[0] else f"{CodexAgentRole.C4.value}-thread"
        )
        assert request.output_schema == json.loads(
            (await workspace.read_text("split", task["output_schema_path"])).content
        )
        if request.node is CodexD1Node.C4E_NETWORK_BUILD:
            assert request.output_schema == {
                "type": "string",
                "pattern": "\\S",
                "description": "C4e network-build 的完整 Markdown 网络研究正文。",
            }
            assert task["structured_output_path"] is None
            assert (await workspace.read_text("split", task["markdown_output_path"])).content == (
                bundle.entity_network_report
            )
        else:
            fields = request.output_schema["properties"]
            assert ("future_nodes" in fields) == (request.node is CodexD1Node.C4F_FUTURE_NODES)
            assert ("entity_relations" in fields) == (
                request.node is not CodexD1Node.C4F_FUTURE_NODES
            )
        context = json.loads((await workspace.read_text("split", f"{base}/context.json")).content)
        payload = context["payload"]
        if request.node is not CodexD1Node.C4_PRE_SCAN:
            for role in ("c1", "c3", "c5"):
                assert payload[f"{role}_report"]["report_markdown"].startswith(f"### {role}")
            assert payload["c1_report"]["observation_candidates"][0]["value"] == 10
            assert "horizontal_collection" in payload
            assert payload["c4_pre_scan"]["entity_relations"]
            assert {item["node"] for item in payload["upstream_artifacts"]} >= {"c1", "c3", "c5"}
        if request.node is CodexD1Node.C4E_NETWORK_BUILD:
            assert payload["c4e_formal_scan"]["entity_relations"]
            assert "continue deep network research" in payload["output_contract"]
        assert DataToolPolicyRegistry().allowed_tools_for_ticker(
            request.node, request.agent_role, "NVDA"
        )
    refs = [bundle.reports[node.value] for node in C4_STAGES[1:]]
    assert len({ref.artifact_id for ref in refs}) == 3
    for ref in refs:
        assert repository.get_citation_manifest("split", ref.artifact_id) is not None
        completions = [
            artifact
            for artifact in repository.list_artifacts("split")
            if artifact.attempt_id == ref.attempt_id
            and artifact.kind is ArtifactKind.STRUCTURED_COMPLETION
        ]
        assert len(completions) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stage", "mode", "product", "expected"),
    [
        (CodexD1Node.C4F_FUTURE_NODES, "empty", "future_nodes", "empty"),
        (CodexD1Node.C4F_FUTURE_NODES, "failed", "future_nodes", "failed"),
        (CodexD1Node.C4E_FORMAL_SCAN, "empty", "entity_relations", "empty"),
        (CodexD1Node.C4E_FORMAL_SCAN, "failed", "entity_relations", "failed"),
        (CodexD1Node.C4E_NETWORK_BUILD, "empty", "entity_network_report", "failed"),
        (CodexD1Node.C4E_NETWORK_BUILD, "failed", "entity_network_report", "failed"),
        (CodexD1Node.C4F_FUTURE_NODES, "mixed", "future_nodes", "failed"),
        (CodexD1Node.C4E_FORMAL_SCAN, "mixed", "entity_relations", "failed"),
    ],
)
async def test_missing_or_invalid_product_never_falls_back_to_another_product(
    tmp_path,
    stage,
    mode,
    product,
    expected,
):
    class ProductWorker(_FakeWorker):
        async def run(self, request):
            job = await super().run(request)
            if request.node is stage:
                value = json.loads(job.final_response)
                if mode == "failed":
                    return job.model_copy(update={"status": "failed", "final_response": None})
                if mode == "mixed":
                    value["entity_relations" if product == "future_nodes" else "future_nodes"] = []
                elif isinstance(value, dict):
                    value[product] = []
                else:
                    value = ""
                return job.model_copy(update={"final_response": json.dumps(value)})
            return job

    orchestrator, worker, _, repository = runtime(tmp_path, ProductWorker)
    bundle = await orchestrator.run(
        GlobalResearchRunRequest(run_id="products", ticker="NVDA", research_brief="test")
    )
    assert bundle.status == "published"
    assert bundle.c4_product_status[product] == expected
    assert not getattr(bundle, product)
    products = ("future_nodes", "entity_relations", "entity_network_report")
    for index, key in enumerate(products):
        if key == product:
            continue
        dependent_failed = expected == "failed" and index > products.index(product)
        assert bundle.c4_product_status[key] == ("failed" if dependent_failed else "available")
    if product == "entity_relations":
        # A nonempty pre-scan is never silently republished as the formal snapshot.
        assert CodexD1Node.C4_PRE_SCAN.value in bundle.reports
        assert bundle.entity_relations == []
    attempts = [item for item in repository.list_attempts("products") if item.node is stage]
    assert attempts[-1].status is (
        AttemptStatus.FAILED if expected == "failed" else AttemptStatus.SUCCEEDED
    )
    expected_stages = list(C4_STAGES)
    if expected == "failed":
        expected_stages = expected_stages[: expected_stages.index(stage) + 1]
    assert [item.node for item in worker.requests if item.node in C4_STAGES] == expected_stages


@pytest.mark.asyncio
async def test_c4_retry_resumes_same_thread_and_stage_schema(tmp_path):
    class RetryWorker(_FakeWorker):
        failed = False

        async def run(self, request):
            job = await super().run(request)
            if request.node is CodexD1Node.C4F_FUTURE_NODES and not self.failed:
                self.failed = True
                return job.model_copy(update={"status": "failed", "final_response": None})
            return job

    orchestrator, worker, _, _ = runtime(tmp_path, RetryWorker, max_attempts=2)
    bundle = await orchestrator.run(
        GlobalResearchRunRequest(run_id="retry", ticker="NVDA", research_brief="test")
    )
    future = [item for item in worker.requests if item.node is CodexD1Node.C4F_FUTURE_NODES]
    assert len(future) == 2
    assert future[0].thread_id == future[1].thread_id == f"{CodexAgentRole.C4.value}-thread"
    assert future[0].output_schema == future[1].output_schema
    assert "Previous attempt failed" in future[1].prompt
    assert bundle.c4_product_status["future_nodes"] == "available"


@pytest.mark.asyncio
async def test_c4_capsules_reload_attempt_mcp_without_changing_thread(tmp_path, monkeypatch):
    from doxagent.codex_worker import capsules

    class FakeCapsule:
        def __init__(self, root):
            self.busy, self.healthy = True, True
            self.thread_id, self.idle_at = None, 0
            self.closed = False

        async def launch(self):
            pass

        async def close(self):
            self.closed = True

    monkeypatch.setattr(capsules, "Capsule", FakeCapsule)
    pool = capsules.CapsuleRuntime(tmp_path)
    request = WorkerRunRequest(
        run_id="shared",
        ticker="NVDA",
        attempt_id="formal-1",
        workflow_version="codex_global_research_v1",
        research_lane=ResearchLane.GLOBAL_RESEARCH,
        node=CodexD1Node.C4E_FORMAL_SCAN,
        agent_role=CodexAgentRole.C4,
        thread_id="persisted-c4",
        max_subagents=0,
        prompt="test",
        output_schema={},
    )
    first = await pool._take(request.thread_id)
    first.thread_id = request.thread_id
    pool.active[pool.key(request)] = first
    await pool.release(request)
    assert first.closed
    assert await pool._take(request.thread_id) is not first
    assert request.thread_id == "persisted-c4"


@pytest.mark.asyncio
async def test_network_report_can_recover_only_from_its_own_markdown_file(tmp_path):
    class FileWorker(_FakeWorker):
        async def run(self, request):
            job = await super().run(request)
            if request.node is CodexD1Node.C4E_NETWORK_BUILD:
                task = json.loads(
                    (
                        await self.workspace.read_text(
                            request.run_id,
                            f"attempts/{request.attempt_id}/input/task.json",
                        )
                    ).content
                )
                await self.workspace.write_text(
                    request.run_id,
                    task["markdown_output_path"],
                    "# 文件恢复的网络研究\n新的关系解释。",
                )
                return job.model_copy(update={"status": "failed", "final_response": None})
            return job

    orchestrator, _, _, _ = runtime(tmp_path, FileWorker)
    bundle = await orchestrator.run(
        GlobalResearchRunRequest(run_id="file", ticker="NVDA", research_brief="test")
    )
    assert bundle.entity_network_report.startswith("# 文件恢复的网络研究")
    assert bundle.c4_product_status["entity_network_report"] == "available"


@pytest.mark.asyncio
async def test_checkpoint_restores_all_three_products_without_new_c4_calls(tmp_path):
    class InterruptedPublish(CodexGlobalResearchOrchestrator):
        interrupted = False

        async def _publish_references(self, *args, **kwargs):
            if not self.interrupted:
                self.interrupted = True
                raise RuntimeError("publish interrupted")
            return await super()._publish_references(*args, **kwargs)

    orchestrator, worker, _, _ = runtime(tmp_path, orchestrator_type=InterruptedPublish)
    request = GlobalResearchRunRequest(run_id="restore", ticker="NVDA", research_brief="test")
    with pytest.raises(RuntimeError, match="publish interrupted"):
        await orchestrator.run(request)
    before = len(worker.requests)
    bundle = await orchestrator.run(request)
    assert len(worker.requests) == before
    assert bundle.future_nodes and bundle.entity_relations and bundle.entity_network_report
    assert set(bundle.c4_product_status.values()) == {"available"}


def test_network_recovery_rejects_old_object_contract_and_accepts_markdown_transport():
    schema = json.loads((ASSET_ROOT / "schemas/c4e_network-build.schema.json").read_text("utf-8"))
    for text in ('{"report_markdown":"old object"}', '"   "', "[]"):
        with pytest.raises(StructuredOutputInvalid):
            recover_node_output(CodexD1Node.C4E_NETWORK_BUILD, text, output_schema=schema)
    for text in ("# Network\nresearch", json.dumps("# Network\nresearch")):
        output, issues = recover_node_output(
            CodexD1Node.C4E_NETWORK_BUILD,
            text,
            output_schema=schema,
        )
        assert output.report_markdown == "# Network\nresearch" and not issues
        assert not output.entity_relations and not output.future_nodes


@pytest.mark.asyncio
async def test_network_uses_rebound_formal_evidence_and_its_own_citation_manifest(tmp_path):
    class CitationWorker(_FakeWorker):
        async def run(self, request):
            job = await super().run(request)
            if request.node not in {CodexD1Node.C4E_FORMAL_SCAN, CodexD1Node.C4E_NETWORK_BUILD}:
                return job
            if request.node is CodexD1Node.C4E_NETWORK_BUILD:
                inherited = await self.workspace.read_attempt_observations(
                    request.run_id,
                    request.attempt_id,
                )
                assert len(inherited) == 1
                assert inherited[0].locator.endswith("c4e_formal_scan")
            text = request.node.value
            stored = await self.workspace.import_attempt_observations(
                request.run_id,
                request.attempt_id,
                [
                    PersistedObservation(
                        run_id=request.run_id,
                        attempt_id=request.attempt_id,
                        block_id=text,
                        tool_call_id=text,
                        tool_name="source_capture",
                        title=text,
                        locator=f"https://example.com/{text}",
                        block_type="text",
                        content=text,
                        content_hash=hashlib.sha256(json.dumps(text).encode()).hexdigest(),
                        provider="test",
                        method_version="1",
                    )
                ],
            )
            if request.node is CodexD1Node.C4E_FORMAL_SCAN:
                # The real Windows Data MCP writes pack files with CRLF. Promotion
                # must keep cited observations resolvable across that newline form.
                suffix, block = render_observation_block(stored[0])
                pack = (
                    self.workspace.store.ensure_run(request.run_id)
                    / "context/mcp_data"
                    / request.attempt_id
                    / stored[0].tool_call_id
                    / "blocks"
                    / f"{stored[0].alias}{suffix}"
                )
                pack.parent.mkdir(parents=True, exist_ok=True)
                pack.write_bytes(block.replace("\n", "\r\n").encode("utf-8"))
            cite = f"【cite:{stored[0].alias}】"
            value = json.loads(job.final_response)
            if request.node is CodexD1Node.C4E_FORMAL_SCAN:
                value["entity_relations"][0]["关系说明"] += cite
            else:
                value += cite
            return job.model_copy(update={"final_response": json.dumps(value, ensure_ascii=False)})

    orchestrator, _, _, repository = runtime(tmp_path, CitationWorker)
    bundle = await orchestrator.run(
        GlobalResearchRunRequest(run_id="citations", ticker="NVDA", research_brief="test"),
    )
    assert "【cite:O2】" in bundle.entity_network_report, [
        (attempt.node.value, attempt.error_message)
        for attempt in repository.list_attempts(bundle.run_id)
        if attempt.status is AttemptStatus.FAILED
    ]
    assert bundle.c4_product_status["entity_network_report"] == "available"
    report = bundle.reports[CodexD1Node.C4E_NETWORK_BUILD.value]
    manifest = repository.get_citation_manifest(bundle.run_id, report.artifact_id)
    assert manifest is not None
    assert len(manifest.entries) == 1
    assert manifest.entries[0].resolved and manifest.entries[0].alias == "O2"
    assert bundle.citation_manifest.entries == []


def test_postgres_lane_bundle_round_trip_keeps_network_and_independent_status(monkeypatch):
    class Cursor:
        row = None

        def execute(self, sql, values):
            if sql.lstrip().startswith("INSERT"):
                assert "entity_network_report=excluded.entity_network_report" in sql
                assert "c4_product_status=excluded.c4_product_status" in sql
                self.row = list(values)
                for index in (5, 6, 7, 13):
                    self.row[index] = json.loads(self.row[index])
            else:
                assert "entity_network_report,c4_product_status" in sql

        def fetchone(self):
            return self.row

    cursor = Cursor()
    repository = PostgresCodexRuntimeRepository("not-used")
    monkeypatch.setattr(
        repository, "_execute", lambda operation, table, callback: callback(None, cursor)
    )
    monkeypatch.setattr(repository, "_audit_read", lambda *args: None)
    bundle = GlobalResearchBundle(
        run_id="pg",
        ticker="NVDA",
        status="draft",
        entity_network_report="# research",
        c4_product_status={
            "future_nodes": "failed",
            "entity_relations": "empty",
            "entity_network_report": "available",
        },
    )
    repository.save_bundle(bundle)
    restored = repository._get_lane_bundle("pg", bundle.workflow_version)
    assert restored == bundle


@pytest.mark.asyncio
async def test_pilot_network_contract_and_manual_formal_snapshot(tmp_path):
    node = CodexD1Node.C4E_NETWORK_BUILD
    workflow, lane, root = _pilot_identity(
        PilotCaseRequest(
            source_run="source",
            node=node,
            case_id="case",
            research_lane=ResearchLane.GLOBAL_RESEARCH,
        )
    )
    workspace = LocalWorkspaceClient(LocalWorkspaceStore(tmp_path / "pilot"))
    seeded = await AttemptBundleSeeder(workspace, root).seed(
        run_id="source",
        node=node,
        attempt_id="network-1",
        context_payload={},
        horizontal=None,
        workflow_version=workflow,
        research_lane=lane,
    )
    assert seeded.markdown_output_path and seeded.structured_output_path is None
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    (upstream / "c4e_formal_scan.json").write_text(
        json.dumps(
            {
                "status": "completed",
                "entity_relations": [],
            }
        ),
        encoding="utf-8",
    )
    for role in ("c1", "c3", "c5"):
        (upstream / f"{role}.md").write_text(f"# {role} full research", encoding="utf-8")
    imported = _load_manual_upstream(node, upstream, research_lane=lane)
    assert imported is not None
    payload = _apply_manual_upstream(node, {}, imported.files, research_lane=lane)
    assert payload["c4e_formal_scan"]["status"] == "completed"
    assert payload["c5_report"]["report_markdown"] == "# c5 full research"
    task = render_task(
        case_root=tmp_path,
        node=node.value,
        run_id="source",
        attempt_id="network-1",
        profile="quality",
        research_lane=lane.value,
    )
    assert "markdown_output_path" in task and "非空 JSON 字符串" in task
    assert "完整 NodeOutput JSON" not in task
