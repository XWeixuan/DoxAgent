from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from doxagent.codex_runtime.repository import InMemoryCodexRuntimeRepository
from doxagent.codex_runtime.schema import (
    ArtifactKind,
    ArtifactRef,
    AttemptStatus,
    CitationEntry,
    CitationManifest,
    CodexD1Node,
    NodeAttempt,
    WorkflowCheckpoint,
)
from doxagent.codex_worker.local_client import LocalWorkspaceClient
from doxagent.codex_worker.workspace_store import LocalWorkspaceStore
from doxagent.horizontal_collection.collector import HorizontalCollector
from doxagent.horizontal_collection.compiler import HorizontalStateCompiler
from doxagent.horizontal_collection.context import render_horizontal_context
from doxagent.horizontal_collection.registry import CollectionTargetRegistry, MetricRegistry
from doxagent.horizontal_collection.schema import (
    CollectionMode,
    CollectionTargetDefinition,
    EntityScope,
    HorizontalCollectionBundle,
    HorizontalCollectionManifest,
    MetricDefinition,
    MetricRequirement,
    MetricValueType,
    OutputPolicy,
    ProviderCapabilityStatus,
    SourceRole,
)
from doxagent.models import ResultStatus
from doxagent.observations.models import PersistedObservation
from doxagent.tools.registry import ToolRegistry
from doxagent.tools.schema import ToolResult
from doxagent.workflows.codex_document1.attempt_bundle import (
    AttemptBundleSeeder,
    AttemptOutputValidator,
)
from doxagent.workflows.codex_document1.context_compiler import CodexD1ContextCompiler
from doxagent.workflows.codex_document1.orchestrator import CodexDocument1Orchestrator
from doxagent.workflows.codex_document1.schema import Document1V2RunRequest, NodeOutput
from doxagent.workflows.codex_document1.upstream_rebinder import (
    UpstreamObservationRebinder,
    upstream_handoff,
)


@pytest.mark.asyncio
async def test_attempt_bundle_is_role_scoped_and_hash_stable(tmp_path: Path) -> None:
    workspace = LocalWorkspaceClient(LocalWorkspaceStore(tmp_path / "workspaces"))
    seeder = AttemptBundleSeeder(workspace, Path("codex_assets/document1_v2"))
    horizontal = {
        "schema_version": "d1-horizontal-agent-input-v1",
        "program_values": [],
        "target_status": [],
        "optional_metrics": [],
        "candidate_format": {},
        "freeform_metrics_allowed": True,
        "instructions": [],
    }
    first = await seeder.seed(
        run_id="run-bundle",
        node=CodexD1Node.C3,
        attempt_id="c3-1",
        context_payload={"ticker": "NVDA"},
        horizontal=horizontal,
    )
    second = await seeder.seed(
        run_id="run-bundle",
        node=CodexD1Node.C3,
        attempt_id="c3-2",
        context_payload={"ticker": "NVDA"},
        horizontal=horizontal,
    )
    assert first.input_sha256 == second.input_sha256
    inventory = await workspace.inventory("run-bundle")
    paths = {item.relative_path for item in inventory.files}
    assert "attempts/c3-1/input/horizontal.json" in paths
    assert "attempts/c3-1/input/skills/industry-research.md" in paths
    assert not any("fundamental-research" in item for item in paths if "c3-1" in item)
    task = json.loads((await workspace.read_text("run-bundle", first.task_path)).content or "")
    assert task["required_skills"] == ["attempts/c3-1/input/skills/industry-research.md"]
    assert task["draft_path"] == "attempts/c3-1/output/report_draft.md"
    audit = json.loads(
        (await workspace.read_text("run-bundle", "attempts/c3-1/audit/bundle.json")).content or ""
    )
    assert audit["bundle_version"] == "codex-d1-agent-bundle-v2"
    assert all(len(value) == 64 for value in audit["files"].values())


