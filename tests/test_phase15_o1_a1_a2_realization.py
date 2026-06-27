import pytest

from doxagent.agents import AgentRunner, default_agent_registry
from doxagent.models import (
    AgentName,
    AgentResult,
    AgentTask,
    BlackboardTarget,
    DelegatedRetrievalRequest,
    DelegatedRetrievalResult,
    Delegation,
    DocumentType,
    DoxAtlasAuditResult,
    EvidenceRef,
    EvidenceSourceType,
    ExpectationConstructionResult,
    ExpectationFieldReviewResult,
    ExpectationShellConstructionResult,
    Objection,
    ObjectionSeverity,
    ObjectionStatus,
    ResearchSection,
    ResultStatus,
    TaskType,
    create_a2_retrieval_delegation,
    new_id,
)
from doxagent.tools import ToolRequest, default_tool_registry
from doxagent.workflows import (
    BlackboardInitializationWorkflow,
    InitializationMockResultFactory,
    WorkflowCheckpoint,
    WorkflowNode,
    WorkflowRunStatus,
)


def _evidence(source_type: EvidenceSourceType = EvidenceSourceType.EXTERNAL_REPORT) -> EvidenceRef:
    return EvidenceRef(
        evidence_id=new_id("evidence"),
        source_type=source_type,
        source_id=f"{source_type.value}:phase15",
        title="Phase 15 evidence",
        summary="Deterministic evidence for O1/A1/A2 realization tests.",
        confidence=0.78,
        citation_scope="phase15",
    )


def _target(ticker: str = "NVDA") -> BlackboardTarget:
    return BlackboardTarget(
        document_type=DocumentType.EXPECTATION_UNIT,
        ticker=ticker,
        expectation_id="exp_mock_core",
        field_path="document",
    )


def _structured(
    task: AgentTask,
    direct: AgentResult,
    payload: dict[str, object] | None = None,
) -> AgentResult:
    if task.required_output_schema == "ResearchSection":
        evidence = _evidence()
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
            payload={"structured": section.model_dump(mode="json")},
            evidence_refs=[evidence],
        )
    if task.required_output_schema == "ExpectationDetailCandidateResult":
        if isinstance(direct.payload, dict) and isinstance(direct.payload.get("candidate"), dict):
            return AgentResult(
                task_id=task.task_id,
                agent_name=task.agent_name,
                status=direct.status,
                payload={"runtime": "maf", "structured": direct.payload},
                evidence_refs=direct.evidence_refs,
            )
        patch = direct.proposed_patches[0]
        evidence_refs = patch.evidence_refs or direct.evidence_refs
        return AgentResult(
            task_id=task.task_id,
            agent_name=task.agent_name,
            status=direct.status,
            payload={
                "runtime": "maf",
                "structured": {
                    "candidate": patch.after,
                    "evidence_refs": [
                        evidence.model_dump(mode="json") for evidence in evidence_refs
                    ],
                    "delegations": [
                        delegation.model_dump(mode="json")
                        for delegation in direct.delegations
                    ],
                    "unknowns": [],
                    "rationale": "O1 completed a sourced detail candidate.",
                },
            },
            evidence_refs=evidence_refs,
        )
    return AgentResult(
        task_id=task.task_id,
        agent_name=task.agent_name,
        status=direct.status,
        payload={
            "runtime": "maf",
            "structured": payload
            if task.required_output_schema
            in {
                "ExpectationShellConstructionResult",
                "ExpectationConstructionResult",
                "ExpectationDetailResult",
                "DoxAtlasAuditResult",
                "DelegatedRetrievalResult",
            }
            else {
                "payload": payload or direct.payload,
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
            },
        },
    )


