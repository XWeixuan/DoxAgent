from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any

from doxagent.cdecr_integration.workflow_runner import _atomic_occurrence_inputs
from doxagent.event_library.compiler import EventLibraryViewCompiler
from doxagent.event_library.contracts import (
    CanonicalAssertionState,
    CanonicalEvent,
    CanonicalEventRevision,
    CanonicalEventType,
    CanonicalFact,
    CanonicalFactRevision,
    CanonicalRevisionBundle,
    DateResolutionLedgerEntry,
    DateResolutionStatus,
    DateSemanticRole,
    DeltaBatch,
    DeltaItem,
    DeltaResolution,
    FrozenRuntimeAtomic,
    FrozenRuntimeSnapshot,
    MUGoldExpectation,
    OccurrenceDateCandidate,
    OccurrenceDateCandidateSource,
    OccurrenceTimePrecision,
    ReferenceReviewDecision,
    ReferenceReviewMode,
    ReferenceReviewReason,
    ReferenceViewBasis,
    ReferenceViewDecisionLedgerEntry,
    ResidualDeltaResolution,
)
from doxagent.event_library.delta_compiler import DeltaCompiler
from doxagent.event_library.quality import compile_quality_report
from doxagent.event_library.reference_review import (
    classify_review,
    event_review_anchor,
)
from doxagent.event_library.repository import EventLibraryRepository
from doxagent.event_library.validator import (
    BundleValidationContext,
    RevisionBundleValidator,
    ValidationStatus,
)


class _Registry:
    def __init__(self, *, mention: Any, source: Any) -> None:
        self.mention = mention
        self.source = source

    def get_mention(self, mention_id: str) -> Any:
        return self.mention if mention_id == self.mention.mention_id else None

    def get_source(self, message_id: str) -> Any:
        return self.source if message_id == self.source.message_id else None


def test_frozen_date_candidates_prioritize_action_date_and_keep_source_trace() -> None:
    mention = SimpleNamespace(
        mention_id="M1",
        message_id="S1",
        canonical_proposition="On August 13, the analyst published a FY2027 forecast.",
        assertion_state=SimpleNamespace(value="ACTUAL"),
        time=SimpleNamespace(
            event_start=date(2026, 8, 13),
            event_end=None,
            precision=SimpleNamespace(value="DAY"),
        ),
    )
    source = SimpleNamespace(
        message_id="S1",
        source_type=SimpleNamespace(value="NEWS"),
        published_at=datetime(2026, 8, 14, 8, tzinfo=UTC),
    )
    event = SimpleNamespace(
        event_id="A1",
        mention_ids=["M1"],
        assertion_state=SimpleNamespace(value="ACTUAL"),
        time=mention.time,
    )
    event_input: Any = event
    registry_input: Any = _Registry(mention=mention, source=source)
    candidates, source_ids = _atomic_occurrence_inputs(event_input, registry_input)
    assert [item.source_kind for item in candidates] == [
        OccurrenceDateCandidateSource.PROPOSITION_EVIDENCE,
        OccurrenceDateCandidateSource.RUNTIME_CONFIRMED_OCCURRENCE,
        OccurrenceDateCandidateSource.SOURCE_PUBLISHED_AT,
    ]
    assert candidates[0].candidate_date == date(2026, 8, 13)
    assert candidates[-1].candidate_date == date(2026, 8, 14)
    assert candidates[-1].source_message_id == "S1"
    assert source_ids == ["S1"]


def test_scheduled_target_is_not_promoted_to_occurrence_candidate() -> None:
    mention = SimpleNamespace(
        mention_id="M1",
        message_id="S1",
        canonical_proposition="The company will report on September 22.",
        assertion_state=SimpleNamespace(value="PLANNED"),
        time=SimpleNamespace(
            event_start=date(2026, 9, 22),
            event_end=None,
            precision=SimpleNamespace(value="DAY"),
        ),
    )
    source = SimpleNamespace(
        message_id="S1",
        source_type=SimpleNamespace(value="ANNOUNCEMENT"),
        published_at=datetime(2026, 8, 20, 8, tzinfo=UTC),
    )
    event = SimpleNamespace(
        event_id="A1",
        mention_ids=["M1"],
        assertion_state=SimpleNamespace(value="PLANNED"),
        time=mention.time,
    )
    event_input: Any = event
    registry_input: Any = _Registry(mention=mention, source=source)
    candidates, _ = _atomic_occurrence_inputs(event_input, registry_input)
    assert [(item.candidate_date, item.source_kind) for item in candidates] == [
        (date(2026, 8, 20), OccurrenceDateCandidateSource.OFFICIAL_RELEASE_DATE)
    ]


