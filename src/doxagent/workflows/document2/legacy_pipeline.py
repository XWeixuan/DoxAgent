# ruff: noqa: F403,F405
"""Behavior-preserving mixin extracted from initialization.py."""

from doxagent.workflows.document2.contracts import (
    Document2ReviewFinding,
    Document2Revision,
    Document2TransactionAudit,
    ExpectationUnitCandidate,
)
from doxagent.workflows.document2.review import (
    DOCUMENT2_REVIEW_FINDINGS_KEY,
    document2_review_findings_from_agent_result,
    review_findings_json,
    sanitize_document2_reviewer_result,
)
from doxagent.workflows.document2.transaction import (
    DOCUMENT2_CONSTRUCTION_TRANSACTION_AUDITS_KEY,
    document2_construction_transaction_audit,
    validate_construction_resolution_transaction,
)
from doxagent.workflows.initialization.shared import *

_DOCUMENT2_PENDING_REVISIONS_KEY = "document2_pending_revisions"
_DOCUMENT2_REVIEW_STATE_KEY = "document2_review_state"


class Document2LegacyPipelineMixin:
    def _assert_no_proposed_patches(
        self,
        result: AgentResult,
        node: WorkflowNode,
        message: str,
    ) -> None:
        if result.proposed_patches:
            raise WorkflowContractError(
                f"{node.value} forbids proposed_patches: {message}"
            )

    def _write_document2_reviewer_acceptance_warning(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
        *,
        agent_name: AgentName,
        expected_schema: str,
        warnings: list[dict[str, Any]],
        result: AgentResult | None = None,
    ) -> None:
        if not warnings:
            return
        self.blackboard.add_working_memory_entry(
            checkpoint.run_id,
            author_agent=agent_name,
            content_type="document2_reviewer_acceptance_warning",
            payload={
                "workflow_node": node.value,
                "agent_name": agent_name.value,
                "expected_schema": expected_schema,
                "warning_count": len(warnings),
                "warnings": warnings,
                "task_id": result.task_id if result else None,
                "result_status": result.status.value if result else "error",
            },
            evidence_refs=result.evidence_refs if result else [],
        )

    def _accepted_document2_reviewer_result(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
        result: AgentResult,
        *,
        expected_schema: str,
    ) -> AgentResult | None:
        accepted, warnings = sanitize_document2_reviewer_result(
            result,
            expected_schema=expected_schema,
        )
        self._write_document2_reviewer_acceptance_warning(
            checkpoint,
            node,
            agent_name=result.agent_name,
            expected_schema=expected_schema,
            warnings=warnings,
            result=result,
        )
        if accepted is None:
            return None
        try:
            self.output_validator.validate(accepted.payload, expected_schema)
        except WorkflowContractError as exc:
            self._write_document2_reviewer_acceptance_warning(
                checkpoint,
                node,
                agent_name=accepted.agent_name,
                expected_schema=expected_schema,
                warnings=[
                    {
                        "issue": "reviewer_result_schema_rejected",
                        "severity": "fatal",
                        "message": str(exc),
                    }
                ],
                result=accepted,
            )
            return None
        return accepted

    def _o1_expectation_generation_context(self) -> dict[str, Any]:
        return {
            "required_tool_names": ["doxa_get_narrative_report"],
            "tool_requirements": [
                {
                    "tool_name": "doxa_get_narrative_report",
                    "required": True,
                    "purpose": (
                        "Required DoxAtlas narrative evidence for expectation-unit construction."
                    ),
                    "gap_policy": (
                        "If unavailable, continue with patches but state the DoxAtlas "
                        "narrative evidence gap in unknowns or rationale."
                    ),
                }
            ],
        }

    def _review_expectation_construction(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
    ) -> WorkflowCheckpoint:
        shells = self._expectation_shells_from_checkpoint(checkpoint)
        if not shells:
            raise WorkflowContractError(
                "ReviewExpectationConstruction requires expectation shells."
            )
        allowed_tools = self._a1_allowed_tools_for_node(node)
        result = self._run_agent(
            checkpoint,
            node,
            AgentName.A1_DOXATLAS_AUDIT,
            TaskType.REVIEW_EXPECTATION_FIELD,
            "DoxAtlasAuditResult",
            extra_context={
                "review_scope": ["expectation_name", "direction", "market_view"],
                "review_instruction": (
                    "Audit construction-phase expectation shells only. Check that "
                    "expectation name, direction, and market view are supported by "
                    "DoxAtlas evidence. Do not review detail fields in this node. "
                    "For DoxAtlas proposition tools, never pass ticker or bare "
                    "narrative_code; use DoxAtlas run_id+narrative_code+event_code, "
                    "narrative_id+event_code, narrative_event_id, or proposition_id. "
                    "For ignored propositions, bare narrative_code is also invalid; "
                    "use run_id+narrative_code or a narrower event scope. If valid "
                    "scope is unavailable but narrative evidence is sufficient for a "
                    "construction-level audit, return DoxAtlasAuditResult with a "
                    "warning instead of retrying invalid tool calls."
                ),
                "expectation_shells": [shell.model_dump(mode="json") for shell in shells],
                "doxatlas_scope_guardrails": {
                    "doxa_query_propositions": (
                        "requires run_id+narrative_code+event_code, "
                        "narrative_id+event_code, narrative_event_id, or proposition_id; "
                        "ticker and bare narrative_code are invalid"
                    ),
                    "doxa_get_ignored_propositions": (
                        "requires run_id, run_id+narrative_code, "
                        "run_id+narrative_code+event_code, narrative_id, "
                        "or narrative_event_id; ticker and bare narrative_code are invalid"
                    ),
                    "non_retryable_scope_error_policy": (
                        "after a non-retryable scope validation error, finalize from "
                        "available narrative/analysis evidence with explicit data gaps "
                        "instead of exhausting ReAct steps"
                    ),
                },
                "tool_requirements": [
                    {
                        "tool_name": tool_name,
                        "required": False,
                        "purpose": self._a1_tool_purpose(tool_name, node),
                    }
                    for tool_name in allowed_tools
                ],
                "required_tool_names": [],
            },
            validate_output=False,
        )
        accepted_result = self._accepted_document2_reviewer_result(
            checkpoint,
            node,
            result,
            expected_schema="DoxAtlasAuditResult",
        )
        if accepted_result is None:
            raise WorkflowContractError(
                "ReviewExpectationConstruction had no usable A1 reviewer output."
            )
        result = accepted_result
        self._write_working_memory(checkpoint, result, "a1_expectation_construction_review")
        self._validate_agent_success(result, node, require_patches=False)
        self._assert_no_proposed_patches(
            result,
            node,
            "A1 construction review may raise objections or delegations only.",
        )
        for objection in result.objections:
            self.blackboard.create_objection(
                checkpoint.run_id,
                self._objection_with_evidence_fallback(objection, result),
            )
        for delegation in result.delegations:
            self.blackboard.create_delegation(checkpoint.run_id, delegation)
        return self._mark_completed(
            checkpoint,
            node,
            metadata=self._agent_metadata(node, [result]),
        )

    def _resolve_expectation_construction(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
    ) -> WorkflowCheckpoint:
        results: list[AgentResult] = []
        for delegation in self.blackboard.list_blocking_delegations(
            checkpoint.run_id,
            target_agent=AgentName.A2_FACT_CHECK,
        ):
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

        unresolved = self.blackboard.list_unresolved_objections(checkpoint.run_id)
        if not unresolved:
            return self._mark_completed(
                checkpoint,
                node,
                metadata=self._agent_metadata(node, results) if results else None,
            )
        shells = self._expectation_shells_from_checkpoint(checkpoint)
        if not shells:
            raise WorkflowContractError(
                "ResolveExpectationConstruction requires expectation shells."
            )
        result = self._run_agent(
            checkpoint,
            node,
            AgentName.O1_EXPECTATION_OWNER,
            TaskType.GENERATE_EXPECTATION_UNIT,
            "ExpectationShellConstructionResult",
            extra_context={
                "resolution_request": (
                    "Resolve A1 construction-review objections by revising expectation "
                    "shells only. Return ExpectationShellConstructionResult. Do not "
                    "return BlackboardPatch, proposed_patches, full expectation_unit "
                    "documents, realized_facts, key_variables, or event monitoring fields."
                ),
                "internal_task_skill_ids": ["expectation-construction"],
                "expectation_shells": [shell.model_dump(mode="json") for shell in shells],
                "unresolved_objections": [
                    objection.model_dump(mode="json") for objection in unresolved
                ],
                "required_tool_names": ["doxa_get_narrative_report"],
                "tool_requirements": [
                    {
                        "tool_name": "doxa_get_narrative_report",
                        "required": True,
                        "purpose": "Re-check narrative evidence before revising shells.",
                        "gap_policy": (
                            "If unavailable, revise shells using current context and list "
                            "the missing DoxAtlas narrative evidence in unknowns."
                        ),
                    }
                ],
            },
        )
        self._validate_agent_success(result, node, require_patches=False)
        self._assert_no_proposed_patches(
            result,
            node,
            "O1 construction resolver must return revised shells, not BlackboardPatch.",
        )
        result = self._ensure_o1_narrative_tool_evidence(checkpoint, result, node)
        self._write_working_memory(checkpoint, result, "expectation_construction_resolution")
        self._validate_o1_narrative_tool_gap(result, node)
        revised = self._validate_expectation_shells(checkpoint.ticker, result)
        audit = self._apply_document2_construction_resolution_transaction(
            checkpoint,
            previous_shells=shells,
            revised=revised,
            unresolved_objections=unresolved,
        )
        results.append(result)
        return self._mark_completed(
            checkpoint,
            node,
            metadata=self._agent_metadata(node, results)
            | {
                "expectation_shells": [
                    shell.model_dump(mode="json") for shell in revised.shells
                ],
                DOCUMENT2_CONSTRUCTION_TRANSACTION_AUDITS_KEY: [
                    audit.model_dump(mode="json")
                ],
            },
        )

    def _apply_document2_construction_resolution_transaction(
        self,
        checkpoint: WorkflowCheckpoint,
        *,
        previous_shells: list[ExpectationShell],
        revised: ExpectationShellConstructionResult,
        unresolved_objections: list[Objection],
    ) -> Document2TransactionAudit:
        try:
            notes, changed_fields = validate_construction_resolution_transaction(
                previous_shells=previous_shells,
                revised=revised,
                unresolved_objections=unresolved_objections,
            )
        except ValueError as exc:
            audit = document2_construction_transaction_audit(
                revised=revised,
                status="rejected",
                retained_objection_ids=[
                    objection.objection_id for objection in unresolved_objections
                ],
                notes=[str(exc)],
            )
            self._record_document2_construction_transaction_audit(checkpoint, audit)
            raise WorkflowContractError(
                f"Document2 construction transaction rejected: {exc}"
            ) from exc

        closed_ids: list[str] = []
        for objection in unresolved_objections:
            self.blackboard.resolve_objection(
                checkpoint.run_id,
                objection.objection_id,
                "Document2 construction transaction validated revised shells.",
                changed_paths=["expectation_shells"],
                evidence_refs=list(revised.evidence_refs),
            )
            closed_ids.append(objection.objection_id)
        audit = document2_construction_transaction_audit(
            revised=revised,
            status="accepted",
            closed_objection_ids=closed_ids,
            changed_fields=changed_fields,
            notes=notes,
        )
        self._record_document2_construction_transaction_audit(checkpoint, audit)
        return audit

    def _record_document2_construction_transaction_audit(
        self,
        checkpoint: WorkflowCheckpoint,
        audit: Document2TransactionAudit,
    ) -> None:
        self.blackboard.add_working_memory_entry(
            checkpoint.run_id,
            author_agent=AgentName.SYSTEM,
            content_type="document2_construction_transaction_audit",
            payload={
                "status": audit.status,
                "audit": audit.model_dump(mode="json"),
            },
            evidence_refs=[],
        )

    def _generate_expectation_details(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
    ) -> WorkflowCheckpoint:
        shells = self._expectation_shells_from_checkpoint(checkpoint)
        if not shells:
            raise WorkflowContractError("GenerateExpectationDetails requires expectation shells.")
        results: list[AgentResult] = []
        legacy_patches: list[BlackboardPatch] = []
        current = checkpoint
        jobs: list[_ParallelAgentJob] = []
        cached_results: dict[int, AgentResult] = {}
        for order, shell in enumerate(shells):
            cache_key = self._expectation_detail_cache_key(order, shell)
            current = self._recover_stale_agent_dispatch(
                current,
                node,
                AgentName.O1_EXPECTATION_OWNER,
                shell.expectation_id,
                cache_key=cache_key,
            )
            cached = self._cached_workflow_agent_result(
                current,
                node,
                AgentName.O1_EXPECTATION_OWNER,
                cache_key=cache_key,
            )
            if cached is not None:
                cached_results[order] = cached
                current = self._record_expectation_detail_status(
                    current,
                    node,
                    order,
                    shell,
                    cache_key=cache_key,
                    status="cached_completed",
                )
                continue
            current = self._mark_agent_dispatch(
                current,
                node,
                AgentName.O1_EXPECTATION_OWNER,
                status="running",
                section_key=shell.expectation_id,
                cache_key=cache_key,
            )
            current = self._record_expectation_detail_status(
                current,
                node,
                order,
                shell,
                cache_key=cache_key,
                status="running",
            )
            jobs.append(
                _ParallelAgentJob(
                    order=order,
                    agent_name=AgentName.O1_EXPECTATION_OWNER,
                    task_type=TaskType.GENERATE_EXPECTATION_DETAIL,
                    output_schema="ExpectationDetailCandidateResult",
                    content_type="expectation_detail_candidate_result",
                    section_key=shell.expectation_id,
                    cache_key=cache_key,
                    extra_context=self._expectation_detail_context(shell),
                )
            )
        if jobs:
            self.checkpoint_repository.save_checkpoint(current)

        accepted_results: dict[int, AgentResult] = {}
        accepted_errors: dict[int, Exception] = {}
        timed_out_orders: dict[int, Exception] = {}

        def accept_detail_result(
            order: int,
            shell: ExpectationShell,
            result: AgentResult,
            *,
            cached: bool,
            retry_attempt: int = 0,
        ) -> None:
            nonlocal current
            cache_key = self._expectation_detail_cache_key(order, shell)
            try:
                self._validate_agent_success(result, node, require_patches=False)
                self._assert_no_proposed_patches(
                    result,
                    node,
                    "O1 detail generation must return ExpectationDetailCandidateResult.",
                )
                result = self._ensure_o1_narrative_tool_evidence(current, result, node)
                if not cached:
                    self._write_working_memory(
                        current,
                        result,
                        "expectation_detail_candidate_result",
                    )
                self._validate_o1_narrative_tool_gap(result, node)
                candidate = self._expectation_unit_candidate_from_detail_result(
                    current.ticker,
                    shell,
                    result,
                )
                revision = self._document2_revision_from_detail_candidate(candidate)
                legacy_patch = self._legacy_pending_patch_from_document2_revision(
                    current,
                    revision,
                )
                self._validate_patch_contract(
                    legacy_patch,
                    WorkflowNode.GENERATE_EXPECTATION_DETAILS,
                )
                current = self._record_document2_pending_revision(
                    current,
                    node,
                    order,
                    shell,
                    candidate,
                    revision,
                    legacy_patch,
                )
            except WorkflowContractError as exc:
                current = self._mark_agent_dispatch(
                    current,
                    node,
                    AgentName.O1_EXPECTATION_OWNER,
                    status="failed",
                    section_key=shell.expectation_id,
                    cache_key=cache_key,
                    error_message=str(exc),
                )
                current = self._record_expectation_detail_status(
                    current,
                    node,
                    order,
                    shell,
                    cache_key=cache_key,
                    status="failed",
                    error_message=str(exc),
                )
                self._save_parallel_outcome_checkpoint(current)
                accepted_errors[order] = exc
                return
            accepted_results[order] = result
            accepted_errors.pop(order, None)
            revision_entry = self._document2_pending_revision_entry(current, shell)
            legacy_patch_ids = (
                [str(revision_entry["legacy_patch_id"])]
                if isinstance(revision_entry, dict)
                and isinstance(revision_entry.get("legacy_patch_id"), str)
                else []
            )
            current = self._record_expectation_detail_status(
                current,
                node,
                order,
                shell,
                cache_key=cache_key,
                status="cached_completed" if cached else "completed",
                retry_attempt=retry_attempt,
                patch_ids=legacy_patch_ids,
                candidate_id=str(revision_entry.get("candidate_id"))
                if isinstance(revision_entry, dict)
                and isinstance(revision_entry.get("candidate_id"), str)
                else None,
                revision_id=str(revision_entry.get("revision_id"))
                if isinstance(revision_entry, dict)
                and isinstance(revision_entry.get("revision_id"), str)
                else None,
                legacy_patch_ids=legacy_patch_ids,
            )
            if not cached:
                current = self._store_workflow_agent_result(
                    current,
                    node,
                    AgentName.O1_EXPECTATION_OWNER,
                    shell.expectation_id,
                    result,
                    cache_key=cache_key,
                )
                self._save_parallel_outcome_checkpoint(current)

        def cache_expectation_detail_outcome(outcome: _ParallelAgentOutcome) -> None:
            nonlocal current
            order = outcome.job.order
            if order < 0 or order >= len(shells):
                return
            shell = shells[order]
            cache_key = self._expectation_detail_cache_key(order, shell)
            if outcome.error is not None:
                status = (
                    "timed_out"
                    if self._is_parallel_agent_timeout_error(outcome.error)
                    else "failed"
                )
                current = self._mark_agent_dispatch(
                    current,
                    node,
                    AgentName.O1_EXPECTATION_OWNER,
                    status="failed",
                    section_key=shell.expectation_id,
                    cache_key=cache_key,
                    error_message=str(outcome.error),
                )
                current = self._record_expectation_detail_status(
                    current,
                    node,
                    order,
                    shell,
                    cache_key=cache_key,
                    status=status,
                    error_message=str(outcome.error),
                )
                self._save_parallel_outcome_checkpoint(current)
                accepted_errors[order] = outcome.error
                return
            if outcome.result is None:
                return
            if self._is_expectation_detail_model_timeout_result(outcome.result):
                timeout_error = self._expectation_detail_model_timeout_error(
                    node,
                    outcome.result,
                )
                current = self._mark_agent_dispatch(
                    current,
                    node,
                    AgentName.O1_EXPECTATION_OWNER,
                    status="failed",
                    section_key=shell.expectation_id,
                    cache_key=cache_key,
                    error_message=str(timeout_error),
                )
                current = self._record_expectation_detail_status(
                    current,
                    node,
                    order,
                    shell,
                    cache_key=cache_key,
                    status="timed_out",
                    error_message=str(timeout_error),
                )
                self._save_parallel_outcome_checkpoint(current)
                accepted_errors[order] = timeout_error
                timed_out_orders[order] = timeout_error
                return
            retry_attempt = (
                1
                if isinstance(outcome.job.extra_context.get("detail_recovery_retry"), dict)
                else 0
            )
            accept_detail_result(
                order,
                shell,
                outcome.result,
                cached=False,
                retry_attempt=retry_attempt,
            )

        outcomes_by_order = {
            outcome.job.order: outcome
            for outcome in self._run_agent_jobs_concurrently(
                current,
                node,
                jobs,
                on_outcome=cache_expectation_detail_outcome,
            )
        }
        first_error: Exception | None = None
        for order, shell in enumerate(shells):
            cached = cached_results.get(order)
            outcome = outcomes_by_order.get(order)
            if cached is not None:
                accept_detail_result(order, shell, cached, cached=True)
            elif outcome is None:
                accepted_errors[order] = WorkflowContractError(
                    f"{node.value}/{shell.expectation_id} did not return a parallel outcome."
                )
            elif outcome.error is not None:
                if self._is_parallel_agent_timeout_error(outcome.error):
                    timed_out_orders[order] = outcome.error
                elif order not in accepted_results:
                    accepted_errors[order] = outcome.error
            elif outcome.result is not None and self._is_expectation_detail_model_timeout_result(
                outcome.result
            ):
                timeout_error = self._expectation_detail_model_timeout_error(
                    node,
                    outcome.result,
                )
                timed_out_orders[order] = timeout_error
                accepted_errors.setdefault(order, timeout_error)
            elif order in accepted_errors:
                continue
            elif order in accepted_results:
                continue
            else:
                if outcome.result is not None:
                    accept_detail_result(order, shell, outcome.result, cached=False)
                else:
                    accepted_errors[order] = WorkflowContractError(
                        f"{node.value}/{shell.expectation_id} returned no result."
                    )

        for order, timeout_error in list(timed_out_orders.items()):
            if order in accepted_results:
                continue
            current = self._prepare_expectation_detail_timeout_retry(
                current,
                node,
                order,
                shells[order],
                timeout_error,
            )
            current = self._run_expectation_detail_recovery_retry(
                current,
                node,
                order,
                shells[order],
                timeout_error,
                on_outcome=cache_expectation_detail_outcome,
            )
            if order not in accepted_results and order not in accepted_errors:
                accepted_errors[order] = timeout_error

        for order, _shell in enumerate(shells):
            result = accepted_results.get(order)
            if result is None:
                first_error = first_error or accepted_errors.get(order)
                continue

            revision_entry = self._document2_pending_revision_entry(current, _shell)
            if isinstance(revision_entry, dict):
                legacy_patch = revision_entry.get("legacy_patch")
                if isinstance(legacy_patch, dict):
                    legacy_patches.append(BlackboardPatch.model_validate(legacy_patch))
            results.append(result)
        if first_error is not None:
            raise first_error
        return self._mark_completed(
            current,
            node,
            pending_patches=current.pending_patches + legacy_patches,
            metadata=self._agent_metadata(node, results)
            | {
                "document2_detail_state": {
                    "primary_state": _DOCUMENT2_PENDING_REVISIONS_KEY,
                    "revision_count": len(legacy_patches),
                    "legacy_pending_patch_count": len(legacy_patches),
                }
            },
        )

    def _expectation_detail_context(
        self,
        shell: ExpectationShell,
        *,
        recovery_error: str | None = None,
    ) -> dict[str, Any]:
        instruction = (
            "Complete exactly one expectation unit from this shell. Preserve "
            "I/II fields and fill realized facts, key variables/current status, "
            "and event prediction or monitoring direction. "
            "Use at most one doxa_get_narrative_report call for this shell; "
            "after the call succeeds, fails with a non-retryable validation error, "
            "or returns limited coverage, finish the ExpectationDetailCandidateResult "
            "from the shell plus compact upstream context and record any evidence "
            "gaps in unknowns or rationale. Do not return BlackboardPatch or "
            "proposed_patches in this node. "
            "event_monitoring_direction must contain known_event_notice plus "
            "positive_events and negative_events as lists of concrete string "
            "triggers; do not use generic deployment/commercialization "
            "placeholders, known_upcoming_events, or dict/object event items."
        )
        budget: dict[str, Any] = {
            "max_successful_doxa_get_narrative_report_calls": 1,
            "evidence_gap_policy": (
                "Record unknowns and rationale when the bounded narrative lookup "
                "cannot supply enough coverage; do not retry low-value tool calls."
            ),
        }
        context: dict[str, Any] = {
            "expectation_shell": shell.model_dump(mode="json"),
            "detail_instruction": instruction,
            "detail_completion_budget": budget,
            "required_tool_names": ["doxa_get_narrative_report"],
            "tool_requirements": [
                {
                    "tool_name": "doxa_get_narrative_report",
                    "required": True,
                    "purpose": "Narrative evidence for expectation detail completion.",
                }
            ],
        }
        if recovery_error:
            context["detail_instruction"] = (
                "Recovery retry after a timed-out expectation detail worker. "
                "Use compact context only, avoid repeating low-value retrieval loops, "
                "and finish one schema-valid ExpectationDetailCandidateResult for this "
                "exact expectation shell. "
                + instruction
            )
            context["detail_completion_budget"] = budget | {
                "recovery_retry": True,
                "previous_timeout": recovery_error,
                "retry_policy": (
                    "At most one narrative-report attempt; if unavailable or already "
                    "insufficient, finish from shell/context with explicit unknowns."
                ),
            }
            context["detail_recovery_retry"] = {
                "retry_attempt": 1,
                "previous_error": recovery_error,
                "previous_status": "timed_out",
            }
        return context

    def _is_expectation_detail_model_timeout_result(self, result: AgentResult) -> bool:
        if result.status is not ResultStatus.FAILED or result.error is None:
            return False
        if result.error.code == "model_request_timeout":
            return True
        gateway_error = result.error.details.get("gateway_error")
        return (
            isinstance(gateway_error, dict)
            and gateway_error.get("code") == "model_request_timeout"
        )

    def _expectation_detail_model_timeout_error(
        self,
        node: WorkflowNode,
        result: AgentResult,
    ) -> WorkflowContractError:
        message = result.error.message if result.error is not None else "model_request_timeout"
        return WorkflowContractError(f"{node.value} agent result failed: {message}")

    def _prepare_expectation_detail_timeout_retry(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
        order: int,
        shell: ExpectationShell,
        timeout_error: Exception,
    ) -> WorkflowCheckpoint:
        cache_key = self._expectation_detail_cache_key(order, shell)
        current = self._record_expectation_detail_status(
            checkpoint,
            node,
            order,
            shell,
            cache_key=cache_key,
            status="retrying",
            error_message=str(timeout_error),
            retry_attempt=1,
        )
        current = self._mark_agent_dispatch(
            current,
            node,
            AgentName.O1_EXPECTATION_OWNER,
            status="running",
            section_key=shell.expectation_id,
            cache_key=cache_key,
            error_message="recovery retry after parallel timeout",
        )
        self._save_parallel_outcome_checkpoint(current)
        return current

    def _run_expectation_detail_recovery_retry(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
        order: int,
        shell: ExpectationShell,
        timeout_error: Exception,
        *,
        on_outcome: Callable[[_ParallelAgentOutcome], None],
    ) -> WorkflowCheckpoint:
        cache_key = self._expectation_detail_cache_key(order, shell)
        retry_job = _ParallelAgentJob(
            order=order,
            agent_name=AgentName.O1_EXPECTATION_OWNER,
            task_type=TaskType.GENERATE_EXPECTATION_DETAIL,
            output_schema="ExpectationDetailCandidateResult",
            content_type="expectation_detail_candidate_result",
            section_key=shell.expectation_id,
            cache_key=cache_key,
            extra_context=self._expectation_detail_context(
                shell,
                recovery_error=str(timeout_error),
            ),
        )
        outcomes = self._run_agent_jobs_concurrently(
            checkpoint,
            node,
            [retry_job],
            on_outcome=on_outcome,
            timeout_seconds=self._expectation_detail_recovery_timeout_seconds(),
        )
        return checkpoint if not outcomes else self._latest_checkpoint_or(checkpoint)

    def _expectation_detail_recovery_timeout_seconds(self) -> float:
        return min(
            float(self.settings.workflow_agent_stale_after_seconds),
            max(5.0, float(self.settings.model_request_timeout_seconds) * 1.5),
        )

    def _record_expectation_detail_status(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
        order: int,
        shell: ExpectationShell,
        *,
        cache_key: str,
        status: str,
        error_message: str | None = None,
        retry_attempt: int = 0,
        patch_ids: list[str] | None = None,
        candidate_id: str | None = None,
        revision_id: str | None = None,
        legacy_patch_ids: list[str] | None = None,
    ) -> WorkflowCheckpoint:
        raw_status = checkpoint.metadata.get(_EXPECTATION_DETAIL_STATUS_KEY)
        statuses = dict(raw_status) if isinstance(raw_status, dict) else {}
        previous = statuses.get(shell.expectation_id)
        history: list[dict[str, Any]] = []
        if isinstance(previous, dict):
            history = [
                item for item in previous.get("history", []) if isinstance(item, dict)
            ][-5:]
            history.append(
                {
                    "status": previous.get("status"),
                    "updated_at": previous.get("updated_at"),
                    "error_message": previous.get("error_message"),
                    "retry_attempt": previous.get("retry_attempt", 0),
                }
            )
        entry: dict[str, Any] = {
            "run_id": checkpoint.run_id,
            "workflow_node": node.value,
            "agent_name": AgentName.O1_EXPECTATION_OWNER.value,
            "order": order,
            "expectation_id": shell.expectation_id,
            "expectation_name": shell.expectation_name,
            "cache_key": cache_key,
            "status": status,
            "retry_attempt": retry_attempt,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        if error_message:
            entry["error_message"] = error_message
        if patch_ids is not None:
            entry["patch_ids"] = patch_ids
        if candidate_id is not None:
            entry["candidate_id"] = candidate_id
        if revision_id is not None:
            entry["revision_id"] = revision_id
        if legacy_patch_ids is not None:
            entry["legacy_patch_ids"] = legacy_patch_ids
        if history:
            entry["history"] = history[-5:]
        statuses[shell.expectation_id] = entry
        return checkpoint.model_copy(
            update={
                "metadata": checkpoint.metadata
                | {_EXPECTATION_DETAIL_STATUS_KEY: statuses}
            },
            deep=True,
        )

    def _expectation_detail_cache_key(
        self,
        order: int,
        shell: ExpectationShell,
    ) -> str:
        return f"expectation_detail:{order}:{shell.expectation_id}"

    def _expectation_shells_from_checkpoint(
        self,
        checkpoint: WorkflowCheckpoint,
    ) -> list[ExpectationShell]:
        raw = checkpoint.metadata.get("expectation_shells", [])
        if not isinstance(raw, list):
            return []
        shells: list[ExpectationShell] = []
        for item in raw:
            if isinstance(item, dict):
                shells.append(ExpectationShell.model_validate(item))
        return shells

    def _validate_expectation_shells(
        self,
        ticker: str,
        result: AgentResult,
    ) -> ExpectationShellConstructionResult:
        construction = self.output_validator.validate(
            result.payload,
            "ExpectationShellConstructionResult",
        )
        if not isinstance(construction, ExpectationShellConstructionResult):
            construction = ExpectationShellConstructionResult.model_validate(construction)
        if not construction.shells:
            raise WorkflowContractError(
                "GenerateExpectationConstruction produced no expectation shells."
            )
        if len(construction.shells) >= 4:
            raise WorkflowContractError(
                "GenerateExpectationConstruction produced too many expectations."
            )
        for shell in construction.shells:
            if shell.market_view.author_agent is not AgentName.O1_EXPECTATION_OWNER:
                raise WorkflowContractError(
                    "GenerateExpectationConstruction shell market_view must be authored by O1."
                )
            if not (shell.evidence_refs or shell.market_view.evidence_refs):
                raise WorkflowContractError(
                    "GenerateExpectationConstruction shell has no evidence."
                )
            if ticker and not shell.expectation_id:
                raise WorkflowContractError(
                    "GenerateExpectationConstruction shell missing expectation_id."
                )
        return construction

    def _expectation_unit_candidate_from_detail_result(
        self,
        ticker: str,
        shell: ExpectationShell,
        result: AgentResult,
    ) -> ExpectationUnitCandidate:
        candidate_result = self.output_validator.validate(
            result.payload,
            "ExpectationDetailCandidateResult",
        )
        if not isinstance(candidate_result, ExpectationDetailCandidateResult):
            candidate_result = ExpectationDetailCandidateResult.model_validate(
                candidate_result,
            )
        if candidate_result.delegations:
            raise WorkflowContractError(
                "GenerateExpectationDetails detail candidates must not return delegations; "
                "record evidence gaps in unknowns or rationale."
            )
        document = candidate_result.candidate
        self._validate_expectation_detail_candidate_identity(ticker, shell, document)
        return ExpectationUnitCandidate(
            document=document,
            source_agent=AgentName.O1_EXPECTATION_OWNER,
            evidence_refs=candidate_result.evidence_refs,
            unknowns=candidate_result.unknowns,
            rationale=candidate_result.rationale,
        )

    def _document2_revision_from_detail_candidate(
        self,
        candidate: ExpectationUnitCandidate,
    ) -> Document2Revision:
        return Document2Revision(
            expectation_id=candidate.document.expectation_id,
            before=None,
            after=candidate.document,
            source="candidate_generation",
            rationale=candidate.rationale,
            evidence_refs=candidate.evidence_refs,
            changed_paths=["document"],
        )

    def _legacy_pending_patch_from_document2_revision(
        self,
        checkpoint: WorkflowCheckpoint,
        revision: Document2Revision,
    ) -> BlackboardPatch:
        return BlackboardPatch(
            patch_id=new_id("patch"),
            target=BlackboardTarget(
                document_type=DocumentType.EXPECTATION_UNIT,
                ticker=checkpoint.ticker,
                expectation_id=revision.expectation_id,
                field_path="document",
            ),
            operation=PatchOperation.CREATE
            if revision.before is None
            else PatchOperation.UPDATE,
            before=revision.before.model_dump(mode="json")
            if revision.before is not None
            else None,
            after=revision.after.model_dump(mode="json"),
            rationale=(
                "Legacy pending patch derived programmatically from "
                f"Document2Revision {revision.revision_id}."
            ),
            evidence_refs=revision.evidence_refs,
            author_agent=AgentName.O1_EXPECTATION_OWNER,
            validation_status=ValidationStatus.VALID,
        )

    def _record_document2_pending_revision(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
        order: int,
        shell: ExpectationShell,
        candidate: ExpectationUnitCandidate,
        revision: Document2Revision,
        legacy_patch: BlackboardPatch,
    ) -> WorkflowCheckpoint:
        raw = checkpoint.metadata.get(_DOCUMENT2_PENDING_REVISIONS_KEY)
        entries = [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
        entries = [
            item
            for item in entries
            if item.get("expectation_id") != shell.expectation_id
        ]
        entries.append(
            {
                "workflow_node": node.value,
                "order": order,
                "expectation_id": shell.expectation_id,
                "expectation_name": shell.expectation_name,
                "candidate_id": candidate.candidate_id,
                "candidate": candidate.model_dump(mode="json"),
                "revision_id": revision.revision_id,
                "revision": revision.model_dump(mode="json"),
                "legacy_patch_id": legacy_patch.patch_id,
                "legacy_patch": legacy_patch.model_dump(mode="json"),
                "primary_state": "document2_revision",
                "legacy_pending_patch_derived": True,
                "updated_at": datetime.now(UTC).isoformat(),
            }
        )
        entries.sort(key=lambda item: int(item.get("order", 0)))
        return checkpoint.model_copy(
            update={
                "metadata": checkpoint.metadata
                | {_DOCUMENT2_PENDING_REVISIONS_KEY: entries}
            },
            deep=True,
        )

    def _document2_pending_revision_entry(
        self,
        checkpoint: WorkflowCheckpoint,
        shell: ExpectationShell,
    ) -> dict[str, Any] | None:
        raw = checkpoint.metadata.get(_DOCUMENT2_PENDING_REVISIONS_KEY)
        if not isinstance(raw, list):
            return None
        for item in raw:
            if isinstance(item, dict) and item.get("expectation_id") == shell.expectation_id:
                return item
        return None

    def _validate_expectation_detail_candidate_identity(
        self,
        ticker: str,
        shell: ExpectationShell,
        document: ExpectationUnitDocument,
    ) -> None:
        if document.ticker != ticker:
            raise WorkflowContractError("GenerateExpectationDetails produced wrong ticker.")
        if document.expectation_id != shell.expectation_id:
            raise WorkflowContractError(
                "GenerateExpectationDetails changed the construction expectation_id."
            )
        if document.expectation_name != shell.expectation_name:
            raise WorkflowContractError(
                "GenerateExpectationDetails changed the construction expectation_name."
            )
        if document.direction.value != shell.direction:
            raise WorkflowContractError(
                "GenerateExpectationDetails changed the construction direction."
            )

    def _validate_expectation_detail_result(
        self,
        ticker: str,
        shell: ExpectationShell,
        result: AgentResult,
    ) -> None:
        self.output_validator.validate(result.payload, "ExpectationDetailResult")
        expectation_patches = [
            patch
            for patch in result.proposed_patches
            if patch.target.document_type == DocumentType.EXPECTATION_UNIT
        ]
        if len(expectation_patches) != 1:
            raise WorkflowContractError(
                "GenerateExpectationDetails must produce exactly one expectation patch per shell."
            )
        patch = expectation_patches[0]
        self._validate_patch_contract(patch, WorkflowNode.GENERATE_EXPECTATION_DETAILS)
        document = ExpectationUnitDocument.model_validate(patch.after)
        if document.ticker != ticker:
            raise WorkflowContractError("GenerateExpectationDetails produced wrong ticker.")
        if document.expectation_id != shell.expectation_id:
            raise WorkflowContractError(
                "GenerateExpectationDetails changed the construction expectation_id."
            )
        if document.expectation_name != shell.expectation_name:
            raise WorkflowContractError(
                "GenerateExpectationDetails changed the construction expectation_name."
            )
        if document.direction.value != shell.direction:
            raise WorkflowContractError(
                "GenerateExpectationDetails changed the construction direction."
            )
        if patch.target.expectation_id != document.expectation_id:
            raise WorkflowContractError(
                "GenerateExpectationDetails target does not match document."
            )
        self._validate_expectation_detail_candidate_identity(ticker, shell, document)

    def _validate_expectation_detail_quality(self, document: ExpectationUnitDocument) -> None:
        if not document.realized_facts:
            raise WorkflowContractError(
                "GenerateExpectationDetails produced empty realized_facts."
            )
        if not document.key_variables:
            raise WorkflowContractError(
                "GenerateExpectationDetails produced empty key_variables."
            )
        for fact in document.realized_facts:
            if not fact.evidence_refs:
                raise WorkflowContractError(
                    "GenerateExpectationDetails realized_fact is missing evidence_refs."
                )
            if self._price_reaction_needs_escalation(fact.price_reaction):
                raise WorkflowContractError(
                    "GenerateExpectationDetails realized_fact has unknown price_reaction."
                )
        for variable in document.key_variables:
            if not variable.evidence_refs:
                raise WorkflowContractError(
                    "GenerateExpectationDetails key_variable is missing evidence_refs."
                )
        monitoring = document.event_monitoring_direction
        monitoring_events = [
            *monitoring.positive_events,
            *monitoring.negative_events,
        ]
        if not monitoring.positive_events or not monitoring.negative_events:
            raise WorkflowContractError(
                "GenerateExpectationDetails event_monitoring_direction needs positive and "
                "negative events."
            )
        if any(_is_generic_monitoring_trigger(item) for item in monitoring_events):
            raise WorkflowContractError(
                "GenerateExpectationDetails event_monitoring_direction is generic."
            )

    def _review_expectation_fields(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
    ) -> WorkflowCheckpoint:
        if not checkpoint.pending_patches:
            raise WorkflowContractError(
                "ReviewExpectationFields requires pending expectation patches."
            )
        review_common_instruction = (
            "This review has two co-equal goals: content supplementation and "
            "calibration review.\n\n"
            "First, review O1's candidate expectation field from your domain "
            "perspective. Identify material content gaps, missing data, stale "
            "assumptions, overclaims, or reasoning errors. Do not focus only on "
            "evidence sufficiency; also check whether the field's actual "
            "interpretation is correct.\n\n"
            "Second, supplement the field from your domain perspective. If the "
            "missing material can reasonably be checked with your allowed tools "
            "in one tool-call batch, you may call tools once. Tool calling is "
            "optional, not mandatory.\n\n"
            "After at most one tool-call batch, return the final review finding. "
            "Do not keep searching. Do not propose patches. Do not modify the "
            "candidate directly.\n\n"
            "When you disagree with O1, do not frame the finding only as "
            "unsupported. State what the better or corrected formulation should "
            "be, based on the data, materials, or domain reasoning available to "
            "you.\n\n"
            "Evidence refs are helpful but optional. Do not fabricate evidence "
            "refs. If no evidence ref is available, provide a concise basis "
            "summary in rationale or recommended_statement.\n\n"
            "Only complete EvidenceRef objects may appear in evidence_refs. A "
            "complete EvidenceRef must include evidence_id, source_type, "
            "source_id, title, summary, confidence, and citation_scope. If you "
            "only have a partial id, title, summary, source_id, source clue, or "
            "material clue, do not put it in evidence_refs; write it into "
            "rationale or recommended_statement instead."
        )
        specs: list[dict[str, Any]] = [
            {
                "agent_name": AgentName.A1_DOXATLAS_AUDIT,
                "schema": "DoxAtlasAuditResult",
                "content_type": "a1_doxatlas_audit",
                "review_scope": [
                    "expectation_name",
                    "direction",
                    "market_view",
                    "realized_facts",
                ],
                "instruction": (
                    "For A1, focus on DoxAtlas traceability, propositions, narrative "
                    "source ids, ignored propositions, media/social capsules, and source "
                    "support for market_view and realized_facts. If O1's statement is "
                    "incomplete or misleading from DoxAtlas traceability, provide the "
                    "corrected DoxAtlas-grounded formulation."
                ),
                "tool_requirements": [
                    {
                        "tool_name": tool_name,
                        "required": False,
                        "purpose": "Optional low-level DoxAtlas evidence for A1 audit.",
                    }
                    for tool_name in self.registry.get(
                        AgentName.A1_DOXATLAS_AUDIT
                    ).runtime.allowed_tools
                ],
            },
            {
                "agent_name": AgentName.C1_FUNDAMENTAL_RESEARCH,
                "schema": "ExpectationFieldReviewResult",
                "content_type": "c1_fundamental_review",
                "review_scope": [
                    "realized_facts",
                    "key_variables.current_state",
                    "event_monitoring_direction",
                ],
                "instruction": (
                    "For C1, focus on fundamentals, filings, financial statements, "
                    "earnings events, press releases, company quality, "
                    "valuation-sensitive facts, and current_state. If O1's statement "
                    "is incomplete or misleading, provide the corrected fundamental "
                    "formulation."
                ),
            },
            {
                "agent_name": AgentName.C3_INDUSTRY_RESEARCH,
                "schema": "ExpectationFieldReviewResult",
                "content_type": "c3_industry_review",
                "review_scope": [
                    "key_variables.current_state",
                    "event_monitoring_direction",
                ],
                "instruction": (
                    "For C3, focus on industry structure, peer context, sector trends, "
                    "competitive position, policy, and thematic evidence. If O1's "
                    "statement misses industry framing or peer-relative context, "
                    "provide the corrected industry formulation."
                ),
            },
            {
                "agent_name": AgentName.O4_MARKET_TRACE,
                "schema": "ExpectationFieldReviewResult",
                "content_type": "o4_market_trace_review",
                "review_scope": [
                    "realized_facts.price_reaction",
                    "market_view.price_reflection",
                    "market_evidence",
                ],
                "instruction": (
                    "For O4, focus on price reaction, OHLCV behavior, relative "
                    "performance, volume, technical context, and market evidence. If "
                    "O1 overstates or understates market pricing, provide the corrected "
                    "market-action formulation."
                ),
            },
        ]

        results: list[AgentResult] = []
        review_findings: list[Document2ReviewFinding] = []
        jobs: list[_ParallelAgentJob] = []
        for order, spec in enumerate(specs):
            agent_name = spec["agent_name"]
            tool_requirements = spec.get("tool_requirements", [])
            if agent_name is AgentName.A1_DOXATLAS_AUDIT:
                tool_requirements = [
                    {
                        "tool_name": tool_name,
                        "required": False,
                        "purpose": self._a1_tool_purpose(tool_name, node),
                    }
                    for tool_name in self._a1_allowed_tools_for_node(node)
                ]
            elif not tool_requirements:
                tool_requirements = [
                    {
                        "tool_name": tool_name,
                        "required": False,
                        "purpose": self._field_review_tool_purpose(
                            agent_name,
                            tool_name,
                        ),
                    }
                    for tool_name in self.registry.get(agent_name).runtime.allowed_tools
                ]
            pending_patch_context = self._field_review_pending_patch_context(
                agent_name,
                checkpoint.pending_patches,
            )
            document1_context_pack_brief = self._field_review_document1_context_pack_brief(
                checkpoint,
                agent_name,
            )
            extra_context = {
                "review_scope": spec["review_scope"],
                "review_instruction": "\n\n".join(
                    [review_common_instruction, spec["instruction"]]
                ),
                "pending_patches": pending_patch_context,
                "global_research_context": self._field_review_global_research_context(
                    checkpoint,
                    agent_name,
                ),
                "review_context_compaction": {
                    "mode": "role_scoped_pending_patch_summary",
                    "reason": (
                        "ReviewExpectationFields uses compact role-specific patch and "
                        "global-research context so reviewers focus on their field scope "
                        "without replaying full expectation documents."
                    ),
                },
                "tool_requirements": tool_requirements,
                "required_tool_names": [],
                "react_runtime_budget": {
                    "max_steps": 3,
                    "max_tool_call_batches": 1,
                },
            }
            if document1_context_pack_brief is not None:
                extra_context["document1_context_pack_brief"] = document1_context_pack_brief
            jobs.append(
                _ParallelAgentJob(
                    order=order,
                    agent_name=agent_name,
                    task_type=TaskType.REVIEW_EXPECTATION_FIELD,
                    output_schema=spec["schema"],
                    content_type=spec["content_type"],
                    section_key=agent_name.value,
                    extra_context=extra_context,
                )
            )

        first_error: Exception | None = None
        skipped_reviewer_count = 0
        for outcome in self._run_agent_jobs_concurrently(checkpoint, node, jobs):
            spec = specs[outcome.job.order]
            if outcome.error is not None:
                first_error = first_error or outcome.error
                skipped_reviewer_count += 1
                self._write_document2_reviewer_acceptance_warning(
                    checkpoint,
                    node,
                    agent_name=outcome.job.agent_name,
                    expected_schema=spec["schema"],
                    warnings=[
                        {
                            "issue": "reviewer_execution_error",
                            "severity": "fatal",
                            "message": str(outcome.error),
                        }
                    ],
                )
                continue
            result = outcome.result
            if result is None:
                error = WorkflowContractError(
                    f"{node.value}/{outcome.job.agent_name.value} returned no result."
                )
                first_error = first_error or error
                skipped_reviewer_count += 1
                self._write_document2_reviewer_acceptance_warning(
                    checkpoint,
                    node,
                    agent_name=outcome.job.agent_name,
                    expected_schema=spec["schema"],
                    warnings=[
                        {
                            "issue": "reviewer_missing_result",
                            "severity": "fatal",
                            "message": str(error),
                        }
                    ],
                )
                continue
            accepted_result = self._accepted_document2_reviewer_result(
                checkpoint,
                node,
                result,
                expected_schema=spec["schema"],
            )
            if accepted_result is None:
                first_error = first_error or WorkflowContractError(
                    f"{node.value}/{outcome.job.agent_name.value} reviewer output "
                    "was skipped by acceptance checks."
                )
                skipped_reviewer_count += 1
                continue
            result = accepted_result
            self._write_working_memory(checkpoint, result, spec["content_type"])
            self._validate_agent_success(result, node, require_patches=False)
            review_findings.extend(
                document2_review_findings_from_agent_result(
                    result,
                    checkpoint.pending_patches,
                )
            )
            for objection in result.objections:
                self.blackboard.create_objection(
                    checkpoint.run_id,
                    self._objection_with_evidence_fallback(objection, result),
                )
            for delegation in result.delegations:
                self.blackboard.create_delegation(checkpoint.run_id, delegation)
            results.append(result)

        deterministic_findings: list[Document2ReviewFinding] = []
        if not results and not deterministic_findings:
            raise first_error or WorkflowContractError(
                "ReviewExpectationFields had no usable reviewer outputs."
            )
        review_findings.extend(deterministic_findings)
        placeholder_findings = [
            finding
            for finding in deterministic_findings
            if any(
                "deterministic_placeholder_detector" in item
                for item in finding.supplemental_context
            )
        ]
        source_less_blocking_finding_count = sum(
            1
            for finding in review_findings
            if finding.blocks_promotion and finding.source_objection_id is None
        )
        review_findings = self._bridge_document2_blocking_findings_to_objections(
            checkpoint,
            review_findings,
        )

        review_state: dict[str, Any] = {
            "primary_state": DOCUMENT2_REVIEW_FINDINGS_KEY,
            "finding_count": len(review_findings),
            "legacy_objection_bridge_count": sum(
                len(result.objections) for result in results
            )
            + source_less_blocking_finding_count,
            "numeric_sanity_disabled": True,
        }
        if skipped_reviewer_count:
            review_state["skipped_reviewer_count"] = skipped_reviewer_count
        if placeholder_findings:
            review_state["placeholder_finding_count"] = len(placeholder_findings)
        metadata = self._agent_metadata(node, results)
        metadata |= {
            DOCUMENT2_REVIEW_FINDINGS_KEY: review_findings_json(review_findings),
            _DOCUMENT2_REVIEW_STATE_KEY: review_state,
        }
        return self._mark_completed(
            checkpoint,
            node,
            metadata=metadata,
        )

    def _field_review_tool_purpose(
        self,
        agent_name: AgentName,
        tool_name: str,
    ) -> str:
        if agent_name is AgentName.C1_FUNDAMENTAL_RESEARCH:
            return (
                "Optional fundamental evidence for calibration or supplementation; "
                "use only if one tool-call batch can materially improve the finding."
            )
        if agent_name is AgentName.C3_INDUSTRY_RESEARCH:
            return (
                "Optional industry, peer, sector, or policy evidence for calibration "
                "or supplementation; use only if one tool-call batch is enough."
            )
        if agent_name is AgentName.O4_MARKET_TRACE:
            return (
                "Optional price, OHLCV, volume, relative-performance, or trade-stream "
                "evidence for calibration or supplementation."
            )
        return f"Optional evidence for {agent_name.value} ReviewExpectationFields."

    def _mock_resolve_blockers(self, checkpoint: WorkflowCheckpoint) -> None:
        if self.execution_mode == "agent_runner":
            raise WorkflowContractError("_mock_resolve_blockers is disabled in agent_runner mode.")
        if not self.auto_resolve_blockers:
            return
        for objection in self.blackboard.list_unresolved_objections(checkpoint.run_id):
            self.blackboard.resolve_objection(
                checkpoint.run_id,
                objection.objection_id,
                "Mock O1 revision resolved the objection.",
            )
        for delegation in self.blackboard.list_blocking_delegations(checkpoint.run_id):
            self.blackboard.complete_delegation(
                checkpoint.run_id,
                delegation.delegation_id,
                "Mock A2 fact-check completed.",
            )

    def _validate_expectation_patches(self, ticker: str, result: AgentResult) -> None:
        self._validate_expectation_patch_list(
            ticker,
            [
                patch
                for patch in result.proposed_patches
                if patch.target.document_type == DocumentType.EXPECTATION_UNIT
            ],
        )

    def _validate_expectation_patch_list(
        self,
        ticker: str,
        expectation_patches: list[BlackboardPatch],
    ) -> None:
        expectation_patches = [
            patch
            for patch in expectation_patches
            if patch.target.document_type == DocumentType.EXPECTATION_UNIT
        ]
        if not expectation_patches:
            raise WorkflowContractError(
                "GenerateExpectationUnits produced no expectation patches."
            )
        if len(expectation_patches) >= 4:
            raise WorkflowContractError("GenerateExpectationUnits produced too many expectations.")
        for patch in expectation_patches:
            if patch.target.ticker != ticker:
                raise WorkflowContractError(
                    "GenerateExpectationUnits produced an expectation for the wrong ticker."
                )
            if not patch.evidence_refs:
                raise WorkflowContractError(
                    "GenerateExpectationUnits produced an expectation patch without evidence."
                )
            if not isinstance(patch.after, dict):
                raise WorkflowContractError(
                    "GenerateExpectationUnits expectation patch must include document content."
                )
            document = ExpectationUnitDocument.model_validate(patch.after)
            if document.ticker != ticker:
                raise WorkflowContractError(
                    "GenerateExpectationUnits expectation document has the wrong ticker."
                )
            if (
                patch.target.expectation_id
                and patch.target.expectation_id != document.expectation_id
            ):
                raise WorkflowContractError(
                    "GenerateExpectationUnits expectation target does not match document."
                )

    def _validate_expectation_patch_count(self, result: AgentResult) -> None:
        expectation_patches = [
            patch
            for patch in result.proposed_patches
            if patch.target.document_type == DocumentType.EXPECTATION_UNIT
        ]
        if not expectation_patches:
            raise WorkflowContractError(
                "GenerateExpectationUnits produced no expectation patches."
            )
        if len(expectation_patches) >= 4:
            raise WorkflowContractError("GenerateExpectationUnits produced too many expectations.")
