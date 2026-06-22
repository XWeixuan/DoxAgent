from datetime import UTC, datetime

from doxagent.models import (
    AgentName,
    AgentPermissions,
    AgentResult,
    AgentTask,
    BlackboardPatch,
    BlackboardTarget,
    Delegation,
    DelegationStatus,
    DocumentType,
    EventMonitoringDirection,
    EvidenceRef,
    EvidenceSourceType,
    ExpectationDirection,
    GlobalResearchDocument,
    KnownEvent,
    KnownEventsDocument,
    MonitoringConfigDocument,
    MonitoringItem,
    MonitoringPolicyDocument,
    MonitoringPolicyRule,
    Objection,
    ObjectionSeverity,
    ObjectionStatus,
    PatchOperation,
    PolicyActionType,
    PriceReaction,
    RealizedFact,
    ResearchSection,
    ResultStatus,
    RunMetadata,
    TaskType,
    ToolCallSummary,
    ValidationStatus,
    VariableStatus,
    new_id,
)

NOW = datetime(2026, 5, 29, 4, 0, tzinfo=UTC)
TICKER = "NVDA"


def evidence_ref(
    source_type: EvidenceSourceType = EvidenceSourceType.DOXATLAS_SOURCE,
) -> EvidenceRef:
    return EvidenceRef(
        evidence_id=new_id("evidence"),
        source_type=source_type,
        source_id="source-001",
        title="DoxAtlas narrative source",
        summary="Market narratives mention AI server demand.",
        retrieval_metadata={"retrieved_by": "fixture"},
        confidence=0.9,
        citation_scope="market_view",
    )


def target() -> BlackboardTarget:
    return BlackboardTarget(
        document_type=DocumentType.EXPECTATION_UNIT,
        ticker=TICKER,
        expectation_id="exp_ai_demand",
        field_path="market_view",
    )


def patch() -> BlackboardPatch:
    return BlackboardPatch(
        patch_id=new_id("patch"),
        target=target(),
        operation=PatchOperation.UPDATE,
        before={"summary": "old view"},
        after={"summary": "AI demand remains central"},
        rationale="A supported narrative update is required.",
        evidence_refs=[evidence_ref()],
        author_agent=AgentName.O1_EXPECTATION_OWNER,
        validation_status=ValidationStatus.PENDING,
    )


def objection(status: ObjectionStatus = ObjectionStatus.OPEN) -> Objection:
    return Objection(
        objection_id=new_id("objection"),
        source_agent=AgentName.A1_DOXATLAS_AUDIT,
        target=target(),
        severity=ObjectionSeverity.BLOCKING,
        reason="The source id does not yet support the market-view wording.",
        evidence_refs=[evidence_ref()],
        status=status,
        resolution_note="Resolved by replacing the unsupported wording."
        if status is ObjectionStatus.RESOLVED
        else None,
    )


def delegation(status: DelegationStatus = DelegationStatus.OPEN) -> Delegation:
    return Delegation(
        delegation_id=new_id("delegation"),
        requester_agent=AgentName.O1_EXPECTATION_OWNER,
        target_agent=AgentName.A2_FACT_CHECK,
        question="Confirm whether the reported shipment timing is accurate.",
        required_evidence=[EvidenceSourceType.FACT_CHECK],
        blocking_scope=target(),
        status=status,
        result_summary="Fact check confirmed timing."
        if status is DelegationStatus.COMPLETED
        else None,
    )


def agent_task() -> AgentTask:
    return AgentTask(
        task_id=new_id("task"),
        ticker=TICKER,
        agent_name=AgentName.O1_EXPECTATION_OWNER,
        task_type=TaskType.GENERATE_EXPECTATION_UNIT,
        input_context={"document_ids": ["global-research-001"]},
        required_output_schema="ExpectationUnitDocument",
        permissions=AgentPermissions(
            readable_context_scopes=["global_research"],
            writable_targets=["expectation_unit"],
            allowed_tools=["doxatlas.query"],
            can_delegate=True,
            can_propose_patch=True,
        ),
        run_metadata=RunMetadata(
            run_id=new_id("run"),
            ticker=TICKER,
            workflow_node="GenerateExpectationConstruction",
            created_at=NOW,
        ),
    )


def agent_result(status: ResultStatus = ResultStatus.SUCCEEDED) -> AgentResult:
    return AgentResult(
        task_id=new_id("task"),
        agent_name=AgentName.O1_EXPECTATION_OWNER,
        status=status,
        payload={"summary": "AI demand remains central"},
        proposed_patches=[patch()],
        evidence_refs=[evidence_ref()],
        objections=[objection()],
        delegations=[delegation()],
        tool_calls=[
            ToolCallSummary(
                tool_name="doxatlas.query",
                status=ResultStatus.SUCCEEDED,
                input_summary="ticker narratives",
                output_summary="AI demand narrative cluster",
                evidence_refs=[evidence_ref()],
            ),
        ],
    )


