"""Document availability adapters for unified runtime scheduling."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from pydantic import ValidationError

from doxagent.blackboard import BlackboardService
from doxagent.blackboard.errors import RunNotFoundError
from doxagent.blackboard.state import BlackboardRun
from doxagent.models import DocumentType
from doxagent.models.documents import (
    DocumentBase,
    ExpectationUnitDocument,
    GlobalResearchDocument,
    KnownEventsDocument,
    MonitoringConfigDocument,
    MonitoringPolicyDocument,
)
from doxagent.runtime_scheduler.schema import (
    DocumentAvailability,
    DocumentBundle,
    DocumentComponentStatus,
    DocumentSetStatus,
)
from doxagent.settings import DoxAgentSettings
from doxagent.workflows import BlackboardInitializationWorkflow

_DOCUMENT_BUNDLE_TYPES = [
    DocumentType.GLOBAL_RESEARCH,
    DocumentType.EXPECTATION_UNIT,
    DocumentType.KNOWN_EVENTS,
    DocumentType.MONITORING_CONFIG,
    DocumentType.MONITORING_POLICY,
]


class RuntimeDocumentProvider(Protocol):
    def latest(self, ticker: str, *, now: datetime | None = None) -> DocumentBundle:
        ...

    def initialize(self, ticker: str, *, now: datetime | None = None) -> DocumentBundle:
        ...


class WorkflowDocumentProvider:
    """Read current Blackboard documents and initialize missing ticker context."""

    def __init__(
        self,
        *,
        workflow: BlackboardInitializationWorkflow | None = None,
        blackboard: BlackboardService | None = None,
        settings: DoxAgentSettings | None = None,
        max_age: timedelta = timedelta(days=3),
    ) -> None:
        self.workflow = workflow or BlackboardInitializationWorkflow(settings=settings)
        self.blackboard = blackboard or self.workflow.blackboard
        self.max_age = max_age

    def latest(self, ticker: str, *, now: datetime | None = None) -> DocumentBundle:
        normalized = ticker.strip().upper()
        checked_at = now or datetime.now(UTC)
        runs = self._candidate_runs(normalized)
        if not runs:
            return _missing_bundle(normalized, checked_at=checked_at)
        bundles = [
            _bundle_from_run(run, checked_at=checked_at, max_age=self.max_age)
            for run in runs
        ]
        for bundle in bundles:
            if bundle.status.usable:
                return bundle
        return bundles[0]

    def initialize(self, ticker: str, *, now: datetime | None = None) -> DocumentBundle:
        normalized = ticker.strip().upper()
        self.workflow.run(normalized)
        return self.latest(normalized, now=now)

    def by_run_id(
        self,
        ticker: str,
        document_run_id: str,
        *,
        now: datetime | None = None,
    ) -> DocumentBundle:
        normalized = ticker.strip().upper()
        checked_at = now or datetime.now(UTC)
        try:
            run = self._run_by_id(normalized, document_run_id)
        except RunNotFoundError:
            return DocumentBundle(
                status=DocumentSetStatus(
                    ticker=normalized,
                    blackboard_run_id=None,
                    checked_at=checked_at,
                    usable=False,
                    missing_document_types=[],
                    components=[],
                )
            )
        if run.ticker != normalized:
            return DocumentBundle(
                status=DocumentSetStatus(
                    ticker=normalized,
                    blackboard_run_id=None,
                    checked_at=checked_at,
                    usable=False,
                    missing_document_types=[],
                    components=[],
                )
            )
        return _bundle_from_run(run, checked_at=checked_at, max_age=self.max_age)

    def _candidate_runs(self, ticker: str) -> list[BlackboardRun]:
        repository = getattr(self.blackboard, "repository", None)
        loader = getattr(repository, "list_document_bundle_candidates", None)
        if callable(loader):
            return loader(ticker, _DOCUMENT_BUNDLE_TYPES, limit=3)
        return [
            _document_only_run(run, _DOCUMENT_BUNDLE_TYPES)
            for run in self.blackboard.list_runs_by_ticker(ticker, limit=3)
        ]

    def _run_by_id(self, ticker: str, document_run_id: str) -> BlackboardRun:
        repository = getattr(self.blackboard, "repository", None)
        loader = getattr(repository, "get_document_bundle_by_run_id", None)
        if callable(loader):
            return loader(ticker, document_run_id, _DOCUMENT_BUNDLE_TYPES)
        return _document_only_run(
            self.blackboard.get_run(document_run_id),
            _DOCUMENT_BUNDLE_TYPES,
        )


def _missing_bundle(ticker: str, *, checked_at: datetime) -> DocumentBundle:
    required = [
        DocumentType.GLOBAL_RESEARCH,
        DocumentType.EXPECTATION_UNIT,
        DocumentType.KNOWN_EVENTS,
        DocumentType.MONITORING_CONFIG,
        DocumentType.MONITORING_POLICY,
    ]
    return DocumentBundle(
        status=DocumentSetStatus(
            ticker=ticker,
            checked_at=checked_at,
            usable=False,
            missing_document_types=required,
            components=[
                DocumentComponentStatus(
                    document_type=document_type,
                    availability=DocumentAvailability.MISSING,
                    reason="No Blackboard run is available for this ticker.",
                )
                for document_type in required
            ],
        )
    )


def _document_only_run(
    run: BlackboardRun,
    document_types: list[DocumentType],
) -> BlackboardRun:
    copied = run.model_copy(deep=True)
    allowed = set(document_types)
    copied.belief_state.documents = {
        document_type: bucket
        for document_type, bucket in copied.belief_state.documents.items()
        if document_type in allowed
    }
    copied.working_memory = []
    copied.commit_log = []
    copied.objections = []
    copied.delegations = []
    return copied


def _bundle_from_run(
    run: BlackboardRun,
    *,
    checked_at: datetime,
    max_age: timedelta,
) -> DocumentBundle:
    global_research, global_research_status = _component(
        run,
        DocumentType.GLOBAL_RESEARCH,
        GlobalResearchDocument,
        checked_at=checked_at,
        max_age=max_age,
        allow_many=False,
    )
    expectation_units, expectation_status = _component(
        run,
        DocumentType.EXPECTATION_UNIT,
        ExpectationUnitDocument,
        checked_at=checked_at,
        max_age=max_age,
        allow_many=True,
    )
    known_events, known_events_status = _component(
        run,
        DocumentType.KNOWN_EVENTS,
        KnownEventsDocument,
        checked_at=checked_at,
        max_age=max_age,
        allow_many=False,
    )
    monitoring_config, monitoring_config_status = _component(
        run,
        DocumentType.MONITORING_CONFIG,
        MonitoringConfigDocument,
        checked_at=checked_at,
        max_age=max_age,
        allow_many=False,
    )
    monitoring_policy, monitoring_policy_status = _component(
        run,
        DocumentType.MONITORING_POLICY,
        MonitoringPolicyDocument,
        checked_at=checked_at,
        max_age=max_age,
        allow_many=False,
    )
    components = [
        global_research_status,
        expectation_status,
        known_events_status,
        monitoring_config_status,
        monitoring_policy_status,
    ]
    missing = [
        component.document_type
        for component in components
        if component.availability
        in {DocumentAvailability.MISSING, DocumentAvailability.INVALID}
    ]
    stale = any(component.availability is DocumentAvailability.STALE for component in components)
    usable = not missing and not stale
    config_doc = (
        monitoring_config[0]
        if monitoring_config and isinstance(monitoring_config[0], MonitoringConfigDocument)
        else None
    )
    policy_doc = (
        monitoring_policy[0]
        if monitoring_policy and isinstance(monitoring_policy[0], MonitoringPolicyDocument)
        else None
    )
    known_doc = (
        known_events[0]
        if known_events and isinstance(known_events[0], KnownEventsDocument)
        else None
    )
    return DocumentBundle(
        status=DocumentSetStatus(
            ticker=run.ticker,
            blackboard_run_id=run.run_id,
            checked_at=checked_at,
            usable=usable,
            stale=stale,
            missing_document_types=missing,
            components=components,
            applied_config_version=(
                config_doc.applied_config_version if config_doc is not None else None
            ),
        ),
        known_events=known_doc,
        monitoring_config=config_doc,
        monitoring_policy=policy_doc,
    )


def _component(
    run: BlackboardRun,
    document_type: DocumentType,
    model: type[DocumentBase],
    *,
    checked_at: datetime,
    max_age: timedelta,
    allow_many: bool,
) -> tuple[list[DocumentBase], DocumentComponentStatus]:
    bucket = run.belief_state.documents.get(document_type, {})
    documents: list[DocumentBase] = []
    errors: list[str] = []
    for raw in bucket.values():
        payload = _unwrap_document(raw)
        try:
            documents.append(model.model_validate(payload))
        except ValidationError as exc:
            errors.append(str(exc).splitlines()[0])
    if not documents and errors:
        return [], DocumentComponentStatus(
            document_type=document_type,
            availability=DocumentAvailability.INVALID,
            document_count=0,
            reason="; ".join(errors[:3]),
        )
    if not documents:
        return [], DocumentComponentStatus(
            document_type=document_type,
            availability=DocumentAvailability.MISSING,
            document_count=0,
            reason=f"{document_type.value} is missing from Blackboard belief state.",
        )
    selected = documents if allow_many else [_latest_document(documents)]
    newest_updated_at = max(_document_time(document) for document in selected)
    stale_after = newest_updated_at + max_age
    availability = (
        DocumentAvailability.STALE
        if stale_after < checked_at.astimezone(UTC)
        else DocumentAvailability.AVAILABLE
    )
    reason = None
    if availability is DocumentAvailability.STALE:
        reason = f"{document_type.value} is older than {max_age.days} day(s)."
    return selected, DocumentComponentStatus(
        document_type=document_type,
        availability=availability,
        document_ids=[document.document_id for document in selected],
        document_count=len(documents),
        newest_updated_at=newest_updated_at,
        stale_after=stale_after,
        reason=reason,
    )


def _unwrap_document(raw: Any) -> Any:
    if isinstance(raw, dict) and isinstance(raw.get("document"), dict):
        return raw["document"]
    return raw


def _latest_document(documents: list[DocumentBase]) -> DocumentBase:
    return max(documents, key=_document_time)


def _document_time(document: DocumentBase) -> datetime:
    value = document.updated_at or document.created_at
    return value.astimezone(UTC)
