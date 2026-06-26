import threading
import time

import pytest

from doxagent.agents import MockAgentRunner, default_agent_registry
from doxagent.blackboard.state import BlackboardRun
from doxagent.models import (
    AgentError,
    AgentName,
    AgentResult,
    AgentTask,
    BlackboardTarget,
    DocumentType,
    EvidenceRef,
    EvidenceSourceType,
    Objection,
    ObjectionSeverity,
    ObjectionStatus,
    ResearchSection,
    ResultStatus,
    new_id,
)
from doxagent.settings import DoxAgentSettings
from doxagent.workflows import (
    INITIALIZATION_NODES,
    BlackboardInitializationWorkflow,
    InitializationMockResultFactory,
    WorkflowCheckpoint,
    WorkflowNode,
    WorkflowRunStatus,
)
from doxagent.workflows.errors import WorkflowContractError


class NoFullLoadRepository:
    def __init__(self, inner: object) -> None:
        self.inner = inner
        self.full_get_called = False

    def add(self, run: BlackboardRun) -> BlackboardRun:
        return self.inner.add(run)

    def get(self, run_id: str) -> BlackboardRun:
        self.full_get_called = True
        raise AssertionError("no-op construction resolver must not full-load BlackboardRun")

    def save(self, run: BlackboardRun) -> BlackboardRun:
        return self.inner.save(run)

    def list_by_ticker(self, ticker: str, *, limit: int = 20) -> list[BlackboardRun]:
        return self.inner.list_by_ticker(ticker, limit=limit)

    def mutate(self, run_id: str, mutator: object) -> BlackboardRun:
        return self.inner.mutate(run_id, mutator)

    def list_unresolved_objections(self, run_id: str) -> list[Objection]:
        return self.inner.list_unresolved_objections(run_id)

    def list_blocking_delegations(
        self,
        run_id: str,
        *,
        target_agent: AgentName | None = None,
    ) -> list[object]:
        return self.inner.list_blocking_delegations(run_id, target_agent=target_agent)

    def summary_counts(self, run_id: str) -> dict[str, int]:
        return self.inner.summary_counts(run_id)


class ParallelStructuredInitializationRunner:
    def __init__(
        self,
        barrier_counts: dict[str, int] | None = None,
        *,
        fail_detail_ids: set[str] | None = None,
        fail_detail_once_ids: set[str] | None = None,
        hang_detail_ids: set[str] | None = None,
        timeout_detail_until_recovery_ids: set[str] | None = None,
        fail_research_once_agents: set[AgentName] | None = None,
        fail_resolve_o1_once: bool = False,
        include_blockers: bool = False,
    ) -> None:
        self.factory = InitializationMockResultFactory(include_blockers=include_blockers)
        self.barriers = {
            node: threading.Barrier(count)
            for node, count in (barrier_counts or {}).items()
        }
        self.fail_detail_ids = fail_detail_ids or set()
        self.fail_detail_once_ids = fail_detail_once_ids or set()
        self.hang_detail_ids = hang_detail_ids or set()
        self.timeout_detail_until_recovery_ids = timeout_detail_until_recovery_ids or set()
        self.fail_research_once_agents = fail_research_once_agents or set()
        self.fail_resolve_o1_once = fail_resolve_o1_once
        self.tasks: list[AgentTask] = []
        self.research_calls: dict[AgentName, int] = {}
        self.detail_calls: dict[str, int] = {}
        self.resolve_o1_calls = 0
        self._active: dict[str, int] = {}
        self.max_active: dict[str, int] = {}
        self._lock = threading.Lock()

    def run(self, task: AgentTask) -> AgentResult:
        node = task.run_metadata.workflow_node or "unknown"
        self._enter_node(node, task)
        try:
            if task.required_output_schema == "ResearchSection":
                self.research_calls[task.agent_name] = (
                    self.research_calls.get(task.agent_name, 0) + 1
                )
                if (
                    task.agent_name in self.fail_research_once_agents
                    and self.research_calls[task.agent_name] == 1
                ):
                    return AgentResult(
                        task_id=task.task_id,
                        agent_name=task.agent_name,
                        status=ResultStatus.FAILED,
                        error=AgentError(
                            code="forced_research_failure",
                            message=f"forced failure for {task.agent_name.value}",
                            retryable=True,
                        ),
                    )
                return self._research_section(task)
            if (
                node == WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS.value
                and task.agent_name is AgentName.O1_EXPECTATION_OWNER
            ):
                self.resolve_o1_calls += 1
                if self.fail_resolve_o1_once and self.resolve_o1_calls == 1:
                    return AgentResult(
                        task_id=task.task_id,
                        agent_name=task.agent_name,
                        status=ResultStatus.FAILED,
                        error=AgentError(
                            code="model_request_timeout",
                            message="forced O1 resolution timeout",
                            retryable=True,
                        ),
                    )
                return self._objection_resolution(task)
            shell = task.input_context.get("expectation_shell")
            if node == WorkflowNode.GENERATE_EXPECTATION_DETAILS.value and isinstance(shell, dict):
                expectation_id = str(shell.get("expectation_id") or "")
                self.detail_calls[expectation_id] = self.detail_calls.get(expectation_id, 0) + 1
                if (
                    expectation_id in self.hang_detail_ids
                    and "detail_recovery_retry" not in task.input_context
                ):
                    time.sleep(60)
                if (
                    expectation_id in self.timeout_detail_until_recovery_ids
                    and "detail_recovery_retry" not in task.input_context
                ):
                    return AgentResult(
                        task_id=task.task_id,
                        agent_name=task.agent_name,
                        status=ResultStatus.FAILED,
                        error=AgentError(
                            code="model_gateway_error",
                            message=f"forced model timeout for {expectation_id}",
                            retryable=True,
                            details={
                                "gateway_error": {
                                    "code": "model_request_timeout",
                                    "message": f"forced model timeout for {expectation_id}",
                                }
                            },
                        ),
                    )
                should_fail_once = (
                    expectation_id in self.fail_detail_once_ids
                    and self.detail_calls[expectation_id] == 1
                )
                if expectation_id in self.fail_detail_ids or should_fail_once:
                    return AgentResult(
                        task_id=task.task_id,
                        agent_name=task.agent_name,
                        status=ResultStatus.FAILED,
                        error=AgentError(
                            code="forced_detail_failure",
                            message=f"forced failure for {expectation_id}",
                            retryable=True,
                        ),
                    )
            direct = self.factory(task)
            return self._structured(task, direct)
        finally:
            self._leave_node(node)

    def _enter_node(self, node: str, task: AgentTask) -> None:
        with self._lock:
            self.tasks.append(task)
            active = self._active.get(node, 0) + 1
            self._active[node] = active
            self.max_active[node] = max(self.max_active.get(node, 0), active)
        barrier = self.barriers.get(node)
        if barrier is not None:
            try:
                barrier.wait(timeout=3)
            except threading.BrokenBarrierError as exc:
                raise AssertionError(f"parallel barrier was not reached for {node}") from exc

    def _leave_node(self, node: str) -> None:
        with self._lock:
            self._active[node] = self._active.get(node, 1) - 1

    def _research_section(self, task: AgentTask) -> AgentResult:
        evidence = self._evidence(task)
        section = ResearchSection(
            text=f"{task.ticker} {task.agent_name.value} research text.",
            summary=f"{task.ticker} {task.agent_name.value} research summary.",
            evidence_refs=[evidence],
            author_agent=task.agent_name,
            reviewer_agents=[AgentName.O1_EXPECTATION_OWNER],
        )
        return AgentResult(
            task_id=task.task_id,
            agent_name=task.agent_name,
            status=ResultStatus.SUCCEEDED,
            payload={"runtime": "maf", "structured": section.model_dump(mode="json")},
        )

    def _structured(self, task: AgentTask, direct: AgentResult) -> AgentResult:
        if task.required_output_schema == "ExpectationShellConstructionResult":
            structured = dict(direct.payload)
        elif task.required_output_schema == "ExpectationDetailCandidateResult":
            structured = dict(direct.payload)
        elif task.required_output_schema == "ExpectationDetailResult":
            structured = {
                "proposed_patches": [
                    patch.model_dump(mode="json") for patch in direct.proposed_patches
                ],
                "evidence_refs": [
                    evidence.model_dump(mode="json") for evidence in direct.evidence_refs
                ],
                "delegations": [
                    delegation.model_dump(mode="json") for delegation in direct.delegations
                ],
                "unknowns": [],
                "rationale": "Structured expectation detail test output.",
            }
        elif task.required_output_schema == "DoxAtlasAuditResult":
            structured = {
                "findings": [],
                "evidence_refs": [
                    evidence.model_dump(mode="json") for evidence in direct.evidence_refs
                ],
                "objections": [
                    objection.model_dump(mode="json") for objection in direct.objections
                ],
                "delegations": [
                    delegation.model_dump(mode="json") for delegation in direct.delegations
                ],
                "unknowns": [],
                "rationale": "Structured audit test output.",
            }
        elif task.required_output_schema == "ExpectationFieldReviewResult":
            structured = {
                "findings": [],
                "evidence_refs": [
                    evidence.model_dump(mode="json") for evidence in direct.evidence_refs
                ],
                "objections": [],
                "delegations": [],
                "unknowns": [],
                "rationale": "Structured expectation field review test output.",
            }
        elif task.required_output_schema == "DelegatedRetrievalResult":
            structured = dict(direct.payload)
        else:
            structured = {
                "payload": direct.payload,
                "proposed_patches": [
                    patch.model_dump(mode="json") for patch in direct.proposed_patches
                ],
                "evidence_refs": [
                    evidence.model_dump(mode="json") for evidence in direct.evidence_refs
                ],
                "objections": [
                    objection.model_dump(mode="json") for objection in direct.objections
                ],
                "delegations": [
                    delegation.model_dump(mode="json") for delegation in direct.delegations
                ],
            }
        return AgentResult(
            task_id=task.task_id,
            agent_name=task.agent_name,
            status=direct.status,
            payload={"runtime": "maf", "structured": structured},
        )

    def _objection_resolution(self, task: AgentTask) -> AgentResult:
        evidence = self._evidence(task)
        objections = task.input_context.get("unresolved_objections")
        objection_items = [
            item
            for item in objections
            if isinstance(item, dict) and isinstance(item.get("objection_id"), str)
        ] if isinstance(objections, list) else []
        objection_ids = [item["objection_id"] for item in objection_items]
        expectation_id = "exp_mock_core"
        if objection_items:
            target = objection_items[0].get("target")
            if isinstance(target, dict) and isinstance(target.get("expectation_id"), str):
                expectation_id = target["expectation_id"]
        structured = {
            "expectation_id": expectation_id,
            "decision": "resolved",
            "decisions": [
                {
                    "objection_id": objection_id,
                    "finding_id": None,
                    "decision": "resolved",
                    "resolution_note": (
                        "Mock O1 retry resolved this objection with supporting evidence."
                    ),
                    "changed_paths": ["expectation_unit.document"],
                    "evidence_refs": [evidence.model_dump(mode="json")],
                }
                for objection_id in objection_ids
            ],
            "target_finding_ids": [],
            "revised_candidate": None,
            "evidence_requests": [],
            "unresolved_finding_ids": [],
            "unresolved_reason": None,
            "rationale": "Mock O1 resolved field-review objections after retry.",
        }
        return AgentResult(
            task_id=task.task_id,
            agent_name=task.agent_name,
            status=ResultStatus.SUCCEEDED,
            payload={"runtime": "maf", "structured": structured},
            evidence_refs=[evidence],
        )

    def _evidence(self, task: AgentTask) -> EvidenceRef:
        return EvidenceRef(
            evidence_id=new_id("evidence"),
            source_type=EvidenceSourceType.AGENT_OUTPUT,
            source_id=f"test:{task.run_metadata.workflow_node}:{task.agent_name.value}",
            title=f"{task.agent_name.value} evidence",
            summary="Structured parallel workflow test evidence.",
            confidence=0.8,
            citation_scope="test.initialization.parallel",
        )