@pytest.mark.asyncio
async def test_c1_bundle_injects_complete_fundamental_research_contract(tmp_path: Path) -> None:
    workspace = LocalWorkspaceClient(LocalWorkspaceStore(tmp_path / "workspaces"))
    seeded = await AttemptBundleSeeder(
        workspace, Path("codex_assets/document1_v2")
    ).seed(
        run_id="run-c1-skill",
        node=CodexD1Node.C1,
        attempt_id="c1-1",
        context_payload={"ticker": "NVDA", "company_name": "NVIDIA Corporation"},
        horizontal=None,
    )

    task = json.loads((await workspace.read_text("run-c1-skill", seeded.task_path)).content or "")
    assert task["required_sections"] == [
        "一、近期基本面状态与变化",
        "二、管理层与卖方当前预期",
        "三、核心基本面驱动因子",
        "四、关键变量传导链",
        "五、潜在基本面因素缺口",
        "六、未知项与证据边界",
    ]

    skill_path = "attempts/c1-1/input/skills/fundamental-research.md"
    injected = (await workspace.read_text("run-c1-skill", skill_path)).content or ""
    canonical = Path("prompts/internal_task_skills/fundamental-research.md").read_text(
        encoding="utf-8"
    ).replace(
        "For BuildGlobalResearch / Document 1, research the current and forward company "
        "fundamentals of `{target}` in the `{market}` market.",
        "For BuildGlobalResearch / Document 1, research the current and forward company "
        "fundamentals of the issuer identified by `ticker` and `company_name` in `context.json`.",
    )
    assert injected == canonical
    assert "## Final quality gates" in injected
    assert "### 综合判断" in injected
    assert "| ID | 上游业务变量" in injected
    assert "Possible future effects" not in injected
    assert len(injected) > 20_000


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("node", "skill_name", "canonical_skill", "canonical_agent", "minimum_bytes"),
    [
        (
            CodexD1Node.C3,
            "industry-research.md",
            "prompts/internal_task_skills/industry-research.md",
            "prompts/agents/c3.md",
            18_000,
        ),
        (
            CodexD1Node.O4_A,
            "market-implied-expectations.md",
            "prompts/internal_task_skills/market-implied-expectations.md",
            "codex_assets/document1_v2/agents/o4_a.md",
            29_000,
        ),
        (
            CodexD1Node.C4_PRE_SCAN,
            "entity-map-and-future-nodes.md",
            "prompts/internal_task_skills/entity-map-and-future-nodes.md",
            "prompts/agents/c4.md",
            3_000,
        ),
        (
            CodexD1Node.C4_ENRICHMENT,
            "entity-map-and-future-nodes.md",
            "prompts/internal_task_skills/entity-map-and-future-nodes.md",
            "prompts/agents/c4.md",
            3_000,
        ),
        (
            CodexD1Node.C4_FINALIZATION,
            "entity-map-and-future-nodes.md",
            "prompts/internal_task_skills/entity-map-and-future-nodes.md",
            "prompts/agents/c4.md",
            3_000,
        ),
    ],
)
async def test_bundle_injects_canonical_agent_and_skill_sources(
    tmp_path: Path,
    node: CodexD1Node,
    skill_name: str,
    canonical_skill: str,
    canonical_agent: str,
    minimum_bytes: int,
) -> None:
    workspace = LocalWorkspaceClient(LocalWorkspaceStore(tmp_path / "workspaces"))
    seeded = await AttemptBundleSeeder(
        workspace, Path("codex_assets/document1_v2")
    ).seed(
        run_id=f"run-{node.value}-skill",
        node=node,
        attempt_id=f"{node.value}-1",
        context_payload={"ticker": "NVDA", "company_name": "NVIDIA Corporation"},
        horizontal=None,
    )
    run_id = f"run-{node.value}-skill"
    skill_path = f"attempts/{node.value}-1/input/skills/{skill_name}"
    injected_skill = (await workspace.read_text(run_id, skill_path)).content or ""
    injected_agent = (await workspace.read_text(run_id, seeded.task_path.replace(
        "task.json", "task.md"
    ))).content or ""
    assert injected_skill == Path(canonical_skill).read_text(encoding="utf-8")
    assert injected_agent == Path(canonical_agent).read_text(encoding="utf-8")
    assert len(injected_skill.encode("utf-8")) >= minimum_bytes