class RealizedO1A1A2Runner(AgentRunner):
    def __init__(self, *, a2_has_evidence: bool = True) -> None:
        self.factory = InitializationMockResultFactory(include_blockers=False)
        self.a2_has_evidence = a2_has_evidence
        self.calls: list[tuple[AgentName, TaskType, str | None]] = []
        self.tasks: list[AgentTask] = []
        self.objection_id: str | None = None

    def run(self, task: AgentTask) -> AgentResult:
        self.calls.append((task.agent_name, task.task_type, task.run_metadata.workflow_node))
        self.tasks.append(task)
        node = task.run_metadata.workflow_node
        if node == WorkflowNode.GENERATE_EXPECTATION_CONSTRUCTION.value:
            direct = self.factory(task)
            return _structured(task, direct, dict(direct.payload))
        if node == WorkflowNode.GENERATE_EXPECTATION_DETAILS.value:
            direct = self.factory(task)
            return _structured(task, direct)
        if node == WorkflowNode.REVIEW_EXPECTATION_CONSTRUCTION.value:
            evidence = _evidence(EvidenceSourceType.DOXATLAS_SOURCE)
            audit = DoxAtlasAuditResult(
                findings=[],
                evidence_refs=[evidence],
                objections=[],
                delegations=[],
                unknowns=[],
                rationale="A1 approved construction shells.",
            )
            return AgentResult(
                task_id=task.task_id,
                agent_name=task.agent_name,
                status=ResultStatus.SUCCEEDED,
                payload={"structured": audit.model_dump(mode="json")},
                evidence_refs=[evidence],
            )
        if node == WorkflowNode.REVIEW_EXPECTATION_FIELDS.value:
            if task.agent_name is not AgentName.A1_DOXATLAS_AUDIT:
                evidence = _evidence()
                review = ExpectationFieldReviewResult(
                    findings=[
                        {
                            "field_path": "review_scope",
                            "status": "supported",
                            "rationale": f"{task.agent_name.value} review found no blocker.",
                            "evidence_refs": [evidence],
                        }
                    ],
                    evidence_refs=[evidence],
                    rationale=f"{task.agent_name.value} completed expectation-field review.",
                )
                return AgentResult(
                    task_id=task.task_id,
                    agent_name=task.agent_name,
                    status=ResultStatus.SUCCEEDED,
                    payload={"structured": review.model_dump(mode="json")},
                )
            evidence = _evidence(EvidenceSourceType.DOXATLAS_SOURCE)
            self.objection_id = new_id("objection")
            objection = Objection(
                objection_id=self.objection_id,
                source_agent=AgentName.A1_DOXATLAS_AUDIT,
                target=_target(task.ticker),
                severity=ObjectionSeverity.BLOCKING,
                reason="A1 needs external support for one realized fact.",
                evidence_refs=[evidence],
                status=ObjectionStatus.OPEN,
            )
            delegation = create_a2_retrieval_delegation(
                DelegatedRetrievalRequest(
                    requester_agent=AgentName.A1_DOXATLAS_AUDIT,
                    question="Find external support for the realized fact.",
                    blocking_scope=_target(task.ticker),
                )
            )
            audit = DoxAtlasAuditResult(
                findings=[
                    {
                        "field_path": "realized_facts",
                        "status": "needs_more_evidence",
                        "rationale": "DoxAtlas source support is incomplete.",
                        "evidence_refs": [evidence],
                    }
                ],
                evidence_refs=[evidence],
                objections=[objection],
                delegations=[delegation],
                unknowns=["External confirmation still required."],
                rationale="A1 created a blocking audit item.",
            )
            return AgentResult(
                task_id=task.task_id,
                agent_name=task.agent_name,
                status=ResultStatus.SUCCEEDED,
                payload={"structured": audit.model_dump(mode="json")},
            )
        if node == WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS.value:
            if task.agent_name is AgentName.A2_FACT_CHECK:
                evidence_refs = (
                    [_evidence(EvidenceSourceType.EXTERNAL_REPORT)]
                    if self.a2_has_evidence
                    else []
                )
                retrieval = DelegatedRetrievalResult(
                    answer="External source supports the delegated fact."
                    if self.a2_has_evidence
                    else "No sufficient support found.",
                    claim_verdict="supported" if self.a2_has_evidence else "unknown",
                    retrieval_summary="Search verification completed."
                    if self.a2_has_evidence
                    else "Search verification found no sufficient source.",
                    evidence_refs=evidence_refs,
                    source_refs=evidence_refs,
                    confidence=0.74 if self.a2_has_evidence else 0.2,
                    unknowns=[] if self.a2_has_evidence else ["No reliable source found."],
                    query_log=["anysearch.search: realized fact"],
                    can_complete_delegation=self.a2_has_evidence,
                )
                return AgentResult(
                    task_id=task.task_id,
                    agent_name=task.agent_name,
                    status=ResultStatus.SUCCEEDED,
                    payload={"structured": retrieval.model_dump(mode="json")},
                )
            return AgentResult(
                task_id=task.task_id,
                agent_name=task.agent_name,
                status=ResultStatus.SUCCEEDED,
                payload={
                    "structured": {
                        "expectation_id": "exp_mock_core",
                        "decision": "resolved",
                        "decisions": [
                            {
                                "objection_id": self.objection_id,
                                "decision": "resolved",
                                "resolution_note": "A2 evidence resolved the A1 objection.",
                                "changed_paths": ["document.realized_facts"],
                                "evidence_refs": [_evidence().model_dump(mode="json")],
                            }
                        ],
                        "revised_candidate": None,
                        "evidence_requests": [],
                        "unresolved_finding_ids": [],
                        "unresolved_reason": None,
                        "rationale": "O1 resolved the A1 objection.",
                    }
                },
            )
        return _structured(task, self.factory(task))


class C1BlockingReviewRunner(RealizedO1A1A2Runner):
    def run(self, task: AgentTask) -> AgentResult:
        if (
            task.run_metadata.workflow_node == WorkflowNode.REVIEW_EXPECTATION_FIELDS.value
            and task.agent_name is AgentName.C1_FUNDAMENTAL_RESEARCH
        ):
            evidence = _evidence()
            objection = Objection(
                objection_id=new_id("objection"),
                source_agent=AgentName.C1_FUNDAMENTAL_RESEARCH,
                target=_target(task.ticker),
                severity=ObjectionSeverity.BLOCKING,
                reason="C1 found the realized fact unsupported by fundamentals.",
                evidence_refs=[evidence],
                status=ObjectionStatus.OPEN,
            )
            review = ExpectationFieldReviewResult(
                findings=[
                    {
                        "field_path": "realized_facts",
                        "status": "unsupported",
                        "rationale": "The realized fact lacks company-fundamental support.",
                        "evidence_refs": [evidence],
                    }
                ],
                evidence_refs=[evidence],
                objections=[objection],
                rationale="C1 raised a blocking field objection.",
            )
            return AgentResult(
                task_id=task.task_id,
                agent_name=task.agent_name,
                status=ResultStatus.SUCCEEDED,
                payload={"structured": review.model_dump(mode="json")},
            )
        return super().run(task)


