from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import timedelta
from pathlib import Path

import pytest

from doxagent.codex_runtime.repository import (
    HybridCodexRuntimeRepository,
    InMemoryCodexRuntimeRepository,
    SQLiteCodexRuntimeRepository,
)
from doxagent.codex_runtime.schema import (
    CODEX_DOCUMENT2_WORKFLOW_VERSION,
    CODEX_GLOBAL_RESEARCH_WORKFLOW_VERSION,
    ArtifactKind,
    ArtifactRef,
    AttemptStatus,
    CodexAgentRole,
    CodexD1Node,
    CodexD2AgentRole,
    CodexD2Node,
    GlobalResearchBundle,
    GlobalResearchHandoffV1,
    NodeAttempt,
    PublishedDocument,
    ResearchLane,
    ThreadRecord,
    WorkflowCheckpoint,
)
from doxagent.codex_worker.local_client import LocalWorkspaceClient
from doxagent.codex_worker.schema import (
    WorkspaceFileResponse,
)
from doxagent.codex_worker.workspace_store import LocalWorkspaceStore
from doxagent.data_runtime.policy import DataToolPolicyRegistry
from doxagent.pilot.document2_case_builder import (
    Document2PilotCaseBuilder,
    _pilot_candidate_sets_context,
    _role_for_node,
)
from doxagent.pilot.templates import render_document2_task
from doxagent.tools.registry import ToolRegistry
from doxagent.workflows.codex_document2.inputs import (
    DoxAtlasNarrativeReportProvider,
    _qualify_d1_context,
    _safe_optional_load,
)
from doxagent.workflows.codex_document2.orchestrator import CodexDocument2Orchestrator
from doxagent.workflows.codex_document2.schema import (
    CANDIDATE_DISCOVERY_SCHEMA,
    DOMAIN_REVIEW_SCHEMA,
    EXPECTATION_SHELL_SCHEMA,
    SHELL_FINALIZATION_SCHEMA,
    SHELL_SYNTHESIS_SCHEMA,
    CitationStatus,
    Document2Bundle,
    Document2Checkpoint,
    Document2HandoffV1,
    Document2RunRequest,
    InputAvailability,
)
from doxagent.workflows.codex_global_research import GlobalResearchRunRequest
from tests.fixtures.codex_document2 import (
    AS_OF,
    _CountingRemoteRepository,
    _Document2OrchestratorStub,
    _Document2Worker,
    _EmptyFinalShellWorker,
    _FailGapOnceWorker,
    _FailO1ByKindWorker,
    _FailSelectedBranchesWorker,
    _global_fixture,
    _GlobalOrchestratorStub,
    _MarketOrchestratorStub,
    _NarrativeProvider,
    _NarrativeTool,
    _PublishedStorage,
    _RaisingOptionalProvider,
    _TwoShellDocument2Worker,
)
from tests.fixtures.codex_run_services import CodexResearchLaneService


def test_document2_shared_agent_contract_recovers_from_command_mistakes() -> None:
    contract = (
        Path(__file__).parents[1] / "prompts" / "codex_v2" / "document2" / "AGENTS.md"
    ).read_text(encoding="utf-8")
    normalized = " ".join(contract.split())

    assert "Recoverable operational mistakes are not workflow blockers" in normalized
    assert "correct the command or use an equivalent safe method and continue" in normalized
    assert "Do not stop the workflow or request user assistance solely" in normalized
    assert "research_cutoff_at" in normalized
    assert "Temporal mismatch remains non-blocking" in normalized


def test_document2_agent_output_schemas_are_strict_at_every_object_boundary() -> None:
    def assert_strict(value: object) -> None:
        if isinstance(value, list):
            for item in value:
                assert_strict(item)
            return
        if not isinstance(value, dict):
            return
        if "$ref" in value:
            assert set(value) == {"$ref"}
        properties = value.get("properties")
        if isinstance(properties, dict):
            assert value.get("additionalProperties") is False
            assert value.get("required") == list(properties)
        for item in value.values():
            assert_strict(item)

    for schema in (
        CANDIDATE_DISCOVERY_SCHEMA,
        SHELL_SYNTHESIS_SCHEMA,
        DOMAIN_REVIEW_SCHEMA,
        SHELL_FINALIZATION_SCHEMA,
        EXPECTATION_SHELL_SCHEMA,
    ):
        assert_strict(schema)


