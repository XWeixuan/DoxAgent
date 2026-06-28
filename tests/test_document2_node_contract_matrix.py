from __future__ import annotations

from typing import Any

import pytest

from doxagent.agents import default_agent_registry
from doxagent.models import (
    AgentName,
    AgentResult,
    AgentTask,
    BlackboardPatch,
    BlackboardTarget,
    DocumentType,
    EvidenceSourceType,
    ExpectationDetailCandidateResult,
    Objection,
    ObjectionSeverity,
    ObjectionStatus,
    PatchOperation,
    TaskType,
    ValidationStatus,
)
from doxagent.workflows import (
    BlackboardInitializationWorkflow,
    WorkflowCheckpoint,
    WorkflowNode,
    WorkflowRunStatus,
)
from doxagent.workflows.document2.contracts import (
    Document2PromotionCandidate,
    Document2ResolutionPlan,
    Document2ReviewFinding,
    EvidenceAssessment,
)
from doxagent.workflows.document2.final_payload_adapter import (
    adapt_document2_resolution_plan_payload,
    adapt_expectation_detail_candidate_payload,
)
from doxagent.workflows.document2.promotion import (
    DOCUMENT2_PROMOTION_AUDITS_KEY,
    blackboard_patch_from_document2_promotion_candidate,
    document2_promotion_candidate_from_patch,
)
from doxagent.workflows.document2.review import DOCUMENT2_REVIEW_FINDINGS_KEY
from doxagent.workflows.document2.transaction import DOCUMENT2_TRANSACTION_AUDITS_KEY
from tests.test_phase13_real_workflow import StructuredInitializationRunner

DOCUMENT2_PENDING_REVISIONS_KEY = "document2_pending_revisions"


