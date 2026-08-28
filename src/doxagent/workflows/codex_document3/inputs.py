"""Deterministic D3 input preparation from Published D2 and Event Library."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Protocol

from doxagent.codex_runtime.published_storage import PublishedDocumentStorage
from doxagent.codex_runtime.schema import PublishedDocument
from doxagent.event_library.provider import PublishedEventLibraryReader
from doxagent.workflows.codex_document2.schema import Document2Bundle, Document2Document

from .repository import Document3PolicyRepository
from .schema import (
    Document2Ref,
    EventLibraryRef,
    FailedShellCoverage,
    PolicySet,
    PublicationState,
)


class D2PublishedRepository(Protocol):
    def get_bundle(self, run_id: str) -> object | None: ...

    def get_published_document(self, run_id: str, artifact_id: str) -> PublishedDocument | None: ...


@dataclass(frozen=True)
class PreparedDocument3Inputs:
    ticker: str
    document2: Document2Document
    document2_ref: Document2Ref
    expected_gap_refs: list[tuple[str, str, str]]
    failed_shells: list[FailedShellCoverage]
    event_library_ref: EventLibraryRef | None
    reference_view: str
    previous_policy_set: PolicySet | None
    warnings: list[str] = field(default_factory=list)


class Document3InputPreparer:
    def __init__(
        self,
        *,
        runtime_repository: D2PublishedRepository,
        policy_repository: Document3PolicyRepository,
        event_library_reader: PublishedEventLibraryReader | None = None,
        published_storage: PublishedDocumentStorage | None = None,
    ) -> None:
        self._runtime_repository = runtime_repository
        self._policy_repository = policy_repository
        self._event_library_reader = event_library_reader
        self._published_storage = published_storage

    async def _read_document(self, published: PublishedDocument) -> bytes:
        if published.content_text is not None:
            return published.content_text.encode("utf-8")
        if self._published_storage is None or published.storage_path is None:
            raise ValueError("Published D2 content is external but storage is unavailable")
        return await self._published_storage.get(published.storage_path)

    async def prepare_initialize(
        self,
        *,
        ticker: str,
        document2_run_id: str,
        event_library_version: int | None = None,
    ) -> PreparedDocument3Inputs:
        normalized_ticker = ticker.upper()
        bundle = self._runtime_repository.get_bundle(document2_run_id)
        if not isinstance(bundle, Document2Bundle):
            raise ValueError(f"Published D2 bundle not found: {document2_run_id}")
        if bundle.status != "published" or bundle.handoff is None:
            raise ValueError(f"D2 run is not Published: {document2_run_id}")
        if bundle.ticker.upper() != normalized_ticker:
            raise ValueError("D2 ticker does not match D3 request")
        artifact_id = bundle.handoff.document2_artifact_id
        published = self._runtime_repository.get_published_document(document2_run_id, artifact_id)
        if published is None:
            raise ValueError("Published D2 artifact metadata is missing")
        content = await self._read_document(published)
        if len(content) != published.size_bytes:
            raise ValueError("Published D2 artifact size mismatch")
        digest = hashlib.sha256(content).hexdigest()
        if digest != published.sha256:
            raise ValueError("Published D2 artifact sha256 mismatch")
        document2 = Document2Document.model_validate_json(content)
        if document2.ticker.upper() != normalized_ticker:
            raise ValueError("Published D2 document ticker mismatch")

        expected_gap_refs = [
            (shell.shell_id, unit.expectation_id, gap.gap_id)
            for shell in document2.shells
            for unit in shell.units
            for gap in unit.potential_gaps
        ]
        failed_shells = [
            FailedShellCoverage(
                shell_id=item.shell_id,
                reason=item.error or item.error_code or "D2 shell failed",
            )
            for item in document2.shell_outcomes
            if item.status == "failed"
        ]
        warnings: list[str] = []
        if failed_shells:
            warnings.append(
                "Published D2 contains failed shells; they are recorded but excluded "
                "from the D3 gap-coverage denominator."
            )

        event_ref: EventLibraryRef | None = None
        reference_view = ""
        if self._event_library_reader is not None:
            snapshot = self._event_library_reader.reference_view(
                normalized_ticker, version=event_library_version
            )
            if snapshot is not None and snapshot.published_at is not None:
                event_ref = EventLibraryRef(
                    contract_version=snapshot.contract_version,
                    ticker=snapshot.ticker,
                    version=snapshot.version,
                    sha256=snapshot.sha256,
                    published_at=snapshot.published_at,
                )
                reference_view = snapshot.reference_view
            elif snapshot is not None:
                warnings.append("Event Library version has no published_at and was omitted")

        publication_state = PublicationState(bundle.handoff.publication_state)
        return PreparedDocument3Inputs(
            ticker=normalized_ticker,
            document2=document2,
            document2_ref=Document2Ref(
                run_id=document2_run_id,
                artifact_id=artifact_id,
                sha256=published.sha256,
                published_at=bundle.handoff.published_at,
                publication_state=publication_state,
            ),
            expected_gap_refs=expected_gap_refs,
            failed_shells=failed_shells,
            event_library_ref=event_ref,
            reference_view=reference_view,
            previous_policy_set=self._policy_repository.get_current(normalized_ticker),
            warnings=warnings,
        )

    def prepare_maintenance_reference(
        self, *, ticker: str, event_library_version: int | None = None
    ) -> tuple[PolicySet, EventLibraryRef | None, str]:
        current = self._policy_repository.get_current(ticker.upper())
        if current is None:
            raise ValueError(f"No current D3 Policy Set for {ticker.upper()}")
        if self._event_library_reader is None:
            return current, None, ""
        snapshot = self._event_library_reader.reference_view(
            ticker.upper(), version=event_library_version
        )
        if snapshot is None or snapshot.published_at is None:
            return current, None, ""
        return (
            current,
            EventLibraryRef(
                contract_version=snapshot.contract_version,
                ticker=snapshot.ticker,
                version=snapshot.version,
                sha256=snapshot.sha256,
                published_at=snapshot.published_at,
            ),
            snapshot.reference_view,
        )