def test_document2_pilot_synthesis_context_qualifies_candidate_refs() -> None:
    context = _pilot_candidate_sets_context(
        {
            CodexD2Node.O0_CANDIDATE_C1: {
                "candidates": [
                    {
                        "candidate_id": "U1",
                        "candidate": "C1 candidate",
                        "reason": "material",
                        "references": [],
                    }
                ],
                "warnings": [],
            },
            CodexD2Node.O0_CANDIDATE_C3: {
                "candidates": [
                    {
                        "candidate_id": "U1",
                        "candidate": "C3 candidate",
                        "reason": "material",
                        "references": [],
                    }
                ],
                "warnings": [],
            },
        },
        {
            CodexD2Node.O0_CANDIDATE_C1: "c1",
            CodexD2Node.O0_CANDIDATE_C3: "c3",
        },
    )

    c1_candidates = context["c1"]["candidates"]
    c3_candidates = context["c3"]["candidates"]
    assert isinstance(c1_candidates, list) and isinstance(c1_candidates[0], dict)
    assert isinstance(c3_candidates, list) and isinstance(c3_candidates[0], dict)
    assert c1_candidates[0]["candidate_ref"] == "C1:U1"
    assert c3_candidates[0]["candidate_ref"] == "C3:U1"


def test_service_get_surfaces_background_error_over_draft_bundle(tmp_path: Path) -> None:
    repository = InMemoryCodexRuntimeRepository()
    repository.save_bundle(
        Document2Bundle(
            run_id="d2-background-failure",
            ticker="MU",
            source_global_run_id="global-1",
            status="draft",
        )
    )
    service = CodexResearchLaneService(
        global_orchestrator=_GlobalOrchestratorStub(  # type: ignore[arg-type]
            GlobalResearchBundle(run_id="unused", ticker="MU", status="draft")
        ),
        market_orchestrator=_MarketOrchestratorStub(),  # type: ignore[arg-type]
        document2_orchestrator=_Document2OrchestratorStub(),  # type: ignore[arg-type]
        repository=repository,
        workspace=LocalWorkspaceClient(LocalWorkspaceStore(tmp_path / "service-workspaces")),
    )
    service._errors["d2-background-failure"] = "schema rejected"  # noqa: SLF001

    result = service.get("d2-background-failure")

    assert result is not None
    assert result["status"] == "failed"
    assert result["error"] == "schema rejected"


@pytest.mark.asyncio
async def test_full_document2_workflow_resumes_d1_and_publishes_with_unresolved_citations(
    tmp_path: Path,
) -> None:
    repository, workspace, source_run_id = await _global_fixture(tmp_path)
    narrative = _NarrativeProvider()
    worker = _Document2Worker(workspace)
    orchestrator = CodexDocument2Orchestrator(
        worker=worker,
        workspace=workspace,
        repository=repository,
        narrative_provider=narrative,
        max_attempts=1,
    )

    bundle = await orchestrator.run(
        Document2RunRequest(
            run_id="document2-run-1",
            source_global_run_id=source_run_id,
            initialization_id="init-nvda-0123456789abcdef0123456789abcdef",
        )
    )

    assert bundle.status == "published"
    assert bundle.publication_state == "COMPLETE"
    assert bundle.current is True
    assert bundle.citation_status is CitationStatus.PARTIAL
    assert bundle.handoff is not None
    assert narrative.calls == [("NVDA", AS_OF)]
    review_requests = [item for item in worker.requests if "review" in item.node.value]
    assert {item.run_id for item in review_requests} == {source_run_id}
    # Fresh attempt-local MCP capabilities are required; provenance stays in context,
    # not in a resident SDK thread carrying an older attempt's tool capability.
    assert {item.thread_id for item in review_requests} == {None}
    assert {item.initialization_id for item in worker.requests} == {
        "init-nvda-0123456789abcdef0123456789abcdef"
    }
    synthesis = next(item for item in worker.requests if item.node is CodexD2Node.O0_SYNTHESIS)
    synthesis_context_file = await workspace.read_text(
        synthesis.run_id,
        f"attempts/{synthesis.attempt_id}/input/context.json",
    )
    synthesis_context = json.loads(synthesis_context_file.content or "{}")
    for role, node in (
        ("c1", CodexD2Node.O0_CANDIDATE_C1),
        ("c3", CodexD2Node.O0_CANDIDATE_C3),
        ("c5", CodexD2Node.O0_CANDIDATE_C5),
    ):
        candidate = next(item for item in worker.requests if item.node == node)
        context = json.loads(
            (
                await workspace.read_text(
                    candidate.run_id, f"attempts/{candidate.attempt_id}/input/context.json"
                )
            ).content
        )
        assert synthesis_context["global_research"]["reports"][role] == context["primary_source"]
    assert synthesis_context["narrative_research"]["status"] == "AVAILABLE"
    assert synthesis_context["narrative_research"]["payload"]
    candidate_refs = {
        candidate["candidate_ref"]
        for candidate_set in synthesis_context["candidate_sets"].values()
        for candidate in candidate_set["candidates"]
    }
    assert candidate_refs == {
        "C1:U-C1",
        "C3:U-C3",
        "C5:U-C5",
        "NARRATIVE:U-NARRATIVE",
    }
    finalization = next(
        item for item in worker.requests if item.node is CodexD2Node.O0_FINALIZATION
    )
    assert finalization.thread_id is None
    o1_requests = [item for item in worker.requests if item.agent_role.value.startswith("o1_")]
    assert [item.node for item in o1_requests] == [
        CodexD2Node.O1_STATE,
        CodexD2Node.O1_REALIZATION,
        CodexD2Node.O1_GAPS,
        CodexD2Node.O1_FINALIZATION,
    ]
    assert len({item.run_id for item in o1_requests}) == 1
    assert o1_requests[0].thread_id is None
    assert {item.thread_id for item in o1_requests[1:]} == {f"o1-{o1_requests[0].run_id}"}
    document = repository.get_published_document(
        bundle.run_id, bundle.handoff.document2_artifact_id
    )
    assert document is not None and document.content_text is not None
    payload = json.loads(document.content_text)
    assert payload["schema_version"] == "document2.v2"
    assert payload["input_manifest"]["event_library"]["status"] == "NOT_CONFIGURED"
    assert payload["shells"][0]["core_question"] == (
        "Can durable AI demand convert into cash earnings?"
    )
    assert payload["shells"][0]["units"][0]["state"]["values"][0]["citation"] == ["【cite:O1】"]
    manifest = repository.get_published_document(
        bundle.run_id, bundle.handoff.citation_manifest_artifact_id or ""
    )
    assert manifest is not None and "UNRESOLVED" in (manifest.content_text or "")
    checkpoint = bundle.checkpoint
    assert checkpoint is not None
    shell_state = next(iter(checkpoint.shell_runs.values()))
    assert len(shell_state.snapshot_paths) == 4