class Document2NodeMatrixRunner(StructuredInitializationRunner):
    def __init__(
        self,
        *,
        construction_case: str | None = None,
        construction_review_case: str | None = None,
        construction_resolution_case: str | None = None,
        detail_case: str | None = None,
        review_case: str | None = None,
        resolver_case: str | None = None,
    ) -> None:
        super().__init__(include_blockers=False)
        self.construction_case = construction_case
        self.construction_review_case = construction_review_case
        self.construction_resolution_case = construction_resolution_case
        self.detail_case = detail_case
        self.review_case = review_case
        self.resolver_case = resolver_case
        self.resolver_calls = 0

    def _structured(self, task: AgentTask, direct: AgentResult) -> AgentResult:
        result = super()._structured(task, direct)
        node = task.run_metadata.workflow_node
        schema = task.required_output_schema
        if (
            node == WorkflowNode.GENERATE_EXPECTATION_CONSTRUCTION.value
            and schema == "ExpectationShellConstructionResult"
        ):
            return self._mutate_construction(result)
        if (
            node == WorkflowNode.REVIEW_EXPECTATION_CONSTRUCTION.value
            and task.agent_name is AgentName.A1_DOXATLAS_AUDIT
        ):
            return self._mutate_construction_review(task, result)
        if (
            node == WorkflowNode.RESOLVE_EXPECTATION_CONSTRUCTION.value
            and schema == "ExpectationShellConstructionResult"
        ):
            return self._mutate_construction_resolution(task, result)
        if (
            node == WorkflowNode.GENERATE_EXPECTATION_DETAILS.value
            and schema == "ExpectationDetailCandidateResult"
        ):
            return self._mutate_detail(task, result)
        if node == WorkflowNode.REVIEW_EXPECTATION_FIELDS.value:
            return self._mutate_review(task, result)
        if (
            node == WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS.value
            and schema == "Document2FieldRepairResult"
        ):
            return self._mutate_resolver(task, result)
        return result

    def _mutate_construction(self, result: AgentResult) -> AgentResult:
        structured = self._structured_payload(result)
        shells = [dict(item) for item in structured.get("shells", [])]
        if self.construction_case == "one_shell":
            structured["shells"] = shells[:1]
        elif self.construction_case == "missing_evidence":
            for shell in shells:
                shell["evidence_refs"] = []
                market_view = dict(shell["market_view"])
                market_view["evidence_refs"] = []
                shell["market_view"] = market_view
            structured["shells"] = shells
            structured["evidence_refs"] = []
        elif self.construction_case == "too_many_shells" and shells:
            extra_a = dict(shells[0])
            extra_a["expectation_id"] = "exp_mock_extra_a"
            extra_a["expectation_name"] = "NVDA mock extra A expectation"
            extra_b = dict(shells[0])
            extra_b["expectation_id"] = "exp_mock_extra_b"
            extra_b["expectation_name"] = "NVDA mock extra B expectation"
            structured["shells"] = [*shells, extra_a, extra_b]
        return self._with_structured(result, structured)

    def _mutate_construction_review(
        self,
        task: AgentTask,
        result: AgentResult,
    ) -> AgentResult:
        if self.construction_review_case is None:
            return result
        if self.construction_review_case == "reviewer_patch_leak":
            patch = self._patch_from_candidate(
                task,
                self._candidate_payload(task.ticker),
                AgentName.A1_DOXATLAS_AUDIT,
            )
            return result.model_copy(update={"proposed_patches": [patch]}, deep=True)
        objection = self._construction_objection(
            task.ticker,
            field_path=self.construction_review_case,
            unrelated=self.construction_review_case == "unrelated",
        )
        return result.model_copy(update={"objections": [objection]}, deep=True)

    def _mutate_construction_resolution(
        self,
        task: AgentTask,
        result: AgentResult,
    ) -> AgentResult:
        structured = self._structured_payload(result)
        shells = [dict(item) for item in structured.get("shells", [])]
        if not shells:
            raw_shells = task.input_context.get("expectation_shells", [])
            shells = [dict(item) for item in raw_shells if isinstance(item, dict)]
            structured = {
                "shells": shells,
                "evidence_refs": [
                    evidence
                    for shell in shells
                    for evidence in shell.get("evidence_refs", [])
                ],
                "delegations": [],
                "unknowns": [],
                "rationale": "Fixture O1 revised construction shells.",
            }
        if not shells or self.construction_resolution_case is None:
            return self._with_structured(result, structured)
        first = dict(shells[0])
        if self.construction_resolution_case == "fix_market_view":
            market_view = dict(first["market_view"])
            market_view["text"] = "NVDA revised market view with construction evidence."
            first["market_view"] = market_view
        elif self.construction_resolution_case == "fix_expectation_name":
            first["expectation_name"] = "NVDA revised construction expectation"
        elif self.construction_resolution_case == "fix_direction":
            first["direction"] = "bearish"
        elif self.construction_resolution_case == "changed_id_set":
            first["expectation_id"] = "exp_changed_core"
        elif self.construction_resolution_case == "empty_revision":
            structured["shells"] = shells
            return self._with_structured(result, structured)
        shells[0] = first
        structured["shells"] = shells
        return self._with_structured(result, structured)

    def _mutate_detail(self, task: AgentTask, result: AgentResult) -> AgentResult:
        if self.detail_case is None:
            return result
        structured = self._structured_payload(result)
        if self.detail_case == "candidate_wrapper_missing":
            structured.pop("candidate", None)
            return self._with_structured(result, structured)
        if self.detail_case == "candidate_wrapper_malformed":
            structured["candidate"] = ["not", "a", "candidate"]
            return self._with_structured(result, structured)
        candidate = dict(structured["candidate"])
        self._apply_detail_case(candidate, self.detail_case)
        if self.detail_case == "numeric_market_view":
            structured["evidence_refs"] = list(candidate["market_view"]["evidence_refs"])
        structured["candidate"] = candidate
        if self.detail_case == "detail_delegation":
            structured["delegations"] = [
                {
                    "delegation_id": "delegation_detail_fixture",
                    "requester_agent": AgentName.O1_EXPECTATION_OWNER.value,
                    "target_agent": AgentName.A2_FACT_CHECK.value,
                    "question": "Verify fixture price reaction evidence before promotion.",
                    "required_evidence": [EvidenceSourceType.MARKET_DATA.value],
                    "blocking_scope": {
                        "document_type": DocumentType.EXPECTATION_UNIT.value,
                        "ticker": task.ticker,
                        "expectation_id": candidate["expectation_id"],
                        "field_path": "realized_facts.price_reaction",
                    },
                    "status": "open",
                    "result_summary": None,
                }
            ]
        mutated = self._with_structured(result, structured)
        if self.detail_case == "proposed_patch_leak":
            patch = self._patch_from_candidate(
                task,
                candidate,
                AgentName.O1_EXPECTATION_OWNER,
            )
            mutated = mutated.model_copy(update={"proposed_patches": [patch]}, deep=True)
        return mutated

    def _mutate_review(self, task: AgentTask, result: AgentResult) -> AgentResult:
        if (
            self.review_case == "structured_blocking_finding"
            and task.agent_name is AgentName.C1_FUNDAMENTAL_RESEARCH
        ):
            structured = self._structured_payload(result)
            evidence = self.factory._evidence(EvidenceSourceType.EXTERNAL_REPORT)
            structured["findings"] = [
                {
                    "expectation_id": "exp_mock_core",
                    "target_path": "key_variables[0].current_status",
                    "status": "unsupported",
                    "rationale": "Fixture reviewer found unsupported current-status evidence.",
                    "evidence_refs": [evidence.model_dump(mode="json")],
                }
            ]
            return self._with_structured(result, structured)
        if (
            self.review_case == "reviewer_patch_leak"
            and task.agent_name is AgentName.C1_FUNDAMENTAL_RESEARCH
        ):
            patch = self._patch_from_candidate(
                task,
                self._candidate_payload(task.ticker),
                AgentName.C1_FUNDAMENTAL_RESEARCH,
            )
            return result.model_copy(update={"proposed_patches": [patch]}, deep=True)
        return result

    def _mutate_resolver(self, task: AgentTask, result: AgentResult) -> AgentResult:
        if self.resolver_case is None:
            return result
        self.resolver_calls += 1
        structured = self._field_repair_payload(task)
        return self._with_structured(result, structured)

    def _field_repair_payload(self, task: AgentTask) -> dict[str, Any]:
        objections = task.input_context.get("unresolved_objections")
        objection_items = [
            item
            for item in objections
            if isinstance(item, dict) and isinstance(item.get("objection_id"), str)
        ] if isinstance(objections, list) else []
        repair_task = task.input_context.get("field_repair_task")
        repair_task = repair_task if isinstance(repair_task, dict) else {}
        evidence = self.factory._evidence(EvidenceSourceType.AGENT_OUTPUT)
        expectation_id = str(repair_task.get("expectation_id") or "exp_mock_core")
        task_id = str(repair_task.get("task_id") or "d2repair_fixture")
        field_family = str(repair_task.get("field_family") or "cross_field")
        finding_ids = [
            str(item)
            for item in repair_task.get("finding_ids", [])
            if isinstance(item, str)
        ]
        current_candidate = dict(repair_task.get("current_candidate") or {})
        decision = "resolved"
        changed_paths: list[str] = [f"document.{field_family}"]
        evidence_refs = [evidence.model_dump(mode="json")]
        revised_candidate: dict[str, Any] | None = None
        typed_updates: dict[str, Any] = {
            "realized_facts": None,
            "key_variables": None,
            "event_monitoring_direction": None,
            "market_view": None,
        }
        if self.resolver_case == "resolved_without_changed_paths_evidence_refs":
            changed_paths = []
            evidence_refs = []
        elif self.resolver_case == "accepted_without_revised_candidate":
            decision = "accepted"
        elif self.resolver_case == "deferred_blocker":
            decision = "deferred"
            changed_paths = []
            evidence_refs = []
        elif self.resolver_case == "revised_candidate_changes_identity":
            decision = "accepted"
            revised_candidate = self._candidate_payload(task.ticker)
            revised_candidate["expectation_id"] = "exp_changed_core"
        elif self.resolver_case == "revised_candidate_fixes_blocker":
            decision = "accepted"
            typed_updates.update(
                self._field_repair_typed_update(
                    field_family,
                    current_candidate,
                    case="fix",
                )
            )
        elif self.resolver_case in {
            "revised_candidate_still_has_blocker",
            "numeric_sanity_revalidation_still_fails",
            "non_numeric_deterministic_blocker_still_fails",
        }:
            if self.resolver_calls > 1:
                decision = "deferred"
                changed_paths = []
                evidence_refs = []
            else:
                decision = "accepted"
                if self.resolver_case == "revised_candidate_still_has_blocker":
                    revised_candidate = self._candidate_payload(task.ticker)
                    self._apply_detail_case(revised_candidate, "unknown_price_reaction")
                elif self.resolver_case == "numeric_sanity_revalidation_still_fails":
                    typed_updates.update(
                        self._field_repair_typed_update(
                            field_family,
                            current_candidate,
                            case="numeric_market_view",
                        )
                    )
                elif self.resolver_case == "non_numeric_deterministic_blocker_still_fails":
                    typed_updates.update(
                        self._field_repair_typed_update(
                            field_family,
                            current_candidate,
                            case="placeholder_text",
                        )
                    )
        decisions = [
            {
                "objection_id": item["objection_id"],
                "finding_id": None,
                "decision": decision,
                "resolution_note": f"Fixture resolver decision: {decision}.",
                "changed_paths": changed_paths,
                "evidence_refs": evidence_refs,
            }
            for item in objection_items
        ]
        return {
            "task_id": task_id,
            "expectation_id": expectation_id,
            "field_family": field_family,
            "decision": decision,
            "decisions": decisions,
            "target_finding_ids": finding_ids,
            "realized_facts": typed_updates["realized_facts"],
            "key_variables": typed_updates["key_variables"],
            "event_monitoring_direction": typed_updates["event_monitoring_direction"],
            "market_view": typed_updates["market_view"],
            "revised_candidate": revised_candidate,
            "evidence_requests": [],
            "unresolved_finding_ids": [],
            "unresolved_reason": "Fixture deferred blocker remains open."
            if decision == "deferred"
            else None,
            "rationale": "Fixture resolver payload for Document2 node matrix.",
        }

    def _field_repair_typed_update(
        self,
        field_family: str,
        current_candidate: dict[str, Any],
        *,
        case: str,
    ) -> dict[str, Any]:
        candidate = dict(current_candidate or self._candidate_payload("NVDA"))
        if case in {"numeric_market_view", "placeholder_text"}:
            self._apply_detail_case(candidate, case)
        if field_family == "realized_facts":
            realized_facts = [dict(item) for item in candidate["realized_facts"]]
            if case == "fix":
                reaction = dict(realized_facts[0]["price_reaction"])
                reaction["price_change"] = "+3%"
                reaction["price_pattern"] = "verified fixture market-data reaction"
                reaction["interpretation"] = "Market data evidence supports the reaction."
                realized_facts[0]["price_reaction"] = reaction
            return {"realized_facts": realized_facts}
        if field_family == "key_variables":
            key_variables = [dict(item) for item in candidate["key_variables"]]
            if case == "fix":
                key_variables[0]["current_status"] = "supported by fixture evidence"
                key_variables[0]["certainty"] = "medium with cited evidence"
            return {"key_variables": key_variables}
        if field_family == "event_monitoring_direction":
            monitoring = dict(candidate["event_monitoring_direction"])
            if case == "fix":
                monitoring["positive_events"] = [
                    "NVDA confirms named customer deployment acceleration."
                ]
                monitoring["negative_events"] = [
                    "Named customer deployment slips beyond the monitored quarter."
                ]
            return {"event_monitoring_direction": monitoring}
        market_view = dict(candidate["market_view"])
        if case == "fix":
            market_view["text"] = "NVDA fixture market view has specific cited support."
            market_view["summary"] = "Specific cited support."
        return {"market_view": market_view}

    def _candidate_payload(self, ticker: str) -> dict[str, Any]:
        return self.factory._expectation_unit(ticker).model_dump(mode="json")

    def _patch_from_candidate(
        self,
        task: AgentTask,
        candidate: dict[str, Any],
        author_agent: AgentName,
    ) -> BlackboardPatch:
        evidence = self.factory._evidence(EvidenceSourceType.AGENT_OUTPUT)
        return BlackboardPatch(
            patch_id=f"patch_matrix_{author_agent.value}",
            target=BlackboardTarget(
                document_type=DocumentType.EXPECTATION_UNIT,
                ticker=task.ticker,
                expectation_id=str(candidate.get("expectation_id") or "exp_mock_core"),
                field_path="document",
            ),
            operation=PatchOperation.UPDATE,
            before=None,
            after=candidate,
            rationale="Fixture patch used to test forbidden patch output.",
            evidence_refs=[evidence],
            author_agent=author_agent,
            validation_status=ValidationStatus.PENDING,
        )

    def _construction_objection(
        self,
        ticker: str,
        *,
        field_path: str,
        unrelated: bool = False,
    ) -> Objection:
        target_document_type = (
            DocumentType.KNOWN_EVENTS if unrelated else DocumentType.EXPECTATION_UNIT
        )
        return Objection(
            objection_id=f"obj_matrix_construction_{field_path}",
            source_agent=AgentName.A1_DOXATLAS_AUDIT,
            target=BlackboardTarget(
                document_type=target_document_type,
                ticker=ticker,
                expectation_id=None if unrelated else "exp_mock_core",
                field_path=field_path,
            ),
            severity=ObjectionSeverity.BLOCKING,
            reason=f"Fixture construction blocker on {field_path}.",
            taxonomy="document2_construction_matrix",
            target_path=field_path,
            status=ObjectionStatus.OPEN,
        )

    def _apply_detail_case(self, candidate: dict[str, Any], case: str) -> None:
        if case == "unknown_price_reaction":
            reaction = candidate["realized_facts"][0]["price_reaction"]
            reaction["price_change"] = "unknown"
            reaction["price_pattern"] = "not established"
            reaction["interpretation"] = "Market reaction evidence is unavailable."
        elif case == "missing_market_evidence":
            market_view = dict(candidate["market_view"])
            market_view["evidence_refs"] = []
            candidate["market_view"] = market_view
        elif case == "generic_monitoring_trigger":
            candidate["event_monitoring_direction"]["positive_events"] = [
                "confirmed deployments"
            ]
            candidate["event_monitoring_direction"]["negative_events"] = [
                "deployment delays"
            ]
        elif case == "missing_realized_fact_evidence_refs":
            candidate["realized_facts"][0]["evidence_refs"] = []
        elif case == "missing_key_variable_evidence_refs":
            candidate["key_variables"][0]["evidence_refs"] = []
        elif case == "empty_key_variables":
            candidate["key_variables"] = []
        elif case == "empty_positive_events":
            candidate["event_monitoring_direction"]["positive_events"] = []
        elif case == "empty_negative_events":
            candidate["event_monitoring_direction"]["negative_events"] = []
        elif case == "changed_expectation_id":
            candidate["expectation_id"] = "exp_changed_core"
        elif case == "changed_expectation_name":
            candidate["expectation_name"] = "Changed expectation name"
        elif case == "changed_direction":
            candidate["direction"] = "bearish"
        elif case == "placeholder_text":
            market_view = dict(candidate["market_view"])
            market_view["text"] = "TBD placeholder"
            candidate["market_view"] = market_view
        elif case == "numeric_market_view":
            market_view = dict(candidate["market_view"])
            market_view["text"] = (
                "The stock price is $123 and market cap is $3.5 billion based only "
                "on narrative context."
            )
            candidate["market_view"] = market_view
        elif case == "empty_realized_facts":
            candidate["realized_facts"] = []
        elif case in {"detail_delegation", "proposed_patch_leak"}:
            return

    def _structured_payload(self, result: AgentResult) -> dict[str, Any]:
        structured = result.payload.get("structured")
        assert isinstance(structured, dict)
        return dict(structured)

    def _with_structured(
        self,
        result: AgentResult,
        structured: dict[str, Any],
    ) -> AgentResult:
        return result.model_copy(
            update={"payload": result.payload | {"structured": structured}},
            deep=True,
        )


