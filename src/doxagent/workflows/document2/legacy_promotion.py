# ruff: noqa: F403,F405
"""Behavior-preserving mixin extracted from initialization.py."""

from doxagent.workflows.document2.contracts import (
    Document2PromotionBlocker,
    Document2PromotionCandidate,
    Document2ReviewFinding,
    Document2TransactionAudit,
)
from doxagent.workflows.document2.promotion import (
    DOCUMENT2_PROMOTION_AUDITS_KEY,
    Document2PromotionBlockedError,
    blackboard_patch_from_document2_promotion_candidate,
    document2_promotion_audit,
    document2_promotion_candidate_from_patch,
    promotion_audits_json,
    validate_document2_promotion_candidate,
)
from doxagent.workflows.document2.review import DOCUMENT2_REVIEW_FINDINGS_KEY
from doxagent.workflows.initialization.shared import *


class Document2LegacyPromotionMixin:
    def _promote_pending_patches(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
    ) -> WorkflowCheckpoint:
        stable_documents = list(checkpoint.stable_document_types)
        promotion_audits: list[Document2TransactionAudit] = []
        for patch in checkpoint.pending_patches:
            self._validate_patch_contract(patch, node)
            if patch.target.document_type is DocumentType.EXPECTATION_UNIT:
                patch, audit = self._promote_document2_candidate_read_only(
                    checkpoint,
                    patch,
                )
                promotion_audits.append(audit)
            else:
                self._submit_patch(
                    checkpoint.run_id,
                    patch,
                    "提升已通过复核的 workflow document。",
                )
            if patch.target.document_type not in stable_documents:
                stable_documents.append(patch.target.document_type)
        metadata = (
            {DOCUMENT2_PROMOTION_AUDITS_KEY: promotion_audits_json(promotion_audits)}
            if promotion_audits
            else {}
        )
        return self._mark_completed(
            checkpoint,
            node,
            stable_document_types=stable_documents,
            pending_patches=[],
            metadata=metadata,
        )

    def _promote_document2_candidate_read_only(
        self,
        checkpoint: WorkflowCheckpoint,
        patch: BlackboardPatch,
    ) -> tuple[BlackboardPatch, Document2TransactionAudit]:
        try:
            candidate = document2_promotion_candidate_from_patch(
                patch,
                review_findings=self._active_document2_review_findings_for_promotion(
                    checkpoint,
                    patch,
                ),
            )
        except ValueError as exc:
            raise WorkflowContractError(f"Document2 promotion candidate invalid: {exc}") from exc

        blockers = self._document2_promotion_runtime_blockers(checkpoint, patch, candidate)
        try:
            validate_document2_promotion_candidate(candidate)
        except Document2PromotionBlockedError as exc:
            blockers.extend(exc.blockers)

        if blockers:
            audit = document2_promotion_audit(
                candidate,
                patch=None,
                status="rejected",
                blockers=blockers,
            )
            self._record_document2_promotion_audit(checkpoint, audit)
            blocker_summary = "; ".join(
                f"{blocker.blocker_type}:{blocker.target_path}:{blocker.reason}"
                for blocker in blockers
            )
            raise WorkflowContractError(f"Document2 promotion blocked: {blocker_summary}")

        try:
            promotion_patch = blackboard_patch_from_document2_promotion_candidate(
                candidate,
                patch,
            )
        except ValueError as exc:
            raise WorkflowContractError(f"Document2 promotion patch invalid: {exc}") from exc
        self._submit_document2_promotion_patch(
            checkpoint,
            promotion_patch,
            "提升已通过复核的 expectation unit。",
        )
        audit = document2_promotion_audit(
            candidate,
            patch=promotion_patch,
            status="accepted",
        )
        self._record_document2_promotion_audit(checkpoint, audit)
        return promotion_patch, audit

    def _active_document2_review_findings_for_promotion(
        self,
        checkpoint: WorkflowCheckpoint,
        patch: BlackboardPatch,
    ) -> list[Document2ReviewFinding]:
        expectation_id = patch.target.expectation_id
        if expectation_id is None:
            return []
        raw_findings = checkpoint.metadata.get(DOCUMENT2_REVIEW_FINDINGS_KEY, [])
        if not isinstance(raw_findings, list):
            return []
        run = self.blackboard.get_run(checkpoint.run_id)
        objections_by_id = {objection.objection_id: objection for objection in run.objections}
        current_deterministic_finding_keys = {
            self._document2_review_finding_key(finding)
            for finding in self._document2_deterministic_findings_for_patch(checkpoint, patch)
            if finding.blocks_promotion
        }
        findings: list[Document2ReviewFinding] = []
        for raw_finding in raw_findings:
            try:
                finding = Document2ReviewFinding.model_validate(raw_finding)
            except ValueError as exc:
                raise WorkflowContractError(
                    f"Document2 review finding failed schema validation: {exc}"
                ) from exc
            if finding.expectation_id != expectation_id or not finding.blocks_promotion:
                continue
            if self._is_numeric_sanity_review_finding(finding):
                continue
            if finding.source_objection_id is None:
                findings.append(finding)
                continue
            objection = objections_by_id.get(finding.source_objection_id)
            if objection is None:
                findings.append(finding)
                continue
            if objection.is_unresolved:
                findings.append(finding)
                continue
            if self._document2_review_finding_key(finding) in current_deterministic_finding_keys:
                findings.append(finding)
        return findings

    def _document2_promotion_runtime_blockers(
        self,
        checkpoint: WorkflowCheckpoint,
        patch: BlackboardPatch,
        _candidate: Document2PromotionCandidate,
    ) -> list[Document2PromotionBlocker]:
        run = self.blackboard.get_run(checkpoint.run_id)
        blockers: list[Document2PromotionBlocker] = []
        unresolved_objections = [
            objection
            for objection in run.objections
            if objection.is_unresolved and not self._is_numeric_sanity_objection(objection)
        ]
        if unresolved_objections:
            blockers.append(
                Document2PromotionBlocker(
                    blocker_type="candidate_not_ready",
                    target_path="document",
                    reason=(
                        "Promotion requires all blocking objections to be resolved before "
                        "the read-only gate."
                    ),
                )
            )
        blocking_delegations = [
            delegation for delegation in run.delegations if delegation.is_blocking
        ]
        if blocking_delegations:
            blockers.append(
                Document2PromotionBlocker(
                    blocker_type="candidate_not_ready",
                    target_path="document",
                    reason=(
                        "Promotion requires all blocking delegations to be completed before "
                        "the read-only gate."
                    ),
                )
            )
        return blockers

    def _submit_document2_promotion_patch(
        self,
        checkpoint: WorkflowCheckpoint,
        patch: BlackboardPatch,
        trigger_reason: str,
    ) -> None:
        if patch.author_agent is AgentName.SYSTEM:
            if patch.target.document_type is not DocumentType.EXPECTATION_UNIT:
                raise WorkflowContractError(
                    "Document2 system promotion patches may only target expectation_unit."
                )
            permissions = AgentPermissions(
                writable_targets=[DocumentType.EXPECTATION_UNIT.value],
                can_propose_patch=True,
            )
        else:
            permissions = self.registry.get(patch.author_agent).runtime.to_permissions()
        self.blackboard.submit_patch(
            checkpoint.run_id,
            patch,
            permissions=permissions,
            trigger_reason=trigger_reason,
        )

    def _record_document2_promotion_audit(
        self,
        checkpoint: WorkflowCheckpoint,
        audit: Document2TransactionAudit,
    ) -> None:
        raw = checkpoint.metadata.get(DOCUMENT2_PROMOTION_AUDITS_KEY)
        existing = [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
        checkpoint.metadata = checkpoint.metadata | {
            DOCUMENT2_PROMOTION_AUDITS_KEY: [
                *existing,
                audit.model_dump(mode="json"),
            ]
        }
        self.checkpoint_repository.save_checkpoint(checkpoint)
        self.blackboard.add_working_memory_entry(
            checkpoint.run_id,
            author_agent=AgentName.SYSTEM,
            content_type="document2_promotion_audit",
            payload={
                "status": audit.status,
                "audit": audit.model_dump(mode="json"),
            },
            evidence_refs=[],
        )

    def _price_reaction_needs_escalation(self, reaction: PriceReaction) -> bool:
        text = " ".join(
            [
                reaction.price_change,
                reaction.price_pattern,
                reaction.interpretation,
            ]
        ).lower()
        return any(
            marker in text
            for marker in (
                "unknown",
                "unresolved",
                "not available",
                "未建立",
                "尚未建立",
                "无法确定",
                "证据不足",
                "待确认",
            )
        )