@pytest.mark.asyncio
async def test_candidate_success_is_saved_before_parallel_sibling_finishes(tmp_path: Path) -> None:
    repository, workspace, source_run_id = await _global_fixture(tmp_path)
    worker = _Document2Worker(workspace)
    orchestrator = CodexDocument2Orchestrator(
        worker=worker,
        workspace=workspace,
        repository=repository,
        narrative_provider=_NarrativeProvider(InputAvailability.ABSENT),
        max_attempts=1,
    )
    original_candidate = orchestrator._run_candidate
    original_save = orchestrator._save_progress
    saved = asyncio.Event()
    blocked = asyncio.Event()

    async def candidate(**kwargs):
        if kwargs["key"] != "c1":
            await blocked.wait()
        return await original_candidate(**kwargs)

    async def save(bundle, checkpoint):
        await original_save(bundle, checkpoint)
        if "o0:candidate:c1" in checkpoint.stage_artifacts:
            saved.set()

    orchestrator._run_candidate = candidate
    orchestrator._save_progress = save
    request = Document2RunRequest(
        run_id="d2-parallel-crash", source_global_run_id=source_run_id, as_of=AS_OF
    )
    task = asyncio.create_task(orchestrator.run(request))
    try:
        await asyncio.wait_for(saved.wait(), timeout=10)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    checkpoint = repository.get_bundle(request.run_id).checkpoint
    assert "o0:candidate:c1" in checkpoint.stage_artifacts
    assert "o0:candidate:c3" not in checkpoint.stage_artifacts
    orchestrator._run_candidate = original_candidate
    orchestrator._save_progress = original_save
    await orchestrator.run(request)
    assert sum(r.node == CodexD2Node.O0_CANDIDATE_C1 for r in worker.requests) == 1