def research_section(author: AgentName = AgentName.C1_FUNDAMENTAL_RESEARCH) -> ResearchSection:
    return ResearchSection(
        text="Detailed research section text.",
        summary="Research section summary.",
        evidence_refs=[evidence_ref(EvidenceSourceType.EXTERNAL_REPORT)],
        author_agent=author,
        reviewer_agents=[AgentName.O1_EXPECTATION_OWNER],
    )


def global_research_document() -> GlobalResearchDocument:
    return GlobalResearchDocument(
        document_id=new_id("doc"),
        ticker=TICKER,
        created_at=NOW,
        fundamental_report=research_section(AgentName.C1_FUNDAMENTAL_RESEARCH),
        macro_report=research_section(AgentName.C2_MACRO_RESEARCH),
        industry_report=research_section(AgentName.C3_INDUSTRY_RESEARCH),
        market_narrative_report=research_section(AgentName.O1_EXPECTATION_OWNER),
        market_trace_report=research_section(AgentName.O4_MARKET_TRACE),
    )


def expectation_document() -> dict[str, object]:
    return {
        "document_id": new_id("doc"),
        "ticker": TICKER,
        "created_at": NOW,
        "expectation_id": "exp_ai_demand",
        "expectation_name": "AI server demand remains the dominant expectation",
        "direction": ExpectationDirection.BULLISH,
        "why_it_matters": "It explains the main market debate.",
        "market_view": research_section(AgentName.O1_EXPECTATION_OWNER),
        "realized_facts": [
            RealizedFact(
                event_id=new_id("event"),
                description="Recent earnings showed strong data-center demand.",
                price_reaction=PriceReaction(
                    price_change="+8%",
                    price_pattern="gap up",
                    interpretation="Market priced stronger demand.",
                    evidence_refs=[evidence_ref(EvidenceSourceType.MARKET_DATA)],
                ),
                evidence_refs=[evidence_ref()],
            ),
        ],
        "realized_facts_summary": "Market has priced part of the demand upside.",
        "key_variables": [
            VariableStatus(
                variable_id=new_id("variable"),
                name="Data center backlog",
                current_status="Elevated",
                certainty="medium",
                evidence_refs=[evidence_ref()],
            ),
        ],
        "event_monitoring_direction": EventMonitoringDirection(
            known_event_notice="Next earnings can move the expectation both ways.",
            positive_events=["Large hyperscaler order"],
            negative_events=["Supply constraint worsening"],
        ),
    }


def known_events_document() -> KnownEventsDocument:
    return KnownEventsDocument(
        document_id=new_id("doc"),
        ticker=TICKER,
        created_at=NOW,
        events=[
            KnownEvent(
                event_id=new_id("event"),
                event_time=NOW,
                description="Prior earnings release",
                source=evidence_ref(),
                expectation_id="exp_ai_demand",
                discussed_by_market=True,
                has_price_reaction=True,
                is_known_old_news=True,
            ),
        ],
    )


def monitoring_config_document() -> MonitoringConfigDocument:
    return MonitoringConfigDocument(
        document_id=new_id("doc"),
        ticker=TICKER,
        created_at=NOW,
        monitoring_items=[
            MonitoringItem(
                item_id=new_id("monitor"),
                base_keywords=["NVDA", "Nvidia"],
                extra_objects=["hyperscaler capex"],
                extra_keywords=["AI server demand"],
                related_entities=["MSFT", "AMZN"],
                expectation_id="exp_ai_demand",
                priority="high",
                trigger_condition="new confirmed order or cancellation",
            ),
        ],
    )


def monitoring_policy_document() -> MonitoringPolicyDocument:
    return MonitoringPolicyDocument(
        document_id=new_id("doc"),
        ticker=TICKER,
        created_at=NOW,
        direct_trade_rules=[
            MonitoringPolicyRule(
                rule_id=new_id("rule"),
                action_type=PolicyActionType.DIRECT_TRADE,
                trigger_condition="confirmed order materially above expectation",
                expectation_id="exp_ai_demand",
                action="标记为 direct_trade 候选",
                strategy_note="策略仅描述处理路径，不触发券商下单。",
                evidence_fields=["source_id", "event_time", "price_reaction"],
                escalation_path="human_review",
            ),
        ],
        push_to_agent_rules=[
            MonitoringPolicyRule(
                rule_id=new_id("rule"),
                action_type=PolicyActionType.PUSH_TO_AGENT,
                trigger_condition="ambiguous supplier signal",
                expectation_id="exp_ai_demand",
                action="推送给 O1 和 O4 复核",
                strategy_note="需要叙事与价格反应复核。",
                evidence_fields=["source_id", "claim", "price_reaction"],
                escalation_path="O1,O4",
            ),
        ],
        cache_rules=[
            MonitoringPolicyRule(
                rule_id=new_id("rule"),
                action_type=PolicyActionType.CACHE,
                trigger_condition="known old message repeated",
                expectation_id="exp_ai_demand",
                action="缓存为批量复核材料",
                strategy_note="不触发即时行动。",
                evidence_fields=["source_id", "duplicate_marker"],
                escalation_path="batch_review",
            ),
        ],
    )