@pytest.mark.asyncio
async def test_c3_and_o4_a_bundle_sections_follow_canonical_skills(tmp_path: Path) -> None:
    workspace = LocalWorkspaceClient(LocalWorkspaceStore(tmp_path / "workspaces"))
    seeder = AttemptBundleSeeder(workspace, Path("codex_assets/document1_v2"))
    c3 = await seeder.seed(
        run_id="run-sections",
        node=CodexD1Node.C3,
        attempt_id="c3-1",
        context_payload={"ticker": "NVDA"},
        horizontal=None,
    )
    o4_a = await seeder.seed(
        run_id="run-sections",
        node=CodexD1Node.O4_A,
        attempt_id="o4-a-1",
        context_payload={"ticker": "NVDA"},
        horizontal=None,
    )
    assert len(c3.required_sections) == 6
    assert c3.required_sections[0] == "Target-Relevant Industry and Value-Chain Fact Baseline"
    assert c3.required_sections[-1] == "Unknowns, Evidence Boundaries, and Cross-Node Handoffs"
    assert len(o4_a.required_sections) == 6
    assert o4_a.required_sections[0] == "Current Market Pricing Baseline"
    assert o4_a.required_sections[-1] == (
        "Unknowns, Identification Boundaries, and Cross-Node Handoffs"
    )


@pytest.mark.asyncio
async def test_c4_bundle_declares_and_seeds_structured_output_path(tmp_path: Path) -> None:
    workspace = LocalWorkspaceClient(LocalWorkspaceStore(tmp_path / "workspaces"))
    seeded = await AttemptBundleSeeder(
        workspace, Path("codex_assets/document1_v2")
    ).seed(
        run_id="run-c4-output",
        node=CodexD1Node.C4_PRE_SCAN,
        attempt_id="c4-pre-1",
        context_payload={"ticker": "NVDA"},
        horizontal=None,
    )
    assert seeded.structured_output_path == (
        "attempts/c4-pre-1/output/completion.json"
    )
    task = json.loads(
        (await workspace.read_text("run-c4-output", seeded.task_path)).content or ""
    )
    assert task["schema_version"] == "codex-d1-attempt-task-v4"
    assert task["structured_output_path"] == seeded.structured_output_path
    assert task["progress_contract"] is None
    assert (
        await workspace.read_text("run-c4-output", seeded.structured_output_path)
    ).content == "{}"


@pytest.mark.asyncio
async def test_attempt_bundle_hashes_and_seals_manual_upstream(tmp_path: Path) -> None:
    workspace = LocalWorkspaceClient(LocalWorkspaceStore(tmp_path / "workspaces"))
    seeder = AttemptBundleSeeder(workspace, Path("codex_assets/document1_v2"))
    first = await seeder.seed(
        run_id="run-manual-upstream",
        node=CodexD1Node.O4_A,
        attempt_id="o4-a-1",
        context_payload={"ticker": "NVDA"},
        horizontal=None,
        manual_upstream={"c1.md": "Current C1 conclusion"},
    )
    second_hash = seeder.input_sha256(
        node=CodexD1Node.O4_A,
        context_payload={"ticker": "NVDA"},
        horizontal=None,
        manual_upstream={"c1.md": "Changed C1 conclusion"},
    )
    assert first.input_sha256 != second_hash
    assert first.manual_upstream_paths == (
        "attempts/o4-a-1/input/manual_upstream/c1.md",
    )
    task = json.loads(
        (await workspace.read_text("run-manual-upstream", first.task_path)).content or ""
    )
    assert task["manual_upstream"] == {
        "mode": "pilot_override",
        "files": ["attempts/o4-a-1/input/manual_upstream/c1.md"],
        "precedence": "manual_over_source_run",
        "citation_policy": "context_only_reverify",
    }
    copied = await workspace.read_text(
        "run-manual-upstream", "attempts/o4-a-1/input/manual_upstream/c1.md"
    )
    assert copied.content == "Current C1 conclusion"
    audit = json.loads(
        (
            await workspace.read_text(
                "run-manual-upstream", "attempts/o4-a-1/audit/bundle.json"
            )
        ).content
        or ""
    )
    assert "attempts/o4-a-1/input/manual_upstream/c1.md" in audit["files"]


