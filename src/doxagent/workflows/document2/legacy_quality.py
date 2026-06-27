# ruff: noqa: F403,F405
"""Behavior-preserving mixin extracted from initialization.py."""

from doxagent.workflows.document2.contracts import (
    Document2ResolutionDecisionRecord,
    Document2ResolutionPlan,
    Document2ReviewFinding,
    Document2Revision,
    Document2TransactionAudit,
    ExpectationUnitCandidate,
)
from doxagent.workflows.document2.deterministic_findings import (
    deterministic_findings_from_patch,
)
from doxagent.workflows.document2.numeric_sanity import (
    numeric_sanity_findings_from_objections,
)
from doxagent.workflows.document2.resolver import (
    DOCUMENT2_RESOLUTION_PLANS_KEY,
    document2_resolution_plan_from_agent_result,
    resolution_plans_json,
)
from doxagent.workflows.document2.review import (
    DOCUMENT2_REVIEW_FINDINGS_KEY,
    review_findings_json,
)
from doxagent.workflows.document2.transaction import (
    DOCUMENT2_TRANSACTION_AUDITS_KEY,
    document2_revision_from_resolution_plan,
    document2_transaction_audit,
    legacy_patch_from_document2_revision,
    transaction_audits_json,
    validate_resolution_plan_for_transaction,
)
from doxagent.workflows.initialization.shared import *

_DOCUMENT2_PENDING_REVISIONS_KEY = "document2_pending_revisions"