def test_initialization_workflow_runs_mock_ticker_to_completion() -> None:
    workflow = BlackboardInitializationWorkflow(execution_mode="mock")

    result = workflow.run("NVDA")

    assert result.status is WorkflowRunStatus.COMPLETED
    assert result.checkpoint.completed_nodes == list(INITIALIZATION_NODES)
    assert result.summary.stable_document_types == [
        DocumentType.GLOBAL_RESEARCH,
        DocumentType.EXPECTATION_UNIT,
        DocumentType.KNOWN_EVENTS,
        DocumentType.MONITORING_CONFIG,
        DocumentType.MONITORING_POLICY,
    ]
    assert result.summary.commit_count == 7
    assert result.summary.working_memory_count >= 5

    run = workflow.blackboard.get_run(result.checkpoint.run_id)
    assert set(run.belief_state.documents) == {
        DocumentType.GLOBAL_RESEARCH,
        DocumentType.EXPECTATION_UNIT,
        DocumentType.KNOWN_EVENTS,
        DocumentType.MONITORING_CONFIG,
        DocumentType.MONITORING_POLICY,
    }
    assert len(run.commit_log) == 7
    assert run.working_memory
    assert WorkflowNode.REVIEW_MONITORING_CONFIG in result.checkpoint.completed_nodes
    assert WorkflowNode.RESOLVE_MONITORING_CONFIG in result.checkpoint.completed_nodes
    assert WorkflowNode.REVIEW_MONITORING_POLICY in result.checkpoint.completed_nodes
    assert WorkflowNode.RESOLVE_MONITORING_POLICY in result.checkpoint.completed_nodes
    content_types = {entry.content_type for entry in run.working_memory}
    assert "c1_monitoring_config_review" in content_types
    assert "c3_monitoring_config_review" in content_types
    assert "o2_monitoring_policy_review" in content_types
    assert run.objections[0].is_unresolved is False
    assert run.delegations[0].is_blocking is False


def test_initialization_workflow_enforces_document_order() -> None:
    workflow = BlackboardInitializationWorkflow(execution_mode="mock")
    partial = workflow.run("NVDA", stop_after=WorkflowNode.START_TICKER_INITIALIZATION)
    bad_checkpoint = partial.checkpoint.model_copy(
        update={"next_node": WorkflowNode.GENERATE_KNOWN_EVENTS},
        deep=True,
    )

    result = workflow.resume(bad_checkpoint)

    assert result.status is WorkflowRunStatus.BLOCKED
    assert result.error is not None
    assert "global_research" in result.error
    assert workflow.blackboard.get_run(partial.checkpoint.run_id).commit_log == []