@pytest.mark.asyncio
async def test_every_o1_shell_context_contains_complete_o0_finalization(
    tmp_path: Path,
) -> None:
    repository, workspace, source_run_id = await _global_fixture(tmp_path)
    worker = _TwoShellDocument2Worker(workspace)
    await CodexDocument2Orchestrator(
        worker=worker,
        workspace=workspace,
        repository=repository,
        narrative_provider=_NarrativeProvider(InputAvailability.ABSENT),
        max_attempts=1,
    ).run(
        Document2RunRequest(
            run_id="document2-complete-o0-context",
            source_global_run_id=source_run_id,
            as_of=AS_OF - timedelta(days=30),
        )
    )

    expected_shell_ids = {"AI需求向盈利兑现", "供应执行与盈利边界"}
    assert {request.cutoff_at for request in worker.requests} == {AS_OF - timedelta(days=30)}
    o1_requests = [item for item in worker.requests if item.agent_role.value.startswith("o1_")]
    assert len(o1_requests) == 8
    for request in o1_requests:
        context_file = await workspace.read_text(
            request.run_id,
            f"attempts/{request.attempt_id}/input/context.json",
        )
        context = json.loads(context_file.content or "{}")
        assert {
            shell["shell_id"] for shell in context["o0_finalization"]["shells"]
        } == expected_shell_ids
        assert context["canonical_shell"]["shell_id"] in expected_shell_ids
        assert context["research_cutoff_at"] == (AS_OF - timedelta(days=30)).isoformat()
        assert context["source_global_research_published_at"] == AS_OF.isoformat()
        assert context["o0_finalization"]["finalization_note"] == [
            "Accepted the reviewers' horizon clarification."
        ]
    prepared_file = await workspace.read_text(
        "document2-complete-o0-context", "context/document2/prepared_inputs.json"
    )
    prepared = json.loads(prepared_file.content or "{}")
    assert prepared["as_of"] == (AS_OF - timedelta(days=30)).isoformat().replace("+00:00", "Z")
    assert (
        "requested research cutoff is preserved"
        in prepared["manifest"]["global_research"]["warning"]
    )


@pytest.mark.asyncio
async def test_absent_narrative_skips_candidate_thread(tmp_path: Path) -> None:
    repository, workspace, source_run_id = await _global_fixture(tmp_path)
    worker = _Document2Worker(workspace)
    orchestrator = CodexDocument2Orchestrator(
        worker=worker,
        workspace=workspace,
        repository=repository,
        narrative_provider=_NarrativeProvider(InputAvailability.ABSENT),
        max_attempts=1,
    )
    await orchestrator.run(
        Document2RunRequest(run_id="document2-no-narrative", source_global_run_id=source_run_id)
    )
    assert all(item.node is not CodexD2Node.O0_CANDIDATE_NARRATIVE for item in worker.requests)


@pytest.mark.asyncio
async def test_document2_uses_storage_for_all_published_bodies_when_configured(
    tmp_path: Path,
) -> None:
    repository, workspace, source_run_id = await _global_fixture(tmp_path)
    storage = _PublishedStorage()
    bundle = await CodexDocument2Orchestrator(
        worker=_Document2Worker(workspace),
        workspace=workspace,
        repository=repository,
        narrative_provider=_NarrativeProvider(InputAvailability.ABSENT),
        published_storage=storage,
        max_attempts=1,
    ).run(Document2RunRequest(run_id="document2-storage", source_global_run_id=source_run_id))
    assert bundle.handoff is not None
    assert len(storage.objects) == 5
    for artifact in bundle.artifacts.values():
        if not artifact.published:
            continue
        document = repository.get_published_document(bundle.run_id, artifact.artifact_id)
        assert document is not None
        assert document.content_text is None
        assert document.storage_path in storage.objects


@pytest.mark.asyncio
async def test_partial_publish_resumes_from_last_successful_shell_turn(tmp_path: Path) -> None:
    repository, workspace, source_run_id = await _global_fixture(tmp_path)
    worker = _FailGapOnceWorker(workspace)
    orchestrator = CodexDocument2Orchestrator(
        worker=worker,
        workspace=workspace,
        repository=repository,
        narrative_provider=_NarrativeProvider(InputAvailability.ABSENT),
        max_attempts=1,
    )
    request = Document2RunRequest(run_id="document2-recovery", source_global_run_id=source_run_id)
    first = await orchestrator.run(request)
    assert first.status == "published"
    assert first.publication_state == "PARTIAL"
    assert first.current is False
    workflow_checkpoint = repository.get_checkpoint(request.run_id)
    assert workflow_checkpoint is not None
    assert CodexD2Node.O1_FINALIZATION in workflow_checkpoint.completed_nodes
    assert all(outcome.status == "completed" for outcome in first.shell_outcomes)
    assert any("D2_ACCEPTANCE" in warning for warning in first.checkpoint.warnings)
    first_o1 = [item.node for item in worker.requests if item.agent_role.value.startswith("o1_")]
    assert first_o1 == [
        CodexD2Node.O1_STATE,
        CodexD2Node.O1_REALIZATION,
        CodexD2Node.O1_GAPS,
        CodexD2Node.O1_FINALIZATION,
    ]
    calls = len(worker.requests)
    adopted = await orchestrator.run(request.model_copy(update={"reuse_published_partial": True}))
    assert adopted.handoff == first.handoff
    second = await orchestrator.run(request)
    assert second.publication_state == "PARTIAL"
    assert len(worker.requests) == calls  # Accepted degraded stages are not re-researched.


