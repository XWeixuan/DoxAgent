from __future__ import annotations

import hashlib
import json
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


def test_o2_run_result_rejects_wrong_stage_base_path_coverage_or_validation() -> None:
    phase = {
        "attempt_id": "o2-wave-001",
        "stage": EventLibraryRunStage.LOCAL_RECONSTRUCTION,
        "skill_asset": "skills/initialize-wave.md",
        "delta_ids": ["D1"],
        "prior_attempt_paths": [],
    }
    manifest = SimpleNamespace(base_library_version=0)
    invalid = [
        O2RunResult(
            status="PENDING",
            stage=EventLibraryRunStage.SURVEY,
            base_library_version=0,
            delta_coverage={"total": 1, "resolved": 0, "pending": 1},
        ),
        O2RunResult(
            status="PENDING",
            stage=EventLibraryRunStage.LOCAL_RECONSTRUCTION,
            base_library_version=1,
            delta_coverage={"total": 1, "resolved": 0, "pending": 1},
        ),
        O2RunResult(
            status="PENDING",
            stage=EventLibraryRunStage.LOCAL_RECONSTRUCTION,
            base_library_version=0,
            delta_coverage={"total": 2, "resolved": 0, "pending": 2},
        ),
        O2RunResult(
            status="PENDING",
            stage=EventLibraryRunStage.LOCAL_RECONSTRUCTION,
            bundle_path="attempts/other/output/revision_bundle",
            base_library_version=0,
            delta_coverage={"total": 1, "resolved": 0, "pending": 1},
        ),
        O2RunResult(
            status="PENDING",
            stage=EventLibraryRunStage.LOCAL_RECONSTRUCTION,
            base_library_version=0,
            delta_coverage={"total": 1, "resolved": 0, "pending": 1},
            validation="PASS",
        ),
    ]
    for result in invalid:
        with pytest.raises(ValueError):
            RemoteEventLibraryInitializer._validate_phase_result(
                result=result,
                phase=phase,
                manifest=manifest,
                expected_final=False,  # type: ignore[arg-type]
            )


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
    flattened = [item.delta_id for wave in waves for item in wave]
    assert len(waves) == 4
    assert len(flattened) == len(set(flattened)) == 346
    assert set(flattened) == {f"D{index}" for index in range(1, 347)}
    assert all(len(wave) <= 100 for wave in waves)


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