def test_document3_registry_permissions_split_config_and_policy_documents() -> None:
    registry = default_agent_registry()
    o2_permissions = registry.get(AgentName.O2_MONITORING_CONFIG).runtime.to_permissions()
    o4_permissions = registry.get(AgentName.O4_MARKET_TRACE).runtime.to_permissions()

    assert DocumentType.MONITORING_CONFIG.value in o2_permissions.writable_targets
    assert DocumentType.MONITORING_POLICY.value not in o2_permissions.writable_targets
    assert DocumentType.MONITORING_POLICY.value in o4_permissions.writable_targets


def test_blockers_stop_expectation_promotion_without_commit() -> None:
    workflow = BlackboardInitializationWorkflow(execution_mode="mock", auto_resolve_blockers=False)

    result = workflow.run("NVDA")

    assert result.status is WorkflowRunStatus.BLOCKED
    assert result.checkpoint.next_node is WorkflowNode.PROMOTE_EXPECTATION_TO_BELIEF_STATE
    assert result.summary.stable_document_types == [DocumentType.GLOBAL_RESEARCH]
    assert result.summary.commit_count == 1
    assert result.summary.unresolved_objection_count == 1
    assert result.summary.blocking_delegation_count == 1

    run = workflow.blackboard.get_run(result.checkpoint.run_id)
    assert set(run.belief_state.documents) == {DocumentType.GLOBAL_RESEARCH}
    assert len(run.commit_log) == 1


def test_blocked_checkpoint_can_resume_after_manual_resolution() -> None:
    workflow = BlackboardInitializationWorkflow(execution_mode="mock", auto_resolve_blockers=False)
    blocked = workflow.run("NVDA")
    run = workflow.blackboard.get_run(blocked.checkpoint.run_id)

    workflow.blackboard.resolve_objection(
        blocked.checkpoint.run_id,
        run.objections[0].objection_id,
        "Manual review resolved the objection.",
    )
    workflow.blackboard.complete_delegation(
        blocked.checkpoint.run_id,
        run.delegations[0].delegation_id,
        "Manual fact-check completed.",
    )

    resumed = workflow.resume(blocked.checkpoint)

    assert resumed.status is WorkflowRunStatus.COMPLETED
    assert resumed.summary.commit_count == 7
    assert resumed.summary.unresolved_objection_count == 0
    assert resumed.summary.blocking_delegation_count == 0


def test_checkpoint_round_trips_and_resumes_in_same_process() -> None:
    workflow = BlackboardInitializationWorkflow(execution_mode="mock")
    partial = workflow.run("NVDA", stop_after=WorkflowNode.GENERATE_EXPECTATION_DETAILS)

    restored = WorkflowCheckpoint.model_validate_json(partial.checkpoint.model_dump_json())
    resumed = workflow.resume(restored)

    assert resumed.status is WorkflowRunStatus.COMPLETED
    assert resumed.checkpoint.completed_nodes == list(INITIALIZATION_NODES)
    assert resumed.summary.commit_count == 7


def test_construction_resolver_noop_avoids_full_blackboard_load() -> None:
    workflow = BlackboardInitializationWorkflow(execution_mode="mock")
    partial = workflow.run("NVDA", stop_after=WorkflowNode.REVIEW_EXPECTATION_CONSTRUCTION)
    repository = NoFullLoadRepository(workflow.blackboard.repository)
    workflow.blackboard.repository = repository

    resolved = workflow._resolve_expectation_construction(
        partial.checkpoint,
        WorkflowNode.RESOLVE_EXPECTATION_CONSTRUCTION,
    )

    assert WorkflowNode.RESOLVE_EXPECTATION_CONSTRUCTION in resolved.completed_nodes
    assert resolved.next_node is WorkflowNode.GENERATE_EXPECTATION_DETAILS
    assert repository.full_get_called is False


def test_construction_review_context_includes_doxatlas_scope_guardrails() -> None:
    workflow = BlackboardInitializationWorkflow(execution_mode="mock")
    factory = InitializationMockResultFactory()
    run = workflow.blackboard.start_run("NVDA", AgentName.SYSTEM)
    checkpoint = WorkflowCheckpoint(
        run_id=run.run_id,
        ticker="NVDA",
        metadata={
            "expectation_shells": [
                shell.model_dump(mode="json") for shell in factory._expectation_shells("NVDA")
            ]
        },
    )
    captured_context: dict[str, object] = {}

    def fake_run_agent(
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
        agent_name: AgentName,
        task_type: object,
        output_schema: str,
        *,
        extra_context: dict[str, object] | None = None,
        **_: object,
    ) -> AgentResult:
        captured_context.update(extra_context or {})
        return AgentResult(
            task_id="task_test",
            agent_name=agent_name,
            status=ResultStatus.SUCCEEDED,
            payload={"structured": {"verdict": "pass", "findings": []}},
        )

    workflow._run_agent = fake_run_agent  # type: ignore[method-assign]

    workflow._review_expectation_construction(
        checkpoint,
        WorkflowNode.REVIEW_EXPECTATION_CONSTRUCTION,
    )

    instruction = str(captured_context["review_instruction"])
    guardrails = captured_context["doxatlas_scope_guardrails"]
    assert "never pass ticker or bare narrative_code" in instruction
    assert "return DoxAtlasAuditResult with a warning" in instruction
    assert isinstance(guardrails, dict)
    assert "ticker and bare narrative_code are invalid" in guardrails[
        "doxa_query_propositions"
    ]
    assert "run_id+narrative_code" in guardrails["doxa_get_ignored_propositions"]


def test_mock_agent_runner_factory_mode_preserves_result_contract() -> None:
    runner = MockAgentRunner(
        default_agent_registry(),
        result_factory=InitializationMockResultFactory(include_blockers=False),
    )
    workflow = BlackboardInitializationWorkflow(runner=runner, execution_mode="mock")

    result = workflow.run("NVDA", stop_after=WorkflowNode.BUILD_GLOBAL_RESEARCH)

    assert result.status is WorkflowRunStatus.RUNNING
    assert runner.calls == 1
    run = workflow.blackboard.get_run(result.checkpoint.run_id)
    assert run.working_memory[0].payload["status"] == ResultStatus.SUCCEEDED.value


def test_build_global_research_runs_research_agents_concurrently_in_spec_order() -> None:
    runner = ParallelStructuredInitializationRunner(
        {WorkflowNode.BUILD_GLOBAL_RESEARCH.value: 4}
    )
    workflow = BlackboardInitializationWorkflow(runner=runner, execution_mode="agent_runner")

    result = workflow.run("NVDA", stop_after=WorkflowNode.BUILD_GLOBAL_RESEARCH)

    assert result.status is WorkflowRunStatus.RUNNING
    assert runner.max_active[WorkflowNode.BUILD_GLOBAL_RESEARCH.value] == 4
    summaries = result.checkpoint.metadata["last_agent_results"][
        WorkflowNode.BUILD_GLOBAL_RESEARCH.value
    ]
    assert [item["agent_name"] for item in summaries] == [
        AgentName.C1_FUNDAMENTAL_RESEARCH.value,
        AgentName.C2_MACRO_RESEARCH.value,
        AgentName.C3_INDUSTRY_RESEARCH.value,
        AgentName.O4_MARKET_TRACE.value,
    ]
    run = workflow.blackboard.get_run(result.checkpoint.run_id)
    assert [
        entry.author_agent.value
        for entry in run.working_memory
        if entry.content_type == "global_research_agent_result"
    ] == [
        AgentName.C1_FUNDAMENTAL_RESEARCH.value,
        AgentName.C2_MACRO_RESEARCH.value,
        AgentName.C3_INDUSTRY_RESEARCH.value,
        AgentName.O4_MARKET_TRACE.value,
    ]


