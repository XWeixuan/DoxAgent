from __future__ import annotations

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
from doxagent.event_library.contracts import (
    CanonicalAssertionState,
    CanonicalRevisionBundle,
    DeltaBatch,
    DeltaItem,
    ReferenceReviewDecision,
    ReferenceReviewMode,
    ReferenceReviewReason,
    RuntimePackageDelta,
)
from doxagent.event_library.reference_review import classify_review, occurrence_anchor
from doxagent.event_library.repository import EventLibraryRepository
from doxagent.event_library.service import EventLibraryService
from doxagent.event_library.validator import ValidationStatus
from doxagent.workflows.codex_event_library.remote_runner import RemoteEventLibraryInitializer
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

    _, _, stopped = classify_review(
        anchor=anchor, as_of=day_30, include_in_reference_view=False
    )
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
    assert repository.reference_review_history_count(
        ticker="MU", run_id="mu-review-only"
    ) == 1


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
            node={"c1": CodexD1Node.C1, "c3": CodexD1Node.C3, "c5": CodexD1Node.C5}[
                role
            ],
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