def _run_matrix(
    *,
    stop_after: WorkflowNode,
    runner: Document2NodeMatrixRunner | None = None,
) -> tuple[BlackboardInitializationWorkflow, Any]:
    workflow = BlackboardInitializationWorkflow(
        execution_mode="agent_runner",
        runner=runner or Document2NodeMatrixRunner(),
    )
    return workflow, workflow.run("NVDA", stop_after=stop_after)


def _resume_at(
    workflow: BlackboardInitializationWorkflow,
    checkpoint: WorkflowCheckpoint,
    node: WorkflowNode,
) -> Any:
    return workflow.resume(
        checkpoint.model_copy(update={"next_node": node}, deep=True),
        stop_after=node,
    )


def _assert_running_to(result: Any, next_node: WorkflowNode) -> None:
    assert result.status is WorkflowRunStatus.RUNNING
    assert result.error is None
    assert result.checkpoint.next_node is next_node


def _assert_blocked(result: Any, message: str) -> None:
    assert result.status is WorkflowRunStatus.BLOCKED
    assert result.error is not None
    assert message in result.error


@pytest.mark.parametrize(
    ("case", "expected_status", "message"),
    [
        pytest.param(
            None,
            WorkflowRunStatus.RUNNING,
            "",
            id="GenerateExpectationConstruction__canonical_output__accepted",
        ),
        pytest.param(
            "one_shell",
            WorkflowRunStatus.RUNNING,
            "",
            id="GenerateExpectationConstruction__recoverable_one_shell__accepted",
        ),
        pytest.param(
            "missing_evidence",
            WorkflowRunStatus.BLOCKED,
            "shell has no evidence",
            id="GenerateExpectationConstruction__contract_missing_evidence__schema_failure",
        ),
        pytest.param(
            "too_many_shells",
            WorkflowRunStatus.BLOCKED,
            "too many expectations",
            id="GenerateExpectationConstruction__contract_too_many_shells__schema_failure",
        ),
    ],
)
def test_generate_expectation_construction_node_matrix(
    case: str | None,
    expected_status: WorkflowRunStatus,
    message: str,
) -> None:
    _, result = _run_matrix(
        stop_after=WorkflowNode.GENERATE_EXPECTATION_CONSTRUCTION,
        runner=Document2NodeMatrixRunner(construction_case=case),
    )

    if expected_status is WorkflowRunStatus.RUNNING:
        _assert_running_to(result, WorkflowNode.REVIEW_EXPECTATION_CONSTRUCTION)
        assert result.checkpoint.metadata["expectation_shells"]
    else:
        _assert_blocked(result, message)