def test_generate_expectation_details_runs_o1_shells_concurrently_and_merges_order() -> None:
    runner = ParallelStructuredInitializationRunner(
        {WorkflowNode.GENERATE_EXPECTATION_DETAILS.value: 2}
    )
    workflow = BlackboardInitializationWorkflow(runner=runner, execution_mode="agent_runner")

    result = workflow.run("NVDA", stop_after=WorkflowNode.GENERATE_EXPECTATION_DETAILS)

    assert result.status is WorkflowRunStatus.RUNNING
    assert runner.max_active[WorkflowNode.GENERATE_EXPECTATION_DETAILS.value] == 2
    detail_tasks = [
        task
        for task in runner.tasks
        if task.run_metadata.workflow_node == WorkflowNode.GENERATE_EXPECTATION_DETAILS.value
    ]
    assert {
        task.input_context["expectation_shell"]["expectation_id"]
        for task in detail_tasks
    } == {"exp_mock_core", "exp_mock_risk"}
    assert all("expectation_shells" not in task.input_context for task in detail_tasks)
    assert all(
        task.input_context["detail_completion_budget"][
            "max_successful_doxa_get_narrative_report_calls"
        ]
        == 1
        for task in detail_tasks
    )
    assert all(
        "Use at most one doxa_get_narrative_report call"
        in task.input_context["detail_instruction"]
        for task in detail_tasks
    )
    assert [patch.target.expectation_id for patch in result.checkpoint.pending_patches] == [
        "exp_mock_core",
        "exp_mock_risk",
    ]


def test_parallel_build_global_research_retryable_failure_retries_once() -> None:
    runner = ParallelStructuredInitializationRunner(
        fail_research_once_agents={AgentName.C2_MACRO_RESEARCH}
    )
    workflow = BlackboardInitializationWorkflow(runner=runner, execution_mode="agent_runner")

    result = workflow.run("NVDA", stop_after=WorkflowNode.BUILD_GLOBAL_RESEARCH)

    assert result.status is WorkflowRunStatus.RUNNING
    assert runner.research_calls[AgentName.C2_MACRO_RESEARCH] == 2
    run = workflow.blackboard.get_run(result.checkpoint.run_id)
    assert [item.value for item in result.summary.stable_document_types] == ["global_research"]
    assert len(run.commit_log) == 1


def test_review_expectation_fields_runs_reviewers_concurrently_in_spec_order() -> None:
    runner = ParallelStructuredInitializationRunner(
        {WorkflowNode.REVIEW_EXPECTATION_FIELDS.value: 4}
    )
    workflow = BlackboardInitializationWorkflow(runner=runner, execution_mode="agent_runner")

    result = workflow.run("NVDA", stop_after=WorkflowNode.REVIEW_EXPECTATION_FIELDS)

    assert result.status is WorkflowRunStatus.RUNNING
    assert runner.max_active[WorkflowNode.REVIEW_EXPECTATION_FIELDS.value] == 4
    summaries = result.checkpoint.metadata["last_agent_results"][
        WorkflowNode.REVIEW_EXPECTATION_FIELDS.value
    ]
    assert [item["agent_name"] for item in summaries] == [
        AgentName.A1_DOXATLAS_AUDIT.value,
        AgentName.C1_FUNDAMENTAL_RESEARCH.value,
        AgentName.C3_INDUSTRY_RESEARCH.value,
        AgentName.O4_MARKET_TRACE.value,
    ]
    run = workflow.blackboard.get_run(result.checkpoint.run_id)
    assert [
        entry.content_type
        for entry in run.working_memory
        if entry.content_type
        in {
            "a1_doxatlas_audit",
            "c1_fundamental_review",
            "c3_industry_review",
            "o4_market_trace_review",
        }
    ] == [
        "a1_doxatlas_audit",
        "c1_fundamental_review",
        "c3_industry_review",
        "o4_market_trace_review",
    ]
    a1_task = next(
        task
        for task in runner.tasks
        if task.run_metadata.workflow_node == WorkflowNode.REVIEW_EXPECTATION_FIELDS.value
        and task.agent_name is AgentName.A1_DOXATLAS_AUDIT
    )
    assert a1_task.permissions.allowed_tools == []
    assert a1_task.input_context["tool_requirements"] == []
    assert a1_task.input_context["required_tool_names"] == []
    assert "Do not call tools" in a1_task.input_context["review_instruction"]
    o4_task = next(
        task
        for task in runner.tasks
        if task.run_metadata.workflow_node == WorkflowNode.REVIEW_EXPECTATION_FIELDS.value
        and task.agent_name is AgentName.O4_MARKET_TRACE
    )
    o4_patch = o4_task.input_context["pending_patches"][0]
    assert o4_task.input_context["pending_expectation_patches"] == o4_task.input_context[
        "pending_patches"
    ]
    assert o4_task.input_context["review_context_compaction"]["mode"] == (
        "role_scoped_pending_patch_summary"
    )
    assert o4_patch["review_context_scope"] == "market_trace"
    assert "after" not in o4_patch
    assert "key_variables" not in o4_patch
    assert "event_monitoring_direction" not in o4_patch
    assert o4_patch["realized_facts_price_reactions"]
    assert "market_trace_report" in o4_task.input_context["global_research_context"]["sections"]
    assert "text" not in o4_task.input_context["global_research_context"]["sections"][
        "market_trace_report"
    ]


def test_expectation_detail_resume_reuses_completed_parallel_shell_cache() -> None:
    runner = ParallelStructuredInitializationRunner(fail_detail_ids={"exp_mock_risk"})
    workflow = BlackboardInitializationWorkflow(runner=runner, execution_mode="agent_runner")

    blocked = workflow.run("NVDA", stop_after=WorkflowNode.GENERATE_EXPECTATION_DETAILS)

    assert blocked.status is WorkflowRunStatus.BLOCKED
    assert runner.detail_calls == {"exp_mock_core": 1, "exp_mock_risk": 2}
    runner.fail_detail_ids.clear()

    resumed = workflow.resume(
        blocked.checkpoint,
        stop_after=WorkflowNode.GENERATE_EXPECTATION_DETAILS,
    )

    assert resumed.status is WorkflowRunStatus.RUNNING
    assert runner.detail_calls == {"exp_mock_core": 1, "exp_mock_risk": 3}
    assert [patch.target.expectation_id for patch in resumed.checkpoint.pending_patches] == [
        "exp_mock_core",
        "exp_mock_risk",
    ]


