# ruff: noqa: F403,F405
"""Mock initialization runner fixtures for the initialization workflow."""

from doxagent.workflows.initialization.shared import *


class InitializationMockResultFactory:
    def __init__(self, *, include_blockers: bool = True) -> None:
        self.include_blockers = include_blockers

    def __call__(self, task: AgentTask) -> AgentResult:
        node = task.run_metadata.workflow_node
        if (
            node == WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS.value
            and task.agent_name == AgentName.A2_FACT_CHECK
        ):
            evidence = self._evidence(EvidenceSourceType.EXTERNAL_REPORT)
            return self._result(
                task,
                payload={
                    "answer": (
                        "Mock search verification supports the delegated information request."
                    ),
                    "claim_verdict": "supported",
                    "retrieval_summary": "Mock search verification completed.",
                    "evidence_refs": [evidence.model_dump(mode="json")],
                    "source_refs": [evidence.model_dump(mode="json")],
                    "confidence": 0.72,
                    "unknowns": [],
                    "query_log": ["mock public-source query"],
                    "can_complete_delegation": True,
                },
                evidence_refs=[evidence],
            )
        if (
            node == WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS.value
            and task.agent_name == AgentName.O1_EXPECTATION_OWNER
        ):
            evidence = self._evidence(EvidenceSourceType.AGENT_OUTPUT)
            objections = task.input_context.get("unresolved_objections")
            objection_items = [
                item
                for item in objections
                if isinstance(item, dict) and isinstance(item.get("objection_id"), str)
            ] if isinstance(objections, list) else []
            expectation_id = "exp_mock_core"
            if objection_items:
                target = objection_items[0].get("target")
                if isinstance(target, dict) and isinstance(target.get("expectation_id"), str):
                    expectation_id = target["expectation_id"]
            return self._result(
                task,
                payload={
                    "expectation_id": expectation_id,
                    "decision": "resolved",
                    "decisions": [
                        {
                            "objection_id": item["objection_id"],
                            "finding_id": None,
                            "decision": "resolved",
                            "resolution_note": (
                                "Mock O1 resolution plan cites existing evidence."
                            ),
                            "changed_paths": ["expectation_unit.document"],
                            "evidence_refs": [evidence.model_dump(mode="json")],
                        }
                        for item in objection_items
                    ],
                    "target_finding_ids": [],
                    "revised_candidate": None,
                    "evidence_requests": [],
                    "unresolved_finding_ids": [],
                    "unresolved_reason": None,
                    "rationale": "Mock O1 produced a typed resolution plan.",
                },
                evidence_refs=[evidence],
            )
        if node == WorkflowNode.BUILD_GLOBAL_RESEARCH.value:
            patch = self._document_patch(
                self._global_research(task.ticker),
                DocumentType.GLOBAL_RESEARCH,
                AgentName.C1_FUNDAMENTAL_RESEARCH,
            )
            return self._result(task, payload={"document_type": "global_research"}, patches=[patch])
        if node == WorkflowNode.GENERATE_EXPECTATION_CONSTRUCTION.value:
            shells = self._expectation_shells(task.ticker)
            return self._result(
                task,
                payload={
                    "shells": [shell.model_dump(mode="json") for shell in shells],
                    "evidence_refs": [
                        evidence.model_dump(mode="json")
                        for shell in shells
                        for evidence in shell.evidence_refs
                    ],
                    "delegations": [],
                    "unknowns": [],
                    "rationale": "Mock O1 constructed differentiated expectation shells.",
                },
                evidence_refs=[evidence for shell in shells for evidence in shell.evidence_refs],
            )
        if node == WorkflowNode.GENERATE_EXPECTATION_DETAILS.value:
            document = self._expectation_unit(task.ticker)
            shell = task.input_context.get("expectation_shell")
            if isinstance(shell, dict):
                document = document.model_copy(
                    update={
                        "expectation_id": shell.get("expectation_id")
                        or document.expectation_id,
                        "expectation_name": shell.get("expectation_name")
                        or document.expectation_name,
                        "direction": ExpectationDirection(shell["direction"])
                        if isinstance(shell.get("direction"), str)
                        else document.direction,
                        "why_it_matters": shell.get("why_it_matters")
                        or document.why_it_matters,
                        "market_view": ResearchSection.model_validate(shell["market_view"])
                        if isinstance(shell.get("market_view"), dict)
                        else document.market_view,
                    },
                    deep=True,
                )
            evidence_refs = list(document.market_view.evidence_refs)
            for fact in document.realized_facts:
                evidence_refs.extend(fact.evidence_refs)
                evidence_refs.extend(fact.price_reaction.evidence_refs)
            for variable in document.key_variables:
                evidence_refs.extend(variable.evidence_refs)
            return self._result(
                task,
                payload={
                    "candidate": document.model_dump(mode="json"),
                    "evidence_refs": [
                        evidence.model_dump(mode="json") for evidence in evidence_refs
                    ],
                    "delegations": [],
                    "unknowns": [],
                    "rationale": "Mock O1 completed expectation detail.",
                },
                evidence_refs=evidence_refs,
            )
        if (
            node == WorkflowNode.REVIEW_EXPECTATION_FIELDS.value
            and task.agent_name == AgentName.A1_DOXATLAS_AUDIT
            and self.include_blockers
        ):
            target = self._expectation_target(task.ticker)
            objection = Objection(
                objection_id=new_id("objection"),
                source_agent=AgentName.A1_DOXATLAS_AUDIT,
                target=target,
                severity=ObjectionSeverity.BLOCKING,
                reason="Mock review requires DoxAtlas source support before promotion.",
                evidence_refs=[self._evidence(EvidenceSourceType.DOXATLAS_SOURCE)],
                status=ObjectionStatus.OPEN,
            )
            delegation = Delegation(
                delegation_id=new_id("delegation"),
                requester_agent=AgentName.O1_EXPECTATION_OWNER,
                target_agent=AgentName.A2_FACT_CHECK,
                question="Confirm the mock realized fact before promotion.",
                required_evidence=[EvidenceSourceType.FACT_CHECK],
                blocking_scope=target,
                status=DelegationStatus.OPEN,
            )
            return self._result(
                task,
                payload={"review": "blocking_items_created"},
                objections=[objection],
                delegations=[delegation],
            )
        if node == WorkflowNode.GENERATE_KNOWN_EVENTS.value:
            patch = self._document_patch(
                self._known_events(task.ticker),
                DocumentType.KNOWN_EVENTS,
                AgentName.O1_EXPECTATION_OWNER,
            )
            return self._result(task, payload={"document_type": "known_events"}, patches=[patch])
        if node == WorkflowNode.GENERATE_GLOBAL_NARRATIVE_REPORT.value:
            return self._result(
                task,
                payload=self._section(
                    task.ticker,
                    AgentName.O1_EXPECTATION_OWNER,
                    "market narrative",
                ).model_dump(mode="json"),
            )
        if node == WorkflowNode.GENERATE_MONITORING_CONFIG.value:
            patch = self._document_patch(
                self._monitoring_config(task.ticker),
                DocumentType.MONITORING_CONFIG,
                AgentName.O2_MONITORING_CONFIG,
            )
            return self._result(
                task,
                payload={"document_type": "monitoring_config"},
                patches=[patch],
            )
        if node == WorkflowNode.GENERATE_MONITORING_POLICY.value:
            patch = self._document_patch(
                self._monitoring_policy(task.ticker),
                DocumentType.MONITORING_POLICY,
                AgentName.O4_MARKET_TRACE,
            )
            return self._result(
                task,
                payload={"document_type": "monitoring_policy"},
                patches=[patch],
            )
        return self._result(task, payload={"node": node or "unknown"})

    def _result(
        self,
        task: AgentTask,
        *,
        payload: dict[str, Any],
        patches: list[BlackboardPatch] | None = None,
        objections: list[Objection] | None = None,
        delegations: list[Delegation] | None = None,
        evidence_refs: list[EvidenceRef] | None = None,
    ) -> AgentResult:
        return AgentResult(
            task_id=task.task_id,
            agent_name=task.agent_name,
            status=ResultStatus.SUCCEEDED,
            payload=payload,
            proposed_patches=patches or [],
            evidence_refs=evidence_refs or [self._evidence(EvidenceSourceType.AGENT_OUTPUT)],
            objections=objections or [],
            delegations=delegations or [],
        )

    def _document_patch(
        self,
        document: GlobalResearchDocument
        | ExpectationUnitDocument
        | KnownEventsDocument
        | MonitoringConfigDocument
        | MonitoringPolicyDocument,
        document_type: DocumentType,
        author_agent: AgentName,
        *,
        expectation_id: str | None = None,
    ) -> BlackboardPatch:
        return BlackboardPatch(
            patch_id=new_id("patch"),
            target=BlackboardTarget(
                document_type=document_type,
                ticker=document.ticker,
                document_id=document.document_id if expectation_id is None else None,
                expectation_id=expectation_id,
                field_path="document",
            ),
            operation=PatchOperation.CREATE,
            before=None,
            after=document.model_dump(mode="json"),
            rationale=f"Promote mock {document_type.value} document.",
            evidence_refs=[self._evidence(EvidenceSourceType.AGENT_OUTPUT)],
            author_agent=author_agent,
            validation_status=ValidationStatus.VALID,
        )

    def _evidence(self, source_type: EvidenceSourceType) -> EvidenceRef:
        retrieval_metadata: dict[str, Any] = {"fixture": "phase5"}
        source_id = f"{source_type.value}:mock"
        if source_type is EvidenceSourceType.MARKET_DATA:
            source_id = "twelvedata:daily_ohlcv:MOCK"
            retrieval_metadata.update(
                {
                    "tool_name": "twelvedata.daily_ohlcv",
                    "market_evidence_snapshot": {
                        "kind": "daily_ohlcv_snapshot",
                        "symbol": "MOCK",
                        "bar_count": 60,
                        "usable_bar_count": 60,
                        "start_close": 100,
                        "end_close": 103,
                        "total_return_pct": 3,
                    },
                }
            )
        return EvidenceRef(
            evidence_id=new_id("evidence"),
            source_type=source_type,
            source_id=source_id,
            title="Mock initialization evidence",
            summary="Deterministic Phase 5 workflow fixture evidence.",
            retrieval_metadata=retrieval_metadata,
            confidence=0.8,
            citation_scope="initialization_workflow",
        )

    def _section(self, ticker: str, author: AgentName, topic: str) -> ResearchSection:
        return ResearchSection(
            text=f"{ticker} mock {topic} research text.",
            summary=f"{ticker} mock {topic} summary.",
            evidence_refs=[self._evidence(EvidenceSourceType.EXTERNAL_REPORT)],
            author_agent=author,
            reviewer_agents=[AgentName.O1_EXPECTATION_OWNER],
        )

    def _global_research(self, ticker: str) -> GlobalResearchDocument:
        now = datetime.now(UTC)
        return GlobalResearchDocument(
            document_id=new_id("doc"),
            ticker=ticker,
            created_at=now,
            fundamental_report=self._section(
                ticker,
                AgentName.C1_FUNDAMENTAL_RESEARCH,
                "fundamental",
            ),
            macro_report=self._section(ticker, AgentName.C2_MACRO_RESEARCH, "macro"),
            industry_report=self._section(ticker, AgentName.C3_INDUSTRY_RESEARCH, "industry"),
            market_trace_report=self._section(ticker, AgentName.O4_MARKET_TRACE, "market trace"),
        )

    def _expectation_shell(self, ticker: str) -> ExpectationShell:
        evidence = self._evidence(EvidenceSourceType.DOXATLAS_SOURCE)
        return ExpectationShell(
            expectation_id="exp_mock_core",
            expectation_name=f"{ticker} mock core expectation",
            direction=ExpectationDirection.BULLISH.value,
            why_it_matters="It anchors the initialization workflow fixture.",
            market_view=ResearchSection(
                text=f"{ticker} mock market view text.",
                summary=f"{ticker} mock market view summary.",
                evidence_refs=[evidence],
                author_agent=AgentName.O1_EXPECTATION_OWNER,
                reviewer_agents=[AgentName.A1_DOXATLAS_AUDIT],
            ),
            evidence_refs=[evidence],
            unknowns=[],
            rationale="Mock construction shell.",
        )

    def _expectation_shells(self, ticker: str) -> list[ExpectationShell]:
        core = self._expectation_shell(ticker)
        risk_evidence = self._evidence(EvidenceSourceType.EXTERNAL_REPORT)
        risk = core.model_copy(
            update={
                "expectation_id": "exp_mock_risk",
                "expectation_name": f"{ticker} mock risk expectation",
                "direction": ExpectationDirection.RISK.value,
                "why_it_matters": "It captures downside risk distinct from the core thesis.",
                "market_view": ResearchSection(
                    text=f"{ticker} mock risk market view text.",
                    summary=f"{ticker} mock risk market view summary.",
                    evidence_refs=[risk_evidence],
                    author_agent=AgentName.O1_EXPECTATION_OWNER,
                    reviewer_agents=[AgentName.A1_DOXATLAS_AUDIT],
                ),
                "evidence_refs": [risk_evidence],
                "rationale": "Mock construction risk shell.",
            },
            deep=True,
        )
        return [core, risk]

    def _expectation_unit(self, ticker: str) -> ExpectationUnitDocument:
        now = datetime.now(UTC)
        return ExpectationUnitDocument(
            document_id=new_id("doc"),
            ticker=ticker,
            created_at=now,
            expectation_id="exp_mock_core",
            expectation_name=f"{ticker} mock core expectation",
            direction=ExpectationDirection.BULLISH,
            why_it_matters="It anchors the initialization workflow fixture.",
            market_view=self._section(ticker, AgentName.O1_EXPECTATION_OWNER, "market view"),
            realized_facts=[
                RealizedFact(
                    event_id=new_id("event"),
                    description="Mock realized fact for initialization.",
                    price_reaction=PriceReaction(
                        price_change="+3%",
                        price_pattern="mock gap up",
                        interpretation="Mock market has partially priced the event.",
                        evidence_refs=[self._evidence(EvidenceSourceType.MARKET_DATA)],
                    ),
                    evidence_refs=[self._evidence(EvidenceSourceType.FACT_CHECK)],
                ),
            ],
            realized_facts_summary="Mock realized fact is available.",
            key_variables=[
                VariableStatus(
                    variable_id=new_id("variable"),
                    name="Mock demand variable",
                    current_status="stable",
                    certainty="medium",
                    evidence_refs=[self._evidence(EvidenceSourceType.EXTERNAL_REPORT)],
                ),
            ],
            event_monitoring_direction=EventMonitoringDirection(
                known_event_notice="Monitor mock event follow-through.",
                positive_events=["mock positive confirmation"],
                negative_events=["mock negative revision"],
            ),
        )

    def _known_events(self, ticker: str) -> KnownEventsDocument:
        return KnownEventsDocument(
            document_id=new_id("doc"),
            ticker=ticker,
            created_at=datetime.now(UTC),
            events=[
                KnownEvent(
                    event_id=new_id("event"),
                    event_time=datetime.now(UTC),
                    description="Mock known event.",
                    core_fact="Mock known event.",
                    duplicate_detection_keys=[ticker, "mock known event"],
                    source=self._evidence(EvidenceSourceType.DOXATLAS_SOURCE),
                    expectation_id="exp_mock_core",
                    discussed_by_market=True,
                    has_price_reaction=True,
                    is_known_old_news=False,
                ),
            ],
        )

    def _monitoring_config(self, ticker: str) -> MonitoringConfigDocument:
        return MonitoringConfigDocument(
            document_id=new_id("doc"),
            ticker=ticker,
            created_at=datetime.now(UTC),
            monitoring_items=[
                MonitoringItem(
                    item_id=new_id("monitor"),
                    tool_input={
                        "ticker": ticker,
                        "source_id": "stocktwits_messages",
                        "keywords": [ticker, "mock confirmation"],
                        "search_terms": ["mock core expectation"],
                        "extra": {
                            "expectation_id": "exp_mock_core",
                            "priority": "high",
                            "trigger_condition": "mock signal changes the expectation",
                        },
                        "reason": "Track mock expectation-changing signals.",
                        "mode": "merge",
                        "enabled": True,
                    },
                    reasoning="Track mock expectation-changing signals.",
                    base_keywords=[ticker],
                    extra_objects=["mock core expectation"],
                    extra_keywords=["mock confirmation"],
                    related_entities=[],
                    expectation_id="exp_mock_core",
                    priority="high",
                    trigger_condition="mock signal changes the expectation",
                ),
            ],
        )

    def _monitoring_policy(self, ticker: str) -> MonitoringPolicyDocument:
        return MonitoringPolicyDocument(
            document_id=new_id("doc"),
            ticker=ticker,
            created_at=datetime.now(UTC),
            direct_trade_rules=[
                MonitoringPolicyRule(
                    policy_id=new_id("policy"),
                    rule_id=new_id("rule"),
                    policy_type="direct_trade",
                    action_type=PolicyActionType.DIRECT_TRADE,
                    scope={"expectation_unit_id": "exp_mock_core"},
                    trigger={"condition": "mock high-confidence positive signal"},
                    trigger_condition="mock high-confidence positive signal",
                    confirmation={"market_confirmation": "price and source confirmation present"},
                    expectation_id="exp_mock_core",
                    action={
                        "side": "long",
                        "conviction": "medium",
                        "size_bucket": "normal",
                        "note": "Create a trade intent candidate only.",
                    },
                    risk_guard={"guardrail": "Do not create broker orders."},
                    reasoning="High-confidence signal can be routed as a trade intent candidate.",
                    strategy_note="Phase 5 does not place broker orders.",
                    evidence_fields=["source_id", "event_time", "price_reaction"],
                    escalation_path="human_review",
                ),
            ],
            push_to_agent_rules=[
                MonitoringPolicyRule(
                    policy_id=new_id("policy"),
                    rule_id=new_id("rule"),
                    policy_type="escalate",
                    action_type=PolicyActionType.PUSH_TO_AGENT,
                    scope={"expectation_unit_id": "exp_mock_core"},
                    trigger={"condition": "mock ambiguous signal"},
                    trigger_condition="mock ambiguous signal",
                    confirmation={"market_confirmation": "signal is ambiguous"},
                    expectation_id="exp_mock_core",
                    action={
                        "send_to": ["O1", "O4"],
                        "question": "Review whether the signal changes the expectation.",
                        "priority": "medium",
                    },
                    risk_guard={"guardrail": "Require agent review before action."},
                    reasoning="Ambiguous signal needs expectation-owner review.",
                    strategy_note="Needs expectation-owner review.",
                    evidence_fields=["source_id", "claim", "uncertainty_reason"],
                    escalation_path="O1",
                ),
            ],
            cache_rules=[],
        )

    def _expectation_target(self, ticker: str) -> BlackboardTarget:
        return BlackboardTarget(
            document_type=DocumentType.EXPECTATION_UNIT,
            ticker=ticker,
            expectation_id="exp_mock_core",
            field_path="document",
        )