@pytest.mark.asyncio
async def test_progressive_validator_accepts_windows_utf8_bom(tmp_path: Path) -> None:
    workspace = LocalWorkspaceClient(LocalWorkspaceStore(tmp_path / "workspaces"))
    seeded = await AttemptBundleSeeder(
        workspace, Path("codex_assets/document1_v2")
    ).seed(
        run_id="run-bom",
        node=CodexD1Node.C1,
        attempt_id="c1-1",
        context_payload={"ticker": "NVDA"},
        horizontal=None,
    )
    assert seeded.report_draft_path and seeded.progress_path
    assert seeded.observation_candidates_path
    await workspace.write_text("run-bom", seeded.report_draft_path, "\ufeff# Report")
    await workspace.write_text(
        "run-bom",
        seeded.progress_path,
        "\ufeff"
        + json.dumps(
            {
                "status": "completed",
                "completed_sections": list(seeded.required_sections),
            }
        ),
    )
    await workspace.write_text(
        "run-bom",
        seeded.observation_candidates_path,
        "\ufeff[]",
    )
    await AttemptOutputValidator(workspace).validate(
        run_id="run-bom",
        node=CodexD1Node.C1,
        seeded=seeded,
        output=NodeOutput(status="completed", report_markdown="# Report"),
    )


def test_horizontal_agent_input_is_single_role_catalog_not_full_registry() -> None:
    bundle = HorizontalCollectionBundle(
        manifest=HorizontalCollectionManifest(
            run_id="run-horizontal",
            ticker="NVDA",
            metric_registry_version="m1",
            target_registry_version="t1",
            target_results=(),
        ),
        observations=(),
        state_values=(),
    )
    c3 = render_horizontal_context(bundle, role="c3")
    o4_a = render_horizontal_context(bundle, role="o4_a")
    assert c3["schema_version"] == "d1-horizontal-agent-input-v1"
    assert 1 < len(c3["optional_metrics"]) < 50
    assert all(item["metric_key"].startswith("ind_") for item in c3["optional_metrics"])
    assert any(item["metric_key"].startswith("fin_") for item in o4_a["optional_metrics"])
    assert "applicability" not in c3 and "output_policy" not in c3


