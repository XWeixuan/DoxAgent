"""Validated Bundle direct importer."""

from __future__ import annotations

from doxagent.event_library.bundle_io import TolerantBundleLoadResult
from doxagent.event_library.contracts import CanonicalRevisionBundle, PublicationResult
from doxagent.event_library.repository import EventLibraryRepository
from doxagent.event_library.validator import (
    BundleValidationOutcome,
    RevisionBundleValidator,
    ValidationIssue,
    ValidationSeverity,
)


class RevisionBundleImporter:
    def __init__(self, repository: EventLibraryRepository) -> None:
        self._repository = repository
        self._validator = RevisionBundleValidator(repository)

    def validate(self, bundle: CanonicalRevisionBundle) -> BundleValidationOutcome:
        return self._validator.validate(bundle)

    def import_and_publish(
        self, bundle: CanonicalRevisionBundle
    ) -> tuple[PublicationResult, BundleValidationOutcome]:
        outcome = self._validator.validate(bundle)
        if not outcome.publishable or outcome.normalized_bundle is None:
            codes = ", ".join(issue.code for issue in outcome.issues) or "UNKNOWN"
            raise ValueError(f"Revision Bundle is not publishable: {codes}")
        result = self._repository.publish_bundle(
            outcome.normalized_bundle,
            source_bundle=bundle,
        )
        return result, outcome

    def import_tolerant_and_publish(
        self, loaded: TolerantBundleLoadResult
    ) -> tuple[PublicationResult, BundleValidationOutcome]:
        initial = [
            ValidationIssue(
                code=item.code,
                severity=ValidationSeverity.WARNING,
                message=item.message,
                item_id=item.item_id,
            )
            for item in loaded.issues
        ]
        outcome = self._validator.validate(
            loaded.bundle,
            initial_issues=initial,
            force_pending_delta_ids=loaded.invalid_delta_ids,
        )
        if not outcome.publishable or outcome.normalized_bundle is None:
            codes = ", ".join(issue.code for issue in outcome.issues) or "UNKNOWN"
            raise ValueError(f"Revision Bundle is not publishable: {codes}")
        result = self._repository.publish_bundle(
            outcome.normalized_bundle,
            source_bundle=loaded.bundle,
        )
        return result, outcome