@pytest.mark.parametrize(
    ("case", "expected_status", "message"),
    [
        pytest.param(
            None,
            WorkflowRunStatus.RUNNING,
            "",
            id="ReviewExpectationConstruction__canonical_output__accepted",
        ),
        pytest.param(
            "market_view",
            WorkflowRunStatus.RUNNING,
            "",
            id="ReviewExpectationConstruction__blocking_objection__bridged_objection",
        ),
        pytest.param(
            "reviewer_patch_leak",
            WorkflowRunStatus.BLOCKED,
            "forbids proposed_patches",
            id="ReviewExpectationConstruction__contract_patch_leak__schema_failure",
        ),
    ],
)
def test_review_expectation_construction_node_matrix(
    case: str | None,
    expected_status: WorkflowRunStatus,
    message: str,
) -> None:
    _, result = _run_matrix(
        stop_after=WorkflowNode.REVIEW_EXPECTATION_CONSTRUCTION,
        runner=Document2NodeMatrixRunner(construction_review_case=case),
    )

    if expected_status is WorkflowRunStatus.RUNNING:
        _assert_running_to(result, WorkflowNode.RESOLVE_EXPECTATION_CONSTRUCTION)
        if case == "market_view":
            assert result.summary.unresolved_objection_count == 1
        if case == "reviewer_patch_leak":
            assert result.summary.unresolved_objection_count == 0
    else:
        _assert_blocked(result, message)


@pytest.mark.parametrize(
    ("review_case", "resolve_case", "expected_status", "message"),
    [
        pytest.param(
            "market_view",
            "fix_market_view",
            WorkflowRunStatus.RUNNING,
            "",
            id="ResolveExpectationConstruction__market_view_blocker_fixed__transaction_accepted",
        ),
        pytest.param(
            "expectation_name",
            "fix_expectation_name",
            WorkflowRunStatus.RUNNING,
            "",
            id="ResolveExpectationConstruction__expectation_name_blocker_fixed__transaction_accepted",
        ),
        pytest.param(
            "direction",
            "fix_direction",
            WorkflowRunStatus.RUNNING,
            "",
            id="ResolveExpectationConstruction__direction_blocker_fixed__transaction_accepted",
        ),
        pytest.param(
            "market_view",
            "changed_id_set",
            WorkflowRunStatus.BLOCKED,
            "expectation_id set",
            id="ResolveExpectationConstruction__changed_expectation_id_set__transaction_rejected",
        ),
        pytest.param(
            "market_view",
            "empty_revision",
            WorkflowRunStatus.BLOCKED,
            "empty revision",
            id="ResolveExpectationConstruction__empty_revision__transaction_rejected",
        ),
        pytest.param(
            "unrelated",
            "fix_market_view",
            WorkflowRunStatus.BLOCKED,
            "unrelated objections",
            id="ResolveExpectationConstruction__unrelated_objection_closed__transaction_rejected",
        ),
    ],
)
def test_resolve_expectation_construction_node_matrix(
    review_case: str,
    resolve_case: str,
    expected_status: WorkflowRunStatus,
    message: str,
) -> None:
    _, result = _run_matrix(
        stop_after=WorkflowNode.RESOLVE_EXPECTATION_CONSTRUCTION,
        runner=Document2NodeMatrixRunner(
            construction_review_case=review_case,
            construction_resolution_case=resolve_case,
        ),
    )

    if expected_status is WorkflowRunStatus.RUNNING:
        _assert_running_to(result, WorkflowNode.GENERATE_EXPECTATION_DETAILS)
        assert result.summary.unresolved_objection_count == 0
    else:
        _assert_blocked(result, message)


@pytest.mark.parametrize(
    ("case", "expected_status", "message"),
    [
        pytest.param(
            None,
            WorkflowRunStatus.RUNNING,
            "",
            id="GenerateExpectationDetails__good_candidate__accepted",
        ),
        pytest.param(
            "unknown_price_reaction",
            WorkflowRunStatus.RUNNING,
            "",
            id="GenerateExpectationDetails__unknown_price_reaction__accepted_for_review",
        ),
        pytest.param(
            "missing_market_evidence",
            WorkflowRunStatus.RUNNING,
            "",
            id="GenerateExpectationDetails__missing_market_evidence__accepted_for_review",
        ),
        pytest.param(
            "generic_monitoring_trigger",
            WorkflowRunStatus.RUNNING,
            "",
            id="GenerateExpectationDetails__generic_monitoring_trigger__accepted_for_review",
        ),
        pytest.param(
            "missing_realized_fact_evidence_refs",
            WorkflowRunStatus.RUNNING,
            "",
            id="GenerateExpectationDetails__missing_realized_fact_evidence_refs__accepted_for_review",
        ),
        pytest.param(
            "missing_key_variable_evidence_refs",
            WorkflowRunStatus.RUNNING,
            "",
            id="GenerateExpectationDetails__missing_key_variable_evidence_refs__accepted_for_review",
        ),
        pytest.param(
            "changed_expectation_id",
            WorkflowRunStatus.BLOCKED,
            "changed the construction expectation_id",
            id="GenerateExpectationDetails__changed_expectation_id__schema_failure",
        ),
        pytest.param(
            "changed_expectation_name",
            WorkflowRunStatus.BLOCKED,
            "changed the construction expectation_name",
            id="GenerateExpectationDetails__changed_expectation_name__schema_failure",
        ),
        pytest.param(
            "changed_direction",
            WorkflowRunStatus.BLOCKED,
            "changed the construction direction",
            id="GenerateExpectationDetails__changed_direction__schema_failure",
        ),
        pytest.param(
            "proposed_patch_leak",
            WorkflowRunStatus.BLOCKED,
            "forbids proposed_patches",
            id="GenerateExpectationDetails__o1_leaks_proposed_patches__schema_failure",
        ),
        pytest.param(
            "candidate_wrapper_missing",
            WorkflowRunStatus.BLOCKED,
            "candidate",
            id="GenerateExpectationDetails__candidate_wrapper_missing__schema_failure",
        ),
        pytest.param(
            "candidate_wrapper_malformed",
            WorkflowRunStatus.BLOCKED,
            "ExpectationDetailCandidateResult",
            id="GenerateExpectationDetails__candidate_wrapper_malformed__schema_failure",
        ),
    ],
)
def test_generate_expectation_details_node_matrix(
    case: str | None,
    expected_status: WorkflowRunStatus,
    message: str,
) -> None:
    _, result = _run_matrix(
        stop_after=WorkflowNode.GENERATE_EXPECTATION_DETAILS,
        runner=Document2NodeMatrixRunner(detail_case=case),
    )

    if expected_status is WorkflowRunStatus.RUNNING:
        _assert_running_to(result, WorkflowNode.REVIEW_EXPECTATION_FIELDS)
        expectation_patches = [
            patch
            for patch in result.checkpoint.pending_patches
            if patch.target.document_type is DocumentType.EXPECTATION_UNIT
        ]
        assert expectation_patches
    else:
        _assert_blocked(result, message)


@pytest.mark.parametrize(
    ("detail_case", "review_case", "expected_status", "message"),
    [
        pytest.param(
            None,
            None,
            WorkflowRunStatus.RUNNING,
            "",
            id="ReviewExpectationFields__canonical_output__accepted",
        ),
        pytest.param(
            None,
            "structured_blocking_finding",
            WorkflowRunStatus.RUNNING,
            "",
            id="ReviewExpectationFields__reviewer_structured_blocking_finding__bridged_objection",
        ),
        pytest.param(
            "placeholder_text",
            None,
            WorkflowRunStatus.RUNNING,
            "",
            id="ReviewExpectationFields__placeholder_generic_finding__bridged_objection",
        ),
        pytest.param(
            "numeric_market_view",
            None,
            WorkflowRunStatus.RUNNING,
            "",
            id="ReviewExpectationFields__numeric_sanity_finding__bridged_objection",
        ),
        pytest.param(
            "unknown_price_reaction",
            None,
            WorkflowRunStatus.RUNNING,
            "",
            id="ReviewExpectationFields__unknown_price_reaction_finding__bridged_objection",
        ),
        pytest.param(
            None,
            "reviewer_patch_leak",
            WorkflowRunStatus.BLOCKED,
            "reviewers must not propose patches",
            id="ReviewExpectationFields__reviewer_proposed_patches__schema_failure",
        ),
    ],
)
def test_review_expectation_fields_node_matrix(
    detail_case: str | None,
    review_case: str | None,
    expected_status: WorkflowRunStatus,
    message: str,
) -> None:
    _, result = _run_matrix(
        stop_after=WorkflowNode.REVIEW_EXPECTATION_FIELDS,
        runner=Document2NodeMatrixRunner(
            detail_case=detail_case,
            review_case=review_case,
        ),
    )

    if expected_status is WorkflowRunStatus.RUNNING:
        _assert_running_to(result, WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS)
        findings = result.checkpoint.metadata.get(DOCUMENT2_REVIEW_FINDINGS_KEY, [])
        if detail_case is not None or review_case == "structured_blocking_finding":
            assert findings
            assert result.summary.unresolved_objection_count >= 1
            blocking = [
                finding
                for finding in findings
                if finding["blocks_promotion"] is True
            ]
            assert blocking
            assert all(finding["source_objection_id"] for finding in blocking)
        else:
            assert findings == []
    else:
        _assert_blocked(result, message)