class O1RevisionDelegationRunner(RealizedO1A1A2Runner):
    def __init__(self) -> None:
        super().__init__(a2_has_evidence=True)
        self.revision_objection_id: str | None = None
        self.revision_delegation_id: str | None = None

    def run(self, task: AgentTask) -> AgentResult:
        node = task.run_metadata.workflow_node
        if node == WorkflowNode.REVIEW_EXPECTATION_FIELDS.value:
            self.calls.append((task.agent_name, task.task_type, task.run_metadata.workflow_node))
            self.tasks.append(task)
            evidence = _evidence()
            if task.agent_name is not AgentName.C1_FUNDAMENTAL_RESEARCH:
                review = ExpectationFieldReviewResult(
                    findings=[
                        {
                            "field_path": "review_scope",
                            "status": "supported",
                            "rationale": f"{task.agent_name.value} review found no blocker.",
                            "evidence_refs": [evidence],
                        }
                    ],
                    evidence_refs=[evidence],
                    rationale=f"{task.agent_name.value} completed expectation-field review.",
                )
                return AgentResult(
                    task_id=task.task_id,
                    agent_name=task.agent_name,
                    status=ResultStatus.SUCCEEDED,
                    payload={"structured": review.model_dump(mode="json")},
                    evidence_refs=[evidence],
                )
            self.revision_objection_id = new_id("objection")
            self.revision_delegation_id = new_id("delegation")
            objection = Objection(
                objection_id=self.revision_objection_id,
                source_agent=AgentName.C1_FUNDAMENTAL_RESEARCH,
                target=_target(task.ticker),
                severity=ObjectionSeverity.BLOCKING,
                reason="C1 requires O1 to revise the expectation patch.",
                evidence_refs=[evidence],
                status=ObjectionStatus.OPEN,
            )
            delegation = Delegation(
                delegation_id=self.revision_delegation_id,
                requester_agent=AgentName.C1_FUNDAMENTAL_RESEARCH,
                target_agent=AgentName.O1_EXPECTATION_OWNER,
                question="Revise the expectation patch to address the C1 objection.",
                required_evidence=[EvidenceSourceType.MARKET_DATA],
                blocking_scope=_target(task.ticker),
            )
            review = ExpectationFieldReviewResult(
                findings=[
                        {
                            "field_path": "realized_facts",
                            "status": "needs_more_evidence",
                            "rationale": "O1 must revise the patch before promotion.",
                            "evidence_refs": [evidence],
                        }
                ],
                evidence_refs=[evidence],
                objections=[objection],
                delegations=[delegation],
                rationale="C1 delegated the revision back to O1.",
            )
            return AgentResult(
                task_id=task.task_id,
                agent_name=task.agent_name,
                status=ResultStatus.SUCCEEDED,
                payload={"structured": review.model_dump(mode="json")},
                evidence_refs=[evidence],
            )
        if (
            node == WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS.value
            and task.agent_name is AgentName.O1_EXPECTATION_OWNER
        ):
            self.calls.append((task.agent_name, task.task_type, task.run_metadata.workflow_node))
            self.tasks.append(task)
            evidence = _evidence()
            return AgentResult(
                task_id=task.task_id,
                agent_name=task.agent_name,
                status=ResultStatus.SUCCEEDED,
                payload={
                    "structured": {
                        "expectation_id": "exp_mock_core",
                        "decision": "resolved",
                        "decisions": [
                            {
                                "objection_id": self.revision_objection_id,
                                "decision": "resolved",
                                "resolution_note": "O1 已按 C1 要求修订预期补丁。",
                                "changed_paths": ["document.realized_facts"],
                                "evidence_refs": [evidence.model_dump(mode="json")],
                            }
                        ],
                        "revised_candidate": None,
                        "evidence_requests": [],
                        "unresolved_finding_ids": [],
                        "unresolved_reason": None,
                        "rationale": "O1 已完成 C1 要求的预期修订。",
                    }
                },
                evidence_refs=[evidence],
            )
        return super().run(task)


class ConstructionObjectionRunner(RealizedO1A1A2Runner):
    def __init__(self) -> None:
        super().__init__(a2_has_evidence=True)
        self.construction_objection_id: str | None = None

    def run(self, task: AgentTask) -> AgentResult:
        node = task.run_metadata.workflow_node
        if (
            node == WorkflowNode.REVIEW_EXPECTATION_CONSTRUCTION.value
            and task.agent_name is AgentName.A1_DOXATLAS_AUDIT
        ):
            self.calls.append((task.agent_name, task.task_type, task.run_metadata.workflow_node))
            self.tasks.append(task)
            evidence = _evidence(EvidenceSourceType.DOXATLAS_SOURCE)
            self.construction_objection_id = new_id("objection")
            objection = Objection(
                objection_id=self.construction_objection_id,
                source_agent=AgentName.A1_DOXATLAS_AUDIT,
                target=_target(task.ticker),
                severity=ObjectionSeverity.BLOCKING,
                reason="A1 requires shell market_view to cite proposition-level evidence.",
                evidence_refs=[evidence],
                status=ObjectionStatus.OPEN,
            )
            audit = DoxAtlasAuditResult(
                verdict="needs_revision",
                revision_required=True,
                findings=[
                    {
                        "field_path": "market_view",
                        "status": "needs_more_evidence",
                        "rationale": "The shell needs tighter DoxAtlas evidence mapping.",
                        "evidence_refs": [evidence],
                    }
                ],
                evidence_refs=[evidence],
                objections=[objection],
                delegations=[],
                unknowns=[],
                rationale="A1 blocked construction shells for revision.",
            )
            return AgentResult(
                task_id=task.task_id,
                agent_name=task.agent_name,
                status=ResultStatus.SUCCEEDED,
                payload={"structured": audit.model_dump(mode="json")},
                evidence_refs=[evidence],
            )
        if (
            node == WorkflowNode.RESOLVE_EXPECTATION_CONSTRUCTION.value
            and task.agent_name is AgentName.O1_EXPECTATION_OWNER
        ):
            self.calls.append((task.agent_name, task.task_type, task.run_metadata.workflow_node))
            self.tasks.append(task)
            evidence = _evidence(EvidenceSourceType.DOXATLAS_SOURCE)
            shells = self.factory._expectation_shells(task.ticker)
            shells[0] = shells[0].model_copy(
                update={
                    "why_it_matters": "Revised shell now cites the specific DoxAtlas support gap.",
                    "evidence_refs": [evidence],
                    "rationale": "Revised after A1 construction review.",
                },
                deep=True,
            )
            construction = ExpectationShellConstructionResult(
                shells=shells,
                evidence_refs=[evidence],
                delegations=[],
                unknowns=[],
                rationale="O1 revised shell construction without creating full patches.",
            )
            return AgentResult(
                task_id=task.task_id,
                agent_name=task.agent_name,
                status=ResultStatus.SUCCEEDED,
                payload={"structured": construction.model_dump(mode="json")},
                evidence_refs=[evidence],
            )
        return super().run(task)