def test_expectation_detail_retryable_failure_retries_once_in_same_node() -> None:
    runner = ParallelStructuredInitializationRunner(fail_detail_once_ids={"exp_mock_risk"})
    workflow = BlackboardInitializationWorkflow(runner=runner, execution_mode="agent_runner")

    result = workflow.run("NVDA", stop_after=WorkflowNode.GENERATE_EXPECTATION_DETAILS)

    assert result.status is WorkflowRunStatus.RUNNING
    assert runner.detail_calls == {"exp_mock_core": 1, "exp_mock_risk": 2}
    assert [patch.target.expectation_id for patch in result.checkpoint.pending_patches] == [
        "exp_mock_core",
        "exp_mock_risk",
    ]


def test_expectation_detail_timeout_records_shell_and_retries_recovery_context() -> None:
    runner = ParallelStructuredInitializationRunner(hang_detail_ids={"exp_mock_risk"})
    workflow = BlackboardInitializationWorkflow(
        runner=runner,
        execution_mode="agent_runner",
        settings=DoxAgentSettings(
            dashscope_api_key="test-key",
            storage_mode="memory",
            workflow_agent_stale_after_seconds=1,
            model_request_timeout_seconds=1,
        ),
    )

    result = workflow.run("NVDA", stop_after=WorkflowNode.GENERATE_EXPECTATION_DETAILS)

    assert result.status is WorkflowRunStatus.RUNNING
    assert runner.detail_calls == {"exp_mock_core": 1, "exp_mock_risk": 2}
    assert [patch.target.expectation_id for patch in result.checkpoint.pending_patches] == [
        "exp_mock_core",
        "exp_mock_risk",
    ]
    statuses = result.checkpoint.metadata["expectation_detail_generation_status"]
    risk_status = statuses["exp_mock_risk"]
    assert risk_status["status"] == "completed"
    assert risk_status["retry_attempt"] == 1
    assert risk_status["cache_key"] == "expectation_detail:1:exp_mock_risk"
    history_statuses = [item["status"] for item in risk_status["history"]]
    assert "timed_out" in history_statuses
    assert "retrying" in history_statuses
    retry_task = [
        task
        for task in runner.tasks
        if task.run_metadata.workflow_node == WorkflowNode.GENERATE_EXPECTATION_DETAILS.value
        and task.input_context["expectation_shell"]["expectation_id"] == "exp_mock_risk"
        and "detail_recovery_retry" in task.input_context
    ][0]
    assert retry_task.input_context["detail_recovery_retry"]["previous_status"] == "timed_out"
    assert "Recovery retry" in retry_task.input_context["detail_instruction"]


def test_expectation_detail_model_request_timeout_uses_recovery_context() -> None:
    runner = ParallelStructuredInitializationRunner(
        timeout_detail_until_recovery_ids={"exp_mock_risk"}
    )
    workflow = BlackboardInitializationWorkflow(runner=runner, execution_mode="agent_runner")

    result = workflow.run("NVDA", stop_after=WorkflowNode.GENERATE_EXPECTATION_DETAILS)

    assert result.status is WorkflowRunStatus.RUNNING
    assert runner.detail_calls == {"exp_mock_core": 1, "exp_mock_risk": 3}
    assert [patch.target.expectation_id for patch in result.checkpoint.pending_patches] == [
        "exp_mock_core",
        "exp_mock_risk",
    ]
    statuses = result.checkpoint.metadata["expectation_detail_generation_status"]
    risk_status = statuses["exp_mock_risk"]
    assert risk_status["status"] == "completed"
    assert risk_status["retry_attempt"] == 1
    history_statuses = [item["status"] for item in risk_status["history"]]
    assert "timed_out" in history_statuses
    assert "retrying" in history_statuses
    retry_task = [
        task
        for task in runner.tasks
        if task.run_metadata.workflow_node == WorkflowNode.GENERATE_EXPECTATION_DETAILS.value
        and task.input_context["expectation_shell"]["expectation_id"] == "exp_mock_risk"
        and "detail_recovery_retry" in task.input_context
    ][0]
    assert retry_task.input_context["detail_recovery_retry"]["previous_status"] == "timed_out"
    assert "forced model timeout for exp_mock_risk" in retry_task.input_context[
        "detail_recovery_retry"
    ]["previous_error"]


def test_resolve_objections_retryable_o1_failure_retries_once() -> None:
    runner = ParallelStructuredInitializationRunner(
        fail_resolve_o1_once=True,
        include_blockers=True,
    )
    workflow = BlackboardInitializationWorkflow(runner=runner, execution_mode="agent_runner")

    result = workflow.run(
        "NVDA",
        stop_after=WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS,
    )

    assert result.status is WorkflowRunStatus.RUNNING
    assert runner.resolve_o1_calls == 2
    assert result.summary.unresolved_objection_count == 0
    assert result.summary.blocking_delegation_count == 0


def test_objection_resolution_batches_related_duplicates_with_cluster_context() -> None:
    workflow = BlackboardInitializationWorkflow(
        runner=ParallelStructuredInitializationRunner(),
        execution_mode="agent_runner",
    )
    target = BlackboardTarget(
        document_type=DocumentType.EXPECTATION_UNIT,
        ticker="NVDA",
        expectation_id="exp_mock_core",
        field_path="realized_facts",
    )
    evidence = EvidenceRef(
        evidence_id=new_id("evidence"),
        source_type=EvidenceSourceType.AGENT_OUTPUT,
        source_id="test:duplicate-objection",
        title="Duplicate objection evidence",
        summary="Field review found repeated earnings-date mismatch objections.",
        confidence=0.8,
        citation_scope="test.objection_resolution",
    )

    def objection(objection_id: str, *, taxonomy: str, reason: str) -> Objection:
        return Objection(
            objection_id=objection_id,
            source_agent=AgentName.C1_FUNDAMENTAL_RESEARCH,
            target=target,
            severity=ObjectionSeverity.HIGH,
            reason=reason,
            evidence_refs=[evidence],
            taxonomy=taxonomy,
            target_path="realized_facts.earnings_date",
        )

    objections = [
        objection(
            "obj_earnings_date_mismatch",
            taxonomy="earnings_date_mismatch",
            reason="The earnings date conflicts with Alpha Vantage earnings calendar.",
        ),
        objection(
            "obj_earnings_date_mismatch_patch2",
            taxonomy="earnings_date_mismatch",
            reason="The earnings date conflicts with Alpha Vantage earnings calendar.",
        ),
        objection(
            "obj_earnings_date_mismatch_patch3",
            taxonomy="earnings_date_mismatch",
            reason="The earnings date conflicts with Alpha Vantage earnings calendar.",
        ),
        objection(
            "obj_ps_ratio_contradiction",
            taxonomy="valuation_ratio_contradiction",
            reason="The P/S multiple conflicts with the cited market data.",
        ),
    ]

    batch = workflow._next_objection_resolution_batch(objections)
    context = workflow._objection_resolution_context(
        WorkflowCheckpoint(run_id="run_test", ticker="NVDA"),
        batch,
        total_unresolved=len(objections),
    )

    assert [item.objection_id for item in batch] == [
        "obj_earnings_date_mismatch",
        "obj_earnings_date_mismatch_patch2",
        "obj_earnings_date_mismatch_patch3",
    ]
    assert context["resolution_batch"]["max_batch_size"] == 3
    assert context["unresolved_objections"][0]["taxonomy"] == "earnings_date_mismatch"
    assert context["unresolved_objections"][0]["target_path"] == "realized_facts.earnings_date"
    cluster_ids = [
        set(cluster["objection_ids"])
        for cluster in context["duplicate_objection_clusters"]
    ]
    assert {
        "obj_earnings_date_mismatch",
        "obj_earnings_date_mismatch_patch2",
        "obj_earnings_date_mismatch_patch3",
    } in cluster_ids