@pytest.mark.parametrize(
    ("detail_case", "review_case", "resolver_case", "expected_status", "message"),
    [
        pytest.param(
            None,
            "structured_blocking_finding",
            None,
            WorkflowRunStatus.RUNNING,
            "",
            id="ResolveObjectionsAndDelegations__canonical_resolution__accepted",
        ),
        pytest.param(
            None,
            "structured_blocking_finding",
            "resolved_without_changed_paths_evidence_refs",
            WorkflowRunStatus.BLOCKED,
            "changed_paths or evidence_refs",
            id="ResolveObjectionsAndDelegations__resolved_without_changed_paths_evidence_refs__transaction_rejected",
        ),
        pytest.param(
            None,
            "structured_blocking_finding",
            "accepted_without_revised_candidate",
            WorkflowRunStatus.BLOCKED,
            "single-field accepted repair requires one typed field update",
            id="ResolveObjectionsAndDelegations__accepted_without_revised_candidate__schema_failure",
        ),
        pytest.param(
            None,
            "structured_blocking_finding",
            "revised_candidate_still_has_blocker",
            WorkflowRunStatus.BLOCKED,
            "single-field repair must not return revised_candidate",
            id="ResolveObjectionsAndDelegations__revised_candidate_still_has_blocker__retained_blocker",
        ),
        pytest.param(
            None,
            "structured_blocking_finding",
            "deferred_blocker",
            WorkflowRunStatus.BLOCKED,
            "left blockers unresolved",
            id="ResolveObjectionsAndDelegations__deferred_blocker_remains_open__retained_blocker",
        ),
        pytest.param(
            None,
            "structured_blocking_finding",
            "revised_candidate_changes_identity",
            WorkflowRunStatus.BLOCKED,
            "single-field repair must not return revised_candidate",
            id="ResolveObjectionsAndDelegations__revised_candidate_changes_identity__schema_failure",
        ),
        pytest.param(
            "numeric_market_view",
            None,
            "numeric_sanity_revalidation_still_fails",
            WorkflowRunStatus.BLOCKED,
            "left blockers unresolved",
            id="ResolveObjectionsAndDelegations__numeric_sanity_revalidation_still_fails__retained_blocker",
        ),
        pytest.param(
            "placeholder_text",
            None,
            "non_numeric_deterministic_blocker_still_fails",
            WorkflowRunStatus.BLOCKED,
            "left blockers unresolved",
            id="ResolveObjectionsAndDelegations__non_numeric_deterministic_blocker_still_fails__retained_blocker",
        ),
    ],
)
def test_resolve_objections_and_delegations_node_matrix(
    detail_case: str | None,
    review_case: str | None,
    resolver_case: str | None,
    expected_status: WorkflowRunStatus,
    message: str,
) -> None:
    _, result = _run_matrix(
        stop_after=WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS,
        runner=Document2NodeMatrixRunner(
            detail_case=detail_case,
            review_case=review_case,
            resolver_case=resolver_case,
        ),
    )

    if expected_status is WorkflowRunStatus.RUNNING:
        _assert_running_to(result, WorkflowNode.PROMOTE_EXPECTATION_TO_BELIEF_STATE)
        assert result.summary.unresolved_objection_count == 0
    else:
        _assert_blocked(result, message)


def _promotion_checkpoint(
    *,
    runner: Document2NodeMatrixRunner | None = None,
) -> tuple[BlackboardInitializationWorkflow, WorkflowCheckpoint]:
    workflow, result = _run_matrix(
        stop_after=WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS,
        runner=runner or Document2NodeMatrixRunner(),
    )
    _assert_running_to(result, WorkflowNode.PROMOTE_EXPECTATION_TO_BELIEF_STATE)
    return workflow, result.checkpoint


def _promotion_checkpoint_with_hidden_detail_issue(
    detail_case: str,
) -> tuple[BlackboardInitializationWorkflow, WorkflowCheckpoint]:
    workflow, checkpoint = _promotion_checkpoint()
    patch = checkpoint.pending_patches[0]
    bad_after = dict(patch.after)
    Document2NodeMatrixRunner()._apply_detail_case(bad_after, detail_case)
    checkpoint = checkpoint.model_copy(
        update={
            "pending_patches": [
                patch.model_copy(update={"after": bad_after}, deep=True),
                *checkpoint.pending_patches[1:],
            ]
        },
        deep=True,
    )
    return workflow, checkpoint


def _blocking_review_finding() -> Document2ReviewFinding:
    evidence = StructuredInitializationRunner(include_blockers=False).factory._evidence(
        EvidenceSourceType.EXTERNAL_REPORT
    )
    return Document2ReviewFinding(
        reviewer_agent=AgentName.C1_FUNDAMENTAL_RESEARCH,
        expectation_id="exp_mock_core",
        target_path="key_variables[0].current_status",
        severity="blocking",
        reason="Fixture active finding blocks promotion.",
        evidence_assessments=[
            EvidenceAssessment(
                target_path="key_variables[0].current_status",
                status="insufficient",
                evidence_refs=[evidence],
                reason="Fixture evidence is insufficient.",
            )
        ],
        supplemental_evidence_refs=[evidence],
        blocks_promotion=True,
    )


def _open_objection(
    workflow: BlackboardInitializationWorkflow,
    checkpoint: WorkflowCheckpoint,
) -> None:
    workflow.blackboard.create_objection(
        checkpoint.run_id,
        Objection(
            objection_id="obj_matrix_promotion_open",
            source_agent=AgentName.A1_DOXATLAS_AUDIT,
            target=BlackboardTarget(
                document_type=DocumentType.EXPECTATION_UNIT,
                ticker=checkpoint.ticker,
                expectation_id="exp_mock_core",
                field_path="document",
            ),
            severity=ObjectionSeverity.BLOCKING,
            reason="Fixture active objection blocks promotion.",
            target_path="document",
            status=ObjectionStatus.OPEN,
        ),
    )


def test_promote_expectation_to_belief_state_no_active_blocker_accepted() -> None:
    workflow, checkpoint = _promotion_checkpoint()

    result = _resume_at(
        workflow,
        checkpoint,
        WorkflowNode.PROMOTE_EXPECTATION_TO_BELIEF_STATE,
    )

    _assert_running_to(result, WorkflowNode.GENERATE_GLOBAL_NARRATIVE_REPORT)
    assert result.checkpoint.pending_patches == []
    assert DocumentType.EXPECTATION_UNIT in result.summary.stable_document_types


def test_promote_expectation_to_belief_state_active_finding_blocks_promotion() -> None:
    workflow, checkpoint = _promotion_checkpoint()
    finding = _blocking_review_finding()
    checkpoint = checkpoint.model_copy(
        update={
            "metadata": checkpoint.metadata
            | {DOCUMENT2_REVIEW_FINDINGS_KEY: [finding.model_dump(mode="json")]}
        },
        deep=True,
    )

    result = _resume_at(
        workflow,
        checkpoint,
        WorkflowNode.PROMOTE_EXPECTATION_TO_BELIEF_STATE,
    )

    _assert_blocked(result, "Document2 promotion blocked")