class Document2LegacyQualityMixin:
    def _numeric_sanity_review_objections(
        self,
        checkpoint: WorkflowCheckpoint,
    ) -> list[Objection]:
        objections: list[Objection] = []
        for patch in checkpoint.pending_patches:
            objections.extend(self._numeric_sanity_objections_for_patch(checkpoint.ticker, patch))
        return objections

    def _numeric_sanity_objections_for_patch(
        self,
        ticker: str,
        patch: BlackboardPatch,
    ) -> list[Objection]:
        if patch.target.document_type is not DocumentType.EXPECTATION_UNIT:
            return []
        if not isinstance(patch.after, dict):
            return []
        document = ExpectationUnitDocument.model_validate(patch.after)
        category_samples: dict[str, list[str]] = {
            "market_data": [],
            "fundamental_data": [],
        }
        category_evidence: dict[str, list[EvidenceRef]] = {
            "market_data": [],
            "fundamental_data": [],
        }

        def add_unsupported_numeric_samples(
            label: str,
            text: str,
            refs: list[EvidenceRef],
        ) -> None:
            compact_text = self._compact_context_text(text, limit=260)
            if not compact_text:
                return
            if self._contains_market_numeric_claim(
                text
            ) and not self._has_source_appropriate_numeric_evidence(
                refs,
                category="market_data",
            ):
                category_samples["market_data"].append(f"{label}: {compact_text}")
                category_evidence["market_data"].extend(refs)
            if self._contains_fundamental_numeric_claim(
                text
            ) and not self._has_source_appropriate_numeric_evidence(
                refs,
                category="fundamental_data",
            ):
                category_samples["fundamental_data"].append(f"{label}: {compact_text}")
                category_evidence["fundamental_data"].extend(refs)

        for index, fact in enumerate(document.realized_facts, start=1):
            reaction = fact.price_reaction
            fact_refs = self._dedupe_evidence_refs(
                [*fact.evidence_refs, *reaction.evidence_refs, *patch.evidence_refs]
            )
            fact_text = " ".join(
                [
                    fact.description,
                    reaction.price_change,
                    reaction.price_pattern,
                    reaction.interpretation,
                ]
            )
            add_unsupported_numeric_samples(
                f"realized_facts[{index}]",
                fact_text,
                fact_refs,
            )

        market_view_refs = self._dedupe_evidence_refs(
            [*document.market_view.evidence_refs, *patch.evidence_refs]
        )
        add_unsupported_numeric_samples(
            "market_view",
            " ".join([document.market_view.text, document.market_view.summary]),
            market_view_refs,
        )
        for index, variable in enumerate(document.key_variables, start=1):
            variable_refs = self._dedupe_evidence_refs(
                [*variable.evidence_refs, *patch.evidence_refs]
            )
            add_unsupported_numeric_samples(
                f"key_variables[{index}]",
                " ".join([variable.name, variable.current_status, variable.certainty]),
                variable_refs,
            )
        for index, event in enumerate(
            document.event_monitoring_direction.positive_events,
            start=1,
        ):
            add_unsupported_numeric_samples(
                f"event_monitoring_direction.positive_events[{index}]",
                event,
                patch.evidence_refs,
            )
        for index, event in enumerate(
            document.event_monitoring_direction.negative_events,
            start=1,
        ):
            add_unsupported_numeric_samples(
                f"event_monitoring_direction.negative_events[{index}]",
                event,
                patch.evidence_refs,
            )
        add_unsupported_numeric_samples(
            "event_monitoring_direction.known_event_notice",
            document.event_monitoring_direction.known_event_notice,
            patch.evidence_refs,
        )

        objections: list[Objection] = []
        for category, samples in category_samples.items():
            if not samples:
                continue
            evidence_refs = self._dedupe_evidence_refs(category_evidence[category])
            taxonomy = f"numeric_sanity_{category}"
            target_path = (
                "realized_facts.price_reaction"
                if category == "market_data"
                else "realized_facts"
            )
            objections.append(
                Objection(
                    objection_id=self._numeric_sanity_objection_id(
                        document.expectation_id,
                        category,
                    ),
                    source_agent=AgentName.SYSTEM,
                    target=BlackboardTarget(
                        document_type=DocumentType.EXPECTATION_UNIT,
                        ticker=ticker,
                        expectation_id=document.expectation_id,
                        field_path=target_path,
                    ),
                    severity=ObjectionSeverity.BLOCKING,
                    reason=self._numeric_sanity_objection_reason(
                        document.expectation_id,
                        category,
                        samples,
                        evidence_refs,
                    ),
                    evidence_refs=evidence_refs,
                    taxonomy=taxonomy,
                    dedupe_hash=f"{taxonomy}:{document.expectation_id}",
                    target_path=target_path,
                    status=ObjectionStatus.OPEN,
                )
            )
        return objections

    def _numeric_sanity_objection_id(self, expectation_id: str, category: str) -> str:
        safe_expectation_id = re.sub(r"[^0-9A-Za-z_]+", "_", expectation_id).strip("_")
        return f"obj_numeric_sanity_{safe_expectation_id[:80]}_{category}"

    def _numeric_sanity_objection_reason(
        self,
        expectation_id: str,
        category: str,
        samples: list[str],
        evidence_refs: list[EvidenceRef],
    ) -> str:
        source_summary = ", ".join(
            sorted({f"{ref.source_type.value}:{ref.source_id}" for ref in evidence_refs})
        )
        required = (
            "market-data evidence such as OHLCV, quote, market-cap, or vendor market data"
            if category == "market_data"
            else (
                "fundamental evidence such as SEC/companyfacts, financial statements, "
                "or issuer filings"
            )
        )
        return (
            f"Deterministic numeric sanity review for {expectation_id}: precise "
            f"{category.replace('_', ' ')} claims require {required}. Current evidence "
            f"refs are insufficient or narrative-only ({source_summary or 'none'}). "
            "O1 must correct the numbers with source-appropriate evidence, downgrade the "
            "claim to non-numeric uncertainty, or remove the false precision. Simply keeping "
            "the same precise number and labelling it narrative-only, unverified, approximate, "
            "or uncertain is not a valid resolution. Samples: "
            + " | ".join(samples[:3])
        )

    def _contains_market_numeric_claim(self, text: str) -> bool:
        lowered = text.lower()
        if not self._contains_numeric_value(lowered):
            return False
        return any(
            marker in lowered
            for marker in (
                "stock price",
                "share price",
                "target price",
                "market cap",
                "ytd",
                "forward p/e",
                "p/e",
                "p/s",
                "p/b",
                "peg",
                "股价",
                "目标价",
                "市值",
                "涨幅",
                "估值",
            )
        )

    def _contains_fundamental_numeric_claim(self, text: str) -> bool:
        lowered = text.lower()
        if not self._contains_numeric_value(lowered):
            return False
        return any(
            marker in lowered
            for marker in (
                "revenue",
                "gross margin",
                "net income",
                "roe",
                "cfo",
                "cash flow",
                "capex",
                "eps",
                "营收",
                "收入",
                "毛利率",
                "净利率",
                "净利润",
                "经营现金流",
                "资本开支",
            )
        )

    def _contains_numeric_value(self, text: str) -> bool:
        pattern = re.compile(
            r"\$?\d[\d,.]*(?:\s*(?:%|x|\u500d|bps|\u4ebf|\u4e07\u4ebf|billion|trillion))?",
            flags=re.IGNORECASE,
        )
        return any(
            not self._is_non_claim_numeric_token(text, match)
            for match in pattern.finditer(text)
        )

    def _is_non_claim_numeric_token(self, text: str, match: re.Match[str]) -> bool:
        token = match.group(0).strip()
        lowered = token.lower()
        if any(marker in lowered for marker in ("$", "%", "x", "bps", "billion", "trillion")):
            return False
        start, end = match.span()
        previous_char = text[start - 1] if start > 0 else ""
        next_char = text[end] if end < len(text) else ""
        if previous_char.isalpha() or next_char.isalpha():
            return True
        numeric = token.replace(",", "")
        if numeric.isdigit() and 1900 <= int(numeric) <= 2100:
            return True
        if len(numeric) <= 2 and previous_char.upper() == "Q":
            return True
        return False

    def _has_source_appropriate_numeric_evidence(
        self,
        evidence_refs: list[EvidenceRef],
        *,
        category: str,
    ) -> bool:
        for ref in evidence_refs:
            if ref.source_type is EvidenceSourceType.FACT_CHECK:
                return True
            source_text = " ".join(
                [
                    ref.source_id,
                    ref.title,
                    ref.summary,
                    str(ref.retrieval_metadata.get("tool_name") or ""),
                    str(ref.retrieval_metadata.get("provider") or ""),
                ]
            ).lower()
            if category == "market_data":
                if ref.source_type is EvidenceSourceType.MARKET_DATA:
                    return True
                if ref.source_type is EvidenceSourceType.EXTERNAL_REPORT and any(
                    marker in source_text
                    for marker in (
                        "alpha_vantage",
                        "finnhub",
                        "fmp",
                        "twelvedata",
                        "twelve",
                        "yfinance",
                        "market",
                        "quote",
                    )
                ):
                    return True
            if category == "fundamental_data":
                if ref.source_type is EvidenceSourceType.EXTERNAL_REPORT and any(
                    marker in source_text
                    for marker in (
                        "sec",
                        "companyfacts",
                        "filing",
                        "10-k",
                        "10-q",
                        "alpha_vantage",
                        "financial_statements",
                        "fmp",
                        "yfinance",
                        "issuer",
                    )
                ):
                    return True
        return False

    def _agent_output_evidence(self, result: AgentResult) -> EvidenceRef:
        return EvidenceRef(
            evidence_id=new_id("evidence"),
            source_type=EvidenceSourceType.AGENT_OUTPUT,
            source_id=f"agent_result:{result.task_id}",
            title=f"{result.agent_name.value} agent 输出",
            summary="agent 直接文档输出已转换为 Blackboard patch。",
            confidence=0.5,
            citation_scope="workflow_document_patch",
        )

    def _document_evidence_refs(
        self,
        document: KnownEventsDocument | MonitoringConfigDocument | MonitoringPolicyDocument,
    ) -> list[EvidenceRef]:
        if isinstance(document, KnownEventsDocument):
            return [
                self._normalize_evidence_ref_language(event.source)
                for event in document.events
            ]
        return []

    def _dedupe_evidence_refs(self, refs: Iterable[EvidenceRef]) -> list[EvidenceRef]:
        deduped: list[EvidenceRef] = []
        seen: set[str] = set()
        for ref in refs:
            key = ref.evidence_id
            if key in seen:
                continue
            seen.add(key)
            deduped.append(ref)
        return deduped

    def _patch_with_nested_evidence_refs(self, patch: BlackboardPatch) -> BlackboardPatch:
        after = self._payload_with_normalized_evidence_refs(patch.after)
        refs = self._dedupe_evidence_refs(
            [
                *(self._normalize_evidence_ref_language(ref) for ref in patch.evidence_refs),
                *self._payload_evidence_refs(after),
            ]
        )
        updates: dict[str, Any] = {}
        if after != patch.after:
            updates["after"] = after
        current_refs = [ref.model_dump(mode="json") for ref in patch.evidence_refs]
        next_refs = [ref.model_dump(mode="json") for ref in refs]
        if next_refs != current_refs:
            updates["evidence_refs"] = refs
        if not updates:
            return patch
        return patch.model_copy(update=updates, deep=True)

    def _payload_with_normalized_evidence_refs(self, value: Any) -> Any:
        if isinstance(value, dict):
            if value.get("evidence_id"):
                try:
                    ref = self._normalize_evidence_ref_language(EvidenceRef.model_validate(value))
                    return ref.model_dump(mode="json")
                except ValueError:
                    pass
            return {
                key: self._payload_with_normalized_evidence_refs(child)
                for key, child in value.items()
            }
        if isinstance(value, list):
            return [self._payload_with_normalized_evidence_refs(child) for child in value]
        return value

    def _payload_evidence_refs(self, value: Any) -> list[EvidenceRef]:
        refs: list[EvidenceRef] = []

        def walk(item: Any) -> None:
            if isinstance(item, dict):
                if item.get("evidence_id"):
                    try:
                        refs.append(EvidenceRef.model_validate(item))
                    except ValueError:
                        pass
                for child in item.values():
                    walk(child)
            elif isinstance(item, list):
                for child in item:
                    walk(child)

        walk(value)
        return self._dedupe_evidence_refs(refs)

    def _normalize_evidence_ref_language(self, ref: EvidenceRef) -> EvidenceRef:
        updates: dict[str, str] = {}
        if not self._has_chinese_text(ref.title):
            updates["title"] = self._evidence_ref_title_text(ref)
        if not self._has_chinese_text(ref.summary):
            updates["summary"] = self._evidence_ref_summary_text(ref)
        if not updates:
            return ref
        return ref.model_copy(update=updates, deep=True)

    def _evidence_ref_title_text(self, ref: EvidenceRef) -> str:
        tool_name = str(ref.retrieval_metadata.get("tool_name") or "")
        if tool_name == "doxa_get_narrative_report":
            return "DoxAtlas 叙事报告"
        if ref.source_type is EvidenceSourceType.DOXATLAS_SOURCE:
            return "DoxAtlas 证据"
        if ref.source_type is EvidenceSourceType.MARKET_DATA:
            return "市场数据证据"
        if ref.source_type is EvidenceSourceType.FACT_CHECK:
            return "事实核查证据"
        if ref.source_type is EvidenceSourceType.EXTERNAL_REPORT:
            return "外部报告证据"
        if ref.source_type is EvidenceSourceType.AGENT_OUTPUT:
            return "agent 输出证据"
        return "工具结果证据"

    def _evidence_ref_summary_text(self, ref: EvidenceRef) -> str:
        tool_name = str(ref.retrieval_metadata.get("tool_name") or "")
        if tool_name == "doxa_get_narrative_report":
            return "已检索 DoxAtlas 叙事报告。"
        if ref.source_type is EvidenceSourceType.DOXATLAS_SOURCE:
            return "已检索 DoxAtlas 证据。"
        if ref.source_type is EvidenceSourceType.MARKET_DATA:
            return "已检索市场数据证据。"
        if ref.source_type is EvidenceSourceType.FACT_CHECK:
            return "已检索事实核查证据。"
        if ref.source_type is EvidenceSourceType.EXTERNAL_REPORT:
            return "已检索外部报告证据。"
        if ref.source_type is EvidenceSourceType.AGENT_OUTPUT:
            return "agent 输出已作为证据保留。"
        return "工具已返回可引用证据。"

    def _objection_with_evidence_fallback(
        self,
        objection: Objection,
        result: AgentResult,
    ) -> Objection:
        if objection.evidence_refs:
            return objection
        refs: list[EvidenceRef] = [*result.evidence_refs]
        for tool_call in result.tool_calls:
            refs.extend(tool_call.evidence_refs)
        if not refs:
            payload = result.payload.get("structured")
            if not isinstance(payload, dict):
                payload = result.payload
            if isinstance(payload, dict):
                refs.extend(self._payload_evidence_refs(payload.get("evidence_refs")))
        if not refs:
            refs = [self._agent_output_evidence(result)]
        refs = self._dedupe_evidence_refs(
            self._normalize_evidence_ref_language(ref) for ref in refs
        )
        return objection.model_copy(update={"evidence_refs": refs}, deep=True)

    def _resolve_blockers(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
    ) -> list[AgentResult]:
        if self.execution_mode != "agent_runner":
            self._mock_resolve_blockers(checkpoint)
            return []

        results: list[AgentResult] = []
        resolution_plans: list[Document2ResolutionPlan] = []
        transaction_audits: list[Document2TransactionAudit] = []
        run = self.blackboard.get_run(checkpoint.run_id)
        for delegation in run.delegations:
            if not delegation.is_blocking or delegation.target_agent is not AgentName.A2_FACT_CHECK:
                continue
            if delegation.status is DelegationStatus.OPEN:
                self.blackboard.assign_delegation(checkpoint.run_id, delegation.delegation_id)
            result = self._run_agent(
                checkpoint,
                node,
                AgentName.A2_FACT_CHECK,
                TaskType.DELEGATED_RETRIEVAL,
                "DelegatedRetrievalResult",
                extra_context=self._a2_delegation_context(delegation),
            )
            self._write_working_memory(checkpoint, result, "delegated_retrieval_result")
            self._validate_agent_success(result, node, require_patches=False)
            if not self._can_complete_a2_delegation(result):
                raise WorkflowContractError(
                    f"A2 did not return sufficient search evidence for {delegation.delegation_id}."
                )
            self.blackboard.complete_delegation(
                checkpoint.run_id,
                delegation.delegation_id,
                self._delegation_completion_summary(result),
            )
            results.append(result)

        run = self.blackboard.get_run(checkpoint.run_id)
        unresolved_objections = [
            objection for objection in run.objections if objection.is_unresolved
        ]
        batch_index = 0
        stalled_objection_ids: set[str] = set()
        while unresolved_objections:
            pending_resolution_objections = [
                objection
                for objection in unresolved_objections
                if objection.objection_id not in stalled_objection_ids
            ]
            if not pending_resolution_objections:
                break
            batch_index += 1
            batch = self._next_objection_resolution_batch(pending_resolution_objections)
            batch_ids = {objection.objection_id for objection in batch}
            result = self._run_agent(
                checkpoint,
                node,
                AgentName.O1_EXPECTATION_OWNER,
                TaskType.REVIEW_EXPECTATION_FIELD,
                "Document2ResolutionPlan",
                extra_context=self._objection_resolution_context(
                    checkpoint,
                    batch,
                    batch_index=batch_index,
                    total_unresolved=len(unresolved_objections),
                ),
            )
            self._write_working_memory(checkpoint, result, "objection_resolution_result")
            self._validate_agent_success(result, node, require_patches=False)
            if result.proposed_patches:
                raise WorkflowContractError(
                    "O1 resolver must not return raw BlackboardPatch; return "
                    "Document2ResolutionPlan with revised_candidate or proposed_revision."
                )
            plan = document2_resolution_plan_from_agent_result(
                result,
                unresolved_objections=batch,
            )
            audit = self._apply_document2_resolution_transaction(checkpoint, plan)
            resolution_plans.append(plan)
            transaction_audits.append(audit)
            self._complete_o1_revision_delegations(checkpoint, result)
            results.append(result)
            run = self.blackboard.get_run(checkpoint.run_id)
            unresolved_objections = [
                objection for objection in run.objections if objection.is_unresolved
            ]
            unresolved_batch_ids = {
                objection.objection_id
                for objection in unresolved_objections
                if objection.objection_id in batch_ids
            }
            if unresolved_batch_ids == batch_ids:
                stalled_objection_ids.update(batch_ids)

        self._complete_o1_revision_delegations(checkpoint)
        run = self.blackboard.get_run(checkpoint.run_id)
        checkpoint.metadata = checkpoint.metadata | {
            DOCUMENT2_RESOLUTION_PLANS_KEY: resolution_plans_json(resolution_plans),
            DOCUMENT2_TRANSACTION_AUDITS_KEY: transaction_audits_json(transaction_audits),
        }
        if any(objection.is_unresolved for objection in run.objections) or any(
            delegation.is_blocking for delegation in run.delegations
        ):
            raise WorkflowContractError("ResolveObjectionsAndDelegations left blockers unresolved.")
        return results

    def _apply_document2_resolution_transaction(
        self,
        checkpoint: WorkflowCheckpoint,
        plan: Document2ResolutionPlan,
    ) -> Document2TransactionAudit:
        try:
            validate_resolution_plan_for_transaction(plan)
        except ValueError as exc:
            raise WorkflowContractError(str(exc)) from exc
        before_patch = self._pending_expectation_patch_for_resolution_plan(
            checkpoint,
            plan,
        )
        revision = document2_revision_from_resolution_plan(
            plan,
            before_patch=before_patch,
        )
        if revision is not None:
            legacy_patch = legacy_patch_from_document2_revision(
                revision,
                ticker=checkpoint.ticker,
            )
            self._validate_expectation_patch_list(checkpoint.ticker, [legacy_patch])
            checkpoint.pending_patches = self._replace_pending_patch_from_transaction(
                checkpoint,
                legacy_patch,
            )
            self._record_document2_transaction_revision(
                checkpoint,
                revision,
                legacy_patch,
            )
            self._reopen_numeric_sanity_objections_after_o1_revision(checkpoint)
            self._revalidate_document2_deterministic_findings_for_patch(
                checkpoint,
                legacy_patch,
            )
        elif before_patch is not None:
            self._revalidate_document2_deterministic_findings_for_patch(
                checkpoint,
                before_patch,
            )

        closed_ids: list[str] = []
        retained_ids: list[str] = []
        for decision in plan.decisions:
            objection_id = decision.objection_id
            if objection_id is None:
                continue
            if self._document2_resolution_decision_retains_blocker(
                checkpoint,
                decision,
            ):
                retained_ids.append(objection_id)
                continue
            self._apply_document2_objection_transition(
                checkpoint,
                decision,
            )
            closed_ids.append(objection_id)

        status = "rejected" if retained_ids else "accepted"
        audit = document2_transaction_audit(
            plan,
            status=status,
            revision=revision,
            closed_objection_ids=closed_ids,
            retained_objection_ids=retained_ids,
            notes=[
                "O1 resolver output was applied through Document2 transaction layer.",
                "O1 plan decisions do not directly close Blackboard objections.",
            ],
        )
        self._record_document2_transaction_audit(checkpoint, audit)
        return audit

    def _pending_expectation_patch_for_resolution_plan(
        self,
        checkpoint: WorkflowCheckpoint,
        plan: Document2ResolutionPlan,
    ) -> BlackboardPatch | None:
        for patch in checkpoint.pending_patches:
            if (
                patch.target.document_type is DocumentType.EXPECTATION_UNIT
                and patch.target.expectation_id == plan.expectation_id
            ):
                return patch
        return None

    def _replace_pending_patch_from_transaction(
        self,
        checkpoint: WorkflowCheckpoint,
        revision_patch: BlackboardPatch,
    ) -> list[BlackboardPatch]:
        expectation_id = revision_patch.target.expectation_id
        if expectation_id is None:
            raise WorkflowContractError("Document2 transaction revision is missing expectation_id.")
        pending = list(checkpoint.pending_patches)
        for index, patch in enumerate(pending):
            if (
                patch.target.document_type is DocumentType.EXPECTATION_UNIT
                and patch.target.expectation_id == expectation_id
            ):
                pending[index] = revision_patch
                return pending
        raise WorkflowContractError(
            "Document2 transaction revised an expectation that is not pending review."
        )

    def _record_document2_transaction_revision(
        self,
        checkpoint: WorkflowCheckpoint,
        revision: Document2Revision,
        legacy_patch: BlackboardPatch,
    ) -> None:
        raw = checkpoint.metadata.get(_DOCUMENT2_PENDING_REVISIONS_KEY)
        entries = [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
        existing = next(
            (
                item
                for item in entries
                if item.get("expectation_id") == revision.expectation_id
            ),
            None,
        )
        order = int(existing.get("order", len(entries))) if existing is not None else len(entries)
        candidate = ExpectationUnitCandidate(
            document=revision.after,
            source_agent=AgentName.SYSTEM,
            evidence_refs=revision.evidence_refs,
            unknowns=[],
            rationale=revision.rationale,
        )
        entries = [
            item
            for item in entries
            if item.get("expectation_id") != revision.expectation_id
        ]
        entries.append(
            {
                "workflow_node": WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS.value,
                "order": order,
                "expectation_id": revision.expectation_id,
                "expectation_name": revision.after.expectation_name,
                "candidate_id": candidate.candidate_id,
                "candidate": candidate.model_dump(mode="json"),
                "revision_id": revision.revision_id,
                "revision": revision.model_dump(mode="json"),
                "previous_revision_id": existing.get("revision_id")
                if existing is not None
                else None,
                "legacy_patch_id": legacy_patch.patch_id,
                "legacy_patch": legacy_patch.model_dump(mode="json"),
                "primary_state": "document2_revision",
                "legacy_pending_patch_derived": True,
                "updated_by_transaction": True,
                "updated_at": datetime.now(UTC).isoformat(),
            }
        )
        entries.sort(key=lambda item: int(item.get("order", 0)))
        checkpoint.metadata = checkpoint.metadata | {_DOCUMENT2_PENDING_REVISIONS_KEY: entries}

    def _revalidate_document2_deterministic_findings_for_patch(
        self,
        checkpoint: WorkflowCheckpoint,
        patch: BlackboardPatch,
    ) -> None:
        numeric_objections = self._numeric_sanity_objections_for_patch(
            checkpoint.ticker,
            patch,
        )
        findings = [
            *deterministic_findings_from_patch(patch),
            *numeric_sanity_findings_from_objections(numeric_objections),
        ]
        if not findings:
            return
        for objection in numeric_objections:
            self.blackboard.create_objection(checkpoint.run_id, objection)
        findings = self._bridge_document2_blocking_findings_to_objections(
            checkpoint,
            findings,
        )
        self._merge_document2_review_findings_metadata(checkpoint, findings)

    def _merge_document2_review_findings_metadata(
        self,
        checkpoint: WorkflowCheckpoint,
        findings: list[Document2ReviewFinding],
    ) -> None:
        if not findings:
            return
        raw = checkpoint.metadata.get(DOCUMENT2_REVIEW_FINDINGS_KEY, [])
        current: list[Document2ReviewFinding] = []
        if isinstance(raw, list):
            for item in raw:
                if not isinstance(item, dict):
                    continue
                try:
                    current.append(Document2ReviewFinding.model_validate(item))
                except ValueError:
                    continue
        by_key = {
            self._document2_review_finding_key(finding): finding
            for finding in current
        }
        for finding in findings:
            by_key[self._document2_review_finding_key(finding)] = finding
        checkpoint.metadata = checkpoint.metadata | {
            DOCUMENT2_REVIEW_FINDINGS_KEY: review_findings_json(list(by_key.values()))
        }

    def _bridge_document2_blocking_findings_to_objections(
        self,
        checkpoint: WorkflowCheckpoint,
        findings: list[Document2ReviewFinding],
    ) -> list[Document2ReviewFinding]:
        bridged: list[Document2ReviewFinding] = []
        for finding in findings:
            if not finding.blocks_promotion or finding.source_objection_id is not None:
                bridged.append(finding)
                continue
            objection = self.blackboard.create_objection(
                checkpoint.run_id,
                self._document2_objection_from_review_finding(checkpoint, finding),
            )
            bridged.append(
                finding.model_copy(
                    update={"source_objection_id": objection.objection_id},
                    deep=True,
                )
            )
        return bridged

    def _document2_objection_from_review_finding(
        self,
        checkpoint: WorkflowCheckpoint,
        finding: Document2ReviewFinding,
    ) -> Objection:
        evidence_refs = self._dedupe_evidence_refs(
            [
                *finding.supplemental_evidence_refs,
                *[
                    ref
                    for assessment in finding.evidence_assessments
                    for ref in assessment.evidence_refs
                ],
            ]
        )
        return Objection(
            objection_id=f"obj_d2finding_{finding.finding_id}",
            source_agent=finding.reviewer_agent,
            target=BlackboardTarget(
                document_type=DocumentType.EXPECTATION_UNIT,
                ticker=checkpoint.ticker,
                expectation_id=finding.expectation_id,
                field_path=finding.target_path,
            ),
            severity=ObjectionSeverity.BLOCKING,
            reason=finding.reason,
            evidence_refs=evidence_refs,
            taxonomy="document2_review_finding",
            dedupe_hash=self._document2_review_finding_key(finding),
            target_path=finding.target_path,
            status=ObjectionStatus.OPEN,
        )

    def _document2_review_finding_key(self, finding: Document2ReviewFinding) -> str:
        return "|".join(
            [
                finding.expectation_id,
                finding.target_path,
                finding.reason[:180],
            ]
        )

    def _document2_resolution_decision_retains_blocker(
        self,
        checkpoint: WorkflowCheckpoint,
        decision: Document2ResolutionDecisionRecord,
    ) -> bool:
        if decision.decision == "deferred":
            return True
        if decision.objection_id is None:
            return False
        current_numeric_objection_ids = {
            objection.objection_id
            for patch in checkpoint.pending_patches
            for objection in self._numeric_sanity_objections_for_patch(
                checkpoint.ticker,
                patch,
            )
        }
        return decision.objection_id in current_numeric_objection_ids

    def _apply_document2_objection_transition(
        self,
        checkpoint: WorkflowCheckpoint,
        decision: Document2ResolutionDecisionRecord,
    ) -> None:
        if decision.objection_id is None:
            return
        changed_paths = self._localized_changed_paths(decision.changed_paths)
        evidence_refs = list(decision.evidence_refs)
        note = self._objection_resolution_note_text(
            decision.resolution_note,
            decision=decision.decision,
        )
        if decision.decision == "resolved":
            self.blackboard.resolve_objection(
                checkpoint.run_id,
                decision.objection_id,
                note,
                changed_paths=changed_paths,
                evidence_refs=evidence_refs,
            )
        elif decision.decision == "accepted":
            self.blackboard.accept_objection(
                checkpoint.run_id,
                decision.objection_id,
                note,
                changed_paths=changed_paths,
                evidence_refs=evidence_refs,
            )
        elif decision.decision == "partially_accepted":
            self.blackboard.partially_accept_objection(
                checkpoint.run_id,
                decision.objection_id,
                note,
                changed_paths=changed_paths,
                evidence_refs=evidence_refs,
            )
        elif decision.decision == "rejected":
            self.blackboard.reject_objection(
                checkpoint.run_id,
                decision.objection_id,
                note,
                changed_paths=changed_paths,
                evidence_refs=evidence_refs,
            )

    def _record_document2_transaction_audit(
        self,
        checkpoint: WorkflowCheckpoint,
        audit: Document2TransactionAudit,
    ) -> None:
        self.blackboard.add_working_memory_entry(
            checkpoint.run_id,
            author_agent=AgentName.SYSTEM,
            content_type="document2_transaction_audit",
            payload={
                "status": audit.status,
                "audit": audit.model_dump(mode="json"),
            },
            evidence_refs=[],
        )

    def _a2_delegation_context(self, delegation: Delegation) -> dict[str, Any]:
        query_hint = delegation.question
        return {
            "delegation": delegation.model_dump(mode="json"),
            "tool_requirements": [
                {
                    "tool_name": "anysearch.search",
                    "required": False,
                    "input_hint": {
                        "query": query_hint,
                        "domain": "finance",
                        "content_types": ["web", "news"],
                        "zone": "intl",
                        "max_results": 5,
                    },
                },
                {
                    "tool_name": "tavily.search",
                    "required": False,
                    "input_hint": {
                        "query": query_hint,
                        "topic": "finance",
                        "search_depth": "basic",
                        "max_results": 5,
                    },
                },
                {
                    "tool_name": "tavily.extract",
                    "required": False,
                    "input_hint": {
                        "urls": ["<url selected from search results>"],
                        "extract_depth": "basic",
                    },
                },
            ],
            "required_tool_names": [],
        }

    def _objection_resolution_context(
        self,
        checkpoint: WorkflowCheckpoint,
        unresolved_objections: list[Objection],
        *,
        batch_index: int = 1,
        total_unresolved: int | None = None,
    ) -> dict[str, Any]:
        relevant_patches = self._objection_resolution_relevant_patches(
            checkpoint.pending_patches,
            unresolved_objections,
        )
        current_numeric_violations = [
            violation
            for violation in (
                self._current_numeric_sanity_violation_summary(
                    checkpoint,
                    objection,
                )
                for objection in unresolved_objections
            )
            if violation is not None
        ]
        output_guidance = [
            (
                "Only resolve the objections present in unresolved_objections for "
                "this batch. Every listed objection_id must appear exactly once in "
                "Document2ResolutionPlan.decisions."
            ),
            (
                "When duplicate_objection_clusters contains ids from this batch, "
                "resolve same-cluster objections with a consistent decision and "
                "do not leave duplicate siblings open."
            ),
            (
                "Use decision='resolved' when the objection can be closed by an "
                "existing field plus evidence."
            ),
            "Do not call external tools; reuse evidence_refs already present here.",
            "Use decision='rejected' only with explicit evidence_refs or rationale support.",
            (
                "Use decision='accepted' or 'partially_accepted' only when also "
                "returning one complete revised_candidate for the affected expectation_id."
            ),
            "Never return BlackboardPatch or proposed_patches in this resolution batch.",
            "Never return patches, changes, path maps, partial updates, list-wrapped "
            "revised_candidate, or multiple revised candidates.",
            "Each resolution must include changed_paths or evidence_refs.",
            (
                "Prioritize numeric sanity blockers: price, market cap, valuation "
                "multiples, dates, and single-source claims must be corrected, "
                "downgraded to non-numeric uncertainty, or explicitly rejected with "
                "evidence. Keeping the same precise number and merely labelling it "
                "narrative-only, unverified, approximate, or uncertain is not a valid "
                "resolution."
            ),
        ]
        if current_numeric_violations:
            output_guidance.append(
                "For every objection listed in current_numeric_sanity_violations, "
                "decision='resolved' with no revised_candidate is invalid because "
                "the current pending patch still reproduces the blocker. Return "
                "decision='accepted' or 'partially_accepted' with a revised_candidate "
                "that removes the listed false precision or adds source-appropriate "
                "evidence."
            )
        return {
            "internal_task_skill_ids": ["document2-resolution-plan"],
            "react_runtime_budget": {
                "max_steps": 1,
                "max_tool_call_batches": 0,
                "model_request_timeout_seconds": min(
                    _O1_RESOLVER_TIMEOUT_SECONDS,
                    float(self.settings.model_request_timeout_seconds),
                ),
            },
            "resolution_request": (
                "Resolve field-review objections using the compact expectation summaries "
                "and objection evidence below. Do not call tools in this node. Return "
                "Document2ResolutionPlan.decisions for every unresolved objection id "
                "with concise notes. Do not return BlackboardPatch. Only include a "
                "complete revised_candidate when a concrete accepted or partially "
                "accepted revision is unavoidable; otherwise cite changed_paths or "
                "evidence_refs."
            ),
            "resolution_mode": "document2_resolution_plan",
            "resolution_batch": {
                "batch_index": batch_index,
                "batch_size": len(unresolved_objections),
                "total_unresolved_before_batch": total_unresolved
                if total_unresolved is not None
                else len(unresolved_objections),
                "max_batch_size": _OBJECTION_RESOLUTION_BATCH_SIZE,
            },
            "global_research_context": {
                "omitted_for": WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS.value,
                "reason": (
                    "Full GlobalResearch text was already reviewed upstream; this node "
                    "uses compact expectation and objection summaries to avoid replaying "
                    "large context into the resolver."
                ),
            },
            "pending_patches": [
                self._compact_pending_expectation_patch(patch)
                for patch in relevant_patches
            ],
            "pending_expectation_patch_summaries": [
                self._pending_expectation_patch_summary(patch)
                for patch in checkpoint.pending_patches
                if patch.target.document_type is DocumentType.EXPECTATION_UNIT
            ],
            "omitted_pending_patch_count": max(
                0,
                len(
                    [
                        patch
                        for patch in checkpoint.pending_patches
                        if patch.target.document_type is DocumentType.EXPECTATION_UNIT
                    ]
                )
                - len(relevant_patches),
            ),
            "unresolved_objections": [
                self._objection_resolution_objection_summary(objection)
                for objection in unresolved_objections
            ],
            "current_numeric_sanity_violations": current_numeric_violations,
            "output_guidance": output_guidance,
            "root_cause_clusters": self._objection_resolution_root_cause_clusters(
                unresolved_objections
            ),
            "duplicate_objection_clusters": self._objection_resolution_duplicate_clusters(
                unresolved_objections
            ),
        }

    def _objection_resolution_relevant_patches(
        self,
        patches: list[BlackboardPatch],
        unresolved_objections: list[Objection],
    ) -> list[BlackboardPatch]:
        expectation_patches = [
            patch
            for patch in patches
            if patch.target.document_type is DocumentType.EXPECTATION_UNIT
        ]
        target_ids: set[str] = set()
        for objection in unresolved_objections:
            target_ids.update(self._objection_target_expectation_ids(objection))
        if not target_ids:
            return expectation_patches
        relevant = [
            patch
            for patch in expectation_patches
            if patch.target.expectation_id in target_ids
        ]
        return relevant or expectation_patches

    def _objection_target_expectation_ids(self, objection: Objection) -> set[str]:
        if objection.target.expectation_id:
            return {objection.target.expectation_id}
        target = objection.target
        ids: set[str] = set()
        for value in [
            objection.target_path,
            target.field_path,
            target.document_id,
            objection.objection_id,
        ]:
            text = str(value or "")
            match = re.search(r"(expectation_[A-Za-z0-9_]+|exp_[A-Za-z0-9_]+)", text)
            if match:
                ids.add(match.group(1))
                continue
            price_suffix = re.search(r"(?:^|_)price_(?P<suffix>[a-z]+_\d+)(?:_|$)", text.lower())
            if price_suffix:
                ids.add(f"expectation_{price_suffix.group('suffix')}")
        return ids

    def _current_numeric_sanity_violation_summary(
        self,
        checkpoint: WorkflowCheckpoint,
        objection: Objection,
    ) -> dict[str, Any] | None:
        if not objection.taxonomy.startswith("numeric_sanity_"):
            return None
        target_ids = self._objection_target_expectation_ids(objection)
        for patch in checkpoint.pending_patches:
            if patch.target.document_type is not DocumentType.EXPECTATION_UNIT:
                continue
            expectation_id = patch.target.expectation_id
            if target_ids and expectation_id not in target_ids:
                continue
            for current in self._numeric_sanity_objections_for_patch(
                checkpoint.ticker,
                patch,
            ):
                if current.objection_id != objection.objection_id:
                    continue
                return {
                    "objection_id": objection.objection_id,
                    "taxonomy": current.taxonomy,
                    "severity": current.severity.value,
                    "target": current.target.model_dump(mode="json"),
                    "patch_id": patch.patch_id,
                    "expectation_id": expectation_id,
                    "requires_revised_patch": True,
                    "current_reason": self._compact_context_text(
                        current.reason,
                        limit=2200,
                    ),
                    "current_evidence_refs": [
                        self._evidence_context_summary(ref)
                        for ref in current.evidence_refs[:6]
                    ],
                }
        return None

    def _reopen_numeric_sanity_objections_after_o1_revision(
        self,
        checkpoint: WorkflowCheckpoint,
    ) -> None:
        revalidation_objections = self._numeric_sanity_review_objections(checkpoint)
        if not revalidation_objections:
            return

        run = self.blackboard.get_run(checkpoint.run_id)
        existing_by_id = {objection.objection_id: objection for objection in run.objections}
        for objection in revalidation_objections:
            if not objection.taxonomy.startswith("numeric_sanity_"):
                continue
            existing = existing_by_id.get(objection.objection_id)
            self.blackboard.create_objection(checkpoint.run_id, objection)
            if existing is not None and not existing.is_unresolved:
                self.blackboard.mark_objection_unresolved(
                    checkpoint.run_id,
                    objection.objection_id,
                    (
                        "Numeric sanity revalidation failed after O1 revision: revised "
                        "expectation still contains precise numeric claims without "
                        "source-appropriate evidence. Narrative-only or unverified "
                        "labelling is not sufficient; remove the false precision or add "
                        "market/fundamental evidence."
                    ),
                )

    def _next_objection_resolution_batch(
        self,
        unresolved_objections: list[Objection],
    ) -> list[Objection]:
        if len(unresolved_objections) <= _OBJECTION_RESOLUTION_BATCH_SIZE:
            return list(unresolved_objections)
        clusters = self._objection_resolution_batch_clusters(unresolved_objections)
        if not clusters:
            return list(unresolved_objections[:_OBJECTION_RESOLUTION_BATCH_SIZE])
        clusters.sort(
            key=lambda cluster: (
                -self._objection_resolution_cluster_priority(cluster[0]),
                -len(cluster),
                unresolved_objections.index(cluster[0]),
            )
        )
        return list(clusters[0][:_OBJECTION_RESOLUTION_BATCH_SIZE])

    def _objection_resolution_batch_clusters(
        self,
        unresolved_objections: list[Objection],
    ) -> list[list[Objection]]:
        by_root: dict[str, list[Objection]] = {}
        for objection in unresolved_objections:
            root = self._objection_resolution_root_cause_key(objection)
            by_root.setdefault(root, []).append(objection)
        return list(by_root.values())

    def _objection_resolution_cluster_priority(self, objection: Objection) -> int:
        root = self._objection_resolution_root_cause_key(objection)
        if objection.taxonomy.startswith("numeric_sanity_"):
            return 100
        if root in {
            "root_cause:price_reaction_evidence_gap",
            "root_cause:market_return_magnitude",
            "root_cause:hbm4_price_reaction_contradiction",
        }:
            return 90
        if root in {
            "root_cause:temporal_event_state",
            "root_cause:fiscal_quarter_label",
            "root_cause:guidance_value_conflict",
        }:
            return 80
        if root == "root_cause:evidence_acquisition_gap":
            return 60
        return 10

    def _objection_resolution_root_cause_clusters(
        self,
        objections: list[Objection],
    ) -> list[dict[str, Any]]:
        clusters = self._objection_resolution_batch_clusters(objections)
        summaries: list[dict[str, Any]] = []
        for items in sorted(
            clusters,
            key=lambda cluster: (
                -self._objection_resolution_cluster_priority(cluster[0]),
                -len(cluster),
            ),
        ):
            sample = items[0]
            affected_ids: list[str] = []
            for objection in items:
                for expectation_id in self._objection_target_expectation_ids(objection):
                    if expectation_id not in affected_ids:
                        affected_ids.append(expectation_id)
            summaries.append(
                {
                    "root_cause_key": self._objection_resolution_root_cause_key(sample),
                    "objection_count": len(items),
                    "objection_ids": [
                        item.objection_id
                        for item in items[:_OBJECTION_RESOLUTION_BATCH_SIZE]
                    ],
                    "omitted_objection_count": max(
                        0,
                        len(items) - _OBJECTION_RESOLUTION_BATCH_SIZE,
                    ),
                    "affected_expectation_ids": affected_ids,
                    "taxonomies": sorted({item.taxonomy for item in items if item.taxonomy}),
                    "target_paths": sorted(
                        {
                            str(item.target_path or item.target.field_path or "document")
                            for item in items
                        }
                    )[:6],
                    "sample_reason": self._compact_context_text(sample.reason, limit=360),
                }
            )
        return summaries

    def _objection_resolution_duplicate_clusters(
        self,
        objections: list[Objection],
    ) -> list[dict[str, Any]]:
        clusters: dict[str, list[Objection]] = {}
        for objection in objections:
            for key in self._objection_resolution_cluster_keys(objection):
                clusters.setdefault(key, []).append(objection)
        seen: set[frozenset[str]] = set()
        summaries: list[dict[str, Any]] = []
        for key, items in clusters.items():
            if len(items) < 2:
                continue
            objection_ids = [item.objection_id for item in items]
            fingerprint = frozenset(objection_ids)
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            sample = items[0]
            summaries.append(
                {
                    "cluster_key": key,
                    "objection_ids": objection_ids,
                    "taxonomy": sample.taxonomy,
                    "target_path": sample.target_path or sample.target.field_path,
                    "target": sample.target.model_dump(mode="json"),
                    "reason_summary": self._compact_context_text(sample.reason, limit=360),
                }
            )
        return summaries

    def _objection_resolution_cluster_keys(self, objection: Objection) -> set[str]:
        keys: set[str] = set()
        keys.add(self._objection_resolution_root_cause_key(objection))
        if objection.dedupe_hash:
            keys.add(f"dedupe:{objection.dedupe_hash}")
        target = objection.target
        target_identity = ":".join(
            str(part or "")
            for part in (
                target.document_type.value,
                target.ticker,
                target.document_id,
                target.expectation_id,
                objection.target_path or target.field_path,
            )
        )
        if objection.taxonomy:
            keys.add(f"taxonomy-target:{objection.taxonomy}:{target_identity}")
        normalized_reason = self._normalize_objection_reason(objection.reason)
        if normalized_reason:
            keys.add(f"reason-target:{target_identity}:{normalized_reason[:140]}")
        normalized_id = re.sub(r"(_patch)?\d+$", "", objection.objection_id.lower())
        normalized_id = re.sub(r"[_-]+$", "", normalized_id)
        if normalized_id:
            keys.add(f"id-family:{normalized_id}")
        return keys

    def _objection_resolution_root_cause_key(self, objection: Objection) -> str:
        if objection.taxonomy.startswith("numeric_sanity_"):
            return f"root_cause:{objection.taxonomy}"
        target_path = str(objection.target_path or objection.target.field_path or "")
        text = " ".join(
            [
                objection.taxonomy,
                target_path,
                objection.reason,
            ]
        ).lower()
        normalized = self._normalize_objection_reason(text)
        if any(marker in text for marker in ("http 432", "tavily", "配额", "quota")):
            return "root_cause:evidence_acquisition_gap"
        if any(marker in text for marker in ("fy2026 q2", "fy2026 q3", "财年季度", "财季")):
            return "root_cause:fiscal_quarter_label"
        if any(marker in text for marker in ("未来催化剂", "已发生", "已发布", "时间错位")):
            return "root_cause:temporal_event_state"
        if any(marker in text for marker in ("$33.5b", "$36b", "33.5b", "36b", "指引数字")):
            return "root_cause:guidance_value_conflict"
        if any(marker in text for marker in ("hbm4", "6月5日", "-13.25")):
            return "root_cause:hbm4_price_reaction_contradiction"
        if any(marker in text for marker in ("3个月", "90日", "90%", "169%", "217%")):
            return "root_cause:market_return_magnitude"
        if any(
            marker in text
            for marker in (
                "price reaction",
                "price-reaction",
                "ohlcv",
                "market-data evidence",
                "unknown",
                "evidence gap",
            )
        ):
            return "root_cause:price_reaction_evidence_gap"
        if any(marker in text for marker in ("p/e", "forward p/e", "估值", "市值")):
            return "root_cause:valuation_or_market_cap_precision"
        if "event_monitoring_direction" in target_path:
            return "root_cause:event_monitoring_consistency"
        return f"root_cause:other:{normalized[:80]}"

    def _normalize_objection_reason(self, reason: str) -> str:
        text = re.sub(r"\s+", " ", reason.lower()).strip()
        text = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", text)
        return " ".join(text.split()[:18])

    def _pending_expectation_patch_summary(self, patch: BlackboardPatch) -> dict[str, Any]:
        after = self._dict_from_model(patch.after)
        return {
            "patch_id": patch.patch_id,
            "target": patch.target.model_dump(mode="json"),
            "expectation_id": after.get("expectation_id") or patch.target.expectation_id,
            "expectation_name": self._compact_context_text(
                after.get("expectation_name"),
                limit=180,
            ),
            "direction": after.get("direction"),
            "realized_fact_count": len(self._list_from_model(after.get("realized_facts"))),
            "key_variable_count": len(self._list_from_model(after.get("key_variables"))),
            "positive_event_count": len(
                self._list_from_model(
                    self._dict_from_model(after.get("event_monitoring_direction")).get(
                        "positive_events"
                    )
                )
            ),
            "negative_event_count": len(
                self._list_from_model(
                    self._dict_from_model(after.get("event_monitoring_direction")).get(
                        "negative_events"
                    )
                )
            ),
        }

    def _compact_pending_expectation_patch(self, patch: BlackboardPatch) -> dict[str, Any]:
        after = self._dict_from_model(patch.after)
        market_view = self._dict_from_model(after.get("market_view"))
        monitoring = self._dict_from_model(after.get("event_monitoring_direction"))
        return {
            "patch_id": patch.patch_id,
            "target": patch.target.model_dump(mode="json"),
            "operation": patch.operation.value,
            "rationale": self._compact_context_text(patch.rationale, limit=260),
            "expectation_id": after.get("expectation_id") or patch.target.expectation_id,
            "expectation_name": self._compact_context_text(
                after.get("expectation_name"),
                limit=160,
            ),
            "direction": after.get("direction"),
            "why_it_matters": self._compact_context_text(
                after.get("why_it_matters"),
                limit=260,
            ),
            "market_view": {
                "text": self._compact_context_text(market_view.get("text"), limit=360),
                "summary": self._compact_context_text(market_view.get("summary"), limit=220),
                "evidence_refs": [
                    self._evidence_context_summary(ref)
                    for ref in self._list_from_model(market_view.get("evidence_refs"))[:4]
                ],
            },
            "realized_facts_summary": self._compact_context_text(
                after.get("realized_facts_summary"),
                limit=260,
            ),
            "realized_facts": [
                self._realized_fact_context_summary(item)
                for item in self._list_from_model(after.get("realized_facts"))[:4]
            ],
            "key_variables": [
                self._variable_context_summary(item)
                for item in self._list_from_model(after.get("key_variables"))[:5]
            ],
            "event_monitoring_direction": {
                "known_event_notice": self._compact_context_text(
                    monitoring.get("known_event_notice"),
                    limit=220,
                ),
                "positive_events": [
                    self._compact_context_text(item, limit=160)
                    for item in self._list_from_model(monitoring.get("positive_events"))[:4]
                ],
                "negative_events": [
                    self._compact_context_text(item, limit=160)
                    for item in self._list_from_model(monitoring.get("negative_events"))[:4]
                ],
            },
            "evidence_refs": [
                self._evidence_context_summary(ref)
                for ref in patch.evidence_refs[:4]
            ],
        }

    def _field_review_pending_patch_context(
        self,
        agent_name: AgentName,
        patches: list[BlackboardPatch],
    ) -> list[dict[str, Any]]:
        expectation_patches = [
            patch
            for patch in patches
            if patch.target.document_type is DocumentType.EXPECTATION_UNIT
        ]
        if agent_name is AgentName.O4_MARKET_TRACE:
            return [
                self._market_trace_review_pending_patch_context(patch)
                for patch in expectation_patches
            ]
        return [
            self._compact_pending_expectation_patch(patch)
            for patch in expectation_patches
        ]

    def _market_trace_review_pending_patch_context(
        self,
        patch: BlackboardPatch,
    ) -> dict[str, Any]:
        after = self._dict_from_model(patch.after)
        market_view = self._dict_from_model(after.get("market_view"))
        facts = self._list_from_model(after.get("realized_facts"))
        return {
            "review_context_scope": "market_trace",
            "patch_id": patch.patch_id,
            "target": patch.target.model_dump(mode="json"),
            "operation": patch.operation.value,
            "expectation_id": after.get("expectation_id") or patch.target.expectation_id,
            "expectation_name": self._compact_context_text(
                after.get("expectation_name"),
                limit=160,
            ),
            "direction": after.get("direction"),
            "market_view": {
                "summary": self._compact_context_text(market_view.get("summary"), limit=260),
                "price_reflection_text": self._compact_context_text(
                    market_view.get("text"),
                    limit=420,
                ),
                "evidence_refs": [
                    self._evidence_context_summary(ref)
                    for ref in self._list_from_model(market_view.get("evidence_refs"))[:4]
                ],
            },
            "realized_facts_price_reactions": [
                self._market_trace_fact_context_summary(item)
                for item in facts[:6]
            ],
            "realized_facts_summary": self._compact_context_text(
                after.get("realized_facts_summary"),
                limit=260,
            ),
            "patch_evidence_refs": [
                self._evidence_context_summary(ref)
                for ref in patch.evidence_refs[:4]
            ],
            "omitted_fields": [
                "key_variables",
                "event_monitoring_direction",
                "full_market_view_text",
                "non-price realized fact prose beyond compact summaries",
            ],
        }

    def _market_trace_fact_context_summary(self, value: Any) -> dict[str, Any]:
        item = self._dict_from_model(value)
        price_reaction = self._dict_from_model(item.get("price_reaction"))
        refs = self._dedupe_evidence_refs(
            [
                *[
                    EvidenceRef.model_validate(ref)
                    for ref in self._list_from_model(item.get("evidence_refs"))
                    if isinstance(ref, dict)
                ],
                *[
                    EvidenceRef.model_validate(ref)
                    for ref in self._list_from_model(price_reaction.get("evidence_refs"))
                    if isinstance(ref, dict)
                ],
            ]
        )
        return {
            "event_id": item.get("event_id"),
            "description": self._compact_context_text(item.get("description"), limit=220),
            "when": item.get("when"),
            "pricing_status": item.get("pricing_status")
            or item.get("pricing_assessment"),
            "price_reaction": {
                "price_change": self._compact_context_text(
                    price_reaction.get("price_change"),
                    limit=180,
                ),
                "price_pattern": self._compact_context_text(
                    price_reaction.get("price_pattern"),
                    limit=180,
                ),
                "interpretation": self._compact_context_text(
                    price_reaction.get("interpretation"),
                    limit=260,
                ),
            },
            "evidence_refs": [
                self._evidence_context_summary(ref)
                for ref in refs[:4]
            ],
        }

    def _field_review_global_research_context(
        self,
        checkpoint: WorkflowCheckpoint,
        agent_name: AgentName,
    ) -> dict[str, Any]:
        document = self._stable_global_research_document(checkpoint)
        if document is None:
            return {
                "omitted_for": WorkflowNode.REVIEW_EXPECTATION_FIELDS.value,
                "reason": "No stable GlobalResearchDocument is available.",
            }
        section_keys_by_agent = {
            AgentName.A1_DOXATLAS_AUDIT: ("market_narrative_report",),
            AgentName.C1_FUNDAMENTAL_RESEARCH: ("fundamental_report",),
            AgentName.C3_INDUSTRY_RESEARCH: ("industry_report", "macro_report"),
            AgentName.O4_MARKET_TRACE: ("market_trace_report",),
        }
        sections: dict[str, Any] = {}
        for key in section_keys_by_agent.get(agent_name, ()):
            section = getattr(document, key, None)
            if isinstance(section, ResearchSection):
                sections[key] = self._field_review_section_context(section, checkpoint.ticker)
        return {
            "document_id": document.document_id,
            "ticker": document.ticker,
            "sections": sections,
            "compaction": {
                "mode": "reviewer_role_scoped_global_research_summary",
                "omitted_full_text": True,
            },
        }

    def _field_review_section_context(
        self,
        section: ResearchSection,
        ticker: str,
    ) -> dict[str, Any]:
        refs = list(section.evidence_refs)
        payload: dict[str, Any] = {
            "summary": self._compact_context_text(section.summary, limit=520),
            "author_agent": section.author_agent.value,
            "evidence_refs": [self._evidence_context_summary(ref) for ref in refs[:6]],
        }
        market_snapshot = self._market_evidence_snapshot_from_payload_refs(
            [ref.model_dump(mode="json") for ref in refs],
            ticker=ticker,
        )
        if market_snapshot is not None:
            payload["market_evidence_snapshot"] = market_snapshot
        return payload

    def _objection_resolution_objection_summary(self, objection: Objection) -> dict[str, Any]:
        return {
            "objection_id": objection.objection_id,
            "source_agent": objection.source_agent.value,
            "severity": objection.severity.value,
            "status": objection.status.value,
            "taxonomy": objection.taxonomy,
            "dedupe_hash": objection.dedupe_hash,
            "root_cause_key": self._objection_resolution_root_cause_key(objection),
            "target_path": objection.target_path,
            "merged_objection_ids": list(objection.merged_objection_ids),
            "target": objection.target.model_dump(mode="json"),
            "reason": self._compact_context_text(objection.reason, limit=520),
            "evidence_refs": [
                self._evidence_context_summary(ref) for ref in objection.evidence_refs[:6]
            ],
        }

    def _realized_fact_context_summary(self, value: Any) -> dict[str, Any]:
        item = self._dict_from_model(value)
        price_reaction = self._dict_from_model(item.get("price_reaction"))
        return {
            "event_id": item.get("event_id"),
            "description": self._compact_context_text(item.get("description"), limit=360),
            "price_reaction": {
                "price_change": self._compact_context_text(
                    price_reaction.get("price_change"),
                    limit=160,
                ),
                "price_pattern": self._compact_context_text(
                    price_reaction.get("price_pattern"),
                    limit=160,
                ),
                "interpretation": self._compact_context_text(
                    price_reaction.get("interpretation"),
                    limit=280,
                ),
            },
            "evidence_refs": [
                self._evidence_context_summary(ref)
                for ref in self._list_from_model(item.get("evidence_refs"))[:4]
            ],
        }

    def _variable_context_summary(self, value: Any) -> dict[str, Any]:
        item = self._dict_from_model(value)
        return {
            "variable_id": item.get("variable_id"),
            "name": self._compact_context_text(item.get("name"), limit=180),
            "current_status": self._compact_context_text(
                item.get("current_status"),
                limit=320,
            ),
            "certainty": self._compact_context_text(item.get("certainty"), limit=120),
            "evidence_refs": [
                self._evidence_context_summary(ref)
                for ref in self._list_from_model(item.get("evidence_refs"))[:4]
            ],
        }

    def _evidence_context_summary(self, value: Any) -> dict[str, Any]:
        item = self._dict_from_model(value)
        return {
            "evidence_id": item.get("evidence_id"),
            "source_type": item.get("source_type"),
            "source_id": item.get("source_id"),
            "title": self._compact_context_text(item.get("title"), limit=220),
            "summary": self._compact_context_text(item.get("summary"), limit=360),
            "citation_scope": item.get("citation_scope"),
            "confidence": item.get("confidence"),
        }

    def _dict_from_model(self, value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        model_dump = getattr(value, "model_dump", None)
        if callable(model_dump):
            dumped = model_dump(mode="json")
            if isinstance(dumped, dict):
                return cast(dict[str, Any], dumped)
        return {}

    def _list_from_model(self, value: Any) -> list[Any]:
        return value if isinstance(value, list) else []

    def _compact_context_text(self, value: Any, *, limit: int) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        if len(text) <= limit:
            return text
        return text[:limit].rstrip() + "..."

    def _can_complete_a2_delegation(self, result: AgentResult) -> bool:
        if result.status is not ResultStatus.SUCCEEDED:
            return False
        structured = result.payload.get("structured")
        candidate = structured if isinstance(structured, dict) else result.payload
        try:
            retrieval = DelegatedRetrievalResult.model_validate(candidate)
        except ValueError:
            return False
        return self._validate_a2_retrieval_quality(retrieval, result)

    def _validate_a2_retrieval_quality(
        self,
        retrieval: DelegatedRetrievalResult,
        result: AgentResult,
    ) -> bool:
        if not retrieval.can_complete_delegation:
            return False
        if retrieval.claim_verdict in {"inconclusive", "unknown", "not_applicable"}:
            return False
        if not retrieval.query_log:
            return False
        if retrieval.confidence < 0.35:
            return False
        if not (retrieval.evidence_refs or retrieval.source_refs or result.evidence_refs):
            return False
        if _looks_like_raw_search_dump(retrieval.answer) or _looks_like_raw_search_dump(
            retrieval.retrieval_summary
        ):
            return False
        declared_tools = {
            str(ref.retrieval_metadata.get("tool_name"))
            for ref in [*retrieval.evidence_refs, *retrieval.source_refs]
            if isinstance(ref.retrieval_metadata.get("tool_name"), str)
        }
        declared_tools.update({item.tool_name for item in retrieval.tool_calls})
        actual_tools = {
            item.tool_name for item in [*result.tool_calls, *retrieval.tool_calls]
            if item.status is ResultStatus.SUCCEEDED
        }
        if declared_tools and not declared_tools.issubset(actual_tools):
            return False
        return True

    def _delegation_completion_summary(self, result: AgentResult) -> str:
        structured = result.payload.get("structured")
        candidate = structured if isinstance(structured, dict) else result.payload
        summary = candidate.get("retrieval_summary") if isinstance(candidate, dict) else None
        if isinstance(summary, str) and summary:
            return summary
        return "A2 检索验证返回了足够证据。"

    def _complete_o1_revision_delegations(
        self,
        checkpoint: WorkflowCheckpoint,
        result: AgentResult | None = None,
    ) -> None:
        run = self.blackboard.get_run(checkpoint.run_id)
        if any(objection.is_unresolved for objection in run.objections):
            return
        summary = self._o1_revision_completion_summary(result)
        for delegation in run.delegations:
            if (
                delegation.is_blocking
                and delegation.target_agent is AgentName.O1_EXPECTATION_OWNER
            ):
                self.blackboard.complete_delegation(
                    checkpoint.run_id,
                    delegation.delegation_id,
                    summary,
                )

    def _o1_revision_completion_summary(self, result: AgentResult | None) -> str:
        if result is not None:
            payload = result.payload.get("structured")
            if not isinstance(payload, dict):
                payload = result.payload
            for key in (
                "resolution_summary",
                "rationale",
                "completion_reason",
                "summary",
            ):
                value = payload.get(key) if isinstance(payload, dict) else None
                if isinstance(value, str) and value.strip():
                    return value
        return "O1 已完成请求的预期修订，相关异议均已处理。"

    def _objection_resolution_note_text(self, value: Any, *, decision: str) -> str:
        text = str(value or "").strip()
        if text and self._has_chinese_text(text):
            return text
        if decision == "resolved":
            return "O1 已解决该 objection。"
        if decision == "accepted":
            return "O1 已接受该 objection，并返回修订后的 expectation patch。"
        if decision == "partially_accepted":
            return "O1 已部分接受该 objection，并保留需要后续复核的不确定性。"
        if decision == "rejected":
            return "O1 已基于现有证据反驳该 objection。"
        return "O1 已处理该 objection。"

    def _localized_changed_paths(self, paths: Iterable[str]) -> list[str]:
        return [self._localized_changed_path(path) for path in paths]

    def _localized_changed_path(self, path: str) -> str:
        text = str(path)

        def replace(match: re.Match[str]) -> str:
            action = match.group("action")
            detail = match.group("detail")
            action_text = {
                "removed": "移除",
                "added": "新增",
                "populated with": "补全",
                "replaced": "替换",
            }[action]
            detail = (
                detail.replace("specific events", "具体事件")
                .replace("specific variables", "具体变量")
                .replace("events", "个事件")
                .replace("variables", "个变量")
                .replace("evidence_gap source", "evidence_gap 溯源")
                .replace("source", "溯源")
            )
            return f"（{action_text} {detail}）"

        return re.sub(
            r"\((?P<action>removed|added|populated with|replaced) (?P<detail>[^)]+)\)",
            replace,
            text,
        )