class DetailMissingNarrativeToolRunner(RealizedO1A1A2Runner):
    def __init__(self) -> None:
        super().__init__(a2_has_evidence=True)
        self.tool_registry = default_tool_registry()

    def run(self, task: AgentTask) -> AgentResult:
        result = super().run(task)
        if (
            task.run_metadata.workflow_node == WorkflowNode.GENERATE_EXPECTATION_DETAILS.value
            and task.agent_name is AgentName.O1_EXPECTATION_OWNER
        ):
            result = result.model_copy(
                update={"payload": dict(result.payload) | {"runtime": "react"}},
                deep=True,
            )
        return result


class DirectDocumentOutputRunner(RealizedO1A1A2Runner):
    def run(self, task: AgentTask) -> AgentResult:
        node = task.run_metadata.workflow_node
        if node == WorkflowNode.GENERATE_KNOWN_EVENTS.value:
            evidence = _evidence()
            return AgentResult(
                task_id=task.task_id,
                agent_name=task.agent_name,
                status=ResultStatus.SUCCEEDED,
                payload={
                    "structured": {
                        "document_id": "doc_known_events_direct",
                        "document_type": "known_events",
                        "ticker": task.ticker,
                        "created_at": "2026-06-12T00:00:00Z",
                        "events": [
                            {
                                "event_id": "evt_direct",
                                "event_time": "2026-06-12T00:00:00Z",
                                "description": "Direct known event output.",
                                "source": evidence.model_dump(mode="json"),
                                "discussed_by_market": True,
                                "has_price_reaction": False,
                                "is_known_old_news": False,
                            }
                        ],
                    }
                },
                evidence_refs=[evidence],
            )
        if node == WorkflowNode.GENERATE_MONITORING_CONFIG.value:
            return AgentResult(
                task_id=task.task_id,
                agent_name=task.agent_name,
                status=ResultStatus.SUCCEEDED,
                payload={
                    "structured": {
                        "document_id": "doc_monitoring_config_direct",
                        "document_type": "monitoring_config",
                        "ticker": task.ticker,
                        "created_at": "2026-06-12T00:00:00Z",
                        "monitoring_items": [
                            {
                                "item_id": "monitor_direct",
                                "base_keywords": [task.ticker, "launch"],
                                "extra_objects": ["launch milestone"],
                                "extra_keywords": ["delay", "acceleration"],
                                "related_entities": ["launch provider"],
                                "expectation_id": "exp_mock_core",
                                "priority": "high",
                                "trigger_condition": "Launch cadence changes materially.",
                            }
                        ],
                    }
                },
                evidence_refs=[_evidence()],
            )
        if node == WorkflowNode.GENERATE_MONITORING_POLICY.value:
            return AgentResult(
                task_id=task.task_id,
                agent_name=task.agent_name,
                status=ResultStatus.SUCCEEDED,
                payload={
                    "structured": {
                        "document_id": "doc_monitoring_policy_direct",
                        "document_type": "monitoring_policy",
                        "ticker": task.ticker,
                        "created_at": "2026-06-12T00:00:00Z",
                        "direct_trade_rules": [
                            {
                                "rule_id": "rule_direct_trade",
                                "action_type": "direct_trade",
                    "trigger_condition": (
                        "Confirmed launch milestone beats expectation."
                    ),
                                "expectation_id": "exp_mock_core",
                                "action": "mark as direct-trade candidate for human review",
                                "strategy_note": "No broker action is triggered.",
                                "evidence_fields": [
                                    "source_id",
                                    "event_time",
                                    "price_reaction",
                                ],
                                "escalation_path": "human_review",
                            }
                        ],
                        "push_to_agent_rules": [
                            {
                                "rule_id": "rule_push_agent",
                                "action_type": "push_to_agent",
                                "trigger_condition": "Ambiguous launch timing update appears.",
                                "expectation_id": "exp_mock_core",
                                "action": "send to O1 and O4",
                                "strategy_note": "Needs narrative and market-reaction review.",
                                "evidence_fields": ["source_id", "claim", "price_reaction"],
                                "escalation_path": "O1,O4",
                            }
                        ],
                        "cache_rules": [
                            {
                                "rule_id": "rule_direct",
                                "action_type": "cache",
                                "trigger_condition": "Low-confidence launch chatter appears.",
                                "expectation_id": "exp_mock_core",
                                "action": "cache for review",
                                "strategy_note": "Direct policy output.",
                                "evidence_fields": ["source_id", "duplicate_marker"],
                                "escalation_path": "batch_review",
                            }
                        ],
                    }
                },
                evidence_refs=[_evidence()],
            )
        return super().run(task)