def test_promote_expectation_to_belief_state_unresolved_objection_blocks_promotion() -> None:
    workflow, checkpoint = _promotion_checkpoint()
    _open_objection(workflow, checkpoint)

    result = _resume_at(
        workflow,
        checkpoint,
        WorkflowNode.PROMOTE_EXPECTATION_TO_BELIEF_STATE,
    )

    _assert_blocked(result, "Promotion requires all blocking objections")


def test_promote_expectation_to_belief_state_candidate_differs_from_source_patch_rejected() -> None:
    _, checkpoint = _promotion_checkpoint()
    patch = checkpoint.pending_patches[0]
    candidate = document2_promotion_candidate_from_patch(patch)
    changed_document = candidate.document.model_copy(
        update={"expectation_name": "Mutated promotion candidate"},
        deep=True,
    )
    changed_candidate = candidate.model_copy(update={"document": changed_document}, deep=True)

    with pytest.raises(ValueError, match="candidate differs from the source patch"):
        blackboard_patch_from_document2_promotion_candidate(changed_candidate, patch)


def test_promote_expectation_to_belief_state_promotion_mutation_attempt_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import doxagent.workflows.document2.legacy_promotion as legacy_promotion

    workflow, checkpoint = _promotion_checkpoint()
    original = legacy_promotion.document2_promotion_candidate_from_patch

    def changed_candidate_from_patch(
        patch: BlackboardPatch,
        *,
        review_findings: Any = (),
    ) -> Document2PromotionCandidate:
        candidate = original(patch, review_findings=review_findings)
        changed_document = candidate.document.model_copy(
            update={"expectation_name": "Mutated promotion candidate"},
            deep=True,
        )
        return candidate.model_copy(update={"document": changed_document}, deep=True)

    monkeypatch.setattr(
        legacy_promotion,
        "document2_promotion_candidate_from_patch",
        changed_candidate_from_patch,
    )

    result = _resume_at(
        workflow,
        checkpoint,
        WorkflowNode.PROMOTE_EXPECTATION_TO_BELIEF_STATE,
    )

    _assert_blocked(result, "candidate differs from the source patch")


def test_promote_expectation_to_belief_state_does_not_first_discover_detail_issue() -> None:
    workflow, checkpoint = _promotion_checkpoint_with_hidden_detail_issue(
        "unknown_price_reaction"
    )

    result = _resume_at(
        workflow,
        checkpoint,
        WorkflowNode.PROMOTE_EXPECTATION_TO_BELIEF_STATE,
    )

    _assert_running_to(result, WorkflowNode.GENERATE_GLOBAL_NARRATIVE_REPORT)
    audits = result.checkpoint.metadata[DOCUMENT2_PROMOTION_AUDITS_KEY]
    assert audits[-1]["status"] == "accepted"


DETERMINISTIC_REVALIDATION_CASES = [
    pytest.param(
        "unknown_price_reaction",
        "realized_facts[0].price_reaction",
        id="MiniFlow_DetailToReview__unknown_price_reaction__typed_finding",
    ),
    pytest.param(
        "missing_realized_fact_evidence_refs",
        "realized_facts[0].evidence_refs",
        id="MiniFlow_DetailToReview__missing_realized_fact_evidence_refs__typed_finding",
    ),
    pytest.param(
        "missing_key_variable_evidence_refs",
        "key_variables[0].evidence_refs",
        id="MiniFlow_DetailToReview__missing_key_variable_evidence_refs__typed_finding",
    ),
    pytest.param(
        "empty_realized_facts",
        "realized_facts",
        id="MiniFlow_DetailToReview__empty_realized_facts__typed_finding",
    ),
    pytest.param(
        "empty_key_variables",
        "key_variables",
        id="MiniFlow_DetailToReview__empty_key_variables__typed_finding",
    ),
    pytest.param(
        "empty_positive_events",
        "event_monitoring_direction.positive_events",
        id="MiniFlow_DetailToReview__empty_positive_events__typed_finding",
    ),
    pytest.param(
        "empty_negative_events",
        "event_monitoring_direction.negative_events",
        id="MiniFlow_DetailToReview__empty_negative_events__typed_finding",
    ),
    pytest.param(
        "generic_monitoring_trigger",
        "event_monitoring_direction.positive_events[0]",
        id="MiniFlow_DetailToReview__generic_monitoring_trigger__typed_finding",
    ),
    pytest.param(
        "placeholder_text",
        "market_view.text",
        id="MiniFlow_DetailToReview__placeholder_generic_text__typed_finding",
    ),
    pytest.param(
        "numeric_market_view",
        "realized_facts.price_reaction",
        id="MiniFlow_DetailToReview__numeric_sanity__typed_finding",
    ),
]


@pytest.mark.parametrize(("detail_case", "target_path"), DETERMINISTIC_REVALIDATION_CASES)
def test_mini_flow_generate_details_to_review_deterministic_revalidation_matrix(
    detail_case: str,
    target_path: str,
) -> None:
    _, result = _run_matrix(
        stop_after=WorkflowNode.REVIEW_EXPECTATION_FIELDS,
        runner=Document2NodeMatrixRunner(detail_case=detail_case),
    )

    _assert_running_to(result, WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS)
    findings = result.checkpoint.metadata[DOCUMENT2_REVIEW_FINDINGS_KEY]
    assert any(finding["target_path"] == target_path for finding in findings)
    blocking = [finding for finding in findings if finding["blocks_promotion"] is True]
    assert blocking
    assert all(finding["source_objection_id"] for finding in blocking)
    assert result.summary.unresolved_objection_count >= 1


@pytest.mark.parametrize(
    ("detail_case", "resolver_case", "expected_status", "message"),
    [
        pytest.param(
            "unknown_price_reaction",
            None,
            WorkflowRunStatus.BLOCKED,
            "left blockers unresolved",
            id="MiniFlow_ReviewToResolver__unknown_price_reaction__retained_blocker",
        ),
        pytest.param(
            "missing_realized_fact_evidence_refs",
            None,
            WorkflowRunStatus.BLOCKED,
            "left blockers unresolved",
            id="MiniFlow_ReviewToResolver__missing_realized_fact_evidence_refs__retained_blocker",
        ),
        pytest.param(
            "missing_key_variable_evidence_refs",
            None,
            WorkflowRunStatus.BLOCKED,
            "left blockers unresolved",
            id="MiniFlow_ReviewToResolver__missing_key_variable_evidence_refs__retained_blocker",
        ),
        pytest.param(
            "empty_realized_facts",
            None,
            WorkflowRunStatus.BLOCKED,
            "left blockers unresolved",
            id="MiniFlow_ReviewToResolver__empty_realized_facts__retained_blocker",
        ),
        pytest.param(
            "empty_key_variables",
            None,
            WorkflowRunStatus.BLOCKED,
            "left blockers unresolved",
            id="MiniFlow_ReviewToResolver__empty_key_variables__retained_blocker",
        ),
        pytest.param(
            "empty_positive_events",
            None,
            WorkflowRunStatus.BLOCKED,
            "left blockers unresolved",
            id="MiniFlow_ReviewToResolver__empty_positive_events__retained_blocker",
        ),
        pytest.param(
            "empty_negative_events",
            None,
            WorkflowRunStatus.BLOCKED,
            "left blockers unresolved",
            id="MiniFlow_ReviewToResolver__empty_negative_events__retained_blocker",
        ),
        pytest.param(
            "generic_monitoring_trigger",
            "deferred_blocker",
            WorkflowRunStatus.BLOCKED,
            "left blockers unresolved",
            id="MiniFlow_ReviewToResolver__generic_monitoring_trigger__retained_blocker",
        ),
        pytest.param(
            "placeholder_text",
            None,
            WorkflowRunStatus.BLOCKED,
            "left blockers unresolved",
            id="MiniFlow_ReviewToResolver__placeholder_generic_text__retained_blocker",
        ),
        pytest.param(
            "numeric_market_view",
            None,
            WorkflowRunStatus.BLOCKED,
            "left blockers unresolved",
            id="MiniFlow_ReviewToResolver__numeric_sanity__retained_blocker",
        ),
    ],
)
def test_mini_flow_review_to_resolver_deterministic_revalidation_matrix(
    detail_case: str,
    resolver_case: str | None,
    expected_status: WorkflowRunStatus,
    message: str,
) -> None:
    _, result = _run_matrix(
        stop_after=WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS,
        runner=Document2NodeMatrixRunner(
            detail_case=detail_case,
            resolver_case=resolver_case,
        ),
    )

    if expected_status is WorkflowRunStatus.RUNNING:
        _assert_running_to(result, WorkflowNode.PROMOTE_EXPECTATION_TO_BELIEF_STATE)
        assert result.summary.unresolved_objection_count == 0
    else:
        _assert_blocked(result, message)


