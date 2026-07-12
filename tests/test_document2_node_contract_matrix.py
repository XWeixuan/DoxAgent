from __future__ import annotations

import pytest

pytest.skip("retired EvidenceRef contract matrix", allow_module_level=True)

from typing import Any


from doxagent.agents import default_agent_registry
from doxagent.models import (
    AgentName,
    AgentResult,
    AgentTask,
    BlackboardPatch,
    BlackboardTarget,
    DocumentType,
    EvidenceRef,
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
    Document2FieldRepairResult,
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
from doxagent.workflows.document2.transaction import (
    DOCUMENT2_TRANSACTION_AUDITS_KEY,
    document2_revision_from_field_repair_result,
)
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
        if self.construction_review_case in {
            "partial_finding_evidence_ref",
            "complete_finding_evidence_ref",
        }:
            structured = self._structured_payload(result)
            evidence_ref = self.factory._evidence(
                EvidenceSourceType.DOXATLAS_SOURCE
            ).model_dump(mode="json")
            if self.construction_review_case == "partial_finding_evidence_ref":
                evidence_ref.pop("confidence", None)
                evidence_ref.pop("citation_scope", None)
            structured["verdict"] = "needs_revision"
            structured["revision_required"] = True
            structured["findings"] = [
                {
                    "field_path": "market_view",
                    "status": "needs_more_evidence",
                    "rationale": "Fixture A1 construction finding cites DoxAtlas material.",
                    "recommended_statement": "Fixture A1 construction formulation.",
                    "evidence_refs": [evidence_ref],
                }
            ]
            return self._with_structured(result, structured)
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
            self.review_case
            in {
                "structured_blocking_finding",
                "structured_blocking_finding_without_expectation_id",
                "structured_blocking_finding_with_target_paths",
                "structured_document_level_data_gap_without_expectation_id",
                "recommended_statement_without_evidence_refs",
                "supported_recommended_statement",
                "invalid_evidence_refs_string",
                "invalid_recommended_statement_object",
                "complete_evidence_ref",
                "bad_finding_mixed",
                "reviewer_result_findings_not_list",
            }
            and task.agent_name is AgentName.C1_FUNDAMENTAL_RESEARCH
        ):
            structured = self._structured_payload(result)
            evidence = self.factory._evidence(EvidenceSourceType.EXTERNAL_REPORT)
            finding = {
                "expectation_id": "exp_mock_core",
                "field_path": "key_variables[0].current_status",
                "status": "unsupported",
                "rationale": "Fixture reviewer found unsupported current-status evidence.",
                "evidence_refs": [evidence.model_dump(mode="json")],
            }
            if self.review_case == "reviewer_result_findings_not_list":
                structured["findings"] = {
                    "field_path": "key_variables[0].current_status",
                    "status": "unsupported",
                    "rationale": "Fixture reviewer returned findings as an object.",
                    "evidence_refs": [evidence.model_dump(mode="json")],
                }
                return self._with_structured(result, structured)
            if self.review_case == "structured_blocking_finding_without_expectation_id":
                finding.pop("expectation_id", None)
                finding["rationale"] = (
                    "Fixture reviewer omitted expectation_id but the finding must be "
                    "routed to pending expectation candidates, not unknown_expectation."
                )
            elif self.review_case == "structured_blocking_finding_with_target_paths":
                finding["field_path"] = "document"
                finding["target_paths"] = [
                    "realized_facts",
                    "event_monitoring_direction",
                ]
                finding["rationale"] = (
                    "Fixture reviewer found a cross-field mismatch between facts and "
                    "monitoring direction."
                )
            elif self.review_case == "structured_document_level_data_gap_without_expectation_id":
                finding.pop("expectation_id", None)
                finding["field_path"] = "document"
                finding["rationale"] = (
                    "Fixture document-level data gap affects both pending candidates."
                )
            elif self.review_case == "recommended_statement_without_evidence_refs":
                finding["status"] = "needs_more_evidence"
                finding["rationale"] = (
                    "Fixture reviewer supplemented the field without evidence refs."
                )
                finding["recommended_statement"] = (
                    "Corrected current-state formulation from the C1 review perspective."
                )
                finding["evidence_refs"] = []
            elif self.review_case == "supported_recommended_statement":
                finding["status"] = "supported"
                finding["field_path"] = "event_monitoring_direction"
                finding["target_paths"] = ["event_monitoring_direction"]
                finding["rationale"] = (
                    "Fixture reviewer supplied a supported supplemental formulation."
                )
                finding["recommended_statement"] = (
                    "Supplemental industry framing for event monitoring is already aligned."
                )
                finding["evidence_refs"] = []
            elif self.review_case == "invalid_evidence_refs_string":
                finding["evidence_refs"] = ["evidence_id_only"]
            elif self.review_case == "invalid_recommended_statement_object":
                finding["recommended_statement"] = {"text": "not a plain string"}
            elif self.review_case == "bad_finding_mixed":
                bad_finding = {
                    "status": "unsupported",
                    "rationale": "Fixture bad finding is missing field_path.",
                    "evidence_refs": [evidence.model_dump(mode="json")],
                }
                structured["findings"] = [bad_finding, finding]
                return self._with_structured(result, structured)
            structured["findings"] = [finding]
            return self._with_structured(result, structured)
        if (
            self.review_case == "a1_recommended_statement_without_evidence_refs"
            and task.agent_name is AgentName.A1_DOXATLAS_AUDIT
        ):
            structured = self._structured_payload(result)
            structured["verdict"] = "needs_revision"
            structured["revision_required"] = True
            structured["findings"] = [
                {
                    "field_path": "market_view",
                    "status": "needs_more_evidence",
                    "rationale": (
                        "Fixture A1 supplemented DoxAtlas traceability without refs."
                    ),
                    "recommended_statement": (
                        "Corrected DoxAtlas-traceable market-view formulation."
                    ),
                    "evidence_refs": [],
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
        if (
            self.review_case == "reviewer_changes_leak"
            and task.agent_name is AgentName.C1_FUNDAMENTAL_RESEARCH
        ):
            structured = self._structured_payload(result)
            structured["changes"] = {"market_view.text": "forbidden partial update"}
            return self._with_structured(result, structured)
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
        elif self.resolver_case == "rejected_without_changed_paths_evidence_refs":
            decision = "rejected"
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


def _working_memory_payloads(
    workflow: BlackboardInitializationWorkflow,
    run_id: str,
    content_type: str,
) -> list[dict[str, Any]]:
    run = workflow.blackboard.get_run(run_id)
    return [
        entry.payload
        for entry in run.working_memory
        if entry.content_type == content_type
    ]


def _reviewer_acceptance_warning_payloads(
    workflow: BlackboardInitializationWorkflow,
    run_id: str,
) -> list[dict[str, Any]]:
    return _working_memory_payloads(
        workflow,
        run_id,
        "document2_reviewer_acceptance_warning",
    )


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
    workflow, result = _run_matrix(
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
            "partial_finding_evidence_ref",
            WorkflowRunStatus.RUNNING,
            "",
            id="ReviewExpectationConstruction__partial_finding_evidence_ref__warning",
        ),
        pytest.param(
            "complete_finding_evidence_ref",
            WorkflowRunStatus.RUNNING,
            "",
            id="ReviewExpectationConstruction__complete_finding_evidence_ref__retained",
        ),
        pytest.param(
            "reviewer_patch_leak",
            WorkflowRunStatus.BLOCKED,
            "no usable A1 reviewer output",
            id="ReviewExpectationConstruction__contract_patch_leak__schema_failure",
        ),
    ],
)
def test_review_expectation_construction_node_matrix(
    case: str | None,
    expected_status: WorkflowRunStatus,
    message: str,
) -> None:
    workflow, result = _run_matrix(
        stop_after=WorkflowNode.REVIEW_EXPECTATION_CONSTRUCTION,
        runner=Document2NodeMatrixRunner(construction_review_case=case),
    )

    if expected_status is WorkflowRunStatus.RUNNING:
        _assert_running_to(result, WorkflowNode.RESOLVE_EXPECTATION_CONSTRUCTION)
        if case == "market_view":
            assert result.summary.unresolved_objection_count == 1
        if case == "partial_finding_evidence_ref":
            warnings = _reviewer_acceptance_warning_payloads(
                workflow,
                result.checkpoint.run_id,
            )
            assert any(
                warning["issue"] == "invalid_evidence_refs_removed"
                and warning["invalid_evidence_ref_count"] == 1
                and {"confidence", "citation_scope"}.issubset(
                    set(warning["missing_fields"])
                )
                for payload in warnings
                for warning in payload["warnings"]
            )
            memory_payloads = _working_memory_payloads(
                workflow,
                result.checkpoint.run_id,
                "a1_expectation_construction_review",
            )
            findings = memory_payloads[-1]["payload"]["structured"]["findings"]
            assert findings[0]["evidence_refs"] == []
        if case == "complete_finding_evidence_ref":
            memory_payloads = _working_memory_payloads(
                workflow,
                result.checkpoint.run_id,
                "a1_expectation_construction_review",
            )
            findings = memory_payloads[-1]["payload"]["structured"]["findings"]
            assert findings[0]["evidence_refs"][0]["confidence"] is not None
            assert findings[0]["evidence_refs"][0]["citation_scope"]
        if case == "reviewer_patch_leak":
            assert result.summary.unresolved_objection_count == 0
    else:
        _assert_blocked(result, message)
        if case == "reviewer_patch_leak":
            warnings = _reviewer_acceptance_warning_payloads(
                workflow,
                result.checkpoint.run_id,
            )
            assert any(
                warning["issue"] == "reviewer_proposed_patches_rejected"
                for payload in warnings
                for warning in payload["warnings"]
            )


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
    workflow, result = _run_matrix(
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
            None,
            "structured_blocking_finding_without_expectation_id",
            WorkflowRunStatus.RUNNING,
            "",
            id=(
                "ReviewExpectationFields__reviewer_omits_expectation_id__"
                "attributed_not_unknown"
            ),
        ),
        pytest.param(
            None,
            "structured_blocking_finding_with_target_paths",
            WorkflowRunStatus.RUNNING,
            "",
            id="ReviewExpectationFields__reviewer_target_paths__preserved_for_cross_field",
        ),
        pytest.param(
            None,
            "structured_document_level_data_gap_without_expectation_id",
            WorkflowRunStatus.RUNNING,
            "",
            id="ReviewExpectationFields__document_level_data_gap__fanout_to_candidates",
        ),
        pytest.param(
            None,
            "recommended_statement_without_evidence_refs",
            WorkflowRunStatus.RUNNING,
            "",
            id="ReviewExpectationFields__recommended_statement_without_evidence_refs__accepted",
        ),
        pytest.param(
            None,
            "a1_recommended_statement_without_evidence_refs",
            WorkflowRunStatus.RUNNING,
            "",
            id="ReviewExpectationFields__a1_recommended_statement_without_evidence_refs__accepted",
        ),
        pytest.param(
            None,
            "supported_recommended_statement",
            WorkflowRunStatus.RUNNING,
            "",
            id="ReviewExpectationFields__supported_recommended_statement__non_blocking",
        ),
        pytest.param(
            None,
            "invalid_evidence_refs_string",
            WorkflowRunStatus.RUNNING,
            "",
            id="ReviewExpectationFields__invalid_evidence_refs_string__warning",
        ),
        pytest.param(
            None,
            "invalid_recommended_statement_object",
            WorkflowRunStatus.RUNNING,
            "",
            id="ReviewExpectationFields__invalid_recommended_statement_object__warning",
        ),
        pytest.param(
            None,
            "complete_evidence_ref",
            WorkflowRunStatus.RUNNING,
            "",
            id="ReviewExpectationFields__complete_evidence_ref__retained",
        ),
        pytest.param(
            None,
            "bad_finding_mixed",
            WorkflowRunStatus.RUNNING,
            "",
            id="ReviewExpectationFields__bad_finding_discarded_good_finding_kept",
        ),
        pytest.param(
            None,
            "reviewer_result_findings_not_list",
            WorkflowRunStatus.RUNNING,
            "",
            id="ReviewExpectationFields__one_bad_reviewer_skipped",
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
            id="ReviewExpectationFields__numeric_sanity__disabled",
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
            WorkflowRunStatus.RUNNING,
            "",
            id="ReviewExpectationFields__reviewer_proposed_patches__skipped_with_audit",
        ),
        pytest.param(
            None,
            "reviewer_changes_leak",
            WorkflowRunStatus.RUNNING,
            "",
            id="ReviewExpectationFields__reviewer_changes_leak__skipped_with_audit",
        ),
    ],
)
def test_review_expectation_fields_node_matrix(
    detail_case: str | None,
    review_case: str | None,
    expected_status: WorkflowRunStatus,
    message: str,
) -> None:
    workflow, result = _run_matrix(
        stop_after=WorkflowNode.REVIEW_EXPECTATION_FIELDS,
        runner=Document2NodeMatrixRunner(
            detail_case=detail_case,
            review_case=review_case,
        ),
    )

    if expected_status is WorkflowRunStatus.RUNNING:
        _assert_running_to(result, WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS)
        findings = result.checkpoint.metadata.get(DOCUMENT2_REVIEW_FINDINGS_KEY, [])
        review_finding_cases = {
            "structured_blocking_finding",
            "structured_blocking_finding_without_expectation_id",
            "structured_blocking_finding_with_target_paths",
            "structured_document_level_data_gap_without_expectation_id",
            "recommended_statement_without_evidence_refs",
            "a1_recommended_statement_without_evidence_refs",
            "invalid_evidence_refs_string",
            "invalid_recommended_statement_object",
            "complete_evidence_ref",
            "bad_finding_mixed",
        }
        nonblocking_review_finding_cases = {"supported_recommended_statement"}
        if (
            detail_case is not None
            and detail_case != "numeric_market_view"
        ) or review_case in review_finding_cases:
            assert findings
            assert result.summary.unresolved_objection_count >= 1
            blocking = [
                finding
                for finding in findings
                if finding["blocks_promotion"] is True
            ]
            assert blocking
            assert all(finding["source_objection_id"] for finding in blocking)
            assert all(
                finding["expectation_id"] != "unknown_expectation"
                for finding in blocking
            )
            if review_case == "structured_blocking_finding_with_target_paths":
                assert any(
                    finding["target_paths"]
                    == ["document", "realized_facts", "event_monitoring_direction"]
                    for finding in blocking
                )
            if review_case in {
                "structured_blocking_finding_without_expectation_id",
                "structured_document_level_data_gap_without_expectation_id",
            }:
                expectation_ids = {finding["expectation_id"] for finding in blocking}
                assert {"exp_mock_core", "exp_mock_risk"}.issubset(expectation_ids)
            if review_case == "recommended_statement_without_evidence_refs":
                assert any(
                    finding.get("recommended_statement")
                    == "Corrected current-state formulation from the C1 review perspective."
                    and finding["supplemental_evidence_refs"] == []
                    for finding in blocking
                )
            if review_case == "invalid_evidence_refs_string":
                warnings = _reviewer_acceptance_warning_payloads(
                    workflow,
                    result.checkpoint.run_id,
                )
                assert any(
                    warning["issue"] == "invalid_evidence_refs_removed"
                    and warning["invalid_evidence_ref_count"] == 1
                    for payload in warnings
                    for warning in payload["warnings"]
                )
                assert any(
                    finding["reason"]
                    == "Fixture reviewer found unsupported current-status evidence."
                    and finding["supplemental_evidence_refs"] == []
                    for finding in blocking
                )
            if review_case == "invalid_recommended_statement_object":
                warnings = _reviewer_acceptance_warning_payloads(
                    workflow,
                    result.checkpoint.run_id,
                )
                assert any(
                    warning["issue"] == "invalid_recommended_statement_removed"
                    for payload in warnings
                    for warning in payload["warnings"]
                )
                assert any(
                    finding["reason"]
                    == "Fixture reviewer found unsupported current-status evidence."
                    and finding.get("recommended_statement") is None
                    for finding in blocking
                )
            if review_case == "complete_evidence_ref":
                assert any(
                    finding["reason"]
                    == "Fixture reviewer found unsupported current-status evidence."
                    and finding["supplemental_evidence_refs"]
                    and finding["supplemental_evidence_refs"][0]["confidence"] is not None
                    and finding["supplemental_evidence_refs"][0]["citation_scope"]
                    for finding in blocking
                )
            if review_case == "bad_finding_mixed":
                warnings = _reviewer_acceptance_warning_payloads(
                    workflow,
                    result.checkpoint.run_id,
                )
                assert any(
                    warning["issue"] == "reviewer_finding_discarded"
                    and warning["reason"] == "missing_field_path"
                    for payload in warnings
                    for warning in payload["warnings"]
                )
                assert any(
                    finding["reason"]
                    == "Fixture reviewer found unsupported current-status evidence."
                    for finding in blocking
                )
            if review_case == "a1_recommended_statement_without_evidence_refs":
                assert any(
                    finding["reviewer_agent"] == AgentName.A1_DOXATLAS_AUDIT.value
                    and finding.get("recommended_statement")
                    == "Corrected DoxAtlas-traceable market-view formulation."
                    and finding["supplemental_evidence_refs"] == []
                    for finding in blocking
                )
        elif review_case in nonblocking_review_finding_cases:
            assert findings
            assert result.summary.unresolved_objection_count == 0
            assert all(finding["blocks_promotion"] is False for finding in findings)
            assert any(
                finding.get("recommended_statement")
                == "Supplemental industry framing for event monitoring is already aligned."
                for finding in findings
            )
        elif detail_case == "numeric_market_view":
            assert not any(
                "numeric_sanity" in " ".join(finding.get("supplemental_context", []))
                for finding in findings
            )
            run = workflow.blackboard.get_run(result.checkpoint.run_id)
            assert not any(
                objection.objection_id.startswith("obj_numeric_sanity_")
                for objection in run.objections
            )
        else:
            if review_case in {
                "reviewer_result_findings_not_list",
                "reviewer_patch_leak",
                "reviewer_changes_leak",
            }:
                warnings = _reviewer_acceptance_warning_payloads(
                    workflow,
                    result.checkpoint.run_id,
                )
                expected_issue = {
                    "reviewer_result_findings_not_list": "reviewer_findings_not_list",
                    "reviewer_patch_leak": "reviewer_proposed_patches_rejected",
                    "reviewer_changes_leak": "reviewer_patch_like_fields_rejected",
                }[review_case]
                assert any(
                    warning["issue"] == expected_issue
                    for payload in warnings
                    for warning in payload["warnings"]
                )
                review_state = result.checkpoint.metadata.get(
                    "document2_review_state",
                    {},
                )
                assert review_state.get("skipped_reviewer_count") == 1
                assert findings == []
                return
            assert findings == []
    else:
        _assert_blocked(result, message)