def test_numeric_sanity_review_flags_doxatlas_only_market_precision() -> None:
    workflow = BlackboardInitializationWorkflow(
        runner=ParallelStructuredInitializationRunner(),
        execution_mode="agent_runner",
    )
    factory = InitializationMockResultFactory()
    narrative_evidence = factory._evidence(EvidenceSourceType.DOXATLAS_SOURCE)
    document = factory._expectation_unit("NVDA")
    fact = document.realized_facts[0]
    reaction = fact.price_reaction.model_copy(
        update={
            "price_change": "NVDA stock price is $1,020, YTD +244%, market cap 1.15 trillion.",
            "interpretation": "Market has fully priced the rerating.",
            "evidence_refs": [narrative_evidence],
        },
        deep=True,
    )
    document = document.model_copy(
        update={
            "realized_facts": [
                fact.model_copy(
                    update={
                        "description": (
                            "NVDA stock price reached $1,020 and market cap 1.15 trillion."
                        ),
                        "price_reaction": reaction,
                        "evidence_refs": [narrative_evidence],
                    },
                    deep=True,
                )
            ]
        },
        deep=True,
    )
    patch = factory._document_patch(
        document,
        DocumentType.EXPECTATION_UNIT,
        AgentName.O1_EXPECTATION_OWNER,
        expectation_id=document.expectation_id,
    )

    objections = workflow._numeric_sanity_objections_for_patch("NVDA", patch)

    assert [item.taxonomy for item in objections] == ["numeric_sanity_market_data"]
    assert objections[0].severity is ObjectionSeverity.BLOCKING
    assert objections[0].dedupe_hash == "numeric_sanity_market_data:exp_mock_core"
    assert "market-data evidence" in objections[0].reason
    assert "narrative-only" in objections[0].reason
    assert "not a valid resolution" in objections[0].reason


def test_numeric_sanity_review_allows_market_precision_with_market_data() -> None:
    workflow = BlackboardInitializationWorkflow(
        runner=ParallelStructuredInitializationRunner(),
        execution_mode="agent_runner",
    )
    factory = InitializationMockResultFactory()
    market_evidence = factory._evidence(EvidenceSourceType.MARKET_DATA)
    document = factory._expectation_unit("NVDA")
    fact = document.realized_facts[0]
    reaction = fact.price_reaction.model_copy(
        update={
            "price_change": "NVDA stock price is $1,020, YTD +244%, market cap 1.15 trillion.",
            "interpretation": "Market data confirms the rerating.",
            "evidence_refs": [market_evidence],
        },
        deep=True,
    )
    document = document.model_copy(
        update={
            "realized_facts": [
                fact.model_copy(
                    update={
                        "description": (
                            "NVDA stock price reached $1,020 and market cap 1.15 trillion."
                        ),
                        "price_reaction": reaction,
                        "evidence_refs": [market_evidence],
                    },
                    deep=True,
                )
            ]
        },
        deep=True,
    )
    patch = factory._document_patch(
        document,
        DocumentType.EXPECTATION_UNIT,
        AgentName.O1_EXPECTATION_OWNER,
        expectation_id=document.expectation_id,
    )

    assert workflow._numeric_sanity_objections_for_patch("NVDA", patch) == []


def test_numeric_sanity_review_flags_thesis_field_false_precision() -> None:
    workflow = BlackboardInitializationWorkflow(
        runner=ParallelStructuredInitializationRunner(),
        execution_mode="agent_runner",
    )
    factory = InitializationMockResultFactory()
    narrative_evidence = factory._evidence(EvidenceSourceType.DOXATLAS_SOURCE)
    document = factory._expectation_unit("NVDA")
    fact = document.realized_facts[0]
    clean_reaction = fact.price_reaction.model_copy(
        update={
            "price_change": "Qualitative market reaction pending source verification.",
            "price_pattern": "qualitative",
            "interpretation": "No precise price, market-cap, or volume claim is attached.",
            "evidence_refs": [narrative_evidence],
        },
        deep=True,
    )
    document = document.model_copy(
        update={
            "market_view": document.market_view.model_copy(
                update={
                    "text": (
                        "Narrative-only thesis says stock price reached $1,020, "
                        "revenue rose 196%, and forward P/E is 8.1x."
                    ),
                    "summary": "Narrative-only $1,020 target and 196% revenue precision.",
                    "evidence_refs": [narrative_evidence],
                },
                deep=True,
            ),
            "realized_facts": [
                fact.model_copy(
                    update={
                        "description": "Qualitative realized fact without precise numbers.",
                        "price_reaction": clean_reaction,
                        "evidence_refs": [narrative_evidence],
                    },
                    deep=True,
                )
            ],
            "key_variables": [
                document.key_variables[0].model_copy(
                    update={
                        "current_status": (
                            "Target price $1,020 and gross margin 74.9% come only "
                            "from narrative evidence."
                        ),
                        "evidence_refs": [narrative_evidence],
                    },
                    deep=True,
                )
            ],
            "event_monitoring_direction": document.event_monitoring_direction.model_copy(
                update={
                    "positive_events": ["Watch target price above $1,020."],
                    "negative_events": ["Watch revenue falling 30%."],
                    "known_event_notice": "Do not use unsupported $33.5B thresholds.",
                },
                deep=True,
            ),
        },
        deep=True,
    )
    patch = factory._document_patch(
        document,
        DocumentType.EXPECTATION_UNIT,
        AgentName.O1_EXPECTATION_OWNER,
        expectation_id=document.expectation_id,
    )

    objections = workflow._numeric_sanity_objections_for_patch("NVDA", patch)

    assert {item.taxonomy for item in objections} == {
        "numeric_sanity_market_data",
        "numeric_sanity_fundamental_data",
    }
    combined_reasons = " ".join(item.reason for item in objections)
    assert "market_view" in combined_reasons
    assert "key_variables[1]" in combined_reasons
    assert "event_monitoring_direction" in combined_reasons


def test_promotion_commits_candidate_without_mutating_document_or_evidence_refs() -> None:
    workflow = BlackboardInitializationWorkflow(
        runner=ParallelStructuredInitializationRunner(),
        execution_mode="agent_runner",
    )
    factory = InitializationMockResultFactory()
    document = factory._expectation_unit("NVDA")
    patch = factory._document_patch(
        document,
        DocumentType.EXPECTATION_UNIT,
        AgentName.O1_EXPECTATION_OWNER,
        expectation_id=document.expectation_id,
    )
    before_after = patch.after
    before_refs = list(patch.evidence_refs)
    run = workflow.blackboard.start_run("NVDA", AgentName.SYSTEM)
    checkpoint = WorkflowCheckpoint(run_id=run.run_id, ticker="NVDA", pending_patches=[patch])

    next_checkpoint = workflow._promote_pending_patches(
        checkpoint,
        WorkflowNode.PROMOTE_EXPECTATION_TO_BELIEF_STATE,
    )

    committed = workflow.blackboard.get_run(run.run_id).commit_log[-1].patch
    assert committed.after == before_after
    assert committed.evidence_refs == before_refs
    assert next_checkpoint.pending_patches == []
    assert next_checkpoint.metadata["document2_promotion_audits"][0]["status"] == "accepted"