@pytest.mark.asyncio
async def test_candidate_and_review_branch_failures_remain_non_blocking(tmp_path: Path) -> None:
    repository, workspace, source_run_id = await _global_fixture(tmp_path)
    bundle = await CodexDocument2Orchestrator(
        worker=_FailSelectedBranchesWorker(workspace),
        workspace=workspace,
        repository=repository,
        narrative_provider=_NarrativeProvider(InputAvailability.ABSENT),
        max_attempts=1,
    ).run(
        Document2RunRequest(
            run_id="document2-branch-degrade",
            source_global_run_id=source_run_id,
        )
    )

    assert bundle.publication_state == "PARTIAL"
    assert bundle.checkpoint is not None
    assert any("D2_ACCEPTANCE" in item for item in bundle.checkpoint.warnings)
    assert any("D2_ACCEPTANCE" in item for item in bundle.checkpoint.warnings)


@pytest.mark.asyncio
async def test_zero_final_shells_publish_partial_instead_of_vacuous_complete(
    tmp_path: Path,
) -> None:
    repository, workspace, source_run_id = await _global_fixture(tmp_path)
    bundle = await CodexDocument2Orchestrator(
        worker=_EmptyFinalShellWorker(workspace),
        workspace=workspace,
        repository=repository,
        narrative_provider=_NarrativeProvider(InputAvailability.ABSENT),
        max_attempts=1,
    ).run(
        Document2RunRequest(
            run_id="document2-empty-shells",
            source_global_run_id=source_run_id,
        )
    )

    assert bundle.publication_state == "PARTIAL"
    assert bundle.current is False
    assert bundle.shell_outcomes == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_kind", ["format", "transient"])
async def test_degradable_o1_failures_still_publish_partial(
    tmp_path: Path, failure_kind: str
) -> None:
    repository, workspace, source_run_id = await _global_fixture(tmp_path)
    bundle = await CodexDocument2Orchestrator(
        worker=_FailO1ByKindWorker(workspace, failure_kind),
        workspace=workspace,
        repository=repository,
        narrative_provider=_NarrativeProvider(InputAvailability.ABSENT),
        max_attempts=1,
    ).run(
        Document2RunRequest(
            run_id=f"document2-{failure_kind}-partial",
            source_global_run_id=source_run_id,
        )
    )

    assert bundle.publication_state == "PARTIAL"
    assert bundle.shell_outcomes[0].status == "completed"
    assert any("D2_ACCEPTANCE" in warning for warning in bundle.checkpoint.warnings)


@pytest.mark.asyncio
async def test_parallel_o1_transport_failure_waits_for_healthy_shell_and_publishes_partial(
    tmp_path: Path,
) -> None:
    repository, workspace, source_run_id = await _global_fixture(tmp_path)
    orchestrator = CodexDocument2Orchestrator(
        worker=_TwoShellDocument2Worker(workspace),
        workspace=workspace,
        repository=repository,
        narrative_provider=_NarrativeProvider(InputAvailability.ABSENT),
        max_attempts=1,
    )
    original = orchestrator._research_shell

    async def one_closed_client(**kwargs):
        if kwargs["seed"].shell_id == "AI需求向盈利兑现":
            raise RuntimeError("Cannot send a request, as the client has been closed.")
        return await original(**kwargs)

    orchestrator._research_shell = one_closed_client
    bundle = await orchestrator.run(
        Document2RunRequest(
            run_id="d2-transport-partial",
            source_global_run_id=source_run_id,
        )
    )

    assert bundle.publication_state == "PARTIAL"
    assert [item.status for item in bundle.shell_outcomes] == ["failed", "completed"]
    assert bundle.shell_outcomes[0].error_code == "RuntimeError"