def test_review_expectation_fields_context_allows_optional_single_batch_tools() -> None:
    runner = Document2NodeMatrixRunner()
    _workflow, result = _run_matrix(
        stop_after=WorkflowNode.REVIEW_EXPECTATION_FIELDS,
        runner=runner,
    )
    _assert_running_to(result, WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS)

    review_tasks = [
        task
        for task in runner.tasks
        if task.run_metadata.workflow_node == WorkflowNode.REVIEW_EXPECTATION_FIELDS.value
    ]
    assert {task.agent_name for task in review_tasks} == {
        AgentName.A1_DOXATLAS_AUDIT,
        AgentName.C1_FUNDAMENTAL_RESEARCH,
        AgentName.C3_INDUSTRY_RESEARCH,
        AgentName.O4_MARKET_TRACE,
    }
    for task in review_tasks:
        context = task.input_context
        budget = context["react_runtime_budget"]
        assert context["required_tool_names"] == []
        assert budget["max_steps"] == 3
        assert budget["max_tool_call_batches"] == 1
        assert "model_request_timeout_seconds" not in budget
        assert all(item["required"] is False for item in context["tool_requirements"])
        assert "content supplementation and calibration review" in context[
            "review_instruction"
        ]

    a1_task = next(
        task
        for task in review_tasks
        if task.agent_name is AgentName.A1_DOXATLAS_AUDIT
    )
    a1_tools = set(a1_task.permissions.allowed_tools)
    assert "tavily.search" not in a1_tools
    assert all(not tool.startswith("doxa_run_") for tool in a1_tools)
    assert {
        "doxa_query_analysis",
        "doxa_get_analysis",
        "doxa_query_propositions",
        "doxa_get_event_source",
        "doxa_get_media_result",
        "doxa_get_media_result_detail",
        "doxa_get_social_result",
        "doxa_get_social_result_detail",
        "doxa_get_ignored_propositions",
    }.issubset(a1_tools)
    assert "Do not call tools" not in a1_task.input_context["review_instruction"]