def _candidate(day: int, *, source: OccurrenceDateCandidateSource) -> OccurrenceDateCandidate:
    return OccurrenceDateCandidate(
        candidate_date=date(2026, 8, day),
        source_kind=source,
        source_id=f"source-{day}-{source.value}",
    )


def _foundation(tmp_path: Any) -> tuple[EventLibraryRepository, DeltaBatch]:
    repository = EventLibraryRepository(tmp_path / "event_library.sqlite3")
    batch = DeltaBatch(
        batch_id="delta-time-reference",
        ticker="MU",
        runtime_scope="cdecr:US:MU",
        source_snapshot_id="snapshot-time-reference",
        source_epoch_id="epoch-time-reference",
        base_library_version=0,
        items=[
            DeltaItem(
                delta_id="D1",
                runtime_atomic_id="A1",
                runtime_atomic_version=1,
                runtime_signature="sig-1",
                proposition="An analyst published a FY2027 forecast.",
                time="2026-08-13 | FY2027",
                subject_time="FY2027",
                occurrence_date_candidates=[
                    _candidate(13, source=OccurrenceDateCandidateSource.PROPOSITION_EVIDENCE),
                    _candidate(14, source=OccurrenceDateCandidateSource.SOURCE_PUBLISHED_AT),
                ],
                assertion_state=CanonicalAssertionState.FORECAST,
            ),
            DeltaItem(
                delta_id="D2",
                runtime_atomic_id="A2",
                runtime_atomic_version=1,
                runtime_signature="sig-2",
                proposition="A second analyst published a FY2027 forecast.",
                time="2026-08-14 | FY2027",
                subject_time="FY2027",
                occurrence_date_candidates=[
                    _candidate(14, source=OccurrenceDateCandidateSource.PROPOSITION_EVIDENCE)
                ],
                assertion_state=CanonicalAssertionState.FORECAST,
            ),
        ],
    )
    repository.save_delta_batch(batch)
    return repository, batch