class AcceptedObjectionRunner(RealizedO1A1A2Runner):
    def __init__(self, *, include_revision: bool) -> None:
        super().__init__(a2_has_evidence=True)
        self.include_revision = include_revision

    def run(self, task: AgentTask) -> AgentResult:
        if (
            task.run_metadata.workflow_node
            == WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS.value
            and task.agent_name is AgentName.O1_EXPECTATION_OWNER
        ):
            evidence = _evidence()
            structured: dict[str, object] = {
                "expectation_id": "exp_mock_core",
                "decision": "accepted",
                "decisions": [
                    {
                        "objection_id": self.objection_id,
                        "decision": "accepted",
                        "resolution_note": "O1 accepted reviewer evidence.",
                        "changed_paths": ["document.realized_facts_summary"],
                        "evidence_refs": [evidence.model_dump(mode="json")],
                    }
                ],
                "revised_candidate": None,
                "evidence_requests": [],
                "unresolved_finding_ids": [],
                "unresolved_reason": None,
                "rationale": "O1 accepted and revised the expectation."
                if self.include_revision
                else "O1 accepted the objection but omitted the required revision.",
            }
            if self.include_revision:
                pending = task.input_context["pending_patches"][0]
                market_view = dict(pending.get("market_view") or {})
                structured["revised_candidate"] = {
                    "document_id": "doc_phase15_revised",
                    "document_type": "expectation_unit",
                    "ticker": task.ticker,
                    "created_at": "2026-06-12T00:00:00Z",
                    "updated_at": None,
                    "expectation_id": pending["expectation_id"],
                    "expectation_name": pending["expectation_name"],
                    "direction": pending["direction"] or "neutral",
                    "why_it_matters": pending["why_it_matters"],
                    "market_view": {
                        "text": market_view.get("text") or "Revised market view.",
                        "summary": market_view.get("summary") or "Revised market view.",
                        "evidence_refs": [evidence.model_dump(mode="json")],
                        "author_agent": AgentName.O1_EXPECTATION_OWNER.value,
                        "reviewer_agents": [],
                    },
                    "realized_facts": [
                        {
                            "event_id": "event_phase15_revised",
                            "description": "Reviewer objection was accepted and revised.",
                            "price_reaction": {
                                "price_change": "unknown",
                                "price_pattern": "unknown",
                                "interpretation": "Price reaction was not established.",
                                "evidence_refs": [evidence.model_dump(mode="json")],
                            },
                            "evidence_refs": [evidence.model_dump(mode="json")],
                        }
                    ],
                    "realized_facts_summary": "Revised after reviewer objection.",
                    "key_variables": [
                        {
                            "variable_id": "var_phase15_revised",
                            "name": "Reviewer evidence coverage",
                            "current_status": "Revision incorporated reviewer evidence.",
                            "certainty": "medium",
                            "evidence_refs": [evidence.model_dump(mode="json")],
                        }
                    ],
                    "event_monitoring_direction": {
                        "known_event_notice": "No fixed known date.",
                        "positive_events": ["Evidence coverage improves."],
                        "negative_events": ["Evidence coverage remains weak."],
                    },
                }
            return AgentResult(
                task_id=task.task_id,
                agent_name=task.agent_name,
                status=ResultStatus.SUCCEEDED,
                payload={"structured": structured},
            )
        return super().run(task)


def test_a2_registry_is_search_only_and_supports_delegated_retrieval() -> None:
    definition = default_agent_registry().get(AgentName.A2_FACT_CHECK)

    assert TaskType.DELEGATED_RETRIEVAL in definition.task_types
    assert set(definition.runtime.allowed_tools) == {
        "anysearch.search",
        "tavily.search",
        "tavily.extract",
    }
    assert definition.runtime.prompt_block_ids == ["agent.a2"]
    assert definition.runtime.default_internal_task_skill_ids == []
    assert definition.runtime.output_schema == "DelegatedRetrievalResult"


def test_a2_delegation_helper_exposes_standard_blocking_request() -> None:
    request = DelegatedRetrievalRequest(
        requester_agent=AgentName.O1_EXPECTATION_OWNER,
        question="Find external confirmation for the event.",
        blocking_scope=_target(),
        purpose="fact_check",
    )

    delegation = create_a2_retrieval_delegation(request)

    assert delegation.target_agent is AgentName.A2_FACT_CHECK
    assert delegation.requester_agent is AgentName.O1_EXPECTATION_OWNER
    assert delegation.required_evidence == [EvidenceSourceType.EXTERNAL_REPORT]
    assert delegation.is_blocking


def test_mock_tool_registry_supports_a2_search_and_denies_old_fact_check_permission() -> None:
    registry = default_tool_registry()
    permissions = default_agent_registry().get(AgentName.A2_FACT_CHECK).runtime.to_permissions()

    tavily = registry.call(
        ToolRequest(tool_name="tavily.search", ticker="NVDA", agent_name=AgentName.A2_FACT_CHECK),
        permissions,
    )
    anysearch = registry.call(
        ToolRequest(
            tool_name="anysearch.search",
            ticker="NVDA",
            agent_name=AgentName.A2_FACT_CHECK,
        ),
        permissions,
    )
    old_fact_check = registry.call(
        ToolRequest(
            tool_name="fact_check.search",
            ticker="NVDA",
            agent_name=AgentName.A2_FACT_CHECK,
        ),
        permissions,
    )

    assert tavily.status is ResultStatus.SUCCEEDED
    assert tavily.evidence_refs[0].source_type is EvidenceSourceType.EXTERNAL_REPORT
    assert anysearch.status is ResultStatus.SUCCEEDED
    assert anysearch.evidence_refs[0].source_type is EvidenceSourceType.EXTERNAL_REPORT
    assert old_fact_check.status is ResultStatus.FAILED
    assert old_fact_check.error is not None
    assert old_fact_check.error.code == "tool_not_allowed"