def test_canonical_evidence_ref_remains_strict_for_stable_contracts() -> None:
    partial_ref = {
        "evidence_id": "evidence_partial",
        "source_type": EvidenceSourceType.DOXATLAS_SOURCE.value,
        "source_id": "source_partial",
        "title": "Partial DoxAtlas source",
        "summary": "Missing confidence and citation scope.",
    }

    with pytest.raises(Exception, match="confidence"):
        EvidenceRef.model_validate(partial_ref)

    with pytest.raises(Exception, match="confidence"):
        BlackboardPatch.model_validate(
            {
                "patch_id": "patch_partial_ref",
                "target": {
                    "document_type": DocumentType.EXPECTATION_UNIT.value,
                    "field_path": "market_view",
                    "ticker": "NVDA",
                    "expectation_id": "exp_mock_core",
                },
                "operation": PatchOperation.CREATE.value,
                "before": None,
                "after": {"document_type": DocumentType.EXPECTATION_UNIT.value},
                "rationale": "Canonical blackboard patch must reject partial EvidenceRef.",
                "evidence_refs": [partial_ref],
                "author_agent": AgentName.O1_EXPECTATION_OWNER.value,
                "validation_status": ValidationStatus.PENDING.value,
            }
        )


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
            "structured_blocking_finding_without_expectation_id",
            None,
            WorkflowRunStatus.RUNNING,
            "",
            id=(
                "ResolveObjectionsAndDelegations__reviewer_omits_expectation_id__"
                "routed_to_candidates"
            ),
        ),
        pytest.param(
            None,
            "structured_document_level_data_gap_without_expectation_id",
            None,
            WorkflowRunStatus.RUNNING,
            "",
            id=(
                "ResolveObjectionsAndDelegations__document_level_data_gap__"
                "fanout_tasks_resolved"
            ),
        ),
        pytest.param(
            None,
            "structured_blocking_finding",
            "resolved_without_changed_paths_evidence_refs",
            WorkflowRunStatus.RUNNING,
            "",
            id="ResolveObjectionsAndDelegations__resolved_without_changed_paths_evidence_refs__audited_not_blocking",
        ),
        pytest.param(
            None,
            "structured_blocking_finding",
            "rejected_without_changed_paths_evidence_refs",
            WorkflowRunStatus.RUNNING,
            "",
            id="ResolveObjectionsAndDelegations__rejected_without_changed_paths_evidence_refs__audited_not_blocking",
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
            "placeholder_text",
            None,
            "non_numeric_deterministic_blocker_still_fails",
            WorkflowRunStatus.RUNNING,
            "",
            id="ResolveObjectionsAndDelegations__non_numeric_deterministic_blocker_disabled__no_retained_blocker",
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


def test_field_repair_noop_rejected_decision_is_audited_not_blocking() -> None:
    _workflow, result = _run_matrix(
        stop_after=WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS,
        runner=Document2NodeMatrixRunner(
            review_case="structured_blocking_finding",
            resolver_case="rejected_without_changed_paths_evidence_refs",
        ),
    )

    _assert_running_to(result, WorkflowNode.PROMOTE_EXPECTATION_TO_BELIEF_STATE)
    audits = result.checkpoint.metadata[DOCUMENT2_TRANSACTION_AUDITS_KEY]
    assert audits
    assert any(
        "without changed_paths or evidence_refs" in note
        for audit in audits
        for note in audit["notes"]
    )


def test_numeric_sanity_disabled_does_not_enter_resolver_repair_tasks() -> None:
    runner = Document2NodeMatrixRunner(detail_case="numeric_market_view")
    workflow, result = _run_matrix(
        stop_after=WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS,
        runner=runner,
    )

    _assert_running_to(result, WorkflowNode.PROMOTE_EXPECTATION_TO_BELIEF_STATE)
    resolver_tasks = [
        task
        for task in runner.tasks
        if task.run_metadata.workflow_node
        == WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS.value
        and task.agent_name is AgentName.O1_EXPECTATION_OWNER
    ]
    assert resolver_tasks == []
    run = workflow.blackboard.get_run(result.checkpoint.run_id)
    assert not any(
        objection.objection_id.startswith("obj_numeric_sanity_")
        for objection in run.objections
    )


def test_legacy_numeric_sanity_objection_is_not_actionable_in_resolver() -> None:
    runner = Document2NodeMatrixRunner()
    workflow, review_result = _run_matrix(
        stop_after=WorkflowNode.REVIEW_EXPECTATION_FIELDS,
        runner=runner,
    )
    _assert_running_to(review_result, WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS)
    checkpoint = review_result.checkpoint
    patch = checkpoint.pending_patches[0]
    expectation_id = str(patch.target.expectation_id)
    workflow.blackboard.create_objection(
        checkpoint.run_id,
        Objection(
            objection_id=f"obj_numeric_sanity_{expectation_id}_market_data",
            source_agent=AgentName.SYSTEM,
            target=BlackboardTarget(
                document_type=DocumentType.EXPECTATION_UNIT,
                ticker=checkpoint.ticker,
                expectation_id=expectation_id,
                field_path="market_view",
            ),
            severity=ObjectionSeverity.BLOCKING,
            reason="Legacy numeric_sanity objection should be ignored by Document2 resolver.",
            taxonomy="numeric_sanity_market_data",
            target_path="market_view",
            status=ObjectionStatus.OPEN,
        ),
    )

    result = _resume_at(
        workflow,
        checkpoint,
        WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS,
    )

    _assert_running_to(result, WorkflowNode.PROMOTE_EXPECTATION_TO_BELIEF_STATE)
    resolver_tasks = [
        task
        for task in runner.tasks
        if task.run_metadata.workflow_node
        == WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS.value
        and task.agent_name is AgentName.O1_EXPECTATION_OWNER
    ]
    assert resolver_tasks == []


def test_resolver_o1_contexts_use_600_second_timeout() -> None:
    review_runner = Document2NodeMatrixRunner(review_case="structured_blocking_finding")
    review_workflow, review_result = _run_matrix(
        stop_after=WorkflowNode.REVIEW_EXPECTATION_FIELDS,
        runner=review_runner,
    )
    _assert_running_to(review_result, WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS)
    unresolved = [
        objection
        for objection in review_workflow.blackboard.get_run(
            review_result.checkpoint.run_id
        ).objections
        if objection.is_unresolved
    ]
    legacy_context = review_workflow._objection_resolution_context(
        review_result.checkpoint,
        unresolved,
    )
    assert legacy_context["react_runtime_budget"]["model_request_timeout_seconds"] == 600.0
    assert "current_numeric_sanity_violations" not in legacy_context

    resolver_runner = Document2NodeMatrixRunner(review_case="structured_blocking_finding")
    _workflow, resolver_result = _run_matrix(
        stop_after=WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS,
        runner=resolver_runner,
    )
    _assert_running_to(resolver_result, WorkflowNode.PROMOTE_EXPECTATION_TO_BELIEF_STATE)
    resolver_tasks = [
        task
        for task in resolver_runner.tasks
        if task.run_metadata.workflow_node
        == WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS.value
        and task.agent_name is AgentName.O1_EXPECTATION_OWNER
    ]
    assert resolver_tasks
    assert all(
        task.input_context["react_runtime_budget"]["model_request_timeout_seconds"] == 600.0
        for task in resolver_tasks
    )


def _field_repair_contract_payload(
    *,
    field_family: str = "realized_facts",
    decision: str = "deferred",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "task_id": "d2repair_contract_matrix",
        "expectation_id": "exp_mock_core",
        "field_family": field_family,
        "decision": decision,
        "decisions": [],
        "target_finding_ids": [],
        "realized_facts": None,
        "key_variables": None,
        "event_monitoring_direction": None,
        "market_view": None,
        "revised_candidate": None,
        "evidence_requests": [],
        "unresolved_finding_ids": [],
        "unresolved_reason": "Fixture deferred repair needs more evidence."
        if decision == "deferred"
        else None,
        "rationale": "Fixture Document2 field repair contract payload.",
    }
    if extra:
        payload.update(extra)
    return payload


def _field_repair_evidence_ref_payload() -> dict[str, Any]:
    runner = Document2NodeMatrixRunner()
    return runner.factory._evidence(EvidenceSourceType.AGENT_OUTPUT).model_dump(
        mode="json"
    )


def _field_repair_candidate_patch(candidate: dict[str, Any]) -> BlackboardPatch:
    runner = Document2NodeMatrixRunner()
    return BlackboardPatch(
        patch_id="patch_field_repair_contract_matrix",
        target=BlackboardTarget(
            document_type=DocumentType.EXPECTATION_UNIT,
            ticker=str(candidate.get("ticker") or "NVDA"),
            expectation_id=str(candidate.get("expectation_id") or "exp_mock_core"),
            field_path="document",
        ),
        operation=PatchOperation.UPDATE,
        before=None,
        after=candidate,
        rationale="Fixture patch for field repair transaction identity validation.",
        evidence_refs=[runner.factory._evidence(EvidenceSourceType.AGENT_OUTPUT)],
        author_agent=AgentName.SYSTEM,
        validation_status=ValidationStatus.PENDING,
    )


def test_document2_field_repair_contract_rejects_object_evidence_requests() -> None:
    payload = _field_repair_contract_payload(
        extra={
            "evidence_requests": [
                {
                    "question": "Need price reaction source.",
                    "target_field": "realized_facts",
                    "reason": "Structured request objects are not allowed.",
                }
            ]
        }
    )

    with pytest.raises(Exception, match="evidence_requests"):
        Document2FieldRepairResult.model_validate(payload)


def test_document2_field_repair_contract_accepts_plain_string_evidence_requests() -> None:
    result = Document2FieldRepairResult.model_validate(
        _field_repair_contract_payload(
            extra={
                "evidence_requests": [
                    "Need primary-source evidence for the observed price reaction."
                ]
            }
        )
    )

    assert result.evidence_requests == [
        "Need primary-source evidence for the observed price reaction."
    ]


@pytest.mark.parametrize(
    "field_name",
    [
        pytest.param(
            "target_finding_ids",
            id="Document2FieldRepairResult__target_finding_ids_object__schema_failure",
        ),
        pytest.param(
            "unresolved_finding_ids",
            id="Document2FieldRepairResult__unresolved_finding_ids_object__schema_failure",
        ),
    ],
)
def test_document2_field_repair_contract_rejects_object_id_lists(
    field_name: str,
) -> None:
    payload = _field_repair_contract_payload(
        extra={field_name: [{"finding_id": "finding_contract_matrix"}]}
    )

    with pytest.raises(Exception, match=field_name):
        Document2FieldRepairResult.model_validate(payload)


def test_document2_field_repair_contract_rejects_string_evidence_ref_ids() -> None:
    payload = _field_repair_contract_payload(
        decision="resolved",
        extra={
            "decisions": [
                {
                    "finding_id": "finding_contract_matrix",
                    "decision": "resolved",
                    "resolution_note": "Evidence was referenced by id only.",
                    "changed_paths": ["document.market_view"],
                    "evidence_refs": ["evidence_contract_id"],
                }
            ]
        },
    )

    with pytest.raises(Exception, match="evidence_refs"):
        Document2FieldRepairResult.model_validate(payload)


def test_document2_field_repair_contract_accepts_full_evidence_ref_objects() -> None:
    result = Document2FieldRepairResult.model_validate(
        _field_repair_contract_payload(
            decision="resolved",
            extra={
                "decisions": [
                    {
                        "finding_id": "finding_contract_matrix",
                        "decision": "resolved",
                        "resolution_note": "EvidenceRef object is complete.",
                        "changed_paths": ["document.market_view"],
                        "evidence_refs": [_field_repair_evidence_ref_payload()],
                    }
                ]
            },
        )
    )

    assert result.decisions[0].evidence_refs[0].evidence_id


def test_document2_field_repair_contract_market_evidence_accepts_market_view() -> None:
    candidate = Document2NodeMatrixRunner()._candidate_payload("NVDA")

    result = Document2FieldRepairResult.model_validate(
        _field_repair_contract_payload(
            field_family="market_evidence",
            decision="accepted",
            extra={"market_view": candidate["market_view"]},
        )
    )

    assert result.field_family == "market_evidence"
    assert result.market_view is not None


def test_field_repair_contract_rejects_top_level_market_evidence() -> None:
    candidate = Document2NodeMatrixRunner()._candidate_payload("NVDA")
    payload = _field_repair_contract_payload(
        field_family="market_evidence",
        decision="accepted",
        extra={"market_evidence": candidate["market_view"]},
    )

    with pytest.raises(Exception, match="market_evidence"):
        Document2FieldRepairResult.model_validate(payload)


@pytest.mark.parametrize(
    "decision",
    [
        pytest.param(
            "accepted",
            id="Document2FieldRepairResult__single_field_accepted_without_update__schema_failure",
        ),
        pytest.param(
            "partially_accepted",
            id="Document2FieldRepairResult__single_field_partially_accepted_without_update__schema_failure",
        ),
    ],
)
def test_document2_field_repair_contract_single_field_acceptance_requires_typed_update(
    decision: str,
) -> None:
    with pytest.raises(Exception, match="single-field accepted repair requires"):
        Document2FieldRepairResult.model_validate(
            _field_repair_contract_payload(
                field_family="market_view",
                decision=decision,
            )
        )


def test_document2_field_repair_contract_deferred_without_typed_update_is_valid() -> None:
    result = Document2FieldRepairResult.model_validate(
        _field_repair_contract_payload(
            field_family="market_view",
            decision="deferred",
            extra={
                "unresolved_reason": "Need primary-source market evidence before editing.",
                "evidence_requests": ["Need primary-source market evidence before editing."],
            },
        )
    )

    assert result.market_view is None
    assert result.revised_candidate is None
    assert result.unresolved_reason


def test_document2_field_repair_contract_single_field_rejects_revised_candidate() -> None:
    candidate = Document2NodeMatrixRunner()._candidate_payload("NVDA")
    payload = _field_repair_contract_payload(
        field_family="market_view",
        decision="accepted",
        extra={
            "market_view": candidate["market_view"],
            "revised_candidate": candidate,
        },
    )

    with pytest.raises(Exception, match="single-field repair must not return revised_candidate"):
        Document2FieldRepairResult.model_validate(payload)


def test_field_repair_contract_cross_field_acceptance_requires_revised_candidate() -> None:
    with pytest.raises(Exception, match="cross_field accepted repair requires revised_candidate"):
        Document2FieldRepairResult.model_validate(
            _field_repair_contract_payload(
                field_family="cross_field",
                decision="accepted",
            )
        )


@pytest.mark.parametrize(
    ("field_name", "replacement"),
    [
        pytest.param(
            "expectation_id",
            "exp_changed_core",
            id="Document2FieldRepairResult__cross_field_changes_expectation_id__transaction_rejected",
        ),
        pytest.param(
            "expectation_name",
            "Changed expectation name",
            id="Document2FieldRepairResult__cross_field_changes_expectation_name__transaction_rejected",
        ),
        pytest.param(
            "direction",
            "bearish",
            id="Document2FieldRepairResult__cross_field_changes_direction__transaction_rejected",
        ),
    ],
)
def test_document2_field_repair_transaction_rejects_cross_field_identity_changes(
    field_name: str,
    replacement: str,
) -> None:
    candidate = Document2NodeMatrixRunner()._candidate_payload("NVDA")
    revised_candidate = dict(candidate)
    revised_candidate[field_name] = replacement
    result = Document2FieldRepairResult.model_validate(
        _field_repair_contract_payload(
            field_family="cross_field",
            decision="accepted",
            extra={"revised_candidate": revised_candidate},
        )
    )

    with pytest.raises(Exception, match="immutable identity fields"):
        document2_revision_from_field_repair_result(
            result,
            before_patch=_field_repair_candidate_patch(candidate),
        )


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


def test_numeric_sanity_disabled_does_not_block_promotion_or_reopen_revalidation() -> None:
    workflow, checkpoint = _promotion_checkpoint()
    patch = checkpoint.pending_patches[0]
    expectation_id = str(patch.target.expectation_id)
    numeric_objection = Objection(
        objection_id=f"obj_numeric_sanity_{expectation_id}_market_data",
        source_agent=AgentName.SYSTEM,
        target=BlackboardTarget(
            document_type=DocumentType.EXPECTATION_UNIT,
            ticker=checkpoint.ticker,
            expectation_id=expectation_id,
            field_path="market_view",
        ),
        severity=ObjectionSeverity.BLOCKING,
        reason="Deterministic numeric sanity review is disabled.",
        taxonomy="numeric_sanity_market_data",
        target_path="market_view",
        status=ObjectionStatus.OPEN,
    )
    workflow.blackboard.create_objection(checkpoint.run_id, numeric_objection)
    numeric_finding = Document2ReviewFinding(
        reviewer_agent=AgentName.SYSTEM,
        expectation_id=expectation_id,
        target_path="market_view",
        target_paths=["market_view"],
        severity="blocking",
        reason="Deterministic numeric sanity review is disabled.",
        supplemental_context=["finding_source: deterministic_numeric_sanity"],
        source_objection_id=numeric_objection.objection_id,
    )
    checkpoint.metadata[DOCUMENT2_REVIEW_FINDINGS_KEY] = [
        *checkpoint.metadata.get(DOCUMENT2_REVIEW_FINDINGS_KEY, []),
        numeric_finding.model_dump(mode="json"),
    ]

    revalidation = workflow._revalidate_document2_deterministic_findings_for_patch(
        checkpoint,
        patch,
    )
    result = _resume_at(
        workflow,
        checkpoint,
        WorkflowNode.PROMOTE_EXPECTATION_TO_BELIEF_STATE,
    )

    assert not any(
        "numeric_sanity" in " ".join(finding.supplemental_context)
        for finding in revalidation
    )
    _assert_running_to(result, WorkflowNode.GENERATE_GLOBAL_NARRATIVE_REPORT)


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
]


