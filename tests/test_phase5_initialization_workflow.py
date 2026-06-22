import threading

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
        objection_ids = [
            item["objection_id"]
            for item in objections
            if isinstance(item, dict) and isinstance(item.get("objection_id"), str)
        ] if isinstance(objections, list) else []
        structured = {
            "proposed_patches": [],
            "evidence_refs": [evidence.model_dump(mode="json")],
            "delegations": [],
            "unknowns": [],
            "rationale": "Mock O1 resolved field-review objections after retry.",
            "resolved_objection_ids": objection_ids,
            "accepted_objection_ids": [],
            "partially_accepted_objection_ids": [],
            "rejected_objection_ids": [],
            "objection_resolutions": [
                {
                    "objection_id": objection_id,
                    "decision": "resolved",
                    "resolution_note": (
                        "Mock O1 retry resolved this objection with supporting evidence."
                    ),
                    "changed_paths": ["expectation_unit.document"],
                    "evidence_refs": [evidence.model_dump(mode="json")],
                }
                for objection_id in objection_ids
            ],
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


def test_o2_registry_permissions_cover_config_and_policy_documents() -> None:
    definition = default_agent_registry().get(AgentName.O2_MONITORING_CONFIG)
    permissions = definition.runtime.to_permissions()

    assert DocumentType.MONITORING_CONFIG.value in permissions.writable_targets
    assert DocumentType.MONITORING_POLICY.value in permissions.writable_targets


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


def test_price_reaction_promotion_requires_structured_market_snapshot() -> None:
    workflow = BlackboardInitializationWorkflow(
        runner=ParallelStructuredInitializationRunner(),
        execution_mode="agent_runner",
    )
    factory = InitializationMockResultFactory()
    narrative_evidence = factory._evidence(EvidenceSourceType.DOXATLAS_SOURCE)
    document = factory._expectation_unit("NVDA")
    fact = document.realized_facts[0]
    original_price_change = "NVDA stock price rose 12% after the event."
    reaction = fact.price_reaction.model_copy(
        update={
            "price_change": original_price_change,
            "price_pattern": "post-event rerating",
            "interpretation": "Narrative says the market has priced it in.",
            "evidence_refs": [narrative_evidence],
        },
        deep=True,
    )
    document = document.model_copy(
        update={
            "realized_facts": [
                fact.model_copy(
                    update={
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
    checkpoint = WorkflowCheckpoint(run_id=run.run_id, ticker="NVDA", pending_patches=[patch])

    normalized = workflow._normalize_expectation_price_reaction_patch(checkpoint, patch)

    normalized_reaction = normalized.after["realized_facts"][0]["price_reaction"]
    assert normalized_reaction["price_change"] != original_price_change
    assert "OHLCV/market_trace" in normalized_reaction["price_change"]
    assert normalized_reaction["evidence_refs"][0]["source_type"] == "doxatlas_source"


def test_price_reaction_promotion_uses_structured_market_snapshot() -> None:
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
    original_price_change = "NVDA stock price rose 12% after the event."
    reaction = fact.price_reaction.model_copy(
        update={
            "price_change": original_price_change,
            "price_pattern": "post-event rerating",
            "interpretation": "Daily OHLCV supports the market reaction.",
            "evidence_refs": [],
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
    run = workflow.blackboard.start_run("NVDA", AgentName.SYSTEM)
    checkpoint = WorkflowCheckpoint(run_id=run.run_id, ticker="NVDA", pending_patches=[patch])

    normalized = workflow._normalize_expectation_price_reaction_patch(checkpoint, patch)

    normalized_reaction = normalized.after["realized_facts"][0]["price_reaction"]
    assert normalized_reaction["price_change"] == original_price_change
    assert normalized_reaction["evidence_refs"][0]["source_id"] == "twelvedata:daily_ohlcv:NVDA"


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


def test_numeric_sanity_revision_fallback_removes_unsupported_false_precision() -> None:
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
            "price_pattern": "narrative_only_market_rerating",
            "interpretation": "The narrative says the precise market move is already priced.",
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
                            "NVDA revenue grew 196% while stock price reached $1,020, "
                            "based only on a narrative source."
                        ),
                        "price_reaction": reaction,
                        "evidence_refs": [narrative_evidence],
                    },
                    deep=True,
                )
            ],
            "realized_facts_summary": (
                "Revenue +196% and stock price $1,020 are treated as precise realized facts."
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
    run = workflow.blackboard.start_run("NVDA", AgentName.SYSTEM)
    checkpoint = WorkflowCheckpoint(run_id=run.run_id, ticker="NVDA", pending_patches=[patch])
    objections = workflow._numeric_sanity_objections_for_patch("NVDA", patch)
    assert {item.taxonomy for item in objections} == {
        "numeric_sanity_market_data",
        "numeric_sanity_fundamental_data",
    }
    for objection in objections:
        workflow.blackboard.create_objection(run.run_id, objection)

    structured = {
        "proposed_patches": [patch.model_dump(mode="json")],
        "evidence_refs": [narrative_evidence.model_dump(mode="json")],
        "delegations": [],
        "unknowns": [],
        "rationale": "O1 accepted the numeric sanity objections but left precision in place.",
        "resolved_objection_ids": [],
        "accepted_objection_ids": [item.objection_id for item in objections],
        "partially_accepted_objection_ids": [],
        "rejected_objection_ids": [],
        "objection_resolutions": [
            {
                "objection_id": item.objection_id,
                "decision": "accepted",
                "resolution_note": "Accepted and revised the affected expectation.",
                "changed_paths": ["realized_facts", "realized_facts.price_reaction"],
                "evidence_refs": [narrative_evidence.model_dump(mode="json")],
            }
            for item in objections
        ],
    }
    result = AgentResult(
        task_id="task_numeric_revision",
        agent_name=AgentName.O1_EXPECTATION_OWNER,
        status=ResultStatus.SUCCEEDED,
        payload={"runtime": "maf", "structured": structured},
        proposed_patches=[patch],
        evidence_refs=[narrative_evidence],
    )

    workflow._apply_o1_objection_resolutions(checkpoint, result)
    replaced = workflow._replace_pending_expectation_patches(checkpoint, result)

    sanitized = replaced[0]
    assert workflow._numeric_sanity_objections_for_patch("NVDA", sanitized) == []
    sanitized_fact = sanitized.after["realized_facts"][0]
    sanitized_reaction = sanitized_fact["price_reaction"]
    combined = " ".join(
        [
            sanitized_fact["description"],
            sanitized_reaction["price_change"],
            sanitized_reaction["price_pattern"],
            sanitized_reaction["interpretation"],
            sanitized.after["realized_facts_summary"],
        ]
    )
    assert "$1,020" not in combined
    assert "+244%" not in combined
    assert "196%" not in combined
    assert "仅保留定性市场反应" in sanitized_reaction["price_change"]
    assert "Numeric sanity fallback removed unsupported precise numeric claims" in (
        sanitized.rationale
    )


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
        self.batches.append(objection_ids)
        if len(self.batches) == 1:
            structured = {
                "proposed_patches": [],
                "evidence_refs": [evidence.model_dump(mode="json")],
                "delegations": [],
                "unknowns": [],
                "rationale": "The first resolver batch made no transition.",
                "resolved_objection_ids": [],
                "accepted_objection_ids": [],
                "partially_accepted_objection_ids": [],
                "rejected_objection_ids": [],
                "objection_resolutions": [],
            }
        else:
            structured = {
                "proposed_patches": [],
                "evidence_refs": [evidence.model_dump(mode="json")],
                "delegations": [],
                "unknowns": [],
                "rationale": "The next resolver batch resolved its objections.",
                "resolved_objection_ids": objection_ids,
                "accepted_objection_ids": [],
                "partially_accepted_objection_ids": [],
                "rejected_objection_ids": [],
                "objection_resolutions": [
                    {
                        "objection_id": objection_id,
                        "decision": "resolved",
                        "resolution_note": "Resolved after stalled sibling batch.",
                        "changed_paths": ["expectation_unit.document"],
                        "evidence_refs": [evidence.model_dump(mode="json")],
                    }
                    for objection_id in objection_ids
                ],
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