def test_workflow_uses_a2_retrieval_to_complete_delegation_and_o1_resolves_objection() -> None:
    runner = RealizedO1A1A2Runner(a2_has_evidence=True)
    workflow = BlackboardInitializationWorkflow(
        runner=runner,
        execution_mode="agent_runner",
        auto_resolve_blockers=False,
    )

    result = workflow.run(
        "NVDA",
        stop_after=WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS,
    )
    run = workflow.blackboard.get_run(result.checkpoint.run_id)

    assert result.status is WorkflowRunStatus.RUNNING
    assert result.summary.unresolved_objection_count == 0
    assert result.summary.blocking_delegation_count == 0
    assert all(not objection.is_unresolved for objection in run.objections)
    assert all(not delegation.is_blocking for delegation in run.delegations)
    assert any(entry.content_type == "delegated_retrieval_result" for entry in run.working_memory)
    review_agents = [
        agent
        for agent, _, node in runner.calls
        if node == WorkflowNode.REVIEW_EXPECTATION_FIELDS.value
    ]
    assert review_agents == [
        AgentName.A1_DOXATLAS_AUDIT,
        AgentName.C1_FUNDAMENTAL_RESEARCH,
        AgentName.C3_INDUSTRY_RESEARCH,
        AgentName.O4_MARKET_TRACE,
    ]
    review_content_types = {entry.content_type for entry in run.working_memory}
    assert {
        "a1_doxatlas_audit",
        "c1_fundamental_review",
        "c3_industry_review",
        "o4_market_trace_review",
    }.issubset(review_content_types)
    o1_resolution_tasks = [
        task
        for task in runner.tasks
        if task.run_metadata.workflow_node == WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS.value
        and task.agent_name is AgentName.O1_EXPECTATION_OWNER
    ]
    assert o1_resolution_tasks
    resolution_context = o1_resolution_tasks[0].input_context
    assert resolution_context["resolution_mode"] == "document2_resolution_plan"
    assert resolution_context["internal_task_skill_ids"] == ["document2-resolution-plan"]
    assert resolution_context["react_runtime_budget"]["max_steps"] == 1
    assert resolution_context["react_runtime_budget"]["max_tool_call_batches"] == 0
    assert resolution_context["pending_patches"]
    assert resolution_context["pending_expectation_patch_summaries"]
    assert resolution_context["global_research_context"]["omitted_for"] == (
        WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS.value
    )
    assert resolution_context["unresolved_objections"][0]["objection_id"] == runner.objection_id


def test_o1_revision_delegation_completes_after_o1_resolves_review_objection() -> None:
    runner = O1RevisionDelegationRunner()
    workflow = BlackboardInitializationWorkflow(
        runner=runner,
        execution_mode="agent_runner",
        auto_resolve_blockers=False,
    )

    result = workflow.run("NVDA")
    run = workflow.blackboard.get_run(result.checkpoint.run_id)

    assert result.status is WorkflowRunStatus.COMPLETED
    assert result.summary.unresolved_objection_count == 0
    assert result.summary.blocking_delegation_count == 0
    delegation = next(
        item
        for item in run.delegations
        if item.delegation_id == runner.revision_delegation_id
    )
    assert not delegation.is_blocking
    assert delegation.result_summary == "O1 已完成 C1 要求的预期修订。"


def test_construction_objection_resolution_revises_shells_without_pending_patches() -> None:
    runner = ConstructionObjectionRunner()
    workflow = BlackboardInitializationWorkflow(
        runner=runner,
        execution_mode="agent_runner",
        auto_resolve_blockers=False,
    )

    result = workflow.run("NVDA", stop_after=WorkflowNode.GENERATE_EXPECTATION_DETAILS)
    run = workflow.blackboard.get_run(result.checkpoint.run_id)
    resolve_tasks = [
        task
        for task in runner.tasks
        if task.run_metadata.workflow_node == WorkflowNode.RESOLVE_EXPECTATION_CONSTRUCTION.value
        and task.agent_name is AgentName.O1_EXPECTATION_OWNER
    ]

    assert result.status is WorkflowRunStatus.RUNNING
    assert resolve_tasks
    assert resolve_tasks[0].required_output_schema == "ExpectationShellConstructionResult"
    assert resolve_tasks[0].permissions.writable_targets == []
    assert resolve_tasks[0].permissions.can_propose_patch is False
    assert resolve_tasks[0].input_context["internal_task_skill_ids"] == [
        "expectation-construction"
    ]
    assert resolve_tasks[0].input_context["expectation_shells"]
    assert resolve_tasks[0].input_context["unresolved_objections"]
    assert result.checkpoint.pending_patches
    assert all(
        not objection.is_unresolved
        for objection in run.objections
        if objection.objection_id == runner.construction_objection_id
    )
    assert result.checkpoint.metadata["expectation_shells"][0]["why_it_matters"].startswith(
        "Revised shell"
    )