@pytest.mark.parametrize(("detail_case", "target_path"), DETERMINISTIC_REVALIDATION_CASES)
def test_mini_flow_generate_details_to_review_deterministic_revalidation_matrix(
    detail_case: str,
    target_path: str,
) -> None:
    workflow, result = _run_matrix(
        stop_after=WorkflowNode.REVIEW_EXPECTATION_FIELDS,
        runner=Document2NodeMatrixRunner(detail_case=detail_case),
    )

    _assert_running_to(result, WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS)
    findings = result.checkpoint.metadata[DOCUMENT2_REVIEW_FINDINGS_KEY]
    run = workflow.blackboard.get_run(result.checkpoint.run_id)

    assert not any(finding["reviewer_agent"] == "SYSTEM" for finding in findings)
    assert not any(objection.source_agent is AgentName.SYSTEM for objection in run.objections)
    assert not any(
        finding["target_path"] == target_path and finding["blocks_promotion"] is True
        for finding in findings
    )


@pytest.mark.parametrize(
    ("detail_case", "resolver_case", "expected_status", "message"),
    [
        pytest.param(
            "unknown_price_reaction",
            None,
            WorkflowRunStatus.RUNNING,
            "",
            id="MiniFlow_ReviewToResolver__unknown_price_reaction__deterministic_disabled",
        ),
        pytest.param(
            "missing_realized_fact_evidence_refs",
            None,
            WorkflowRunStatus.RUNNING,
            "",
            id="MiniFlow_ReviewToResolver__missing_realized_fact_evidence_refs__deterministic_disabled",
        ),
        pytest.param(
            "missing_key_variable_evidence_refs",
            None,
            WorkflowRunStatus.RUNNING,
            "",
            id="MiniFlow_ReviewToResolver__missing_key_variable_evidence_refs__deterministic_disabled",
        ),
        pytest.param(
            "empty_realized_facts",
            None,
            WorkflowRunStatus.RUNNING,
            "",
            id="MiniFlow_ReviewToResolver__empty_realized_facts__deterministic_disabled",
        ),
        pytest.param(
            "empty_key_variables",
            None,
            WorkflowRunStatus.RUNNING,
            "",
            id="MiniFlow_ReviewToResolver__empty_key_variables__deterministic_disabled",
        ),
        pytest.param(
            "empty_positive_events",
            None,
            WorkflowRunStatus.RUNNING,
            "",
            id="MiniFlow_ReviewToResolver__empty_positive_events__deterministic_disabled",
        ),
        pytest.param(
            "empty_negative_events",
            None,
            WorkflowRunStatus.RUNNING,
            "",
            id="MiniFlow_ReviewToResolver__empty_negative_events__deterministic_disabled",
        ),
        pytest.param(
            "generic_monitoring_trigger",
            "deferred_blocker",
            WorkflowRunStatus.RUNNING,
            "",
            id="MiniFlow_ReviewToResolver__generic_monitoring_trigger__deterministic_disabled",
        ),
        pytest.param(
            "placeholder_text",
            None,
            WorkflowRunStatus.RUNNING,
            "",
            id="MiniFlow_ReviewToResolver__placeholder_generic_text__deterministic_disabled",
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
