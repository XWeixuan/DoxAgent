from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from doxagent.cdecr_integration.contracts import (
    InitializationOrchestrationStage,
    TickerJobMode,
    TickerJobStage,
    TickerJobState,
    TickerPipelineResult,
)
from doxagent.cdecr_integration.initialization_orchestrator import (
    BlackboardInitializationOrchestrator,
    InitializationStateRepository,
)
from doxagent.codex_runtime.schema import (
    CODEX_GLOBAL_RESEARCH_WORKFLOW_VERSION,
    ArtifactKind,
    ArtifactRef,
    CitationManifest,
    CodexD1Node,
    GlobalResearchBundle,
    GlobalResearchHandoffV1,
    ResearchLane,
)
from doxagent.event_library.bundle_io import RevisionBundleIO
from doxagent.event_library.compiler import _event_order
from doxagent.event_library.contracts import (
    CanonicalAssertionState,
    CanonicalEvent,
    CanonicalEventRevision,
    CanonicalObjectStatus,
    CanonicalRevisionBundle,
    DeltaBatch,
    DeltaItem,
    ReferenceReviewDecision,
    ReferenceReviewMode,
    ReferenceReviewReason,
    RuntimePackageDelta,
)
from doxagent.event_library.importer import RevisionBundleImporter
from doxagent.event_library.quality import (
    compile_bundle_semantic_report,
    compile_quality_report,
)
from doxagent.event_library.reference_review import classify_review, occurrence_anchor
from doxagent.event_library.repository import EventLibraryRepository
from doxagent.event_library.service import EventLibraryService
from doxagent.event_library.validator import BundleValidationContext, ValidationStatus
from doxagent.workflows.codex_event_library.remote_runner import RemoteEventLibraryInitializer
from doxagent.workflows.codex_event_library.schema import EventLibraryRunStage, O2RunResult
from doxagent.workflows.codex_global_research.schema import GlobalResearchRunRequest
from tests.test_event_library_foundation import _copy_bundle, _foundation, _snapshot


def test_reference_review_boundaries_are_frozen_and_exact() -> None:
    anchor = date(2026, 7, 1)
    day_29 = datetime(2026, 7, 30, tzinfo=UTC)
    mode, reason, next_at = classify_review(
        anchor=anchor, as_of=day_29, include_in_reference_view=False
    )
    assert mode is ReferenceReviewMode.IMPLICIT
    assert reason is ReferenceReviewReason.PERIODIC_10D
    assert next_at == datetime(2026, 7, 31, tzinfo=UTC)

    day_30 = datetime(2026, 7, 31, tzinfo=UTC)
    mode, reason, next_at = classify_review(
        anchor=anchor, as_of=day_30, include_in_reference_view=True
    )
    assert mode is ReferenceReviewMode.EXPLICIT
    assert reason is ReferenceReviewReason.INCLUDED_RECHECK_7D
    assert next_at == day_30 + timedelta(days=7)

    _, _, stopped = classify_review(anchor=anchor, as_of=day_30, include_in_reference_view=False)
    assert stopped is None
    assert occurrence_anchor("2026-04..2026-06-24") == date(2026, 6, 24)
    assert occurrence_anchor("2026-Q2") == date(2026, 6, 30)
    assert occurrence_anchor("2026-02") == date(2026, 2, 28)
    assert occurrence_anchor("UNKNOWN") is None