def test_objection_resolution_batches_large_unresolved_sets() -> None:
    class BatchResolvingRunner(AgentRunner):
        def __init__(self) -> None:
            self.batches: list[list[str]] = []
            self.contexts: list[dict[str, object]] = []

        def run(self, task: AgentTask) -> AgentResult:
            context = task.input_context
            ids = [
                item["objection_id"]
                for item in context.get("unresolved_objections", [])
                if isinstance(item, dict)
            ]
            self.batches.append(list(ids))
            self.contexts.append(dict(context))
            payload = ExpectationConstructionResult(
                rationale="Resolved current objection batch.",
            ).model_dump(mode="json")
            payload = {
                "expectation_id": "exp_mock_core",
                "decision": "resolved",
                "decisions": [
                    {
                        "objection_id": objection_id,
                        "decision": "resolved",
                        "resolution_note": f"Resolved {objection_id}.",
                        "changed_paths": ["document"],
                        "evidence_refs": [],
                    }
                    for objection_id in ids
                ],
                "revised_candidate": None,
                "evidence_requests": [],
                "unresolved_finding_ids": [],
                "unresolved_reason": None,
                "rationale": payload["rationale"],
            }
            return AgentResult(
                task_id=task.task_id,
                agent_name=task.agent_name,
                status=ResultStatus.SUCCEEDED,
                payload={"structured": payload},
            )

    runner = BatchResolvingRunner()
    workflow = BlackboardInitializationWorkflow(
        runner=runner,
        execution_mode="agent_runner",
        auto_resolve_blockers=False,
    )
    run = workflow.blackboard.start_run("NVDA", AgentName.SYSTEM)
    checkpoint = WorkflowCheckpoint(
        run_id=run.run_id,
        ticker="NVDA",
        next_node=WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS,
    )
    for index in range(5):
        workflow.blackboard.create_objection(
            run.run_id,
            Objection(
                objection_id=f"obj_batch_{index}",
                source_agent=AgentName.A1_DOXATLAS_AUDIT,
                target=_target("NVDA"),
                severity=ObjectionSeverity.BLOCKING,
                reason=f"Batch objection {index}.",
                evidence_refs=[_evidence()],
                status=ObjectionStatus.OPEN,
            ),
        )

    results = workflow._resolve_blockers(
        checkpoint,
        WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS,
    )
    updated = workflow.blackboard.get_run(run.run_id)

    assert len(results) == 5
    assert [len(batch) for batch in runner.batches] == [1, 1, 1, 1, 1]
    assert runner.contexts[0]["resolution_batch"]["batch_index"] == 1
    assert runner.contexts[0]["resolution_batch"]["total_unresolved_before_batch"] == 5
    assert runner.contexts[1]["resolution_batch"]["batch_index"] == 2
    assert runner.contexts[1]["resolution_batch"]["total_unresolved_before_batch"] == 4
    assert runner.contexts[2]["resolution_batch"]["batch_index"] == 3
    assert runner.contexts[2]["resolution_batch"]["total_unresolved_before_batch"] == 3
    assert runner.contexts[4]["resolution_batch"]["batch_index"] == 5
    assert runner.contexts[4]["resolution_batch"]["total_unresolved_before_batch"] == 1
    assert all(not objection.is_unresolved for objection in updated.objections)
    assert [
        entry.content_type for entry in updated.working_memory
    ] == [
        "objection_resolution_result",
        "objection_resolution_result",
        "objection_resolution_result",
        "objection_resolution_result",
        "objection_resolution_result",
    ]


def test_review_objection_inherits_result_evidence_when_missing() -> None:
    workflow = BlackboardInitializationWorkflow(execution_mode="mock")
    evidence = _evidence(EvidenceSourceType.DOXATLAS_SOURCE)
    objection = Objection(
        objection_id="obj_missing_evidence",
        source_agent=AgentName.A1_DOXATLAS_AUDIT,
        target=_target("NVDA"),
        severity=ObjectionSeverity.BLOCKING,
        reason="A1 objection omitted local evidence refs.",
        evidence_refs=[],
        status=ObjectionStatus.OPEN,
    )
    result = AgentResult(
        task_id="task_review",
        agent_name=AgentName.A1_DOXATLAS_AUDIT,
        status=ResultStatus.SUCCEEDED,
        payload={"structured": {"evidence_refs": []}},
        evidence_refs=[evidence],
    )

    hydrated = workflow._objection_with_evidence_fallback(objection, result)

    assert [ref.evidence_id for ref in hydrated.evidence_refs] == [evidence.evidence_id]


def test_workflow_prefetches_missing_o1_detail_narrative_tool_evidence() -> None:
    runner = DetailMissingNarrativeToolRunner()
    workflow = BlackboardInitializationWorkflow(
        runner=runner,
        execution_mode="agent_runner",
        auto_resolve_blockers=False,
    )

    result = workflow.run("NVDA", stop_after=WorkflowNode.GENERATE_EXPECTATION_DETAILS)
    run = workflow.blackboard.get_run(result.checkpoint.run_id)
    detail_entries = [
        entry for entry in run.working_memory if entry.content_type == "expectation_detail_result"
    ]

    assert result.status is WorkflowRunStatus.RUNNING
    assert detail_entries
    assert detail_entries[0].payload["tool_calls"][0]["tool_name"] == (
        "doxa_get_narrative_report"
    )
    assert detail_entries[0].payload["tool_calls"][0]["status"] == "succeeded"
    assert detail_entries[0].evidence_refs


def test_workflow_converts_direct_document_outputs_to_blackboard_patches() -> None:
    workflow = BlackboardInitializationWorkflow(
        runner=DirectDocumentOutputRunner(a2_has_evidence=True),
        execution_mode="agent_runner",
        auto_resolve_blockers=False,
    )

    result = workflow.run("NVDA")
    run = workflow.blackboard.get_run(result.checkpoint.run_id)

    assert result.status is WorkflowRunStatus.COMPLETED
    assert DocumentType.KNOWN_EVENTS in run.belief_state.documents
    assert DocumentType.MONITORING_CONFIG in run.belief_state.documents
    assert DocumentType.MONITORING_POLICY in run.belief_state.documents
    assert any(
        entry.patch.target.document_type is DocumentType.KNOWN_EVENTS
        for entry in run.commit_log
    )


def test_a1_workflow_nodes_receive_minimal_doxatlas_tool_sets() -> None:
    runner = RealizedO1A1A2Runner(a2_has_evidence=True)
    workflow = BlackboardInitializationWorkflow(
        runner=runner,
        execution_mode="agent_runner",
        auto_resolve_blockers=False,
    )

    result = workflow.run("NVDA", stop_after=WorkflowNode.REVIEW_EXPECTATION_FIELDS)

    assert result.status is WorkflowRunStatus.RUNNING
    a1_tasks = [
        task
        for task in runner.tasks
        if task.agent_name is AgentName.A1_DOXATLAS_AUDIT
    ]
    tools_by_node = {
        task.run_metadata.workflow_node: set(task.permissions.allowed_tools)
        for task in a1_tasks
    }
    assert tools_by_node[WorkflowNode.REVIEW_EXPECTATION_CONSTRUCTION.value] == {
        "doxa_query_analysis",
        "doxa_get_analysis",
        "doxa_get_narrative_report",
        "doxa_query_propositions",
        "doxa_get_ignored_propositions",
    }
    assert tools_by_node[WorkflowNode.REVIEW_EXPECTATION_FIELDS.value] == {
        "doxa_query_analysis",
        "doxa_get_analysis",
        "doxa_query_propositions",
        "doxa_get_event_source",
        "doxa_get_media_result",
        "doxa_get_media_result_detail",
        "doxa_get_social_result",
        "doxa_get_social_result_detail",
        "doxa_get_ignored_propositions",
    }
    assert "doxa_get_narrative_report" not in tools_by_node[
        WorkflowNode.REVIEW_EXPECTATION_FIELDS.value
    ]
    assert all(
        not any(tool.startswith("doxa_run_") for tool in tools)
        for tools in tools_by_node.values()
    )