def test_promotion_blocks_unresolved_price_reaction_without_rewrite() -> None:
    workflow = BlackboardInitializationWorkflow(
        runner=ParallelStructuredInitializationRunner(),
        execution_mode="agent_runner",
    )
    factory = InitializationMockResultFactory()
    market_evidence = factory._evidence(EvidenceSourceType.MARKET_DATA).model_copy(
        update={
            "source_id": "twelvedata:daily_ohlcv:NVDA",
            "retrieval_metadata": {
                "tool_name": "twelvedata.daily_ohlcv",
                "market_evidence_snapshot": {
                    "kind": "daily_ohlcv_snapshot",
                    "symbol": "NVDA",
                    "bar_count": 60,
                    "start_date": "2026-01-02",
                    "end_date": "2026-06-23",
                    "start_close": 100,
                    "end_close": 112,
                    "total_return_pct": 12,
                },
            },
        },
        deep=True,
    )
    document = factory._expectation_unit("NVDA")
    fact = document.realized_facts[0]
    reaction = fact.price_reaction.model_copy(
        update={
            "price_change": "Unknown price reaction pending market-data evidence.",
            "price_pattern": "unresolved market reaction",
            "interpretation": "Treat the pricing conclusion as unresolved.",
            "evidence_refs": [market_evidence],
        },
        deep=True,
    )
    document = document.model_copy(
        update={
            "realized_facts": [
                fact.model_copy(
                    update={
                        "price_reaction": reaction,
                        "evidence_refs": [market_evidence],
                    },
                    deep=True,
                )
            ]
        },
        deep=True,
    )
    patch = factory._document_patch(
        document,
        DocumentType.EXPECTATION_UNIT,
        AgentName.O1_EXPECTATION_OWNER,
        expectation_id=document.expectation_id,
    ).model_copy(update={"evidence_refs": [market_evidence]}, deep=True)
    before_after = patch.after
    run = workflow.blackboard.start_run("NVDA", AgentName.SYSTEM)
    checkpoint = WorkflowCheckpoint(run_id=run.run_id, ticker="NVDA", pending_patches=[patch])

    with pytest.raises(WorkflowContractError, match="Document2 promotion blocked") as exc:
        workflow._promote_pending_patches(
            checkpoint,
            WorkflowNode.PROMOTE_EXPECTATION_TO_BELIEF_STATE,
        )

    current_run = workflow.blackboard.get_run(run.run_id)
    audits = [
        entry
        for entry in current_run.working_memory
        if entry.content_type == "document2_promotion_audit"
    ]
    assert "schema_validation" in str(exc.value)
    assert current_run.commit_log == []
    assert checkpoint.pending_patches[0].after == before_after
    assert audits[-1].payload["status"] == "rejected"


def test_numeric_value_detection_ignores_fiscal_years_and_product_generations() -> None:
    workflow = BlackboardInitializationWorkflow(
        runner=ParallelStructuredInitializationRunner(),
        execution_mode="agent_runner",
    )

    assert not workflow._contains_numeric_value("Q3 FY2026 and HBM4 launch timing")
    assert workflow._contains_numeric_value("revenue guidance is $36B")
    assert workflow._contains_numeric_value("stock gained +90% versus SOXX")


def test_o1_revision_reopens_numeric_sanity_when_false_precision_remains() -> None:
    workflow = BlackboardInitializationWorkflow(
        runner=ParallelStructuredInitializationRunner(),
        execution_mode="agent_runner",
    )
    factory = InitializationMockResultFactory()
    narrative_evidence = factory._evidence(EvidenceSourceType.DOXATLAS_SOURCE)
    document = factory._expectation_unit("NVDA")
    fact = document.realized_facts[0]
    reaction = fact.price_reaction.model_copy(
        update={
            "price_change": (
                "Narrative-only and unverified: NVDA stock price is $1,020, "
                "YTD +244%, market cap 1.15 trillion."
            ),
            "price_pattern": "narrative_only",
            "interpretation": "The same precise market data is labelled uncertain.",
            "evidence_refs": [narrative_evidence],
        },
        deep=True,
    )
    document = document.model_copy(
        update={
            "realized_facts": [
                fact.model_copy(
                    update={
                        "description": (
                            "NVDA stock price reached $1,020 and market cap 1.15 trillion "
                            "according to a narrative-only source."
                        ),
                        "price_reaction": reaction,
                        "evidence_refs": [narrative_evidence],
                    },
                    deep=True,
                )
            ]
        },
        deep=True,
    )
    patch = factory._document_patch(
        document,
        DocumentType.EXPECTATION_UNIT,
        AgentName.O1_EXPECTATION_OWNER,
        expectation_id=document.expectation_id,
    )
    run = workflow.blackboard.start_run("NVDA", AgentName.SYSTEM)
    checkpoint = WorkflowCheckpoint(
        run_id=run.run_id,
        ticker="NVDA",
        pending_patches=[patch],
    )
    objection = workflow._numeric_sanity_objections_for_patch("NVDA", patch)[0]
    workflow.blackboard.create_objection(run.run_id, objection)
    workflow.blackboard.partially_accept_objection(
        run.run_id,
        objection.objection_id,
        "O1 labelled the precise number as narrative-only.",
        changed_paths=["realized_facts.price_reaction"],
    )

    workflow._reopen_numeric_sanity_objections_after_o1_revision(checkpoint)

    updated = workflow.blackboard.get_run(run.run_id).objections[0]
    assert updated.objection_id == objection.objection_id
    assert updated.status is ObjectionStatus.UNRESOLVED
    assert "Narrative-only or unverified labelling is not sufficient" in (
        updated.resolution_note or ""
    )


def test_objection_resolution_context_includes_current_numeric_sanity_violations() -> None:
    workflow = BlackboardInitializationWorkflow(
        runner=ParallelStructuredInitializationRunner(),
        execution_mode="agent_runner",
    )
    factory = InitializationMockResultFactory()
    narrative_evidence = factory._evidence(EvidenceSourceType.DOXATLAS_SOURCE)
    document = factory._expectation_unit("NVDA")
    fact = document.realized_facts[0]
    document = document.model_copy(
        update={
            "market_view": document.market_view.model_copy(
                update={
                    "text": "NVDA revenue grew 196% and stock price reached $1,020.",
                    "summary": "Narrative-only numeric market view.",
                    "evidence_refs": [narrative_evidence],
                },
                deep=True,
            ),
            "realized_facts": [
                fact.model_copy(
                    update={
                        "description": "NVDA revenue grew 196% according to a narrative report.",
                        "evidence_refs": [narrative_evidence],
                    },
                    deep=True,
                )
            ],
        },
        deep=True,
    )
    patch = factory._document_patch(
        document,
        DocumentType.EXPECTATION_UNIT,
        AgentName.O1_EXPECTATION_OWNER,
        expectation_id=document.expectation_id,
    )
    checkpoint = WorkflowCheckpoint(
        run_id="run_numeric_context",
        ticker="NVDA",
        pending_patches=[patch],
    )
    objection = next(
        item
        for item in workflow._numeric_sanity_objections_for_patch("NVDA", patch)
        if item.taxonomy == "numeric_sanity_fundamental_data"
    )

    context = workflow._objection_resolution_context(
        checkpoint,
        [objection],
        batch_index=1,
        total_unresolved=1,
    )

    violations = context["current_numeric_sanity_violations"]
    assert len(violations) == 1
    assert violations[0]["objection_id"] == objection.objection_id
    assert violations[0]["requires_revised_patch"] is True
    assert "NVDA revenue grew 196%" in violations[0]["current_reason"]
    assert any(
        "decision='resolved' with no revised_candidate is invalid" in item
        for item in context["output_guidance"]
    )