def _broad_bundle(batch: DeltaBatch) -> CanonicalRevisionBundle:
    event = CanonicalEventRevision(
        event_id="T1",
        ticker="MU",
        title="Analysts updated Micron FY2027 expectations",
        event_type=CanonicalEventType.ANALYST_RESPONSE_EPISODE,
        occurred_at="2026-08",
        occurrence_time_precision=OccurrenceTimePrecision.MONTH,
        canonical_summary="Two analysts updated Micron FY2027 forecasts in August.",
        known_event_summary="August 2026 analyst-response episode for Micron FY2027.",
        is_important=False,
        include_in_reference_view=True,
        facts=[
            CanonicalFactRevision(
                fact_id="TF1",
                proposition="The first analyst published a FY2027 forecast.",
                assertion_state=CanonicalAssertionState.FORECAST,
                subject_time="FY2027",
                fact_occurred_at="2026-08-13",
                fact_occurrence_time_precision=OccurrenceTimePrecision.DAY,
                consumes_delta_ids=["D1"],
            ),
            CanonicalFactRevision(
                fact_id="TF2",
                proposition="The second analyst published a FY2027 forecast.",
                assertion_state=CanonicalAssertionState.FORECAST,
                subject_time="FY2027",
                fact_occurred_at="2026-08-14",
                fact_occurrence_time_precision=OccurrenceTimePrecision.DAY,
                consumes_delta_ids=["D2"],
            ),
        ],
    )
    as_of = datetime(2026, 8, 24, tzinfo=UTC)
    return CanonicalRevisionBundle(
        contract_version="event-library-maintenance-v3",
        run_id="time-reference-v1",
        ticker="MU",
        base_library_version=0,
        delta_batch_ids=[batch.batch_id],
        event_revisions=[event],
        date_resolution_ledger=[
            DateResolutionLedgerEntry(
                delta_id="D1",
                runtime_atomic_id="A1",
                semantic_role=DateSemanticRole.EVENT_OCCURRENCE,
                status=DateResolutionStatus.GENUINELY_PERIOD_WIDE,
                event_id="T1",
                note="Analyst-response episode spans multiple publication days.",
            ),
            DateResolutionLedgerEntry(
                delta_id="D1",
                runtime_atomic_id="A1",
                candidates=batch.items[0].occurrence_date_candidates,
                selected_date=date(2026, 8, 13),
                selected_precision=OccurrenceTimePrecision.DAY,
                semantic_role=DateSemanticRole.FACT_OCCURRENCE,
                status=DateResolutionStatus.RESOLVED,
                event_id="T1",
                fact_id="TF1",
                subject_time="FY2027",
            ),
            DateResolutionLedgerEntry(
                delta_id="D2",
                runtime_atomic_id="A2",
                candidates=batch.items[1].occurrence_date_candidates,
                selected_date=date(2026, 8, 14),
                selected_precision=OccurrenceTimePrecision.DAY,
                semantic_role=DateSemanticRole.FACT_OCCURRENCE,
                status=DateResolutionStatus.RESOLVED,
                event_id="T1",
                fact_id="TF2",
                subject_time="FY2027",
            ),
        ],
        reference_review_decisions=[
            ReferenceReviewDecision(
                event_id="T1",
                reviewed_at=as_of,
                review_mode=ReferenceReviewMode.IMPLICIT,
                candidate_reason=ReferenceReviewReason.NEW_OR_MODIFIED,
                changed=True,
                include_in_reference_view=True,
                is_important=False,
                reference_view_basis=ReferenceViewBasis.OPEN_OR_EVOLVING_MATTER,
                next_review_at=datetime(2026, 9, 3, tzinfo=UTC),
                note="The FY2027 expectation change remains current.",
            )
        ],
        reference_view_decision_ledger=[
            ReferenceViewDecisionLedgerEntry(
                event_id="T1",
                is_important=False,
                include_in_reference_view=True,
                reference_view_basis=ReferenceViewBasis.OPEN_OR_EVOLVING_MATTER,
                note="The FY2027 expectation change remains current.",
                review_reason=ReferenceReviewReason.NEW_OR_MODIFIED,
                as_of=as_of,
            )
        ],
    )


def _context(batch: DeltaBatch) -> BundleValidationContext:
    return BundleValidationContext(
        run_id="time-reference-v1",
        ticker="MU",
        base_library_version=0,
        delta_batch_ids=[batch.batch_id],
        frozen_as_of=datetime(2026, 8, 24, tzinfo=UTC),
        mode="INCREMENTAL",
        required_contract_version="event-library-maintenance-v3",
    )


def test_broad_episode_keeps_exact_fact_days_and_independent_reference_flag(tmp_path: Any) -> None:
    repository, batch = _foundation(tmp_path)
    outcome = RevisionBundleValidator(repository).validate(
        _broad_bundle(batch), context=_context(batch)
    )
    assert outcome.status is ValidationStatus.PASS
    assert outcome.publishable


def test_broad_event_same_fact_and_candidate_priority_are_hard_errors(tmp_path: Any) -> None:
    repository, batch = _foundation(tmp_path)
    bundle = _broad_bundle(batch)
    first = bundle.event_revisions[0].facts[0].model_copy(
        update={"fact_occurred_at": "SAME"}
    )
    event = bundle.event_revisions[0].model_copy(
        update={"facts": [first, bundle.event_revisions[0].facts[1]]}
    )
    bad_fact_ledger = bundle.date_resolution_ledger[1].model_copy(
        update={"selected_date": date(2026, 8, 14)}
    )
    bad = bundle.model_copy(
        update={
            "event_revisions": [event],
            "date_resolution_ledger": [
                bundle.date_resolution_ledger[0],
                bad_fact_ledger,
                bundle.date_resolution_ledger[2],
            ],
        }
    )
    outcome = RevisionBundleValidator(repository).validate(bad, context=_context(batch))
    codes = {item.code for item in outcome.issues}
    assert "FACT_SAME_WITH_BROAD_EVENT" in codes
    assert "DATE_CANDIDATE_PRIORITY_VIOLATION" in codes
    assert outcome.status is ValidationStatus.FAIL