def test_reviewer_nodes_receive_role_specific_tool_sets() -> None:
    runner = RealizedO1A1A2Runner(a2_has_evidence=True)
    workflow = BlackboardInitializationWorkflow(
        runner=runner,
        execution_mode="agent_runner",
        auto_resolve_blockers=False,
    )

    result = workflow.run("NVDA", stop_after=WorkflowNode.REVIEW_EXPECTATION_FIELDS)

    assert result.status is WorkflowRunStatus.RUNNING
    review_tools = {
        task.agent_name: set(task.permissions.allowed_tools)
        for task in runner.tasks
        if task.run_metadata.workflow_node == WorkflowNode.REVIEW_EXPECTATION_FIELDS.value
    }
    assert review_tools[AgentName.C1_FUNDAMENTAL_RESEARCH] == {
        "sec.company_facts_and_filings",
        "sec.filing_sections",
        "alpha.company_overview",
        "alpha.financial_statements",
        "alpha.earnings_events",
        "tavily.search",
    }
    assert review_tools[AgentName.C3_INDUSTRY_RESEARCH] == {
        "finnhub.company_peers",
        "sec.company_facts_and_filings",
        "fmp.sector_performance",
        "tavily.search",
        "tavily.extract",
    }
    assert review_tools[AgentName.O4_MARKET_TRACE] == {
        "twelvedata.daily_ohlcv",
        "yfinance.daily_ohlcv",
        "finnhub.trade_stream",
    }


def test_workflow_blocks_when_a2_search_retrieval_has_no_sufficient_evidence() -> None:
    workflow = BlackboardInitializationWorkflow(
        runner=RealizedO1A1A2Runner(a2_has_evidence=False),
        execution_mode="agent_runner",
        auto_resolve_blockers=False,
    )

    result = workflow.run("NVDA")
    run = workflow.blackboard.get_run(result.checkpoint.run_id)

    assert result.status is WorkflowRunStatus.BLOCKED
    assert result.error is not None
    assert "A2 did not return sufficient search evidence" in result.error
    assert DocumentType.EXPECTATION_UNIT not in run.belief_state.documents
    assert len(run.commit_log) == 1


def test_c1_reviewer_objection_blocks_expectation_promotion() -> None:
    workflow = BlackboardInitializationWorkflow(
        runner=C1BlockingReviewRunner(a2_has_evidence=True),
        execution_mode="agent_runner",
        auto_resolve_blockers=False,
    )

    result = workflow.run("NVDA")
    run = workflow.blackboard.get_run(result.checkpoint.run_id)

    assert result.status is WorkflowRunStatus.BLOCKED
    assert result.error is not None
    assert "left blockers unresolved" in result.error
    assert any(
        objection.source_agent is AgentName.C1_FUNDAMENTAL_RESEARCH
        for objection in run.objections
    )
    assert DocumentType.EXPECTATION_UNIT not in run.belief_state.documents


def test_o1_accepting_objection_requires_revised_expectation_patch() -> None:
    workflow = BlackboardInitializationWorkflow(
        runner=AcceptedObjectionRunner(include_revision=False),
        execution_mode="agent_runner",
        auto_resolve_blockers=False,
    )

    result = workflow.run("NVDA")

    assert result.status is WorkflowRunStatus.BLOCKED
    assert result.error is not None
    assert "accepted resolution decisions require a proposed_revision or revised_candidate" in (
        result.error
    )


def test_o1_revised_candidate_replaces_pending_expectation_patch() -> None:
    workflow = BlackboardInitializationWorkflow(
        runner=AcceptedObjectionRunner(include_revision=True),
        execution_mode="agent_runner",
        auto_resolve_blockers=False,
    )

    result = workflow.run(
        "NVDA",
        stop_after=WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS,
    )
    patch = result.checkpoint.pending_patches[0]

    assert result.status is WorkflowRunStatus.RUNNING
    assert patch.target.expectation_id == "exp_mock_core"
    assert patch.after["realized_facts_summary"] == "Revised after reviewer objection."


def test_a2_delegation_context_uses_react_requirements_not_legacy_tool_requests() -> None:
    workflow = BlackboardInitializationWorkflow(execution_mode="mock")
    delegation = create_a2_retrieval_delegation(
        DelegatedRetrievalRequest(
            requester_agent=AgentName.A1_DOXATLAS_AUDIT,
            question="Find evidence for a realized fact.",
            blocking_scope=_target(),
        )
    )

    context = workflow._a2_delegation_context(delegation)

    assert "tool_requests" not in context
    assert context["required_tool_names"] == []
    assert [item["tool_name"] for item in context["tool_requirements"]] == [
        "anysearch.search",
        "tavily.search",
        "tavily.extract",
    ]


def test_agent_runner_cannot_use_mock_blocker_auto_resolve_backdoor() -> None:
    workflow = BlackboardInitializationWorkflow(
        runner=RealizedO1A1A2Runner(a2_has_evidence=True),
        execution_mode="agent_runner",
        auto_resolve_blockers=True,
    )
    result = workflow.run("NVDA", stop_after=WorkflowNode.START_TICKER_INITIALIZATION)

    with pytest.raises(Exception, match="disabled in agent_runner mode"):
        workflow._mock_resolve_blockers(result.checkpoint)