@pytest.mark.asyncio
async def test_invalid_request_schema_is_not_misreported_as_shell_partial(tmp_path: Path) -> None:
    repository, workspace, source_run_id = await _global_fixture(tmp_path)
    worker = _FailO1ByKindWorker(workspace, "system")
    orchestrator = CodexDocument2Orchestrator(
        worker=worker,
        workspace=workspace,
        repository=repository,
        narrative_provider=_NarrativeProvider(InputAvailability.ABSENT),
        max_attempts=2,
    )

    bundle = await orchestrator.run(
        Document2RunRequest(
            run_id="document2-system-failure",
            source_global_run_id=source_run_id,
        )
    )
    assert bundle.status == "published" and bundle.publication_state == "PARTIAL"
    assert all(outcome.status == "completed" for outcome in bundle.shell_outcomes)
    assert len([item for item in worker.requests if item.node is CodexD2Node.O1_STATE]) == 1
    assert any("D2_ACCEPTANCE" in warning for warning in bundle.checkpoint.warnings)


@pytest.mark.asyncio
async def test_doxatlas_narrative_provider_enforces_seven_day_window() -> None:
    recent_tools = ToolRegistry()
    recent_tools.register("doxa_get_narrative_report", _NarrativeTool("2026-08-16T12:00:00Z"))
    recent = await DoxAtlasNarrativeReportProvider(recent_tools).load(ticker="NVDA", as_of=AS_OF)
    assert recent.status is InputAvailability.AVAILABLE
    assert recent.source_run_id == "narrative-tool-run"

    stale_tools = ToolRegistry()
    stale_tools.register("doxa_get_narrative_report", _NarrativeTool("2026-08-10T11:59:59Z"))
    stale = await DoxAtlasNarrativeReportProvider(stale_tools).load(ticker="NVDA", as_of=AS_OF)
    assert stale.status is InputAvailability.ABSENT

    unavailable = await _safe_optional_load(
        _RaisingOptionalProvider(),
        ticker="NVDA",
        as_of=AS_OF,
        label="Event Library",
    )
    assert unavailable.status is InputAvailability.UNAVAILABLE
    assert "provider failed" in (unavailable.warning or "")


def test_document2_pilot_contract_preserves_roles_and_formal_output(tmp_path: Path) -> None:
    assert _role_for_node(CodexD2Node.O0_REVIEW_C1) is CodexAgentRole.C1
    assert _role_for_node(CodexD2Node.O1_STATE).value == "o1_expectation_owner"
    task = render_document2_task(
        case_root=tmp_path / "case",
        node=CodexD2Node.O1_GAPS.value,
        run_id="d2ws-pilot",
        attempt_id="d2-o1-gaps-1",
    )
    assert "这不是 smoke test" in task
    assert "output/completion.json" in task
    assert "引用失败或未解析只能作为 warning" in task


def test_document2_data_policy_preserves_review_tools_and_gives_o1_research_union() -> None:
    policy = DataToolPolicyRegistry()
    assert (
        policy.allowed_tools(
            CodexD2Node.O0_SYNTHESIS,
            _role_for_node(CodexD2Node.O0_SYNTHESIS),
        )
        == frozenset()
    )
    assert policy.allowed_tools(CodexD2Node.O0_REVIEW_C1, CodexAgentRole.C1) == (
        policy.allowed_tools(CodexD1Node.C1, CodexAgentRole.C1)
    )
    for candidate, source_node, source_role in (
        (CodexD2Node.O0_CANDIDATE_C1, CodexD1Node.C1, CodexAgentRole.C1),
        (CodexD2Node.O0_CANDIDATE_C3, CodexD1Node.C3, CodexAgentRole.C3),
        (CodexD2Node.O0_CANDIDATE_C5, CodexD1Node.C5, CodexAgentRole.C5),
    ):
        assert policy.allowed_tools(candidate, _role_for_node(candidate)) == (
            policy.allowed_tools(source_node, source_role)
        )
    o1 = policy.allowed_tools(CodexD2Node.O1_STATE, _role_for_node(CodexD2Node.O1_STATE))
    assert policy.allowed_tools(CodexD1Node.C1, CodexAgentRole.C1).issubset(o1)
    assert policy.allowed_tools(CodexD1Node.C3, CodexAgentRole.C3).issubset(o1)
    assert policy.allowed_tools(CodexD1Node.C5, CodexAgentRole.C5).issubset(o1)


def test_document2_qualifies_nested_document1_context_citations() -> None:
    source = {
        "future_nodes": [
            {
                "event": "capacity milestone 【cite:O688】",
                "references": ["【cite:O691】", "D1-O7", "O9"],
            }
        ]
    }

    qualified = _qualify_d1_context(source)

    assert qualified["future_nodes"][0]["event"] == ("capacity milestone 【cite:D1-O688】")
    assert qualified["future_nodes"][0]["references"] == [
        "【cite:D1-O691】",
        "D1-O7",
        "O9",
    ]