def test_unresolved_date_stays_locally_pending_without_blocking_valid_fact(tmp_path: Any) -> None:
    repository, batch = _foundation(tmp_path)
    bundle = _broad_bundle(batch)
    event = bundle.event_revisions[0].model_copy(
        update={"facts": [bundle.event_revisions[0].facts[0]]}
    )
    unresolved = DateResolutionLedgerEntry(
        delta_id="D2",
        runtime_atomic_id="A2",
        candidates=batch.items[1].occurrence_date_candidates,
        semantic_role=DateSemanticRole.EVENT_OCCURRENCE,
        status=DateResolutionStatus.UNRESOLVED,
        note="Conflicting date remains Pending.",
    )
    partial = bundle.model_copy(
        update={
            "event_revisions": [event],
            "date_resolution_ledger": [
                bundle.date_resolution_ledger[0],
                bundle.date_resolution_ledger[1],
                unresolved,
            ],
            "residual_delta_resolutions": [
                ResidualDeltaResolution(
                    delta_id="D2", resolution=DeltaResolution.KEEP_PENDING
                )
            ],
        }
    )
    outcome = RevisionBundleValidator(repository).validate(partial, context=_context(batch))
    assert outcome.publishable
    assert outcome.pending_delta_count == 1
    assert outcome.resolved_delta_count == 1


def test_time_error_codes_are_stable_for_missing_future_and_subject_leak(tmp_path: Any) -> None:
    repository, batch = _foundation(tmp_path)
    validator = RevisionBundleValidator(repository)
    base = _broad_bundle(batch)
    event = base.event_revisions[0]

    missing_fact = event.facts[0].model_copy(
        update={"fact_occurred_at": None, "fact_occurrence_time_precision": None}
    )
    missing_bundle = base.model_copy(
        update={
            "event_revisions": [
                event.model_copy(update={"facts": [missing_fact, event.facts[1]]})
            ]
        }
    )
    missing_codes = {
        item.code
        for item in validator.validate(missing_bundle, context=_context(batch)).issues
    }
    assert "FACT_OCCURRENCE_MISSING" in missing_codes

    non_day_fact = event.facts[0].model_copy(
        update={
            "fact_occurred_at": "2026-08",
            "fact_occurrence_time_precision": OccurrenceTimePrecision.MONTH,
        }
    )
    non_day_bundle = base.model_copy(
        update={
            "event_revisions": [
                event.model_copy(update={"facts": [non_day_fact, event.facts[1]]})
            ],
            "date_resolution_ledger": [
                base.date_resolution_ledger[0],
                base.date_resolution_ledger[1].model_copy(
                    update={
                        "selected_date": date(2026, 8, 31),
                        "selected_precision": OccurrenceTimePrecision.MONTH,
                    }
                ),
                base.date_resolution_ledger[2],
            ],
        }
    )
    non_day_codes = {
        item.code
        for item in validator.validate(non_day_bundle, context=_context(batch)).issues
    }
    assert "FACT_OCCURRENCE_NOT_DAY" in non_day_codes

    future_fact = event.facts[0].model_copy(
        update={
            "assertion_state": CanonicalAssertionState.SCHEDULED,
            "subject_time": "2026-09-22",
            "fact_occurred_at": "2026-09-22",
        }
    )
    future_event = event.model_copy(
        update={
            "occurred_at": "2026-09-22",
            "occurrence_time_precision": OccurrenceTimePrecision.DAY,
            "facts": [future_fact, event.facts[1]],
        }
    )
    future_ledger = [
        base.date_resolution_ledger[0].model_copy(
            update={
                "status": DateResolutionStatus.RESOLVED,
                "selected_date": date(2026, 9, 22),
                "selected_precision": OccurrenceTimePrecision.DAY,
            }
        ),
        base.date_resolution_ledger[1].model_copy(
            update={"selected_date": date(2026, 9, 22), "subject_time": "2026-09-22"}
        ),
        base.date_resolution_ledger[2],
    ]
    future_bundle = base.model_copy(
        update={"event_revisions": [future_event], "date_resolution_ledger": future_ledger}
    )
    future_codes = {
        item.code
        for item in validator.validate(future_bundle, context=_context(batch)).issues
    }
    assert {
        "FUTURE_OCCURRENCE_AFTER_AS_OF",
        "FACT_OCCURRENCE_AFTER_AS_OF",
        "SUBJECT_PERIOD_USED_AS_OCCURRENCE",
        "SCHEDULED_TARGET_USED_AS_OCCURRENCE",
    }.issubset(future_codes)

    broad_without_basis = event.model_copy(
        update={
            "occurred_at": "2026",
            "occurrence_time_precision": OccurrenceTimePrecision.YEAR,
        }
    )
    downgraded = base.model_copy(
        update={
            "event_revisions": [broad_without_basis],
            "date_resolution_ledger": [
                base.date_resolution_ledger[0].model_copy(
                    update={
                        "status": DateResolutionStatus.RESOLVED,
                        "selected_date": date(2026, 12, 31),
                        "selected_precision": OccurrenceTimePrecision.YEAR,
                    }
                ),
                *base.date_resolution_ledger[1:],
            ],
        }
    )
    downgraded_codes = {
        item.code for item in validator.validate(downgraded, context=_context(batch)).issues
    }
    assert "TRACEABLE_DAY_DOWNGRADED" in downgraded_codes