@pytest.mark.asyncio
async def test_upstream_observation_is_rehydrated_with_lineage(tmp_path: Path) -> None:
    store = LocalWorkspaceStore(tmp_path / "workspaces")
    workspace = LocalWorkspaceClient(store)
    repository = InMemoryCodexRuntimeRepository()
    content = {"value": 123}
    content_hash = hashlib.sha256(
        json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    upstream = PersistedObservation(
        run_id="run-rebind",
        attempt_id="c1-upstream",
        block_id="source_block",
        tool_call_id="source_call",
        tool_name="source_capture",
        title="Revenue evidence",
        locator="https://example.com/evidence",
        block_type="record",
        content=content,
        content_hash=content_hash,
        source_locator="https://example.com/evidence",
        provider="test",
        method_version="1",
    )
    stored = (await workspace.import_attempt_observations("run-rebind", "c1-upstream", [upstream]))[
        0
    ]
    artifact = ArtifactRef(
        artifact_id="artifact-c1",
        run_id="run-rebind",
        node=CodexD1Node.C1,
        attempt_id="c1-upstream",
        kind=ArtifactKind.REPORT,
        relative_path="artifacts/c1/c1-upstream/report.md",
        sha256="a" * 64,
        size_bytes=1,
        content_type="text/markdown",
    )
    repository.save_artifact(artifact)
    repository.save_citation_manifest(
        CitationManifest(
            run_id="run-rebind",
            artifact_id=artifact.artifact_id,
            entries=[
                CitationEntry(
                    alias=stored.alias,
                    source_id="canonical-source-1",
                    attempt_id="c1-upstream",
                    resolved=True,
                )
            ],
        )
    )
    rebinder = UpstreamObservationRebinder(workspace, repository)
    payload = upstream_handoff(
        {
            "report_markdown": f"Revenue fact【cite:{stored.alias}】",
            "warnings": [],
            "observation_candidates": [
                {"metric_key": "fin_revenue", "source_aliases": [stored.alias]}
            ],
        },
        artifact.artifact_id,
    )
    rebound = await rebinder.rebind_payload(
        run_id="run-rebind", attempt_id="o4-a-current", payload={"upstream": payload}
    )
    handoff = rebound["upstream"]
    assert handoff["report_markdown"] == "Revenue fact【cite:O1】"
    assert handoff["observation_candidates"][0]["source_aliases"] == ["O1"]
    imported = await workspace.read_attempt_observations("run-rebind", "o4-a-current")
    assert imported[0].metadata["rehydrated"] is True
    assert imported[0].metadata["derived_from_attempt_id"] == "c1-upstream"
    assert imported[0].metadata["canonical_source_id"] == "canonical-source-1"


class _MultiWindowTool:
    def __init__(self) -> None:
        self.calls = 0

    def call(self, request):
        self.calls += 1
        return ToolResult(
            tool_name=request.tool_name,
            status=ResultStatus.SUCCEEDED,
            output={
                "fin_revenue": [
                    {"value": 100, "as_of": "2026-03-31"},
                    {"value": 120, "as_of": "2026-06-30"},
                ]
            },
        )


def test_horizontal_collector_keeps_every_window_item() -> None:
    metric = MetricDefinition(
        metric_id="fin_revenue",
        standard_name="revenue",
        definition="issuer revenue",
        requirement=MetricRequirement.REQUIRED,
        value_type=MetricValueType.NUMBER,
        default_unit="USD",
        default_time_scope="MULTI_PERIOD",
    )
    target = CollectionTargetDefinition(
        collection_target_id="c1_revenue_multi",
        metric_id="fin_revenue",
        requirement=MetricRequirement.REQUIRED,
        source_role=SourceRole.ACTUAL,
        time_scope="MULTI_PERIOD",
        entity_scope=EntityScope.ISSUER,
        collection_mode=CollectionMode.PROGRAM,
        provider="test",
        tool_name="test.multi",
        output_policy=OutputPolicy.STATE_VALUE,
        capability_status=ProviderCapabilityStatus.PRODUCTION_READY,
    )
    tools = ToolRegistry()
    tools.register("test.multi", _MultiWindowTool())
    collector = HorizontalCollector(
        tools=tools,
        metrics=MetricRegistry([metric]),
        targets=CollectionTargetRegistry([target]),
    )
    manifest, observations = collector.collect(run_id="run-multi", ticker="NVDA")
    assert [item.value for item in observations] == [100, 120]
    assert len({item.item_key for item in observations}) == 2
    assert manifest.target_results[0].requested_items == 2
    assert manifest.target_results[0].succeeded_items == 2


@pytest.mark.asyncio
async def test_program_collection_recovers_checksum_bundle_without_provider_rerun(
    tmp_path: Path,
) -> None:
    metric = MetricDefinition(
        metric_id="fin_revenue",
        standard_name="revenue",
        definition="issuer revenue",
        requirement=MetricRequirement.REQUIRED,
        value_type=MetricValueType.NUMBER,
        default_unit="USD",
        default_time_scope="MULTI_PERIOD",
    )
    target = CollectionTargetDefinition(
        collection_target_id="c1_revenue_multi",
        metric_id="fin_revenue",
        requirement=MetricRequirement.REQUIRED,
        source_role=SourceRole.ACTUAL,
        time_scope="MULTI_PERIOD",
        entity_scope=EntityScope.ISSUER,
        collection_mode=CollectionMode.PROGRAM,
        provider="test",
        tool_name="test.multi",
        output_policy=OutputPolicy.STATE_VALUE,
        capability_status=ProviderCapabilityStatus.PRODUCTION_READY,
    )
    metrics = MetricRegistry([metric])
    targets = CollectionTargetRegistry([target])
    tool = _MultiWindowTool()
    tools = ToolRegistry()
    tools.register("test.multi", tool)
    workspace = LocalWorkspaceClient(LocalWorkspaceStore(tmp_path / "workspaces"))
    repository = InMemoryCodexRuntimeRepository()
    collector = HorizontalCollector(tools=tools, metrics=metrics, targets=targets)
    orchestrator = CodexDocument1Orchestrator(
        worker=_NeverWorker(),
        workspace=workspace,
        repository=repository,
        horizontal_collector=collector,
        horizontal_compiler=HorizontalStateCompiler(metrics=metrics, targets=targets),
        prompt_root=Path("codex_assets/document1_v2"),
    )
    request = Document1V2RunRequest(
        run_id="run-program-recovery", ticker="NVDA", research_brief="test"
    )
    checkpoint = WorkflowCheckpoint(ticker="NVDA", run_id=request.run_id)
    first = await orchestrator._collect_or_restore_horizontal(request, checkpoint)
    second = await orchestrator._collect_or_restore_horizontal(request, checkpoint)
    assert first == second
    assert tool.calls == 1
    inventory = await workspace.inventory(request.run_id)
    assert any(
        item.relative_path.endswith("/targets/c1_revenue_multi.json") for item in inventory.files
    )


class _NeverWorker:
    async def run(self, request):
        raise AssertionError("worker must not run for a valid matching completion")

    async def cancel(self, job_id):
        return None


@pytest.mark.asyncio
async def test_matching_input_hash_restores_completion_without_worker(tmp_path: Path) -> None:
    workspace = LocalWorkspaceClient(LocalWorkspaceStore(tmp_path / "workspaces"))
    repository = InMemoryCodexRuntimeRepository()
    metrics = MetricRegistry([])
    targets = CollectionTargetRegistry([])
    orchestrator = CodexDocument1Orchestrator(
        worker=_NeverWorker(),
        workspace=workspace,
        repository=repository,
        horizontal_collector=HorizontalCollector(
            tools=ToolRegistry(), metrics=metrics, targets=targets
        ),
        horizontal_compiler=HorizontalStateCompiler(metrics=metrics, targets=targets),
        prompt_root=Path("codex_assets/document1_v2"),
        max_attempts=1,
    )
    request = Document1V2RunRequest(run_id="run-restore", ticker="NVDA", research_brief="restore")
    payload: dict[str, object] = {"ticker": "NVDA", "task": "pre-scan"}
    input_hash = orchestrator._attempt_bundles.input_sha256(
        node=CodexD1Node.C4_PRE_SCAN, context_payload=payload, horizontal=None
    )
    attempt = NodeAttempt(
        attempt_id="c4-pre-success",
        cutoff_at=datetime.now(UTC),
        ticker="NVDA",
        run_id=request.run_id,
        node=CodexD1Node.C4_PRE_SCAN,
        status=AttemptStatus.SUCCEEDED,
        input_sha256=input_hash,
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
    )
    repository.save_attempt(attempt)
    output = NodeOutput(status="completed", report_markdown="# restored")
    report, completion = await CodexD1ContextCompiler(workspace, repository).write_output(
        run_id=request.run_id,
        node=CodexD1Node.C4_PRE_SCAN,
        attempt_id=attempt.attempt_id,
        report_markdown=output.report_markdown,
        completion_json=output.model_dump_json(),
    )
    checkpoint = WorkflowCheckpoint(
        ticker="NVDA",
        run_id=request.run_id,
        completed_nodes=[CodexD1Node.C4_PRE_SCAN],
    )
    repository.save_checkpoint(checkpoint)
    restored, restored_ref = await orchestrator._execute_node(
        request, CodexD1Node.C4_PRE_SCAN, payload, checkpoint
    )
    assert restored.report_markdown == "# restored"
    assert restored_ref == report
    assert completion.kind is ArtifactKind.STRUCTURED_COMPLETION


def test_stale_current_node_marks_running_attempt_failed(tmp_path: Path) -> None:
    workspace = LocalWorkspaceClient(LocalWorkspaceStore(tmp_path / "workspaces"))
    repository = InMemoryCodexRuntimeRepository()
    metrics = MetricRegistry([])
    targets = CollectionTargetRegistry([])
    orchestrator = CodexDocument1Orchestrator(
        worker=_NeverWorker(),
        workspace=workspace,
        repository=repository,
        horizontal_collector=HorizontalCollector(
            tools=ToolRegistry(), metrics=metrics, targets=targets
        ),
        horizontal_compiler=HorizontalStateCompiler(metrics=metrics, targets=targets),
        prompt_root=Path("codex_assets/document1_v2"),
    )
    attempt = NodeAttempt(
        attempt_id="c1-stale",
        ticker="NVDA",
        run_id="run-stale",
        node=CodexD1Node.C1,
        status=AttemptStatus.RUNNING,
        started_at=datetime.now(UTC),
    )
    repository.save_attempt(attempt)
    checkpoint = WorkflowCheckpoint(
        ticker="NVDA", run_id="run-stale", current_nodes=[CodexD1Node.C1]
    )
    orchestrator._recover_stale_attempts("run-stale", checkpoint)
    recovered = repository.list_attempts("run-stale")[0]
    assert recovered.status is AttemptStatus.FAILED
    assert recovered.error_code == "WORKER_RESTARTED"
    assert checkpoint.current_nodes == []