@pytest.mark.parametrize(
    ("detail_case", "expected_status", "message"),
    [
        pytest.param(
            None,
            WorkflowRunStatus.RUNNING,
            "",
            id="MiniFlow_ResolverToPromotion__canonical__accepted",
        ),
        pytest.param(
            "unknown_price_reaction",
            WorkflowRunStatus.RUNNING,
            "",
            id="MiniFlow_ResolverToPromotion__unknown_price_reaction__not_first_discovered",
        ),
        pytest.param(
            "placeholder_text",
            WorkflowRunStatus.RUNNING,
            "",
            id="MiniFlow_ResolverToPromotion__placeholder_generic_text__not_first_discovered",
        ),
    ],
)
def test_mini_flow_resolver_to_promotion_boundary_matrix(
    detail_case: str | None,
    expected_status: WorkflowRunStatus,
    message: str,
) -> None:
    if detail_case is None:
        workflow, checkpoint = _promotion_checkpoint()
    else:
        workflow, checkpoint = _promotion_checkpoint_with_hidden_detail_issue(detail_case)

    result = _resume_at(
        workflow,
        checkpoint,
        WorkflowNode.PROMOTE_EXPECTATION_TO_BELIEF_STATE,
    )

    if expected_status is WorkflowRunStatus.RUNNING:
        _assert_running_to(result, WorkflowNode.GENERATE_GLOBAL_NARRATIVE_REPORT)
    else:
        _assert_blocked(result, message)


def test_detail_candidate_delegation_output_is_rejected_by_workflow() -> None:
    _workflow, result = _run_matrix(
        stop_after=WorkflowNode.GENERATE_EXPECTATION_DETAILS,
        runner=Document2NodeMatrixRunner(detail_case="detail_delegation"),
    )

    _assert_blocked(result, "detail candidates must not return delegations")


def test_metadata_sync_after_resolver_revision_and_promotion_audits() -> None:
    workflow, result = _run_matrix(
        stop_after=WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS,
        runner=Document2NodeMatrixRunner(
            construction_case="one_shell",
            detail_case="placeholder_text",
            resolver_case="revised_candidate_fixes_blocker",
        ),
    )
    _assert_running_to(result, WorkflowNode.PROMOTE_EXPECTATION_TO_BELIEF_STATE)

    checkpoint = result.checkpoint
    revision_entries = checkpoint.metadata[DOCUMENT2_PENDING_REVISIONS_KEY]
    assert len(revision_entries) == 1
    revision_entry = revision_entries[0]
    assert revision_entry["updated_by_transaction"] is True
    assert revision_entry["revision"]["source"] == "resolution_plan"
    assert revision_entry["legacy_patch_id"] == checkpoint.pending_patches[0].patch_id
    assert revision_entry["legacy_patch"]["patch_id"] == checkpoint.pending_patches[0].patch_id
    assert checkpoint.metadata[DOCUMENT2_TRANSACTION_AUDITS_KEY][-1]["status"] == "accepted"
    assert checkpoint.metadata[DOCUMENT2_REVIEW_FINDINGS_KEY]

    promoted = _resume_at(
        workflow,
        checkpoint,
        WorkflowNode.PROMOTE_EXPECTATION_TO_BELIEF_STATE,
    )

    _assert_running_to(promoted, WorkflowNode.GENERATE_GLOBAL_NARRATIVE_REPORT)
    assert promoted.checkpoint.metadata[DOCUMENT2_PROMOTION_AUDITS_KEY][-1]["status"] == "accepted"
    run = workflow.blackboard.get_run(checkpoint.run_id)
    assert any(
        entry.content_type == "document2_transaction_audit"
        for entry in run.working_memory
    )
    assert any(
        entry.content_type == "document2_promotion_audit"
        for entry in run.working_memory
    )


def test_finding_lifecycle_source_objection_id_unresolved_blocks_promotion() -> None:
    workflow, result = _run_matrix(
        stop_after=WorkflowNode.REVIEW_EXPECTATION_FIELDS,
        runner=Document2NodeMatrixRunner(detail_case="placeholder_text"),
    )
    _assert_running_to(result, WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS)
    findings = result.checkpoint.metadata[DOCUMENT2_REVIEW_FINDINGS_KEY]
    source_ids = {
        finding["source_objection_id"]
        for finding in findings
        if finding["blocks_promotion"] is True
    }
    run = workflow.blackboard.get_run(result.checkpoint.run_id)
    unresolved_ids = {
        objection.objection_id
        for objection in run.objections
        if objection.is_unresolved
    }
    assert source_ids.intersection(unresolved_ids)

    promoted = _resume_at(
        workflow,
        result.checkpoint,
        WorkflowNode.PROMOTE_EXPECTATION_TO_BELIEF_STATE,
    )

    _assert_blocked(promoted, "Promotion requires all blocking objections")


def test_finding_lifecycle_source_objection_id_resolved_allows_promotion() -> None:
    workflow, checkpoint = _promotion_checkpoint(
        runner=Document2NodeMatrixRunner(
            construction_case="one_shell",
            detail_case="placeholder_text",
            resolver_case="revised_candidate_fixes_blocker",
        ),
    )
    findings = checkpoint.metadata[DOCUMENT2_REVIEW_FINDINGS_KEY]
    source_ids = {
        finding["source_objection_id"]
        for finding in findings
        if finding["blocks_promotion"] is True
    }
    run = workflow.blackboard.get_run(checkpoint.run_id)
    inactive_ids = {
        objection.objection_id
        for objection in run.objections
        if not objection.is_unresolved
    }
    assert source_ids
    assert source_ids.issubset(inactive_ids)

    promoted = _resume_at(
        workflow,
        checkpoint,
        WorkflowNode.PROMOTE_EXPECTATION_TO_BELIEF_STATE,
    )

    _assert_running_to(promoted, WorkflowNode.GENERATE_GLOBAL_NARRATIVE_REPORT)


def test_finding_lifecycle_missing_source_objection_id_blocks_promotion() -> None:
    workflow, checkpoint = _promotion_checkpoint()
    finding = _blocking_review_finding().model_copy(
        update={"source_objection_id": "obj_missing_from_blackboard"},
        deep=True,
    )
    checkpoint = checkpoint.model_copy(
        update={
            "metadata": checkpoint.metadata
            | {DOCUMENT2_REVIEW_FINDINGS_KEY: [finding.model_dump(mode="json")]}
        },
        deep=True,
    )

    result = _resume_at(
        workflow,
        checkpoint,
        WorkflowNode.PROMOTE_EXPECTATION_TO_BELIEF_STATE,
    )

    _assert_blocked(result, "Document2 promotion blocked")