def _review_event(
    event_id: str,
    *,
    title: str,
    include: bool,
    supersedes: str | None = None,
) -> CanonicalEventRevision:
    return CanonicalEventRevision(
        event_id=event_id,
        ticker="MU",
        title=title,
        event_type=CanonicalEventType.GUIDANCE_UPDATE,
        occurred_at="2026-08-10",
        occurrence_time_precision=OccurrenceTimePrecision.DAY,
        canonical_summary=f"{title}.",
        known_event_summary=f"On 2026-08-10, {title}.",
        is_important=True,
        include_in_reference_view=include,
        supersedes_event_id=supersedes,
        facts=[
            CanonicalFactRevision(
                fact_id="TF1" if event_id == "T1" else "TF2",
                proposition=f"{title}.",
                assertion_state=CanonicalAssertionState.ACTUAL,
                subject_time="SAME",
                fact_occurred_at="SAME",
                fact_occurrence_time_precision=OccurrenceTimePrecision.DAY,
                consumes_delta_ids=[],
            )
        ],
    )


def test_supersession_requires_both_sides_and_normalizes_target_reason(tmp_path: Any) -> None:
    repository = EventLibraryRepository(tmp_path / "review.sqlite3")
    as_of = datetime(2026, 8, 24, tzinfo=UTC)
    new = _review_event(
        "T1", title="Micron issued the latest controlling guidance", include=True, supersedes="T2"
    )
    old = _review_event("T2", title="Micron issued earlier guidance", include=False)

    def date_rows(event: CanonicalEventRevision) -> list[DateResolutionLedgerEntry]:
        fact = event.facts[0]
        return [
            DateResolutionLedgerEntry(
                selected_date=date(2026, 8, 10),
                selected_precision=OccurrenceTimePrecision.DAY,
                semantic_role=DateSemanticRole.EVENT_OCCURRENCE,
                status=DateResolutionStatus.RESOLVED,
                event_id=event.event_id,
            ),
            DateResolutionLedgerEntry(
                selected_date=date(2026, 8, 10),
                selected_precision=OccurrenceTimePrecision.DAY,
                semantic_role=DateSemanticRole.FACT_OCCURRENCE,
                status=DateResolutionStatus.RESOLVED,
                event_id=event.event_id,
                fact_id=fact.fact_id,
                subject_time="SAME",
            ),
        ]

    new_decision = ReferenceReviewDecision(
        event_id="T1",
        reviewed_at=as_of,
        review_mode=ReferenceReviewMode.IMPLICIT,
        candidate_reason=ReferenceReviewReason.NEW_OR_MODIFIED,
        changed=True,
        include_in_reference_view=True,
        is_important=True,
        reference_view_basis=ReferenceViewBasis.LATEST_CONTROLLING_UPDATE,
        note="Latest controlling guidance.",
    )
    ledgers = [
        ReferenceViewDecisionLedgerEntry(
            event_id="T1",
            is_important=True,
            include_in_reference_view=True,
            reference_view_basis=ReferenceViewBasis.LATEST_CONTROLLING_UPDATE,
            note="Latest controlling guidance.",
            review_reason=ReferenceReviewReason.NEW_OR_MODIFIED,
            as_of=as_of,
        ),
        ReferenceViewDecisionLedgerEntry(
            event_id="T2",
            is_important=True,
            include_in_reference_view=False,
            reference_view_basis=ReferenceViewBasis.SUPERSEDED,
            note="Replaced by T1.",
            review_reason=ReferenceReviewReason.SUPERSEDED_TARGET,
            as_of=as_of,
        ),
    ]
    bundle = CanonicalRevisionBundle(
        contract_version="event-library-maintenance-v3",
        run_id="supersession-review",
        ticker="MU",
        base_library_version=0,
        event_revisions=[new, old],
        date_resolution_ledger=[*date_rows(new), *date_rows(old)],
        reference_review_decisions=[new_decision],
        reference_view_decision_ledger=ledgers,
    )
    context = BundleValidationContext(
        run_id="supersession-review",
        ticker="MU",
        base_library_version=0,
        delta_batch_ids=[],
        frozen_as_of=as_of,
        mode="INCREMENTAL",
        review_only=True,
        required_contract_version="event-library-maintenance-v3",
    )
    missing = RevisionBundleValidator(repository).validate(bundle, context=context)
    assert "SUPERSESSION_REVIEW_PAIR_MISSING" in {item.code for item in missing.issues}

    old_decision = ReferenceReviewDecision(
        event_id="T2",
        reviewed_at=as_of,
        review_mode=ReferenceReviewMode.EXPLICIT,
        candidate_reason=ReferenceReviewReason.SUPERSEDED_TARGET,
        changed=True,
        include_in_reference_view=False,
        is_important=True,
        reference_view_basis=ReferenceViewBasis.SUPERSEDED,
        note="Replaced by T1.",
    )
    complete = bundle.model_copy(
        update={"reference_review_decisions": [new_decision, old_decision]}
    )
    outcome = RevisionBundleValidator(repository).validate(complete, context=context)
    assert outcome.status in {ValidationStatus.PASS, ValidationStatus.PARTIAL}
    assert outcome.normalized_bundle is not None
    normalized = {
        item.event_id: item for item in outcome.normalized_bundle.reference_review_decisions
    }
    assert normalized["T2"].candidate_reason is ReferenceReviewReason.SUPERSEDED_TARGET


