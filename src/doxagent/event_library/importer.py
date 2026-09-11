"""Validated Bundle direct importer."""

from __future__ import annotations

import json
import warnings

from doxagent.event_library.bundle_io import TolerantBundleLoadResult
from doxagent.event_library.compiler import EventLibraryViewCompiler
from doxagent.event_library.contracts import CanonicalRevisionBundle, PublicationResult
from doxagent.event_library.repository import EventLibraryRepository
from doxagent.event_library.validator import (
    BundleValidationContext,
    BundleValidationOutcome,
    RevisionBundleValidator,
    ValidationIssue,
    ValidationSeverity,
)


class RevisionBundleImporter:
    def __init__(self, repository: EventLibraryRepository) -> None:
        self._repository = repository
        self._validator = RevisionBundleValidator(repository)

    def validate(
        self,
        bundle: CanonicalRevisionBundle,
        *,
        context: BundleValidationContext | None = None,
    ) -> BundleValidationOutcome:
        return self._validator.validate(bundle, context=context)

    def import_and_publish(
        self,
        bundle: CanonicalRevisionBundle,
        *,
        context: BundleValidationContext | None = None,
    ) -> tuple[PublicationResult, BundleValidationOutcome]:
        outcome = self._validator.validate(bundle, context=context)
        if not outcome.publishable or outcome.normalized_bundle is None:
            codes = ", ".join(issue.code for issue in outcome.issues) or "UNKNOWN"
            raise ValueError(f"Revision Bundle is not publishable: {codes}")
        result = self._repository.publish_bundle(
            outcome.normalized_bundle,
            source_bundle=bundle,
            frozen_as_of=(None if context is None else context.frozen_as_of),
        )
        self._persist_reference_delta(result)
        return result, outcome

    def import_tolerant_and_publish(
        self,
        loaded: TolerantBundleLoadResult,
        *,
        context: BundleValidationContext | None = None,
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
        if loaded.normalization_actions:
            initial.append(
                ValidationIssue(
                    code="O2_WIRE_NORMALIZED",
                    severity=ValidationSeverity.WARNING,
                    message=(
                        f"Applied {len(loaded.normalization_actions)} mechanical wire "
                        "normalizations"
                    ),
                )
            )
        outcome = self._validator.validate(
            loaded.bundle,
            initial_issues=initial,
            force_pending_delta_ids=loaded.invalid_delta_ids,
            context=context,
        )
        if not outcome.publishable or outcome.normalized_bundle is None:
            codes = ", ".join(issue.code for issue in outcome.issues) or "UNKNOWN"
            raise ValueError(f"Revision Bundle is not publishable: {codes}")
        diagnostics = self._diagnostic_payload(loaded)
        result = self._repository.publish_bundle(
            outcome.normalized_bundle,
            source_bundle=loaded.bundle,
            import_diagnostics=diagnostics,
            frozen_as_of=(None if context is None else context.frozen_as_of),
        )
        self._persist_reference_delta(result)
        self._persist_import_diagnostics(loaded, result.published_library_version)
        return result, outcome

    def _persist_import_diagnostics(
        self, loaded: TolerantBundleLoadResult, published_version: int
    ) -> None:
        if loaded.raw_bundle_hash is None:
            return
        payload = self._diagnostic_payload(loaded)
        assert payload is not None
        payload["published_library_version"] = published_version
        path = (
            self._repository.path.parent
            / "artifacts"
            / "event_library"
            / "import_diagnostics"
            / f"{loaded.raw_bundle_hash}.json"
        )
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            warnings.warn(
                f"Event Library publication committed but diagnostics write failed: {exc}",
                RuntimeWarning,
                stacklevel=2,
            )

    @staticmethod
    def _diagnostic_payload(loaded: TolerantBundleLoadResult) -> dict[str, object] | None:
        if loaded.raw_bundle_hash is None:
            return None
        return {
            "raw_bundle_hash": loaded.raw_bundle_hash,
            "normalization_actions": loaded.normalization_actions,
            "rejected_records": loaded.rejected_records,
            "recovered_delta_ids": loaded.recovered_delta_ids,
            "load_issues": [
                {"code": item.code, "message": item.message, "item_id": item.item_id}
                for item in loaded.issues
            ],
        }

    def _persist_reference_delta(self, result: PublicationResult) -> None:
        if result.published_library_version <= result.base_library_version:
            return
        EventLibraryViewCompiler(self._repository).reference_view_delta(
            result.ticker,
            from_version=result.base_library_version,
            to_version=result.published_library_version,
            persist=True,
        )
