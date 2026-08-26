"""Deterministic, locally degrading Canonical Revision Bundle validator."""

from __future__ import annotations

from collections import Counter
from enum import StrEnum

from pydantic import Field

from doxagent.event_library.contracts import (
    CanonicalEventRevision,
    CanonicalRevisionBundle,
    DeltaBatch,
    DeltaResolution,
    EventRetirement,
    ResidualDeltaResolution,
    StrictModel,
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


class RevisionBundleValidator:
    def __init__(self, repository: EventLibraryRepository) -> None:
        self._repository = repository

    def validate(
        self,
        bundle: CanonicalRevisionBundle,
        *,
        initial_issues: list[ValidationIssue] | None = None,
        force_pending_delta_ids: list[str] | None = None,
    ) -> BundleValidationOutcome:
        issues: list[ValidationIssue] = list(initial_issues or [])
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
            affected = set(cycle)
            kept: list[CanonicalEventRevision] = []
            pending: list[str] = []
            for event in normalized.event_revisions:
                if event.event_id in affected:
                    pending.extend(
                        delta_id for fact in event.facts for delta_id in fact.consumes_delta_ids
                    )
                    issues.append(
                        self._warning(
                            "RELATION_CYCLE",
                            "Event revision was skipped because it creates a relationship cycle",
                            event.event_id,
                        )
                    )
                else:
                    kept.append(event)
            normalized = normalized.model_copy(update={"event_revisions": kept}, deep=True)
            normalized = self._force_pending(normalized, pending)
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
            event.event_id
            for event in bundle.event_revisions
            if event.event_id.startswith("E")
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
        for event in bundle.event_revisions:
            reason: tuple[str, str] | None = None
            current = existing.get(event.event_id)
            if event.ticker != bundle.ticker:
                reason = ("EVENT_TICKER_MISMATCH", "Event ticker does not match Bundle ticker")
            elif event.status.value != "ACTIVE":
                reason = ("INVALID_EVENT_STATUS", "Event revisions must target ACTIVE state")
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
                issues.append(self._warning(reason[0], reason[1], event.event_id))
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
                    self._warning(
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
        targets = existing | {item.event_id for item in accepted_events}
        accepted: list[EventRetirement] = []
        for item in bundle.event_retirements:
            if item.event_id not in existing:
                issues.append(
                    self._warning(
                        "UNKNOWN_RETIREMENT_SOURCE",
                        "Retirement source is not an active stable Event",
                        item.event_id,
                    )
                )
            elif item.redirect_to_event_id not in targets:
                issues.append(
                    self._warning(
                        "UNKNOWN_REDIRECT_TARGET",
                        "Retirement redirect target does not exist",
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
        for event in self._repository.published_events(
            bundle.ticker, bundle.base_library_version
        ):
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
    def _failed(
        issues: list[ValidationIssue], total: int = 0
    ) -> BundleValidationOutcome:
        return BundleValidationOutcome(
            status=ValidationStatus.FAIL,
            issues=issues,
            total_delta_count=total,
            resolved_delta_count=0,
            pending_delta_count=total,
        )
