"""Deterministic, locally degrading Canonical Revision Bundle validator."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import Field

from doxagent.event_library.contracts import (
    CanonicalEventRevision,
    CanonicalObjectStatus,
    CanonicalRevisionBundle,
    CanonicalSubjectTimeMarker,
    DateResolutionStatus,
    DateSemanticRole,
    DeltaBatch,
    DeltaResolution,
    EventRetirement,
    OccurrenceTimePrecision,
    ReferenceReviewMode,
    ReferenceReviewReason,
    ReferenceViewBasis,
    ResidualDeltaResolution,
    StrictModel,
)
from doxagent.event_library.quality import compile_bundle_semantic_report
from doxagent.event_library.reference_review import (
    classify_review,
    event_review_anchor,
    initial_review_reason,
    occurrence_anchor,
    occurrence_start,
    occurrence_time_matches_precision,
)
from doxagent.event_library.repository import EventLibraryRepository


class ValidationStatus(StrEnum):
    PASS = "PASS"
    PARTIAL = "PARTIAL"
    FAIL = "FAIL"


class ValidationSeverity(StrEnum):
    WARNING = "WARNING"
    ERROR = "ERROR"


class ValidationIssue(StrictModel):
    code: str
    severity: ValidationSeverity
    message: str
    item_id: str | None = None


class BundleValidationOutcome(StrictModel):
    status: ValidationStatus
    issues: list[ValidationIssue] = Field(default_factory=list)
    normalized_bundle: CanonicalRevisionBundle | None = None
    total_delta_count: int = Field(ge=0)
    resolved_delta_count: int = Field(ge=0)
    pending_delta_count: int = Field(ge=0)

    @property
    def publishable(self) -> bool:
        return self.normalized_bundle is not None and self.status is not ValidationStatus.FAIL


class BundleValidationContext(StrictModel):
    run_id: str
    ticker: str
    base_library_version: int = Field(ge=0)
    delta_batch_ids: list[str]
    frozen_as_of: datetime
    mode: str
    review_only: bool = False
    required_contract_version: str | None = None
    deterministic_review_fields_by_event: dict[str, dict[str, object]] = Field(default_factory=dict)


class RevisionBundleValidator:
    def __init__(self, repository: EventLibraryRepository) -> None:
        self._repository = repository

    def validate(
        self,
        bundle: CanonicalRevisionBundle,
        *,
        initial_issues: list[ValidationIssue] | None = None,
        force_pending_delta_ids: list[str] | None = None,
        context: BundleValidationContext | None = None,
    ) -> BundleValidationOutcome:
        """Iteratively isolate conflicting edits, never publish invalid graph edges."""
        original_issues = list(initial_issues or [])
        current = bundle.model_copy(deep=True)
        recovered: list[ValidationIssue] = []
        pending = set(force_pending_delta_ids or [])
        fatal = {
            "BUNDLE_IDENTITY_MISMATCH",
            "UNKNOWN_DELTA_BATCH",
            "STALE_BASE",
            "TICKER_MISMATCH",
            "BASE_MISMATCH",
            "AMBIGUOUS_DELTA_IDS",
        }
        # Each iteration removes at least one edit/ledger row. Bound by input size.
        limit = 2 + sum(len(v) for v in current.model_dump().values() if isinstance(v, list))
        for _ in range(limit):
            outcome = self._validate_once(
                current,
                initial_issues=original_issues,
                force_pending_delta_ids=sorted(pending),
                context=context,
            )
            errors = [i for i in outcome.issues if i.severity is ValidationSeverity.ERROR]
            if not errors:
                if recovered:
                    outcome.status = ValidationStatus.PARTIAL
                    outcome.issues = recovered + outcome.issues
                return outcome
            if any(i.code in fatal or i in original_issues for i in errors):
                return outcome
            bad = {i.item_id for i in errors if i.item_id}
            cycle = self._relation_cycle(current)
            bad.update(cycle or [])
            for event in current.event_revisions:
                if any(f.fact_id in bad for f in event.facts):
                    bad.add(event.event_id)
                if any(
                    getattr(row, "delta_id", None) in bad and row.event_id == event.event_id
                    for row in current.date_resolution_ledger
                ):
                    bad.add(event.event_id)
            # Remove dependent edits as well; existing published events remain intact.
            changed = True
            while changed:
                before = len(bad)
                for event in current.event_revisions:
                    refs = {
                        *event.related_event_ids,
                        *event.derived_from_event_ids,
                        event.supersedes_event_id,
                    }
                    if refs & bad:
                        bad.add(event.event_id)
                for item in current.event_retirements:
                    if item.redirect_to_event_id in bad:
                        bad.add(item.event_id)
                changed = len(bad) != before
            before = current.model_dump_json()
            edits = [e for e in current.event_revisions if e.event_id not in bad]
            for row in current.date_resolution_ledger:
                if row.event_id in bad or row.fact_id in bad or row.delta_id in bad:
                    if row.delta_id:
                        pending.add(row.delta_id)
            current = current.model_copy(
                update={
                    "event_revisions": edits,
                    "event_retirements": [
                        r for r in current.event_retirements if r.event_id not in bad
                    ],
                    "reference_review_decisions": [
                        r for r in current.reference_review_decisions if r.event_id not in bad
                    ],
                    "reference_view_decision_ledger": [
                        r for r in current.reference_view_decision_ledger if r.event_id not in bad
                    ],
                    "date_resolution_ledger": [
                        r
                        for r in current.date_resolution_ledger
                        if r.event_id not in bad and r.fact_id not in bad and r.delta_id not in bad
                    ],
                }
            )
            if current.model_dump_json() == before:
                return outcome  # Unknown/global defect: never pretend it was repaired.
            recovered.extend(
                i.model_copy(
                    update={
                        "severity": ValidationSeverity.WARNING,
                        "message": "QUARANTINED: " + i.message,
                    }
                )
                for i in errors
            )
        return outcome

    def _validate_once(
        self,
        bundle: CanonicalRevisionBundle,
        *,
        initial_issues: list[ValidationIssue] | None = None,
        force_pending_delta_ids: list[str] | None = None,
        context: BundleValidationContext | None = None,
    ) -> BundleValidationOutcome:
        issues: list[ValidationIssue] = list(initial_issues or [])
        if context is not None:
            identity = (
                bundle.run_id == context.run_id
                and bundle.ticker == context.ticker.upper()
                and bundle.base_library_version == context.base_library_version
                and bundle.delta_batch_ids
                == ([] if context.review_only else context.delta_batch_ids)
                and (
                    context.required_contract_version is None
                    or bundle.contract_version == context.required_contract_version
                )
            )
            if not identity:
                issues.append(
                    self._error(
                        "BUNDLE_IDENTITY_MISMATCH",
                        "Bundle run/ticker/base/Delta identity does not equal the Frozen task",
                    )
                )
        if any(issue.severity is ValidationSeverity.ERROR for issue in issues):
            return self._failed(issues)
        prior = self._repository.prior_publication(bundle)
        if prior is not None:
            total = sum(
                len(batch.items)
                for batch_id in bundle.delta_batch_ids
                if (batch := self._repository.get_delta_batch(batch_id)) is not None
            )
            return BundleValidationOutcome(
                status=ValidationStatus.PASS,
                normalized_bundle=bundle,
                total_delta_count=total,
                resolved_delta_count=total - prior.pending_delta_count,
                pending_delta_count=prior.pending_delta_count,
            )
        batches: list[DeltaBatch] = []
        for batch_id in bundle.delta_batch_ids:
            batch = self._repository.get_delta_batch(batch_id)
            if batch is None:
                issues.append(
                    self._error("UNKNOWN_DELTA_BATCH", "Delta batch does not exist", batch_id)
                )
            else:
                batches.append(batch)
        if len(batches) != len(bundle.delta_batch_ids):
            return self._failed(issues)
        head = self._repository.published_version(bundle.ticker)
        if bundle.base_library_version != head:
            issues.append(
                self._error(
                    "STALE_BASE",
                    f"Bundle base V{bundle.base_library_version} does not match Published V{head}",
                )
            )
        if any(batch.ticker != bundle.ticker for batch in batches):
            issues.append(self._error("TICKER_MISMATCH", "Bundle and Delta batch tickers differ"))
        if any(batch.base_library_version != bundle.base_library_version for batch in batches):
            issues.append(
                self._error("BASE_MISMATCH", "Bundle and Delta batch base versions differ")
            )
        expected_delta_ids = [item.delta_id for batch in batches for item in batch.items]
        if len(expected_delta_ids) != len(set(expected_delta_ids)):
            issues.append(
                self._error(
                    "AMBIGUOUS_DELTA_IDS",
                    "Combined Delta batches reuse request-local D# IDs",
                )
            )
        if any(issue.severity is ValidationSeverity.ERROR for issue in issues):
            return self._failed(issues, total=len(expected_delta_ids))

        accepted_events = self._filter_event_revisions(bundle, issues)
        accepted_retirements = self._filter_retirements(bundle, accepted_events, issues)
        if any(issue.severity is ValidationSeverity.ERROR for issue in issues):
            return self._failed(issues, total=len(expected_delta_ids))
        normalized = bundle.model_copy(
            update={
                "event_revisions": accepted_events,
                "event_retirements": accepted_retirements,
            },
            deep=True,
        )
        if force_pending_delta_ids:
            normalized = self._force_pending(normalized, force_pending_delta_ids)
        normalized = self._normalize_delta_coverage(normalized, expected_delta_ids, issues)
        normalized = self._filter_invalid_duplicate_targets(normalized, issues)
        cycle = self._relation_cycle(normalized)
        if cycle:
            issues.append(
                self._error(
                    "RELATION_CYCLE",
                    f"Event lifecycle/derivation graph contains a cycle: {' -> '.join(cycle)}",
                )
            )
        normalized = self._validate_and_normalize_reviews(normalized, issues, context)
        if context is not None and normalized.contract_version == "event-library-maintenance-v3":
            from .contracts import ReferenceViewDecisionLedgerEntry

            ledgers = {row.event_id: row for row in normalized.reference_view_decision_ledger}
            for decision in normalized.reference_review_decisions:
                if (
                    decision.is_important is not None
                    and decision.reference_view_basis is not None
                    and decision.note
                    and decision.reference_view_basis.includes == decision.include_in_reference_view
                ):
                    ledger = ReferenceViewDecisionLedgerEntry(
                        event_id=decision.event_id,
                        is_important=decision.is_important,
                        include_in_reference_view=decision.include_in_reference_view,
                        reference_view_basis=decision.reference_view_basis,
                        note=decision.note,
                        review_reason=decision.candidate_reason,
                        as_of=context.frozen_as_of,
                    )
                    if ledgers.get(decision.event_id) != ledger:
                        issues.append(
                            self._warning(
                                "REFERENCE_LEDGER_REBUILT",
                                "Redundant ledger reconstructed from canonical review decision",
                                decision.event_id,
                            )
                        )
                    ledgers[decision.event_id] = ledger
            normalized = normalized.model_copy(
                update={"reference_view_decision_ledger": list(ledgers.values())}
            )
        self._validate_time_contract(normalized, batches, issues, context)
        self._validate_reference_contract(normalized, issues, context)
        self._semantic_checks(normalized, issues, context)
        if any(issue.severity is ValidationSeverity.ERROR for issue in issues):
            return self._failed(issues, total=len(expected_delta_ids))
        normalized = self._normalize_delta_coverage(normalized, expected_delta_ids, issues)
        pending_count = sum(
            item.resolution is DeltaResolution.KEEP_PENDING
            for item in normalized.residual_delta_resolutions
        )
        resolved_count = len(expected_delta_ids) - pending_count
        status = ValidationStatus.PARTIAL if issues else ValidationStatus.PASS
        return BundleValidationOutcome(
            status=status,
            issues=issues,
            normalized_bundle=normalized,
            total_delta_count=len(expected_delta_ids),
            resolved_delta_count=resolved_count,
            pending_delta_count=pending_count,
        )

    def _filter_event_revisions(
        self,
        bundle: CanonicalRevisionBundle,
        issues: list[ValidationIssue],
    ) -> list[CanonicalEventRevision]:
        existing = {
            event.event_id: event
            for event in self._repository.published_events(
                bundle.ticker, bundle.base_library_version
            )
        }
        revised_ids = {
            event.event_id for event in bundle.event_revisions if event.event_id.startswith("E")
        }
        retired_ids = {item.event_id for item in bundle.event_retirements}
        fact_owner = {
            fact.fact_id: event.event_id
            for event in existing.values()
            if event.event_id not in revised_ids and event.event_id not in retired_ids
            for fact in event.facts
        }
        known_fact_ids = {fact.fact_id for event in existing.values() for fact in event.facts}
        seen_temp_events: set[str] = set()
        seen_temp_facts: set[str] = set()
        accepted: list[CanonicalEventRevision] = []
        retirement_by_source = {item.event_id: item for item in bundle.event_retirements}
        for event in bundle.event_revisions:
            reason: tuple[str, str] | None = None
            current = existing.get(event.event_id)
            if event.ticker != bundle.ticker:
                reason = ("EVENT_TICKER_MISMATCH", "Event ticker does not match Bundle ticker")
            elif event.event_id.startswith("E") and current is None:
                reason = ("UNKNOWN_STABLE_EVENT", "Stable Event ID does not exist at base version")
            elif event.event_id.startswith("T") and event.event_id in seen_temp_events:
                reason = ("DUPLICATE_TEMP_EVENT", "Temporary Event ID is reused")
            elif event.event_id.startswith("T") and event.price_analysis is not None:
                reason = ("O2_PRICE_ANALYSIS_WRITE", "New O2 Events must keep price_analysis null")
            elif current is not None and current.price_analysis != event.price_analysis:
                reason = (
                    "O2_PRICE_ANALYSIS_OVERWRITE",
                    "O2 Event revision changed an existing price_analysis value",
                )
            if reason is None:
                for fact in event.facts:
                    if fact.fact_id.startswith("F") and fact.fact_id not in known_fact_ids:
                        reason = (
                            "UNKNOWN_STABLE_FACT",
                            "Stable Fact ID does not exist at base version",
                        )
                        break
                    if fact.fact_id.startswith("TF") and fact.fact_id in seen_temp_facts:
                        reason = ("DUPLICATE_TEMP_FACT", "Temporary Fact ID is reused")
                        break
                    owner = fact_owner.get(fact.fact_id)
                    if owner is not None and owner != event.event_id:
                        reason = (
                            "FACT_MULTIPLE_OWNERS",
                            f"Fact is already active under {owner}",
                        )
                        break
            if reason is not None:
                issues.append(self._error(reason[0], reason[1], event.event_id))
                continue
            retirement = retirement_by_source.get(event.event_id)
            if event.status is CanonicalObjectStatus.ACTIVE and retirement is not None:
                issues.append(
                    self._error(
                        "ACTIVE_REVISION_RETIRED",
                        "An ACTIVE revision cannot be retired in the same Bundle",
                        event.event_id,
                    )
                )
                continue
            if event.status is not CanonicalObjectStatus.ACTIVE and retirement is None:
                issues.append(
                    self._error(
                        "NONACTIVE_REVISION_MISSING_RETIREMENT",
                        "MERGED/SUPPRESSED revisions require a matching retirement redirect",
                        event.event_id,
                    )
                )
                continue
            if retirement is not None:
                expected_suppressed = retirement.reason == "SUPPRESSED_INVALID_OCCURRENCE"
                if expected_suppressed != (event.status is CanonicalObjectStatus.SUPPRESSED):
                    issues.append(
                        self._error(
                            "REVISION_RETIREMENT_STATUS_MISMATCH",
                            "Revision status and retirement reason describe different "
                            "lifecycle states",
                            event.event_id,
                        )
                    )
                    continue
            seen_temp_events.add(event.event_id)
            for fact in event.facts:
                seen_temp_facts.add(fact.fact_id)
                fact_owner[fact.fact_id] = event.event_id
            accepted.append(event)
        known_event_ids = set(existing) | {item.event_id for item in accepted}
        final: list[CanonicalEventRevision] = []
        for event in accepted:
            refs = set(event.related_event_ids) | set(event.derived_from_event_ids)
            if event.supersedes_event_id is not None:
                refs.add(event.supersedes_event_id)
            missing = sorted(refs.difference(known_event_ids))
            if missing:
                issues.append(
                    self._error(
                        "UNKNOWN_EVENT_RELATION_TARGET",
                        f"Event revision references missing targets: {', '.join(missing)}",
                        event.event_id,
                    )
                )
                continue
            final.append(event)
        return final

    def _filter_retirements(
        self,
        bundle: CanonicalRevisionBundle,
        accepted_events: list[CanonicalEventRevision],
        issues: list[ValidationIssue],
    ) -> list[EventRetirement]:
        existing = {
            event.event_id
            for event in self._repository.published_events(
                bundle.ticker, bundle.base_library_version
            )
        }
        accepted_by_id = {item.event_id: item for item in accepted_events}
        retired_sources = {item.event_id for item in bundle.event_retirements}
        targets = (
            existing
            | {
                item.event_id
                for item in accepted_events
                if item.status is CanonicalObjectStatus.ACTIVE
            }
        ) - retired_sources
        accepted: list[EventRetirement] = []
        for item in bundle.event_retirements:
            source_exists = item.event_id in existing or item.event_id in accepted_by_id
            if not source_exists:
                issues.append(
                    self._error(
                        "UNKNOWN_RETIREMENT_SOURCE",
                        "Retirement source is neither a base Event nor a same-Bundle revision",
                        item.event_id,
                    )
                )
            elif item.redirect_to_event_id not in targets:
                issues.append(
                    self._error(
                        "UNKNOWN_REDIRECT_TARGET",
                        "Retirement redirect target is missing or becomes non-active "
                        "in this version",
                        item.event_id,
                    )
                )
            else:
                accepted.append(item)
        return accepted

    def _normalize_delta_coverage(
        self,
        bundle: CanonicalRevisionBundle,
        expected: list[str],
        issues: list[ValidationIssue],
    ) -> CanonicalRevisionBundle:
        placements: list[str] = [
            delta_id
            for event in bundle.event_revisions
            for fact in event.facts
            for delta_id in fact.consumes_delta_ids
        ] + [item.delta_id for item in bundle.residual_delta_resolutions]
        counts = Counter(placements)
        expected_set = set(expected)
        conflicts = {item for item, count in counts.items() if count != 1} | (
            set(placements) - expected_set
        )
        missing = expected_set - set(placements)
        if conflicts:
            for delta_id in sorted(conflicts):
                issues.append(
                    self._warning(
                        "CONFLICTING_DELTA_DISPOSITION",
                        "Conflicting or unknown Delta disposition was normalized to KEEP_PENDING",
                        delta_id,
                    )
                )
        if missing:
            for delta_id in sorted(missing):
                issues.append(
                    self._warning(
                        "MISSING_DELTA_DISPOSITION",
                        "Missing Delta disposition was normalized to KEEP_PENDING",
                        delta_id,
                    )
                )
        forced_pending = conflicts.intersection(expected_set) | missing
        events = []
        for event in bundle.event_revisions:
            facts = [
                fact.model_copy(
                    update={
                        "consumes_delta_ids": [
                            item
                            for item in fact.consumes_delta_ids
                            if item not in forced_pending and item in expected_set
                        ]
                    }
                )
                for fact in event.facts
            ]
            events.append(event.model_copy(update={"facts": facts}))
        residuals = [
            item
            for item in bundle.residual_delta_resolutions
            if item.delta_id not in forced_pending and item.delta_id in expected_set
        ]
        residuals.extend(
            ResidualDeltaResolution(delta_id=item, resolution=DeltaResolution.KEEP_PENDING)
            for item in sorted(forced_pending)
        )
        return bundle.model_copy(
            update={"event_revisions": events, "residual_delta_resolutions": residuals}
        )

    def _filter_invalid_duplicate_targets(
        self,
        bundle: CanonicalRevisionBundle,
        issues: list[ValidationIssue],
    ) -> CanonicalRevisionBundle:
        event_facts = {
            event.event_id: {fact.fact_id for fact in event.facts}
            for event in bundle.event_revisions
        }
        for event in self._repository.published_events(bundle.ticker, bundle.base_library_version):
            event_facts.setdefault(event.event_id, {fact.fact_id for fact in event.facts})
        residuals: list[ResidualDeltaResolution] = []
        for item in bundle.residual_delta_resolutions:
            if item.resolution is not DeltaResolution.DUPLICATE_FACT:
                residuals.append(item)
                continue
            valid = (
                item.target_event_id in event_facts
                and item.target_fact_id in event_facts[item.target_event_id]
            )
            if valid:
                residuals.append(item)
            else:
                issues.append(
                    self._warning(
                        "INVALID_DUPLICATE_TARGET",
                        "Invalid duplicate target was normalized to KEEP_PENDING",
                        item.delta_id,
                    )
                )
                residuals.append(
                    ResidualDeltaResolution(
                        delta_id=item.delta_id,
                        resolution=DeltaResolution.KEEP_PENDING,
                    )
                )
        return bundle.model_copy(update={"residual_delta_resolutions": residuals})

    def _validate_and_normalize_reviews(
        self,
        bundle: CanonicalRevisionBundle,
        issues: list[ValidationIssue],
        context: BundleValidationContext | None,
    ) -> CanonicalRevisionBundle:
        ids = [item.event_id for item in bundle.reference_review_decisions]
        for event_id, count in Counter(ids).items():
            if count > 1:
                issues.append(
                    self._error(
                        "DUPLICATE_REFERENCE_REVIEW",
                        "One run may contain only one review decision per Event",
                        event_id,
                    )
                )
        existing = {
            item.event_id: item
            for item in self._repository.published_events(
                bundle.ticker, bundle.base_library_version
            )
        }
        revisions = {item.event_id: item for item in bundle.event_revisions}
        known = set(existing) | set(revisions)
        normalized = []
        for decision in bundle.reference_review_decisions:
            if decision.event_id not in known:
                issues.append(
                    self._error(
                        "UNKNOWN_REFERENCE_REVIEW_EVENT",
                        "Reference Review Event is neither in the base nor this Bundle",
                        decision.event_id,
                    )
                )
                continue
            if context is None:
                normalized.append(decision)
                continue
            current = existing.get(decision.event_id)
            revision = revisions.get(decision.event_id)
            if revision is not None and (
                revision.include_in_reference_view != decision.include_in_reference_view
            ):
                issues.append(
                    self._error(
                        "REVIEW_REVISION_FLAG_MISMATCH",
                        "Review decision and complete Event revision disagree on "
                        "Reference inclusion",
                        decision.event_id,
                    )
                )
                continue
            if (
                decision.is_important is not None
                and revision is not None
                and revision.is_important != decision.is_important
            ):
                issues.append(
                    self._error(
                        "REVIEW_REVISION_IMPORTANCE_MISMATCH",
                        "Review decision and complete Event revision disagree on importance",
                        decision.event_id,
                    )
                )
                continue
            prior_important = current.is_important if current is not None else False
            prior_include = current.include_in_reference_view if current is not None else False
            next_important = (
                decision.is_important
                if decision.is_important is not None
                else (revision.is_important if revision is not None else prior_important)
            )
            next_include = decision.include_in_reference_view
            changed = prior_important != next_important or prior_include != next_include
            source = revision or current
            assert source is not None
            anchor = event_review_anchor(source)
            task_fields = context.deterministic_review_fields_by_event.get(decision.event_id)
            superseded_targets = {
                item.supersedes_event_id
                for item in bundle.event_revisions
                if item.supersedes_event_id is not None
            }
            if decision.event_id in superseded_targets:
                mode = ReferenceReviewMode.EXPLICIT
                reason = ReferenceReviewReason.SUPERSEDED_TARGET
                _, _, next_at = classify_review(
                    anchor=anchor,
                    as_of=context.frozen_as_of,
                    include_in_reference_view=next_include,
                )
            elif task_fields is not None:
                mode, reason, next_at = classify_review(
                    anchor=anchor,
                    as_of=context.frozen_as_of,
                    include_in_reference_view=next_include,
                    last_reviewed_at=None,
                )
            else:
                mode = decision.review_mode
                reason = initial_review_reason(anchor, context.frozen_as_of)
                _, _, next_at = classify_review(
                    anchor=anchor,
                    as_of=context.frozen_as_of,
                    include_in_reference_view=next_include,
                )
            corrected = decision.model_copy(
                update={
                    "reviewed_at": context.frozen_as_of,
                    "review_mode": mode,
                    "candidate_reason": reason,
                    "changed": changed,
                    "next_review_at": next_at,
                }
            )
            if corrected != decision:
                issues.append(
                    self._warning(
                        "REFERENCE_REVIEW_FIELDS_NORMALIZED",
                        "Frozen-clock review fields were replaced by deterministic values",
                        decision.event_id,
                    )
                )
            if changed and revision is None:
                issues.append(
                    self._error(
                        "CHANGED_REVIEW_MISSING_REVISION",
                        "A flag change requires a complete stable-ID Event revision",
                        decision.event_id,
                    )
                )
            if context.review_only and not changed and revision is not None:
                issues.append(
                    self._error(
                        "UNCHANGED_REVIEW_HAS_REVISION",
                        "A review-only unchanged decision must not create an Event revision",
                        decision.event_id,
                    )
                )
            normalized.append(corrected)
        return bundle.model_copy(update={"reference_review_decisions": normalized})

    def _validate_time_contract(
        self,
        bundle: CanonicalRevisionBundle,
        batches: list[DeltaBatch],
        issues: list[ValidationIssue],
        context: BundleValidationContext | None,
    ) -> None:
        if bundle.contract_version != "event-library-maintenance-v3":
            return
        as_of = None if context is None else context.frozen_as_of.astimezone(UTC).date()
        delta_by_id = {item.delta_id: item for batch in batches for item in batch.items}
        ledger_delta_ids = {
            item.delta_id for item in bundle.date_resolution_ledger if item.delta_id is not None
        }
        for delta_id in sorted(set(delta_by_id) - ledger_delta_ids):
            issues.append(
                self._warning(
                    "DATE_RESOLUTION_LEDGER_DELTA_COVERAGE",
                    "Every maintenance-v3 Delta requires at least one date ledger row",
                    delta_id,
                )
            )
        for delta_id in sorted(ledger_delta_ids - set(delta_by_id)):
            issues.append(
                self._error(
                    "DATE_RESOLUTION_LEDGER_UNKNOWN_DELTA",
                    "Date ledger references a Delta outside the Frozen Bundle",
                    delta_id,
                )
            )
        priority = {
            "PROPOSITION_EVIDENCE": 1,
            "OFFICIAL_RELEASE_DATE": 2,
            "RUNTIME_CONFIRMED_OCCURRENCE": 3,
            "SOURCE_PUBLISHED_AT": 4,
            "FOCUSED_WEB_SEARCH": 5,
        }
        for entry in bundle.date_resolution_ledger:
            if (
                entry.delta_id is None
                or entry.delta_id not in delta_by_id
                or entry.status is not DateResolutionStatus.RESOLVED
            ):
                continue
            candidates = delta_by_id[entry.delta_id].occurrence_date_candidates
            if not candidates:
                continue
            highest = min(priority[item.source_kind.value] for item in candidates)
            top_dates = {
                item.candidate_date
                for item in candidates
                if priority[item.source_kind.value] == highest
            }
            if len(top_dates) == 1 and entry.selected_date not in top_dates:
                issues.append(
                    self._error(
                        "DATE_CANDIDATE_PRIORITY_VIOLATION",
                        "Selected occurrence date bypasses the highest-priority Frozen candidate",
                        entry.fact_id or entry.event_id or entry.delta_id,
                    )
                )
        event_ledger = {
            item.event_id: item
            for item in bundle.date_resolution_ledger
            if item.semantic_role is DateSemanticRole.EVENT_OCCURRENCE and item.event_id is not None
        }
        fact_ledger = {
            item.fact_id: item
            for item in bundle.date_resolution_ledger
            if item.semantic_role is DateSemanticRole.FACT_OCCURRENCE and item.fact_id is not None
        }
        for event in bundle.event_revisions:
            event_anchor = occurrence_anchor(event.occurred_at, event.occurrence_time_precision)
            ledger = event_ledger.get(event.event_id)
            if ledger is None:
                issues.append(
                    self._error(
                        "DATE_RESOLUTION_LEDGER_MISSING",
                        "Every maintenance-v3 Event revision requires an occurrence ledger row",
                        event.event_id,
                    )
                )
            elif ledger.status in {
                DateResolutionStatus.CONFLICTING,
                DateResolutionStatus.UNRESOLVED,
            }:
                issues.append(
                    self._error(
                        "DATE_SPECIFIC_EVENT_UNRESOLVED",
                        "An unresolved/conflicting Event date must remain Pending",
                        event.event_id,
                    )
                )
            elif (
                ledger.status is DateResolutionStatus.RESOLVED
                and event_anchor is not None
                and ledger.selected_date != event_anchor
            ):
                issues.append(
                    self._error(
                        "DATE_LEDGER_EVENT_MISMATCH",
                        "Selected Event ledger date does not match occurred_at",
                        event.event_id,
                    )
                )
            event_start = occurrence_start(event.occurred_at, event.occurrence_time_precision)
            if as_of is not None and event_start is not None and event_start > as_of:
                issues.append(
                    self._error(
                        "FUTURE_OCCURRENCE_AFTER_AS_OF",
                        "Event occurrence is later than the Frozen as_of clock",
                        event.event_id,
                    )
                )
            consumed = {delta_id for fact in event.facts for delta_id in fact.consumes_delta_ids}
            traceable = any(
                delta_by_id[delta_id].occurrence_date_candidates
                for delta_id in consumed
                if delta_id in delta_by_id
            )
            broad_precision = event.occurrence_time_precision in {
                OccurrenceTimePrecision.MONTH,
                OccurrenceTimePrecision.QUARTER,
                OccurrenceTimePrecision.YEAR,
                OccurrenceTimePrecision.INTERVAL,
            }
            if (
                (traceable or broad_precision)
                and (
                    event.occurrence_time_precision is OccurrenceTimePrecision.UNKNOWN
                    or broad_precision
                )
                and (
                    ledger is None
                    or (
                        broad_precision
                        and ledger.status is not DateResolutionStatus.GENUINELY_PERIOD_WIDE
                    )
                    or (
                        event.occurrence_time_precision is OccurrenceTimePrecision.UNKNOWN
                        and ledger.status is not DateResolutionStatus.UNRESOLVED
                    )
                )
            ):
                code = (
                    "DATE_SPECIFIC_EVENT_UNRESOLVED"
                    if event.occurrence_time_precision is OccurrenceTimePrecision.UNKNOWN
                    else "TRACEABLE_DAY_DOWNGRADED"
                )
                issues.append(
                    self._error(
                        code,
                        "Traceable DAY evidence cannot be hidden by a broad/unknown Event time",
                        event.event_id,
                    )
                )
            for fact in event.facts:
                if fact.fact_occurred_at is None:
                    issues.append(
                        self._error(
                            "FACT_OCCURRENCE_MISSING",
                            "maintenance-v3 Facts require fact_occurred_at",
                            fact.fact_id,
                        )
                    )
                    continue
                if fact.fact_occurrence_time_precision is not OccurrenceTimePrecision.DAY or (
                    fact.fact_occurred_at != "SAME"
                    and not occurrence_time_matches_precision(
                        fact.fact_occurred_at, OccurrenceTimePrecision.DAY
                    )
                ):
                    issues.append(
                        self._error(
                            "FACT_OCCURRENCE_NOT_DAY",
                            "Fact occurrence precision must be DAY",
                            fact.fact_id,
                        )
                    )
                if (
                    fact.fact_occurred_at == "SAME"
                    and event.occurrence_time_precision is not OccurrenceTimePrecision.DAY
                ):
                    issues.append(
                        self._error(
                            "FACT_SAME_WITH_BROAD_EVENT",
                            "Broad Event time cannot substitute for an exact Fact date",
                            fact.fact_id,
                        )
                    )
                fact_anchor = (
                    occurrence_anchor(event.occurred_at, event.occurrence_time_precision)
                    if fact.fact_occurred_at == "SAME"
                    else occurrence_anchor(
                        fact.fact_occurred_at, fact.fact_occurrence_time_precision
                    )
                )
                if as_of is not None and fact_anchor is not None and fact_anchor > as_of:
                    issues.append(
                        self._error(
                            "FACT_OCCURRENCE_AFTER_AS_OF",
                            "Fact occurrence is later than the Frozen as_of clock",
                            fact.fact_id,
                        )
                    )
                fact_entry = fact_ledger.get(fact.fact_id)
                if fact_entry is None:
                    issues.append(
                        self._error(
                            "DATE_RESOLUTION_LEDGER_MISSING",
                            "Every maintenance-v3 Fact revision requires an occurrence ledger row",
                            fact.fact_id,
                        )
                    )
                elif fact_entry.status is not DateResolutionStatus.RESOLVED:
                    issues.append(
                        self._error(
                            "DATE_SPECIFIC_EVENT_UNRESOLVED",
                            "An unresolved Fact date must remain Pending",
                            fact.fact_id,
                        )
                    )
                elif fact_entry.selected_precision is not OccurrenceTimePrecision.DAY:
                    issues.append(
                        self._error(
                            "FACT_OCCURRENCE_NOT_DAY",
                            "Fact occurrence ledger precision must be DAY",
                            fact.fact_id,
                        )
                    )
                elif fact_anchor is not None and fact_entry.selected_date != fact_anchor:
                    issues.append(
                        self._error(
                            "DATE_LEDGER_FACT_MISMATCH",
                            "Selected Fact ledger date does not match fact_occurred_at",
                            fact.fact_id,
                        )
                    )
                subject = None if fact.subject_time is None else str(fact.subject_time)
                occurrence_value = (
                    event.occurred_at if fact.fact_occurred_at == "SAME" else fact.fact_occurred_at
                )
                if subject not in {None, CanonicalSubjectTimeMarker.SAME.value} and (
                    occurrence_value == subject
                ):
                    issues.append(
                        self._error(
                            "SUBJECT_PERIOD_USED_AS_OCCURRENCE",
                            "Fact subject period was reused as occurrence time",
                            fact.fact_id,
                        )
                    )
                if fact.assertion_state.value in {"SCHEDULED", "PLAN", "FORECAST", "EXPECTED"}:
                    if subject not in {None, CanonicalSubjectTimeMarker.SAME.value} and (
                        event.occurred_at == subject or occurrence_value == subject
                    ):
                        issues.append(
                            self._error(
                                "SCHEDULED_TARGET_USED_AS_OCCURRENCE",
                                "Future scheduled/forecast target belongs in subject_time",
                                fact.fact_id,
                            )
                        )

    def _validate_reference_contract(
        self,
        bundle: CanonicalRevisionBundle,
        issues: list[ValidationIssue],
        context: BundleValidationContext | None,
    ) -> None:
        if bundle.contract_version != "event-library-maintenance-v3":
            return
        revisions = {item.event_id: item for item in bundle.event_revisions}
        decisions = {item.event_id: item for item in bundle.reference_review_decisions}
        ledger = {item.event_id: item for item in bundle.reference_view_decision_ledger}
        required = set(revisions) | set(decisions)
        for event_id in sorted(set(revisions) - set(decisions)):
            issues.append(
                self._error(
                    "REFERENCE_REVIEW_DECISION_MISSING",
                    "Every maintenance-v3 Event revision requires an auditable decision",
                    event_id,
                )
            )
        for event_id, decision in decisions.items():
            if (
                decision.is_important is None
                or decision.reference_view_basis is None
                or not (decision.note or "").strip()
            ):
                issues.append(
                    self._error(
                        "REFERENCE_DECISION_BASIS_MISSING",
                        "maintenance-v3 review decisions require importance and basis",
                        event_id,
                    )
                )
        for event_id in sorted(required - set(ledger)):
            issues.append(
                self._error(
                    "REFERENCE_VIEW_DECISION_LEDGER_MISSING",
                    "Every new, revised, or reviewed Event requires a Reference "
                    "decision ledger row",
                    event_id,
                )
            )
        for event_id, row in ledger.items():
            revision = revisions.get(event_id)
            current_decision = decisions.get(event_id)
            if event_id not in required:
                issues.append(
                    self._error(
                        "REFERENCE_VIEW_DECISION_LEDGER_ORPHAN",
                        "Reference decision ledger row does not target this Bundle",
                        event_id,
                    )
                )
                continue
            expected_important = (
                revision.is_important
                if revision is not None
                else (None if current_decision is None else current_decision.is_important)
            )
            expected_include = (
                revision.include_in_reference_view
                if revision is not None
                else (
                    None if current_decision is None else current_decision.include_in_reference_view
                )
            )
            if expected_important is not None and row.is_important != expected_important:
                issues.append(
                    self._error(
                        "REFERENCE_LEDGER_IMPORTANCE_MISMATCH",
                        "Reference ledger and Event/review importance disagree",
                        event_id,
                    )
                )
            if expected_include is not None and row.include_in_reference_view != expected_include:
                issues.append(
                    self._error(
                        "REFERENCE_LEDGER_FLAG_MISMATCH",
                        "Reference ledger and Event/review inclusion disagree",
                        event_id,
                    )
                )
            if (
                current_decision is not None
                and current_decision.reference_view_basis != row.reference_view_basis
            ):
                issues.append(
                    self._error(
                        "REFERENCE_LEDGER_BASIS_MISMATCH",
                        "Reference decision and ledger basis disagree",
                        event_id,
                    )
                )
            if (
                current_decision is not None
                and current_decision.candidate_reason != row.review_reason
            ):
                issues.append(
                    self._error(
                        "REFERENCE_LEDGER_REASON_MISMATCH",
                        "Reference decision and ledger review reason disagree",
                        event_id,
                    )
                )
            if (
                current_decision is not None
                and (current_decision.note or "").strip() != row.note.strip()
            ):
                issues.append(
                    self._error(
                        "REFERENCE_LEDGER_NOTE_MISMATCH",
                        "Reference decision and ledger note disagree",
                        event_id,
                    )
                )
            if context is not None and row.as_of != context.frozen_as_of:
                issues.append(
                    self._error(
                        "REFERENCE_LEDGER_AS_OF_MISMATCH",
                        "Reference ledger must use the Frozen as_of clock",
                        event_id,
                    )
                )
        supersession_pairs = [
            (item.event_id, item.supersedes_event_id)
            for item in bundle.event_revisions
            if item.supersedes_event_id is not None
        ]
        for new_id, old_id in supersession_pairs:
            assert old_id is not None
            if new_id not in decisions or old_id not in decisions:
                issues.append(
                    self._error(
                        "SUPERSESSION_REVIEW_PAIR_MISSING",
                        "A supersession must review both the new Event and superseded target",
                        new_id,
                    )
                )
            old_row = ledger.get(old_id)
            if old_row is not None and (
                old_row.include_in_reference_view
                or old_row.reference_view_basis is not ReferenceViewBasis.SUPERSEDED
            ):
                issues.append(
                    self._error(
                        "SUPERSEDED_REFERENCE_INCONSISTENT",
                        "Superseded target must be excluded with SUPERSEDED basis",
                        old_id,
                    )
                )

    def _semantic_checks(
        self,
        bundle: CanonicalRevisionBundle,
        issues: list[ValidationIssue],
        context: BundleValidationContext | None,
    ) -> None:
        report = compile_bundle_semantic_report(
            bundle, mode=(None if context is None else context.mode)
        )
        for item in report.issues:
            issues.append(
                ValidationIssue(
                    code=item.code,
                    severity=(
                        ValidationSeverity.ERROR
                        if item.severity == "ERROR"
                        else ValidationSeverity.WARNING
                    ),
                    message=item.message,
                    item_id=item.item_id,
                )
            )

    @staticmethod
    def _force_pending(
        bundle: CanonicalRevisionBundle, delta_ids: list[str]
    ) -> CanonicalRevisionBundle:
        forced = set(delta_ids)
        residuals = [
            item for item in bundle.residual_delta_resolutions if item.delta_id not in forced
        ]
        residuals.extend(
            ResidualDeltaResolution(delta_id=item, resolution=DeltaResolution.KEEP_PENDING)
            for item in sorted(forced)
        )
        return bundle.model_copy(update={"residual_delta_resolutions": residuals})

    @staticmethod
    def _relation_cycle(bundle: CanonicalRevisionBundle) -> list[str]:
        graph: dict[str, set[str]] = {}
        for event in bundle.event_revisions:
            targets = set(event.derived_from_event_ids)
            if event.supersedes_event_id is not None:
                targets.add(event.supersedes_event_id)
            graph.setdefault(event.event_id, set()).update(targets)
        for item in bundle.event_retirements:
            graph.setdefault(item.event_id, set()).add(item.redirect_to_event_id)
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str, path: list[str]) -> list[str]:
            if node in visiting:
                start = path.index(node) if node in path else 0
                return path[start:] + [node]
            if node in visited:
                return []
            visiting.add(node)
            for target in graph.get(node, set()):
                cycle = visit(target, [*path, node])
                if cycle:
                    return cycle
            visiting.remove(node)
            visited.add(node)
            return []

        for node in graph:
            cycle = visit(node, [])
            if cycle:
                return cycle
        return []

    @staticmethod
    def _warning(code: str, message: str, item_id: str | None = None) -> ValidationIssue:
        return ValidationIssue(
            code=code,
            severity=ValidationSeverity.WARNING,
            message=message,
            item_id=item_id,
        )

    @staticmethod
    def _error(code: str, message: str, item_id: str | None = None) -> ValidationIssue:
        return ValidationIssue(
            code=code,
            severity=ValidationSeverity.ERROR,
            message=message,
            item_id=item_id,
        )

    @staticmethod
    def _failed(issues: list[ValidationIssue], total: int = 0) -> BundleValidationOutcome:
        return BundleValidationOutcome(
            status=ValidationStatus.FAIL,
            issues=issues,
            total_delta_count=total,
            resolved_delta_count=0,
            pending_delta_count=total,
        )