def test_objection_resolution_context_only_includes_relevant_pending_patch() -> None:
    workflow = BlackboardInitializationWorkflow(
        runner=ParallelStructuredInitializationRunner(),
        execution_mode="agent_runner",
    )
    factory = InitializationMockResultFactory()
    patches = []
    for suffix in ("01", "02", "03"):
        document = factory._expectation_unit("MU").model_copy(
            update={
                "expectation_id": f"expectation_mu_{suffix}",
                "expectation_name": f"MU expectation {suffix}",
            },
            deep=True,
        )
        patches.append(
            factory._document_patch(
                document,
                DocumentType.EXPECTATION_UNIT,
                AgentName.O1_EXPECTATION_OWNER,
                expectation_id=document.expectation_id,
            ).model_copy(update={"patch_id": f"patch_expectation_mu_{suffix}_detail"})
        )
    checkpoint = WorkflowCheckpoint(run_id="run_context", ticker="MU", pending_patches=patches)
    objection = Objection(
        objection_id="obj_price_mu_02",
        source_agent=AgentName.O4_MARKET_TRACE,
        target=BlackboardTarget(
            document_type=DocumentType.EXPECTATION_UNIT,
            ticker="MU",
            field_path="document",
        ),
        severity=ObjectionSeverity.HIGH,
        reason="价格基准与涨幅计算错误",
    )

    context = workflow._objection_resolution_context(
        checkpoint,
        [objection],
        batch_index=1,
        total_unresolved=1,
    )

    assert [item["patch_id"] for item in context["pending_patches"]] == [
        "patch_expectation_mu_02_detail"
    ]
    assert context["omitted_pending_patch_count"] == 2
    assert len(str(context)) < 15000


class StalledFirstObjectionBatchRunner:
    def __init__(self) -> None:
        self.batches: list[list[str]] = []

    def run(self, task: AgentTask) -> AgentResult:
        evidence = EvidenceRef(
            evidence_id=new_id("evidence"),
            source_type=EvidenceSourceType.AGENT_OUTPUT,
            source_id="test:stalled-objection-batch",
            title="Stalled objection batch evidence",
            summary="Resolver batch test evidence.",
            confidence=0.8,
            citation_scope="test.objection_resolution",
        )
        raw_objections = task.input_context.get("unresolved_objections")
        objection_ids = [
            item["objection_id"]
            for item in raw_objections
            if isinstance(item, dict) and isinstance(item.get("objection_id"), str)
        ] if isinstance(raw_objections, list) else []
        objection_items = [
            item
            for item in raw_objections
            if isinstance(item, dict) and isinstance(item.get("objection_id"), str)
        ] if isinstance(raw_objections, list) else []
        expectation_id = "exp_mock_core"
        if objection_items:
            target = objection_items[0].get("target")
            if isinstance(target, dict) and isinstance(target.get("expectation_id"), str):
                expectation_id = target["expectation_id"]
        self.batches.append(objection_ids)
        if len(self.batches) == 1:
            structured = {
                "expectation_id": expectation_id,
                "decision": "deferred",
                "decisions": [],
                "target_finding_ids": [],
                "revised_candidate": None,
                "evidence_requests": [],
                "unresolved_finding_ids": [],
                "unresolved_reason": "The first resolver batch made no transition.",
                "rationale": "The first resolver batch made no transition.",
            }
        else:
            structured = {
                "expectation_id": expectation_id,
                "decision": "resolved",
                "decisions": [
                    {
                        "objection_id": objection_id,
                        "finding_id": None,
                        "decision": "resolved",
                        "resolution_note": "Resolved after stalled sibling batch.",
                        "changed_paths": ["expectation_unit.document"],
                        "evidence_refs": [evidence.model_dump(mode="json")],
                    }
                    for objection_id in objection_ids
                ],
                "target_finding_ids": [],
                "revised_candidate": None,
                "evidence_requests": [],
                "unresolved_finding_ids": [],
                "unresolved_reason": None,
                "rationale": "The next resolver batch resolved its objections.",
            }
        return AgentResult(
            task_id=task.task_id,
            agent_name=task.agent_name,
            status=ResultStatus.SUCCEEDED,
            payload={"runtime": "maf", "structured": structured},
            evidence_refs=[evidence],
        )


def test_objection_resolver_continues_after_one_batch_stalls() -> None:
    runner = StalledFirstObjectionBatchRunner()
    workflow = BlackboardInitializationWorkflow(
        runner=runner,
        execution_mode="agent_runner",
    )
    run = workflow.blackboard.start_run("NVDA", AgentName.SYSTEM)
    checkpoint = WorkflowCheckpoint(run_id=run.run_id, ticker="NVDA")
    evidence = EvidenceRef(
        evidence_id=new_id("evidence"),
        source_type=EvidenceSourceType.AGENT_OUTPUT,
        source_id="test:resolver-continues",
        title="Resolver continue test evidence",
        summary="Objection evidence for resolver continuation test.",
        confidence=0.8,
        citation_scope="test.objection_resolution",
    )
    target = BlackboardTarget(
        document_type=DocumentType.EXPECTATION_UNIT,
        ticker="NVDA",
        expectation_id="exp_mock_core",
        field_path="realized_facts",
    )
    for index in range(4):
        workflow.blackboard.create_objection(
            run.run_id,
            Objection(
                objection_id=f"obj_batch_{index}",
                source_agent=AgentName.C1_FUNDAMENTAL_RESEARCH,
                target=target,
                severity=ObjectionSeverity.BLOCKING,
                reason=f"Batch continuation objection {index}.",
                evidence_refs=[evidence],
                taxonomy=f"batch_continuation_{index}",
                target_path="realized_facts",
            ),
        )

    try:
        workflow._resolve_blockers(
            checkpoint,
            WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS,
        )
    except WorkflowContractError as exc:
        assert "left blockers unresolved" in str(exc)
    else:
        raise AssertionError("Expected unresolved stalled objections to keep blocking.")

    assert runner.batches == [
        ["obj_batch_0", "obj_batch_1", "obj_batch_2"],
        ["obj_batch_3"],
    ]
    objections_by_id = {
        objection.objection_id: objection
        for objection in workflow.blackboard.get_run(run.run_id).objections
    }
    assert objections_by_id["obj_batch_3"].is_unresolved is False
    assert objections_by_id["obj_batch_0"].is_unresolved is True