@pytest.mark.asyncio
async def test_document2_pilot_bootstrap_reads_and_caches_horizontal_bundle(
    tmp_path: Path,
) -> None:
    content = '{"metric":{"note":"source 【cite:O7】"}}'
    raw = content.encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    repository = InMemoryCodexRuntimeRepository()
    repository.save_artifact(
        ArtifactRef(
            workflow_version=CODEX_GLOBAL_RESEARCH_WORKFLOW_VERSION,
            research_lane=ResearchLane.GLOBAL_RESEARCH,
            artifact_id="horizontal-artifact",
            run_id="global-horizontal",
            node=CodexD1Node.PROGRAM_COLLECTION,
            attempt_id="horizontal-attempt",
            kind=ArtifactKind.BUNDLE,
            relative_path="context/horizontal.json",
            sha256=digest,
            size_bytes=len(raw),
            content_type="application/json",
            published=True,
        )
    )

    class _HorizontalClient:
        calls = 0

        async def read_text(self, run_id: str, relative_path: str) -> WorkspaceFileResponse:
            assert run_id == "global-horizontal"
            assert relative_path == "context/horizontal.json"
            self.calls += 1
            return WorkspaceFileResponse(
                relative_path=relative_path,
                sha256=digest,
                size_bytes=len(raw),
                content_type="application/json",
                content=content,
            )

    client = _HorizontalClient()
    builder = object.__new__(Document2PilotCaseBuilder)
    builder._repository = repository  # noqa: SLF001
    builder._source_cache_root = tmp_path / "sources"  # noqa: SLF001
    builder._client = client  # type: ignore[assignment]  # noqa: SLF001
    bundle = GlobalResearchBundle(
        run_id="global-horizontal",
        ticker="MU",
        status="draft",
    )

    first, first_id = await builder._bootstrap_horizontal(bundle)  # noqa: SLF001
    second, second_id = await builder._bootstrap_horizontal(bundle)  # noqa: SLF001

    assert first_id == second_id == "horizontal-artifact"
    assert first == second == {"metric": {"note": "source 【cite:D1-O7】"}}
    assert client.calls == 1


@pytest.mark.asyncio
async def test_global_publish_waits_for_total_initialization_before_document2(
    tmp_path: Path,
) -> None:
    repository = InMemoryCodexRuntimeRepository()
    source = GlobalResearchBundle(
        run_id="global-auto",
        ticker="NVDA",
        status="published",
        handoff=GlobalResearchHandoffV1(
            run_id="global-auto",
            ticker="NVDA",
            document_artifact_id="global-auto-document",
            published_at=AS_OF,
        ),
        published_at=AS_OF,
    )
    repository.save_bundle(source)
    document2 = _Document2OrchestratorStub()
    service = CodexResearchLaneService(
        global_orchestrator=_GlobalOrchestratorStub(source),  # type: ignore[arg-type]
        market_orchestrator=_MarketOrchestratorStub(),  # type: ignore[arg-type]
        document2_orchestrator=document2,  # type: ignore[arg-type]
        repository=repository,
        workspace=LocalWorkspaceClient(LocalWorkspaceStore(tmp_path / "service-workspaces")),
    )
    started = await service.start(
        GlobalResearchRunRequest(run_id="global-auto", ticker="NVDA", research_brief="test")
    )
    await service._tasks[str(started["run_id"])]  # noqa: SLF001
    assert set(service._tasks) == {"global-auto"}
    assert document2.requests == []


def test_sqlite_round_trips_document2_bundle(tmp_path: Path) -> None:
    repository = SQLiteCodexRuntimeRepository(tmp_path / "codex.sqlite3")
    bundle = Document2Bundle(
        run_id="d2-sqlite",
        ticker="NVDA",
        source_global_run_id="global-1",
        status="draft",
    )
    repository.save_bundle(bundle)
    restored = repository.get_bundle(bundle.run_id)
    assert isinstance(restored, Document2Bundle)
    assert restored == bundle