def _adapter_task(
    *,
    schema: str,
    node: WorkflowNode,
    input_context: dict[str, Any] | None = None,
) -> AgentTask:
    return AgentTask.model_validate(
        {
            "task_id": f"task_adapter_{schema}",
            "ticker": "NVDA",
            "agent_name": AgentName.O1_EXPECTATION_OWNER,
            "task_type": TaskType.REVIEW_EXPECTATION_FIELD,
            "input_context": input_context or {},
            "required_output_schema": schema,
            "permissions": default_agent_registry()
            .get(AgentName.O1_EXPECTATION_OWNER)
            .runtime.to_permissions(),
            "run_metadata": {
                "run_id": "run_adapter",
                "ticker": "NVDA",
                "workflow_node": node.value,
                "created_at": "2026-06-28T00:00:00Z",
            },
        }
    )


def _adapter_resolution_payload(
    revised_candidate: Any,
    *,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "expectation_id": "exp_mock_core",
        "decision": "accepted",
        "decisions": [
            {
                "objection_id": "obj_adapter",
                "finding_id": None,
                "decision": "accepted",
                "resolution_note": "Adapter fixture accepted a revision.",
                "changed_paths": ["document"],
                "evidence_refs": [],
            }
        ],
        "target_finding_ids": [],
        "revised_candidate": revised_candidate,
        "evidence_requests": [],
        "unresolved_finding_ids": [],
        "unresolved_reason": None,
        "rationale": "Adapter boundary fixture.",
    }
    if extra:
        payload.update(extra)
    return payload


@pytest.mark.parametrize(
    ("case_name", "revised_candidate"),
    [
        pytest.param(
            "list_wrapped_revised_candidate",
            [Document2NodeMatrixRunner()._candidate_payload("NVDA")],
            id="FinalPayloadAdapter__list_wrapped_revised_candidate__schema_failure",
        ),
        pytest.param(
            "multi_candidate_revised_candidate",
            [
                Document2NodeMatrixRunner()._candidate_payload("NVDA"),
                Document2NodeMatrixRunner()._candidate_payload("NVDA"),
            ],
            id="FinalPayloadAdapter__multi_candidate_revised_candidate__schema_failure",
        ),
    ],
)
def test_final_payload_adapter_rejects_list_wrapped_revised_candidates(
    case_name: str,
    revised_candidate: Any,
) -> None:
    task = _adapter_task(
        schema="Document2ResolutionPlan",
        node=WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS,
    )
    adapted = adapt_document2_resolution_plan_payload(
        _adapter_resolution_payload(revised_candidate),
        task=task,
        tool_results=[],
        delegation_results=[],
    )

    assert case_name
    assert isinstance(adapted["revised_candidate"], list)
    with pytest.raises(Exception, match="revised_candidate"):
        Document2ResolutionPlan.model_validate(adapted)


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        pytest.param(
            {"changes": {"market_view.text": "partial update only"}},
            "Extra inputs",
            id="FinalPayloadAdapter__partial_patch_changes_map__schema_failure",
        ),
        pytest.param(
            {"path_map": {"market_view.text": "partial update only"}},
            "Extra inputs",
            id="FinalPayloadAdapter__partial_patch_path_map__schema_failure",
        ),
        pytest.param(
            {"proposed_patches": [{"patch_id": "patch_leak"}]},
            "extra",
            id="FinalPayloadAdapter__proposed_patches_leak__schema_failure",
        ),
    ],
)
def test_final_payload_adapter_rejects_partial_patch_and_patch_leak_shapes(
    extra: dict[str, Any],
    message: str,
) -> None:
    task = _adapter_task(
        schema="Document2ResolutionPlan",
        node=WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS,
    )
    adapted = adapt_document2_resolution_plan_payload(
        _adapter_resolution_payload(None, extra=extra),
        task=task,
        tool_results=[],
        delegation_results=[],
    )

    with pytest.raises(Exception, match=message):
        Document2ResolutionPlan.model_validate(adapted)


def test_final_payload_adapter_accepts_complete_candidate_like_revised_candidate_boundary() -> None:
    task = _adapter_task(
        schema="Document2ResolutionPlan",
        node=WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS,
    )
    runner = Document2NodeMatrixRunner()
    candidate = runner._candidate_payload("NVDA")
    adapted = adapt_document2_resolution_plan_payload(
        _adapter_resolution_payload(
            {
                "name": candidate["expectation_name"],
                "description": candidate["why_it_matters"],
                "direction": candidate["direction"],
                "market_view": candidate["market_view"],
                "realized_facts": candidate["realized_facts"],
                "realized_facts_summary": candidate["realized_facts_summary"],
                "key_variables": candidate["key_variables"],
                "event_monitoring_direction": candidate["event_monitoring_direction"],
                "evidence_refs": [
                    ref
                    for fact in candidate["realized_facts"]
                    for ref in fact["evidence_refs"]
                ],
            }
        ),
        task=task,
        tool_results=[],
        delegation_results=[],
    )

    plan = Document2ResolutionPlan.model_validate(adapted)

    assert plan.revised_candidate is not None
    assert plan.revised_candidate.expectation_id == "exp_mock_core"
    assert plan.revised_candidate.ticker == "NVDA"
    assert plan.revised_candidate.realized_facts
    assert plan.revised_candidate.key_variables


def test_final_payload_adapter_rejects_partial_candidate_like_revised_candidate_boundary() -> None:
    task = _adapter_task(
        schema="Document2ResolutionPlan",
        node=WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS,
    )
    evidence = Document2NodeMatrixRunner().factory._evidence(
        EvidenceSourceType.EXTERNAL_REPORT
    )
    adapted = adapt_document2_resolution_plan_payload(
        _adapter_resolution_payload(
            {
                "name": "Adapter candidate-like expectation",
                "description": "bullish adapter thesis with enough fixture detail",
                "realized_facts": ["adapter realized fact"],
                "key_variables": ["adapter variable"],
                "positive_events": ["adapter positive trigger"],
                "negative_events": ["adapter negative trigger"],
                "evidence_refs": [evidence.model_dump(mode="json")],
            }
        ),
        task=task,
        tool_results=[],
        delegation_results=[],
    )

    with pytest.raises(Exception, match="revised_candidate"):
        Document2ResolutionPlan.model_validate(adapted)


def test_final_payload_adapter_rejects_partial_candidate_like_detail_payload_boundary() -> None:
    shell = Document2NodeMatrixRunner().factory._expectation_shell("NVDA")
    task = _adapter_task(
        schema="ExpectationDetailCandidateResult",
        node=WorkflowNode.GENERATE_EXPECTATION_DETAILS,
        input_context={"expectation_shell": shell.model_dump(mode="json")},
    )
    evidence = Document2NodeMatrixRunner().factory._evidence(
        EvidenceSourceType.EXTERNAL_REPORT
    )
    adapted = adapt_expectation_detail_candidate_payload(
        {
            "expectation_unit": {
                "realized_facts": ["adapter realized fact"],
                "key_variables": ["adapter variable"],
                "positive_events": ["adapter positive trigger"],
                "negative_events": ["adapter negative trigger"],
                "evidence_refs": [evidence.model_dump(mode="json")],
            },
            "rationale": "Adapter detail candidate-like payload.",
        },
        task=task,
        tool_results=[],
        delegation_results=[],
    )

    assert adapted["candidate"]["expectation_id"] == shell.expectation_id
    assert adapted["candidate"]["expectation_name"] == shell.expectation_name
    assert adapted["candidate"]["direction"] == shell.direction
    with pytest.raises(Exception, match="candidate"):
        ExpectationDetailCandidateResult.model_validate(adapted)