def test_tolerant_residual_wire_normalizes_known_alias_only(tmp_path: Path) -> None:
    path = tmp_path / "residual_delta_resolutions.jsonl"
    path.write_text(
        json.dumps(
            {
                "delta_id": "D1",
                "disposition": "KEEP_PENDING",
                "reason": "audit-only explanation",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    rows, count = RevisionBundleIO._tolerant_residuals(path)
    assert count == 1
    assert rows == [{"delta_id": "D1", "resolution": "KEEP_PENDING"}]


def test_tolerant_event_wire_applies_only_audited_mechanical_defaults(
    tmp_path: Path,
) -> None:
    bundle_path = _copy_bundle(tmp_path / "wire-normalization", "delta:test")
    manifest_path = bundle_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["event_revisions"] = ["events/T1.json"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    event_path = bundle_path / "events/T1.json"
    event = json.loads(event_path.read_text(encoding="utf-8"))
    event.update(
        {
            "ticker": "wrong",
            "title": None,
            "event_type": "UNRECOGNIZED",
            "occurred_at": None,
            "occurrence_time_precision": "BROKEN",
            "is_important": None,
            "include_in_reference_view": None,
            "price_analysis": {"forbidden": True},
        }
    )
    event["facts"][0]["assertion_state"] = "BROKEN"
    event["facts"][0]["consumes_delta_ids"] = ["D1", "D1", "invalid"]
    event_path.write_text(json.dumps(event), encoding="utf-8")

    loaded = RevisionBundleIO.load_tolerant(bundle_path)

    normalized = loaded.bundle.event_revisions[0]
    assert normalized.ticker == "MU"
    assert normalized.title == normalized.canonical_summary
    assert normalized.event_type.value == "OTHER_CORPORATE_EVENT"
    assert normalized.occurred_at is None
    assert normalized.occurrence_time_precision.value == "UNKNOWN"
    assert normalized.is_important is False
    assert normalized.include_in_reference_view is False
    assert normalized.price_analysis is None
    assert normalized.facts[0].assertion_state is CanonicalAssertionState.UNKNOWN
    assert normalized.facts[0].consumes_delta_ids == ["D1"]
    assert {
        "EVENT_TICKER_NORMALIZED",
        "EVENT_TITLE_DEFAULTED",
        "EVENT_TYPE_NORMALIZED",
        "EVENT_OCCURRENCE_LEFT_NULL",
        "EVENT_IMPORTANCE_DEFAULTED",
        "EVENT_REFERENCE_DEFAULTED",
        "PRICE_ANALYSIS_CLEARED",
        "FACT_ASSERTION_STATE_NORMALIZED",
        "FACT_CONSUMES_NORMALIZED",
    }.issubset({item["code"] for item in loaded.normalization_actions})


def test_tolerant_event_wire_falls_back_to_earliest_fact_occurrence(tmp_path: Path) -> None:
    bundle_path = _copy_bundle(tmp_path / "event-time-fallback", "delta:test")
    event_path = bundle_path / "events/T1.json"
    event = json.loads(event_path.read_text(encoding="utf-8"))
    event["occurred_at"] = None
    event["occurrence_time_precision"] = "UNKNOWN"
    event["facts"] = [
        {
            "fact_id": "TF1",
            "proposition": "Later exact-day Fact.",
            "assertion_state": "ACTUAL",
            "subject_time": None,
            "fact_occurred_at": "2026-06-24",
            "fact_occurrence_time_precision": "DAY",
            "consumes_delta_ids": ["D1"],
        },
        {
            "fact_id": "TF2",
            "proposition": "Earlier exact-day Fact.",
            "assertion_state": "ACTUAL",
            "subject_time": None,
            "fact_occurred_at": "2026-06-20",
            "fact_occurrence_time_precision": "DAY",
            "consumes_delta_ids": ["D2"],
        },
        {
            "fact_id": "TF3",
            "proposition": "Earlier but broad Fact.",
            "assertion_state": "ACTUAL",
            "subject_time": None,
            "fact_occurred_at": "2026-01",
            "fact_occurrence_time_precision": "MONTH",
            "consumes_delta_ids": ["D3"],
        },
    ]
    event_path.write_text(json.dumps(event), encoding="utf-8")

    loaded = RevisionBundleIO.load_tolerant(bundle_path)

    normalized = loaded.bundle.event_revisions[0]
    assert normalized.occurred_at == "2026-06-20"
    assert normalized.occurrence_time_precision.value == "DAY"
    assert any(
        item["code"] == "EVENT_OCCURRENCE_FACT_FALLBACK"
        and item["fact_id"] == "TF2"
        for item in loaded.normalization_actions
    )


def test_tolerant_event_wire_uses_earliest_broad_fact_when_no_day_exists(
    tmp_path: Path,
) -> None:
    bundle_path = _copy_bundle(tmp_path / "event-time-broad-fallback", "delta:test")
    event_path = bundle_path / "events/T1.json"
    event = json.loads(event_path.read_text(encoding="utf-8"))
    event["occurred_at"] = None
    event["occurrence_time_precision"] = "UNKNOWN"
    for fact, value, precision in zip(
        event["facts"],
        ["2026-Q3", "2026-05", None, None],
        ["QUARTER", "MONTH", None, None],
        strict=True,
    ):
        fact["fact_occurred_at"] = value
        fact["fact_occurrence_time_precision"] = precision
    event_path.write_text(json.dumps(event), encoding="utf-8")

    normalized = RevisionBundleIO.load_tolerant(bundle_path).bundle.event_revisions[0]

    assert normalized.occurred_at == "2026-05"
    assert normalized.occurrence_time_precision.value == "MONTH"


def test_frozen_view_materializes_complete_schema_index_and_combined_skill(
    tmp_path: Path,
) -> None:
    _, _, workspace, _, orchestrator = _foundation(tmp_path)
    _batch, frozen_root, manifest = orchestrator.prepare(
        snapshot=_snapshot(), run_id="schema-and-prompt", attempt_id="o2", mode="INITIALIZE"
    )
    assert frozen_root is not None and manifest is not None
    expected = {
        "canonical_event_revision.schema.json",
        "revision_bundle_manifest.schema.json",
        "event_retirement.schema.json",
        "residual_delta_resolution.schema.json",
        "reference_review_decision.schema.json",
        "date_resolution_ledger.schema.json",
        "reference_view_decision_ledger.schema.json",
        "candidate_map.schema.json",
        "survey_delta_catalog.schema.json",
        "wave_index.schema.json",
        "schema_index.json",
    }
    assert expected.issubset({item.name for item in (frozen_root / "schemas").iterdir()})
    legacy = json.loads((frozen_root / "schemas/revision_bundle.schema.json").read_text())
    assert "Manifest Only" in legacy["title"]
    skill = workspace.read_text("schema-and-prompt", "attempts/o2/input/skill.md").content
    task = json.loads(
        workspace.read_text("schema-and-prompt", "attempts/o2/input/task.json").content or "{}"
    )
    assert skill is not None and "# Foundation Contract" in skill
    assert (
        hashlib.sha256(skill.encode()).hexdigest()
        == task["prompt_manifest"]["combined_skill_sha256"]
    )
    assert task["content_input_order"][0] == "AGENTS.md"
    assert task["required_bundle_identity"]["contract_version"] == (
        "event-library-maintenance-v3"
    )
    assert task["date_resolution_ledger_path"].endswith(
        "output/work/date_resolution_ledger.jsonl"
    )


def test_prompt_stages_share_analyst_episode_and_search_contract() -> None:
    root = Path("prompts/codex_v2/event_library")
    files = [
        root / "skills/initialize-survey.md",
        root / "skills/initialize-wave.md",
        root / "skills/initialize-reconcile.md",
        root / "skills/incremental-edit.md",
    ]
    for path in files:
        content = path.read_text(encoding="utf-8").casefold().replace("-", " ")
        assert "shared catalyst" in content
        assert "bounded" in content and "window" in content
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in [root / "AGENTS.md", *(root / "skills").glob("*.md")]
    )
    assert "Do not use Web Search" not in combined


def test_o2_run_result_rejects_wrong_base_and_normalizes_model_owned_fields() -> None:
    phase = {
        "attempt_id": "o2-wave-001",
        "stage": EventLibraryRunStage.LOCAL_RECONSTRUCTION,
        "skill_asset": "skills/initialize-wave.md",
        "delta_ids": ["D1"],
        "prior_attempt_paths": [],
    }
    manifest = SimpleNamespace(base_library_version=0)
    wrong_base = O2RunResult(
        status="PENDING",
        stage=EventLibraryRunStage.LOCAL_RECONSTRUCTION,
        base_library_version=1,
        delta_coverage={"total": 1, "resolved": 0, "pending": 1},
    )
    with pytest.raises(ValueError):
        RemoteEventLibraryInitializer._validate_phase_result(
            result=wrong_base,
            phase=phase,
            manifest=manifest,
            expected_final=False,  # type: ignore[arg-type]
        )

    normalized = O2RunResult(
        status="BUNDLE_READY",
        stage=EventLibraryRunStage.SURVEY,
        bundle_path="attempts/other/output/revision_bundle",
        base_library_version=0,
        delta_coverage={"total": 2, "resolved": 2, "pending": 0},
        validation="PASS",
    )
    RemoteEventLibraryInitializer._validate_phase_result(
        result=normalized,
        phase=phase,
        manifest=manifest,
        expected_final=False,  # type: ignore[arg-type]
    )
    assert normalized.status == "PENDING"
    assert normalized.stage is EventLibraryRunStage.LOCAL_RECONSTRUCTION
    assert normalized.bundle_path is None
    assert normalized.delta_coverage.model_dump() == {
        "total": 1,
        "resolved": 0,
        "pending": 1,
    }
    assert normalized.validation == "NOT_RUN"


@pytest.mark.asyncio
async def test_d1_report_bodies_are_verified_and_copied_into_frozen_view(tmp_path: Path) -> None:
    repository = EventLibraryRepository(tmp_path / "library.sqlite3")
    service = EventLibraryService(repository)
    from tests.test_codex_event_library_initialization import AsyncLocalWorkspace

    workspace = AsyncLocalWorkspace(tmp_path / "remote")
    refs: dict[str, dict[str, object]] = {}
    for role in ("c1", "c3", "c5"):
        body = f"# {role.upper()} published report\n"
        path = f"artifacts/{role}.md"
        await workspace.write_text("d1-run", path, body)
        encoded = body.encode()
        refs[role] = {
            "run_id": "d1-run",
            "relative_path": path,
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "size_bytes": len(encoded),
            "published": True,
        }
    upstream = {
        "d1_run_id": "d1-run",
        "d1_published_at": "2026-08-24T00:00:00Z",
        "research_artifacts": refs,
    }
    initializer = RemoteEventLibraryInitializer(
        worker=None,  # type: ignore[arg-type]
        workspace=workspace,  # type: ignore[arg-type]
        service=service,
        local_workspace_root=tmp_path / "local",
    )
    reports = await initializer._load_and_verify_d1_reports(upstream)
    assert reports is not None
    broken = json.loads(json.dumps(upstream))
    broken["research_artifacts"]["c3"]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="hash"):
        await initializer._load_and_verify_d1_reports(broken)
    batch = service.delta_compiler.compile(_snapshot())
    root, manifest = service.views.materialize_frozen_view(
        run_root=tmp_path / "local/d1-copy",
        run_id="d1-copy",
        mode="INITIALIZE",
        batches=[batch],
        as_of=_snapshot().as_of,
        upstream_context_manifest=upstream,
        upstream_d1_reports=reports,
    )
    artifact_manifest = json.loads((root / manifest.upstream_d1_artifact_manifest_path).read_text())
    for role, body in reports.items():
        copied = (root / manifest.upstream_d1_report_paths[role]).read_text()
        assert copied == body
        assert (
            artifact_manifest["reports"][role]["sha256"]
            == hashlib.sha256(body.encode()).hexdigest()
        )


def test_same_bundle_temporary_retirement_is_applied_atomically(tmp_path: Path) -> None:
    repository, service, workspace, _, orchestrator = _foundation(tmp_path)
    batch, _, _ = orchestrator.prepare(
        snapshot=_snapshot(), run_id="lifecycle-v1", attempt_id="o2", mode="INITIALIZE"
    )
    root = workspace.ensure_run("lifecycle-v1")
    service.importer.import_and_publish(
        RevisionBundleIO.load(_copy_bundle(root / "bundle", batch.batch_id))
    )
    suppressed = CanonicalEventRevision(
        event_id="T1",
        ticker="MU",
        title="Invalid duplicate occurrence",
        event_type="OTHER_CORPORATE_EVENT",
        occurred_at="2026-08-20",
        occurrence_time_precision="DAY",
        status="SUPPRESSED",
        canonical_summary="This occurrence is suppressed as invalid.",
        known_event_summary="On 2026-08-20, the invalid duplicate was suppressed.",
        is_important=False,
        include_in_reference_view=False,
        facts=[
            {
                "fact_id": "TF1",
                "proposition": "The extraction duplicated an occurrence.",
                "assertion_state": "ACTUAL",
                "subject_time": "SAME",
                "consumes_delta_ids": [],
            }
        ],
        price_analysis=None,
    )
    bundle = CanonicalRevisionBundle(
        run_id="lifecycle-v2",
        ticker="MU",
        base_library_version=1,
        event_revisions=[suppressed],
        event_retirements=[
            {
                "event_id": "T1",
                "redirect_to_event_id": "E1",
                "reason": "SUPPRESSED_INVALID_OCCURRENCE",
            }
        ],
    )
    publication, outcome = service.importer.import_and_publish(bundle)
    stable = publication.event_id_map["T1"]
    assert outcome.status is ValidationStatus.PASS
    assert repository.event_status("MU", stable, 2) == (CanonicalObjectStatus.SUPPRESSED, "E1")
    assert stable not in {item.event_id for item in repository.published_events("MU", 2)}
    current = repository.get_event("MU", "E2", 2)
    assert current is not None
    merged_payload = current.model_dump(mode="json")
    merged_payload["status"] = "MERGED"
    merged_payload["facts"] = [
        {**fact.model_dump(mode="json"), "consumes_delta_ids": []} for fact in current.facts
    ]
    merged_bundle = CanonicalRevisionBundle(
        run_id="lifecycle-v3",
        ticker="MU",
        base_library_version=2,
        event_revisions=[CanonicalEventRevision.model_validate(merged_payload)],
        event_retirements=[
            {
                "event_id": "E2",
                "redirect_to_event_id": "E1",
                "reason": "MERGED_DUPLICATE_OCCURRENCE",
            }
        ],
    )
    service.importer.import_and_publish(merged_bundle)
    assert repository.event_status("MU", "E2", 3) == (CanonicalObjectStatus.MERGED, "E1")


def test_shared_occurrence_sort_anchor_orders_unknown_last() -> None:
    def event(event_id: str, value: str, precision: str) -> CanonicalEvent:
        return CanonicalEvent(
            event_id=event_id,
            ticker="XYZ",
            title=event_id,
            event_type="OTHER_CORPORATE_EVENT",
            occurred_at=value,
            occurrence_time_precision=precision,
            canonical_summary="Summary.",
            known_event_summary="Known summary.",
            is_important=True,
            include_in_reference_view=True,
            facts=[
                {"fact_id": f"F{event_id[1:]}", "proposition": "Fact.", "assertion_state": "ACTUAL"}
            ],
        )

    ordered = _event_order(
        [
            event("E1", "UNKNOWN", "UNKNOWN"),
            event("E2", "2025", "YEAR"),
            event("E3", "2026-Q2", "QUARTER"),
            event("E4", "2026-05", "MONTH"),
        ]
    )
    assert [item.event_id for item in ordered] == ["E3", "E4", "E2", "E1"]


def test_empty_important_set_is_undefined_without_quadrant_quality_failure(
    tmp_path: Path,
) -> None:
    repository, service, workspace, _, orchestrator = _foundation(tmp_path)
    batch, _, _ = orchestrator.prepare(
        snapshot=_snapshot(), run_id="quality-v1", attempt_id="o2", mode="INITIALIZE"
    )
    root = workspace.ensure_run("quality-v1")
    service.importer.import_and_publish(
        RevisionBundleIO.load(_copy_bundle(root / "bundle", batch.batch_id))
    )
    revisions = []
    for current in repository.published_events("MU", 1):
        payload = current.model_dump(mode="json")
        payload.update({"is_important": False, "include_in_reference_view": False})
        payload["facts"] = [
            {**fact.model_dump(mode="json"), "consumes_delta_ids": []} for fact in current.facts
        ]
        revisions.append(CanonicalEventRevision.model_validate(payload))
    all_false_bundle = CanonicalRevisionBundle(
        run_id="quality-v2",
        ticker="MU",
        base_library_version=1,
        event_revisions=revisions,
    )
    semantic = compile_bundle_semantic_report(all_false_bundle, mode="INITIALIZE")
    assert semantic.release_gate_passed is False
    assert {
        "INITIALIZATION_IMPORTANT_ALL_FALSE",
        "INITIALIZATION_REFERENCE_ALL_FALSE",
    }.issubset({item.code for item in semantic.issues})
    reference_preserved = all_false_bundle.model_copy(
        update={
            "event_revisions": [
                item.model_copy(update={"include_in_reference_view": True})
                for item in all_false_bundle.event_revisions
            ]
        }
    )
    validation = service.validator.validate(
        reference_preserved,
        context=BundleValidationContext(
            run_id="quality-v2",
            ticker="MU",
            base_library_version=1,
            delta_batch_ids=[],
            frozen_as_of=_snapshot().as_of,
            mode="INITIALIZE",
        ),
    )
    assert validation.publishable
    assert validation.status is ValidationStatus.PASS
    assert validation.issues == []
    service.importer.import_and_publish(all_false_bundle)
    report = compile_quality_report(repository, ticker="MU", version=2)
    assert report.important_event_count == 0
    assert report.reference_event_count == 0
    assert report.reference_important_event_recall is None
    assert report.semantic_release_gate_passed is True


def test_reference_review_fields_are_normalized_from_frozen_clock(tmp_path: Path) -> None:
    repository, service, workspace, _, orchestrator = _foundation(tmp_path)
    batch, _, _ = orchestrator.prepare(
        snapshot=_snapshot(), run_id="review-v1", attempt_id="o2", mode="INITIALIZE"
    )
    root = workspace.ensure_run("review-v1")
    service.importer.import_and_publish(
        RevisionBundleIO.load(_copy_bundle(root / "bundle", batch.batch_id))
    )
    current = repository.get_event("MU", "E2", 1)
    assert current is not None
    payload = current.model_dump(mode="json")
    payload["include_in_reference_view"] = False
    payload["facts"] = [
        {**fact.model_dump(mode="json"), "consumes_delta_ids": []} for fact in current.facts
    ]
    as_of = datetime(2026, 8, 24, tzinfo=UTC)
    bundle = CanonicalRevisionBundle(
        run_id="review-v2",
        ticker="MU",
        base_library_version=1,
        event_revisions=[CanonicalEventRevision.model_validate(payload)],
        reference_review_decisions=[
            {
                "event_id": "E2",
                "reviewed_at": "2026-01-01T00:00:00Z",
                "review_mode": "EXPLICIT",
                "candidate_reason": "EXPIRED_30D",
                "changed": False,
                "include_in_reference_view": False,
                "next_review_at": None,
            }
        ],
    )
    context = BundleValidationContext(
        run_id="review-v2",
        ticker="MU",
        base_library_version=1,
        delta_batch_ids=[],
        frozen_as_of=as_of,
        mode="INCREMENTAL",
        review_only=True,
        deterministic_review_fields_by_event={"E2": {}},
    )
    outcome = service.importer.validate(bundle, context=context)
    assert outcome.publishable and outcome.normalized_bundle is not None
    decision = outcome.normalized_bundle.reference_review_decisions[0]
    assert decision.reviewed_at == as_of
    assert decision.changed is True
    assert decision.review_mode is ReferenceReviewMode.IMPLICIT
    publication, _ = service.importer.import_and_publish(bundle, context=context)
    assert publication.published_library_version == 2
    assert repository.reference_review_history_count(ticker="MU", run_id="review-v2") == 1


def test_tolerant_bundle_keeps_valid_events_and_marks_bad_event_delta_pending(
    tmp_path: Path,
) -> None:
    repository, service, workspace, _, orchestrator = _foundation(tmp_path)
    batch, _, _ = orchestrator.prepare(
        snapshot=_snapshot(),
        run_id="mu-tolerant",
        attempt_id="o2-attempt-1",
        mode="INITIALIZE",
    )
    root = workspace.ensure_run("mu-tolerant")
    bundle_path = _copy_bundle(root / "bad-one-event", batch.batch_id)
    first_path = bundle_path / "events" / "T1.json"
    first = json.loads(first_path.read_text(encoding="utf-8"))
    first["related_event_ids"] = []
    first_path.write_text(json.dumps(first), encoding="utf-8")
    bad_path = bundle_path / "events" / "T2.json"
    raw_bad = bad_path.read_text(encoding="utf-8") + "INVALID"
    bad_path.write_text(raw_bad, encoding="utf-8")

    loaded = RevisionBundleIO.load_tolerant(bundle_path)
    assert [item.code for item in loaded.issues] == ["EVENT_FILE_INVALID"]
    assert loaded.invalid_delta_ids == ["D6", "D7"]
    result, outcome = service.importer.import_tolerant_and_publish(loaded)
    assert outcome.status is ValidationStatus.PARTIAL
    assert result.published_library_version == 1
    assert [item.event_id for item in repository.published_events("MU")] == ["E1"]
    assert bad_path.read_text(encoding="utf-8") == raw_bad


def test_mu_996_original_o2_bundle_replays_without_semantic_quarantine(
    tmp_path: Path,
) -> None:
    """The production MU artifact is the regression boundary for O2 ingest."""

    fixture = Path("tests/fixtures/event_library/mu_996_o2_raw")
    batch = DeltaBatch.model_validate_json(
        (fixture / "delta_batch.json").read_text(encoding="utf-8")
    )
    repository = EventLibraryRepository(tmp_path / "event_library.sqlite3")
    repository.save_delta_batch(batch)
    loaded = RevisionBundleIO.load_tolerant(fixture / "revision_bundle")
    context = BundleValidationContext(
        run_id=loaded.bundle.run_id,
        ticker="MU",
        base_library_version=0,
        delta_batch_ids=[batch.batch_id],
        frozen_as_of=datetime(2026, 9, 9, 12, 54, 48, tzinfo=UTC),
        mode="INITIALIZE",
        required_contract_version="event-library-maintenance-v3",
    )

    result, outcome = RevisionBundleImporter(repository).import_tolerant_and_publish(
        loaded,
        context=context,
    )

    assert outcome.publishable
    assert len(outcome.normalized_bundle.event_revisions) == 23
    assert sum(len(event.facts) for event in outcome.normalized_bundle.event_revisions) == 296
    assert outcome.resolved_delta_count == 399
    assert outcome.pending_delta_count == 597
    assert sum(
        issue.code == "DATE_CANDIDATE_SELECTION_DIFFERENCE" for issue in outcome.issues
    ) == 204
    assert result.applied_event_count == 23
    assert result.pending_delta_count == 597
    assert len(repository.published_events("MU")) == 23
    assert sum(len(event.facts) for event in repository.published_events("MU")) == 296
    with sqlite3.connect(repository.path) as connection:
        stored_hash = connection.execute(
            "SELECT raw_bundle_hash FROM bundle_import_diagnostics WHERE batch_id=?",
            (batch.batch_id,),
        ).fetchone()[0]
    assert stored_hash == loaded.raw_bundle_hash
    assert (
        tmp_path
        / "artifacts/event_library/import_diagnostics"
        / f"{loaded.raw_bundle_hash}.json"
    ).is_file()
    repeated, _ = RevisionBundleImporter(repository).import_tolerant_and_publish(
        loaded,
        context=context,
    )
    assert repeated.published_library_version == result.published_library_version == 1

    runner = RemoteEventLibraryInitializer(
        worker=None,  # type: ignore[arg-type]
        workspace=None,  # type: ignore[arg-type]
        service=EventLibraryService(repository),
        local_workspace_root=tmp_path,
    )
    plans = runner._plan_waves(batch)
    manifest = SimpleNamespace(frozen_view_id="fv-mu-996", delta_batch_ids=[batch.batch_id])
    contexts = [
        runner._wave_runtime_context(
            batch=batch,
            manifest=manifest,  # type: ignore[arg-type]
            plan=plan,
            plans=plans,
        )
        for plan in plans
    ]
    assert sum(item["coverage"]["package_grouped_delta_count"] for item in contexts) == 12
    assert sum(item["coverage"]["ungrouped_delta_count"] for item in contexts) == 984


def test_conflicting_and_unknown_delta_mappings_preserve_canonical_content(
    tmp_path: Path,
) -> None:
    repository = EventLibraryRepository(tmp_path / "event_library.sqlite3")
    batch = DeltaBatch(
        batch_id="delta:mapping-fail-open",
        ticker="MU",
        runtime_scope="cdecr:US:MU",
        source_snapshot_id="snapshot",
        source_epoch_id="epoch",
        base_library_version=0,
        items=[
            DeltaItem(
                delta_id=f"D{index}",
                runtime_atomic_id=f"A{index}",
                runtime_atomic_version=1,
                runtime_signature=f"sig-{index}",
                proposition=f"Delta {index}",
                assertion_state=CanonicalAssertionState.ACTUAL,
            )
            for index in (1, 2)
        ],
    )
    repository.save_delta_batch(batch)
    events = []
    for event_no in (1, 2):
        events.append(
            CanonicalEventRevision.model_validate(
                {
                    "event_id": f"T{event_no}",
                    "ticker": "MU",
                    "title": f"Event {event_no}",
                    "event_type": "OTHER_CORPORATE_EVENT",
                    "occurred_at": "UNKNOWN",
                    "occurrence_time_precision": "UNKNOWN",
                    "canonical_summary": f"Event {event_no}",
                    "known_event_summary": f"Event {event_no}",
                    "is_important": False,
                    "include_in_reference_view": False,
                    "facts": [
                        {
                            "fact_id": f"TF{event_no}",
                            "proposition": f"Fact {event_no}",
                            "assertion_state": "ACTUAL",
                            "consumes_delta_ids": ["D1", "D999"],
                        }
                    ],
                }
            )
        )
    bundle = CanonicalRevisionBundle(
        run_id="mapping-fail-open",
        ticker="MU",
        base_library_version=0,
        delta_batch_ids=[batch.batch_id],
        event_revisions=events,
        residual_delta_resolutions=[
            {"delta_id": "D1", "resolution": "KEEP_PENDING"}
        ],
    )

    result, outcome = RevisionBundleImporter(repository).import_and_publish(bundle)

    assert result.applied_event_count == 2
    assert result.pending_delta_count == 2
    assert len(repository.published_events("MU")) == 2
    assert sum(len(item.facts) for item in repository.published_events("MU")) == 2
    assert outcome.normalized_bundle.event_revisions == bundle.event_revisions
    assert {item.code for item in outcome.issues} == {
        "CONFLICTING_DELTA_DISPOSITION",
        "MISSING_DELTA_DISPOSITION",
        "UNKNOWN_DELTA_DISPOSITION_IGNORED",
    }


def test_review_only_publish_keeps_head_and_is_idempotent(tmp_path: Path) -> None:
    repository, service, workspace, _, orchestrator = _foundation(tmp_path)
    batch, _, _ = orchestrator.prepare(
        snapshot=_snapshot(), run_id="mu-v1", attempt_id="o2", mode="INITIALIZE"
    )
    root = workspace.ensure_run("mu-v1")
    service.importer.import_and_publish(
        RevisionBundleIO.load(_copy_bundle(root / "bundle", batch.batch_id))
    )
    reviewed_at = datetime(2026, 8, 24, tzinfo=UTC)
    review = CanonicalRevisionBundle(
        run_id="mu-review-only",
        ticker="MU",
        base_library_version=1,
        reference_review_decisions=[
            ReferenceReviewDecision(
                event_id="E2",
                reviewed_at=reviewed_at,
                review_mode=ReferenceReviewMode.IMPLICIT,
                candidate_reason=ReferenceReviewReason.PERIODIC_10D,
                include_in_reference_view=True,
            )
        ],
    )
    first, _ = service.importer.import_and_publish(review)
    second, _ = service.importer.import_and_publish(review)
    assert first.published_library_version == second.published_library_version == 1
    assert repository.published_version("MU") == 1
    assert repository.reference_review_history_count(ticker="MU", run_id="mu-review-only") == 1


def test_package_aware_waves_cover_346_atomic_deltas_once(tmp_path: Path) -> None:
    repository = EventLibraryRepository(tmp_path / "event_library.sqlite3")
    service = EventLibraryService(repository)
    items = [
        DeltaItem(
            delta_id=f"D{index}",
            runtime_atomic_id=f"A{index}",
            runtime_atomic_version=1,
            runtime_signature=f"s{index}",
            proposition=f"Atomic proposition {index}",
            time=f"2026-08-{(index % 28) + 1:02d}",
            assertion_state=CanonicalAssertionState.ACTUAL,
            entities=["ADI"],
            runtime_hint_ids=[f"R{((index - 1) // 90) + 1}"],
        )
        for index in range(1, 347)
    ]
    packages = []
    for package_no, start in enumerate(range(1, 347, 90), start=1):
        end = min(start + 90, 347)
        packages.append(
            RuntimePackageDelta(
                runtime_hint_id=f"R{package_no}",
                title=f"Package {package_no}",
                runtime_package_version=1,
                member_delta_ids=[f"D{index}" for index in range(start, end)],
            )
        )
    batch = DeltaBatch(
        batch_id="delta:adi-346",
        ticker="ADI",
        runtime_scope="cdecr:US:ADI",
        source_snapshot_id="adi-snapshot",
        source_epoch_id="adi-epoch",
        base_library_version=0,
        items=items,
        runtime_packages=packages,
    )
    runner = RemoteEventLibraryInitializer(
        worker=None,  # type: ignore[arg-type]
        workspace=None,  # type: ignore[arg-type]
        service=service,
        local_workspace_root=tmp_path,
        wave_size=100,
        wave_token_budget=100_000,
    )
    waves = runner._plan_waves(batch)
    flattened = [delta_id for wave in waves for delta_id in wave.delta_ids]
    assert len(waves) == 4
    assert len(flattened) == len(set(flattened)) == 346
    assert set(flattened) == {f"D{index}" for index in range(1, 347)}
    assert all(len(wave.delta_ids) <= 100 for wave in waves)


def test_wave_runtime_context_is_stable_and_preserves_split_package_members(
    tmp_path: Path,
) -> None:
    items = [
        DeltaItem(
            delta_id=f"D{index}",
            runtime_atomic_id=f"A{index}",
            runtime_atomic_version=1,
            runtime_signature=f"s{index}",
            proposition=f"Atomic {index}",
            assertion_state=CanonicalAssertionState.ACTUAL,
            runtime_hint_ids=(["R1", "R2"] if index <= 2 else ["R1"]),
        )
        for index in range(1, 6)
    ]
    batch = DeltaBatch(
        batch_id="delta:split",
        ticker="MU",
        runtime_scope="cdecr:US:MU",
        source_snapshot_id="snapshot",
        source_epoch_id="epoch",
        base_library_version=0,
        items=items,
        runtime_packages=[
            RuntimePackageDelta(
                runtime_hint_id="R1",
                title="Primary package",
                runtime_package_version=1,
                member_delta_ids=[f"D{index}" for index in range(1, 6)],
            ),
            RuntimePackageDelta(
                runtime_hint_id="R2",
                title="Secondary package",
                runtime_package_version=1,
                member_delta_ids=["D1", "D2"],
            ),
        ],
    )
    service = EventLibraryService(EventLibraryRepository(tmp_path / "library.sqlite3"))
    runner = RemoteEventLibraryInitializer(
        worker=None,  # type: ignore[arg-type]
        workspace=None,  # type: ignore[arg-type]
        service=service,
        local_workspace_root=tmp_path,
        wave_size=2,
        wave_token_budget=100_000,
    )
    plans = runner._plan_waves(batch)
    manifest = SimpleNamespace(frozen_view_id="fv-test", delta_batch_ids=[batch.batch_id])
    contexts = [
        runner._wave_runtime_context(
            batch=batch,
            manifest=manifest,  # type: ignore[arg-type]
            plan=plan,
            plans=plans,
        )
        for plan in plans
    ]
    represented = []
    for context in contexts:
        grouped = [
            atomic["delta_id"]
            for group in context["package_groups"]
            for atomic in group["atomics"]
        ]
        ungrouped = [item["delta_id"] for item in context["ungrouped_atomics"]]
        assert grouped + ungrouped == context["assigned_delta_ids"]
        represented.extend(grouped + ungrouped)
        assert context["coverage"]["split_package_count"] == 1
    assert represented == [f"D{index}" for index in range(1, 6)]
    assert contexts[0]["package_groups"][0]["full_member_delta_ids"] == [
        "D1",
        "D2",
        "D3",
        "D4",
        "D5",
    ]
    assert contexts[0]["package_groups"][0]["atomics"][0][
        "secondary_runtime_hint_ids"
    ] == ["R2"]
    assert json.dumps(contexts, sort_keys=True) == json.dumps(
        [
            runner._wave_runtime_context(
                batch=batch,
                manifest=manifest,  # type: ignore[arg-type]
                plan=plan,
                plans=plans,
            )
            for plan in plans
        ],
        sort_keys=True,
    )


@pytest.mark.asyncio
async def test_total_initialization_waits_for_o2_and_pins_document2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    as_of = datetime(2026, 8, 24, tzinfo=UTC)
    reports = {
        role: ArtifactRef(
            workflow_version=CODEX_GLOBAL_RESEARCH_WORKFLOW_VERSION,
            research_lane=ResearchLane.GLOBAL_RESEARCH,
            artifact_id=f"d1-{role}",
            run_id="d1-adi",
            node={"c1": CodexD1Node.C1, "c3": CodexD1Node.C3, "c5": CodexD1Node.C5}[role],
            attempt_id=f"d1-{role}-1",
            kind=ArtifactKind.REPORT,
            relative_path=f"artifacts/{role}.md",
            sha256=role * 32,
            size_bytes=10,
            content_type="text/markdown",
            published=True,
        )
        for role in ("c1", "c3", "c5")
    }
    d1_bundle = GlobalResearchBundle(
        run_id="d1-adi",
        ticker="ADI",
        status="published",
        reports=reports,
        citation_manifest=CitationManifest(run_id="d1-adi", artifact_id="citations"),
        handoff=GlobalResearchHandoffV1(
            run_id="d1-adi",
            ticker="ADI",
            document_artifact_id="d1-document",
            published_at=as_of,
        ),
        published_at=as_of,
    )

    class GlobalStub:
        async def run(self, request: GlobalResearchRunRequest) -> GlobalResearchBundle:
            assert request.run_id == "d1-adi"
            return d1_bundle

    job = TickerJobState(
        job_id="cdecr-adi",
        market="US",
        ticker="ADI",
        mode=TickerJobMode.INITIALIZE,
        as_of=as_of,
        stage=TickerJobStage.DELTA_READY,
        runtime_scope="cdecr:US:ADI",
        registry_path=str(tmp_path / "registry.sqlite3"),
        staging_path=str(tmp_path / "staging"),
        event_library_path=str(tmp_path / "library"),
        epoch_id="adi-epoch",
        runtime_snapshot_id="adi-snapshot",
        delta_batch_id="adi-delta",
        o2_run_id="o2-adi",
        updated_at=as_of,
    )

    class PipelineStub:
        event_library_root = tmp_path / "library"

        def __init__(self) -> None:
            self.upstream_manifest: dict[str, object] | None = None

        async def prepare_runtime_through_delta(self, **_: object) -> TickerPipelineResult:
            return TickerPipelineResult(job=job, delta_batch_id="adi-delta")

        async def run_o2_with_upstream_context(
            self, *, upstream_context_manifest: dict[str, object], **_: object
        ) -> TickerPipelineResult:
            self.upstream_manifest = upstream_context_manifest
            return TickerPipelineResult(
                job=job.model_copy(
                    update={
                        "stage": TickerJobStage.PUBLISHED,
                        "published_library_version": 1,
                    }
                ),
                delta_batch_id="adi-delta",
                published_library_version=1,
            )

    class Document2Stub:
        def __init__(self) -> None:
            self.version: int | None = None

        async def run_pinned(self, *, event_library_version: int, **_: object) -> str:
            self.version = event_library_version
            return "d2-adi-pinned"

    monkeypatch.setattr(
        "doxagent.cdecr_integration.initialization_orchestrator."
        "PublishedEventLibraryReader.reference_view",
        lambda _self, _ticker, *, version=None: SimpleNamespace(
            version=version, sha256="a" * 64, published_at=as_of
        ),
    )
    pipeline = PipelineStub()
    document2 = Document2Stub()
    orchestrator = BlackboardInitializationOrchestrator(
        state_repository=InitializationStateRepository(tmp_path / "state.sqlite3"),
        global_research=GlobalStub(),
        ticker_pipeline=pipeline,  # type: ignore[arg-type]
        document2=document2,
    )
    state = await orchestrator.run(
        run_id="init-adi",
        market="US",
        ticker="ADI",
        as_of=as_of,
        global_request=GlobalResearchRunRequest(
            run_id="d1-adi", ticker="ADI", research_brief="initialization"
        ),
        export_dir=tmp_path / "published",
    )

    assert state.stage is InitializationOrchestrationStage.PUBLISHED
    assert state.d2_run_id == "d2-adi-pinned"
    assert pipeline.upstream_manifest is not None
    assert pipeline.upstream_manifest["delta_batch_id"] == "adi-delta"
    assert document2.version == 1