def test_review_age_is_due_not_semantically_expired_and_anchor_uses_latest_fact() -> None:
    event = CanonicalEvent(
        event_id="E1",
        ticker="MU",
        title="Micron analyst response episode",
        event_type="ANALYST_RESPONSE_EPISODE",
        occurred_at="2026-07",
        occurrence_time_precision=OccurrenceTimePrecision.MONTH,
        canonical_summary="Analysts updated expectations.",
        known_event_summary="July analyst-response episode.",
        is_important=True,
        include_in_reference_view=True,
        facts=[
            CanonicalFact(
                fact_id="F1",
                proposition="An analyst updated expectations.",
                assertion_state=CanonicalAssertionState.FORECAST,
                subject_time="FY2027",
                fact_occurred_at="2026-08-13",
                fact_occurrence_time_precision=OccurrenceTimePrecision.DAY,
            )
        ],
    )
    assert event_review_anchor(event) == date(2026, 8, 13)
    _, reason, _ = classify_review(
        anchor=date(2026, 7, 1),
        as_of=datetime(2026, 8, 24, tzinfo=UTC),
        include_in_reference_view=False,
    )
    assert reason is ReferenceReviewReason.AGE_REVIEW_DUE_30D


def test_gold_metrics_report_reference_quality_without_quadrant_quota(tmp_path: Any) -> None:
    repository, batch = _foundation(tmp_path)
    bundle = _broad_bundle(batch)
    outcome = RevisionBundleValidator(repository).validate(bundle, context=_context(batch))
    assert outcome.normalized_bundle is not None
    repository.publish_bundle(outcome.normalized_bundle, frozen_as_of=_context(batch).frozen_as_of)
    candidates = repository.due_reference_review_candidates(
        ticker="MU", as_of=datetime(2026, 9, 20, tzinfo=UTC)
    )
    assert len(candidates) == 1
    assert candidates[0].event_type == CanonicalEventType.ANALYST_RESPONSE_EPISODE.value
    assert candidates[0].facts[0].fact_occurred_at == "2026-08-13"
    assert candidates[0].subject_horizons == ["FY2027"]
    assert candidates[0].prior_reference_view_basis is ReferenceViewBasis.OPEN_OR_EVOLVING_MATTER
    assert candidates[0].frozen_as_of == datetime(2026, 9, 20, tzinfo=UTC)
    report = compile_quality_report(
        repository,
        ticker="MU",
        gold=MUGoldExpectation(
            expected_event_count=1,
            expected_fact_count=2,
            current_state_critical_event_ids=["E1"],
            expected_reference_event_ids=["E1"],
            expected_excluded_event_ids=["E99"],
            superseded_event_ids=["E99"],
            current_baseline_event_ids=["E1"],
            d2_current_state_required_event_ids=["E1"],
            d3_maintenance_change_event_ids=["E1"],
        ),
    )
    assert report.importance_reference_quadrants["not_important_reference"] == 1
    assert report.current_state_critical_recall == 1.0
    assert report.exclusion_precision == 1.0
    assert report.superseded_false_positive_rate == 0.0
    assert report.current_baseline_false_negative_rate == 0.0
    assert report.d2_current_state_coverage == 1.0
    assert report.d3_maintenance_change_recall == 1.0
    assert report.reference_view_token_ratio > 0
    assert report.semantic_release_gate_passed is True