def test_hybrid_keeps_document2_recovery_state_local_and_syncs_lifecycle_only(
    tmp_path: Path,
) -> None:
    local = SQLiteCodexRuntimeRepository(tmp_path / "hybrid.sqlite3")
    remote = _CountingRemoteRepository()
    repository = HybridCodexRuntimeRepository(local=local, remote=remote)
    checkpoint = Document2Checkpoint(
        run_id="d2-hybrid",
        source_global_run_id="global-1",
        o0_workspace_run_id="d2-hybrid-o0",
    )
    bundle = Document2Bundle(
        run_id="d2-hybrid",
        ticker="NVDA",
        source_global_run_id="global-1",
        status="draft",
        checkpoint=checkpoint,
    )
    repository.save_bundle(bundle)
    for index in range(5):
        checkpoint.warnings = [f"local-progress-{index}"]
        bundle.checkpoint = checkpoint
        repository.save_bundle(bundle)
    assert remote.calls["save_bundle"] == 1
    remote_projection = remote.get_bundle(bundle.run_id)
    assert isinstance(remote_projection, Document2Bundle)
    assert remote_projection.checkpoint is not None
    assert remote_projection.checkpoint.warnings == []

    attempt = NodeAttempt(
        workflow_version=CODEX_DOCUMENT2_WORKFLOW_VERSION,
        research_lane=ResearchLane.DOCUMENT2,
        attempt_id="d2-state-1",
        ticker="NVDA",
        run_id=bundle.run_id,
        node=CodexD2Node.O1_STATE,
        status=AttemptStatus.SUCCEEDED,
        attempt_number=1,
    )
    repository.save_attempt(attempt)
    assert repository.next_attempt_number(bundle.run_id, CodexD2Node.O1_STATE) == 2
    assert remote.calls.get("save_attempt", 0) == 0
    assert remote.calls.get("list_attempts", 0) == 0

    workflow_checkpoint = WorkflowCheckpoint(
        workflow_version=CODEX_DOCUMENT2_WORKFLOW_VERSION,
        research_lane=ResearchLane.DOCUMENT2,
        ticker="NVDA",
        run_id=bundle.run_id,
    )
    repository.save_checkpoint(workflow_checkpoint)
    assert local.get_checkpoint(bundle.run_id) == workflow_checkpoint
    assert remote.get_checkpoint(bundle.run_id) is None

    artifact = ArtifactRef(
        workflow_version=CODEX_DOCUMENT2_WORKFLOW_VERSION,
        research_lane=ResearchLane.DOCUMENT2,
        artifact_id="d2-artifact",
        run_id=bundle.run_id,
        node=CodexD2Node.ASSEMBLE,
        attempt_id="d2-assemble-1",
        kind=ArtifactKind.BUNDLE,
        relative_path="artifacts/document2/document2.json",
        sha256="a" * 64,
        size_bytes=4,
        content_type="application/json",
    )
    repository.save_artifact(artifact)
    assert repository.get_artifact_by_path(bundle.run_id, artifact.relative_path) == artifact
    assert remote.calls.get("save_artifact", 0) == 0
    published_artifact = artifact.model_copy(update={"published": True})
    repository.save_artifact(published_artifact)
    repository.save_published_document(
        PublishedDocument(
            artifact_id=artifact.artifact_id,
            run_id=bundle.run_id,
            artifact_kind="bundle",
            sha256=artifact.sha256,
            size_bytes=4,
            content_type=artifact.content_type,
            content_text="body",
            published_at=AS_OF,
        )
    )
    assert remote.calls["save_artifact"] == 1
    assert remote.calls.get("save_published_document", 0) == 0
    assert repository.get_published_document(bundle.run_id, artifact.artifact_id) is not None

    thread = ThreadRecord(
        workflow_version=CODEX_DOCUMENT2_WORKFLOW_VERSION,
        research_lane=ResearchLane.DOCUMENT2,
        ticker="NVDA",
        run_id=bundle.run_id,
        agent_role=CodexD2AgentRole.O1,
        thread_id="o1-thread",
        model="test-model",
    )
    repository.save_thread(thread)
    repository.save_thread(thread)
    assert remote.calls["save_thread"] == 1

    published_at = AS_OF
    published = bundle.model_copy(
        update={
            "status": "published",
            "publication_state": "COMPLETE",
            "citation_status": CitationStatus.PARTIAL,
            "handoff": Document2HandoffV1(
                run_id=bundle.run_id,
                ticker=bundle.ticker,
                source_global_run_id=bundle.source_global_run_id,
                document2_artifact_id=artifact.artifact_id,
                publication_state="COMPLETE",
                citation_status=CitationStatus.PARTIAL,
                published_at=published_at,
            ),
            "current": True,
            "published_at": published_at,
        }
    )
    repository.save_bundle(published)
    assert remote.calls["save_bundle"] == 2
    restored = repository.get_bundle(bundle.run_id)
    assert isinstance(restored, Document2Bundle)
    assert restored.checkpoint is not None
    assert restored.checkpoint.warnings == ["local-progress-4"]