def test_delta_compiler_keeps_raw_time_separate_from_subject_time(tmp_path: Any) -> None:
    repository = EventLibraryRepository(tmp_path / "library.sqlite3")
    candidate = _candidate(13, source=OccurrenceDateCandidateSource.PROPOSITION_EVIDENCE)
    snapshot = FrozenRuntimeSnapshot(
        contract_version="frozen-runtime-time-v2",
        snapshot_id="snapshot-1",
        runtime_scope="cdecr:US:MU",
        epoch_id="epoch-1",
        market="US",
        ticker="MU",
        as_of=datetime(2026, 8, 24, tzinfo=UTC),
        atomics=[
            FrozenRuntimeAtomic(
                runtime_atomic_id="A1",
                version=1,
                proposition="An analyst forecast FY2027 revenue.",
                time="2026-08-13 | FY2027",
                subject_time="FY2027",
                occurrence_date_candidates=[candidate],
                source_message_ids=["S1"],
                assertion_state=CanonicalAssertionState.FORECAST,
            )
        ],
    )
    batch = DeltaCompiler(repository).compile(snapshot)
    assert batch.items[0].time == "2026-08-13 | FY2027"
    assert batch.items[0].subject_time == "FY2027"
    assert batch.items[0].occurrence_date_candidates == [candidate]
    assert batch.items[0].source_message_ids == ["S1"]


def test_legacy_v1_remains_readable_and_scopes_repair_without_mutation(tmp_path: Any) -> None:
    repository = EventLibraryRepository(tmp_path / "legacy.sqlite3")
    event = _review_event("T1", title="Micron legacy guidance", include=True)
    legacy_facts = [
        fact.model_copy(
            update={"fact_occurred_at": None, "fact_occurrence_time_precision": None}
        )
        for fact in event.facts
    ]
    legacy = CanonicalRevisionBundle(
        contract_version="event-library-foundation-v1",
        run_id="legacy-v1",
        ticker="MU",
        base_library_version=0,
        event_revisions=[event.model_copy(update={"facts": legacy_facts})],
    )
    published = repository.publish_bundle(legacy)
    assert published.published_library_version == 1
    before = repository.get_event("MU", "E1", 1)
    assert before is not None and before.facts[0].fact_occurred_at is None
    worklist = EventLibraryViewCompiler(repository).time_reference_repair_worklist(
        "MU",
        as_of=datetime(2026, 8, 24, tzinfo=UTC),
        version=1,
        affected_event_ids={"E1"},
    )
    assert [item.event_id for item in worklist] == ["E1"]
    assert {item.value for item in worklist[0].reasons} == {
        "LEGACY_FACT_OCCURRENCE_MISSING",
        "REFERENCE_BASIS_MISSING",
    }
    after = repository.get_event("MU", "E1", 1)
    assert after == before
