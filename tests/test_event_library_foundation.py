from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from doxagent.cdecr_integration.contracts import RuntimeRegistryBinding
from doxagent.cdecr_integration.registry_resolver import PerTickerRegistryResolver
from doxagent.cdecr_integration.workflow_runner import CDECRWorkflowRunner
from doxagent.codex_runtime.schema import (
    CODEX_EVENT_LIBRARY_WORKFLOW_VERSION,
    CodexEventLibraryAgentRole,
    CodexEventLibraryNode,
    ResearchLane,
)
from doxagent.codex_worker.workspace_store import LocalWorkspaceStore
from doxagent.event_library.bundle_io import RevisionBundleIO
from doxagent.event_library.contracts import (
    CanonicalAssertionState,
    CanonicalEvent,
    CanonicalEventRevision,
    CanonicalObjectStatus,
    CanonicalRevisionBundle,
    DeltaBatch,
    DeltaBatchStatus,
    DeltaItem,
    DeltaResolution,
    FrozenRuntimeSnapshot,
    MUGoldContract,
    ResidualDeltaResolution,
)
from doxagent.event_library.repository import EventLibraryRepository
from doxagent.event_library.service import EventLibraryService
from doxagent.event_library.validator import RevisionBundleValidator, ValidationStatus
from doxagent.workflows.codex_event_library.orchestrator import (
    EventLibraryFoundationOrchestrator,
)
from doxagent.workflows.codex_event_library.runner import EventLibraryAgentRunner

FIXTURE = Path("tests/fixtures/event_library/mu_v1")


def _snapshot() -> FrozenRuntimeSnapshot:
    return FrozenRuntimeSnapshot.model_validate_json(
        (FIXTURE / "runtime_snapshot.json").read_text(encoding="utf-8")
    )


def _copy_bundle(target: Path, batch_id: str) -> Path:
    shutil.copytree(FIXTURE / "revision_bundle", target)
    manifest_path = target / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["delta_batch_ids"] = [batch_id]
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return target


def _foundation(tmp_path: Path) -> tuple[
    EventLibraryRepository,
    EventLibraryService,
    LocalWorkspaceStore,
    EventLibraryAgentRunner,
    EventLibraryFoundationOrchestrator,
]:
    repository = EventLibraryRepository(tmp_path / "event_library.sqlite3")
    service = EventLibraryService(repository)
    workspace = LocalWorkspaceStore(tmp_path / "workspaces")
    runner = EventLibraryAgentRunner(workspace=workspace, repository=repository)
    orchestrator = EventLibraryFoundationOrchestrator(service=service, agent_runner=runner)
    return repository, service, workspace, runner, orchestrator


def test_frozen_contracts_reject_runtime_provenance_fields() -> None:
    payload = {
        "event_id": "E1",
        "ticker": "MU",
        "title": "Occurrence",
        "event_type": "DISCLOSURE",
        "occurred_at": "2026-08-24",
        "occurrence_time_precision": "DAY",
        "status": "ACTIVE",
        "canonical_summary": "A canonical summary.",
        "known_event_summary": "A distinguishing known event summary.",
        "is_important": False,
        "include_in_reference_view": False,
        "related_event_ids": [],
        "supersedes_event_id": None,
        "derived_from_event_ids": [],
        "facts": [
            {
                "fact_id": "F1",
                "proposition": "A fact.",
                "assertion_state": "ACTUAL",
                "subject_time": None,
                "entities": ["Micron"],
            }
        ],
        "price_analysis": None,
        "sources": ["forbidden"],
    }
    with pytest.raises(ValidationError):
        CanonicalEvent.model_validate(payload)


def test_per_ticker_registry_binding_is_deterministic_and_rejects_mismatch(
    tmp_path: Path,
) -> None:
    resolver = PerTickerRegistryResolver(tmp_path / "registries")
    binding = resolver.bind(market="us", ticker="mu")
    assert binding.runtime_scope == "cdecr:US:MU"
    assert Path(binding.registry_path) == tmp_path / "registries" / "US" / "MU" / "runtime.sqlite3"
    resolver.verify(binding)
    mismatched = binding.model_copy(update={"ticker": "NVDA"})
    with pytest.raises(ValueError, match="canonical"):
        resolver.verify(mismatched)

    legacy = resolver.resolve(market="US", ticker="NVDA")
    legacy_path = Path(legacy.registry_path)
    legacy_path.parent.mkdir(parents=True)
    with sqlite3.connect(legacy_path) as connection:
        connection.execute("CREATE TABLE legacy_runtime_data(id TEXT PRIMARY KEY)")
    with pytest.raises(ValueError, match="unbound"):
        resolver.bind(market="US", ticker="NVDA")


def test_workflow_runner_uses_explicit_ids_and_noops_without_eligible_documents() -> None:
    class Registry:
        def get_source(self, message_id: str) -> object | None:
            return object() if message_id in {"m1", "m2"} else None

        def get_bulk_epoch(self, epoch_id: str) -> dict[str, str]:
            return {"epoch_id": epoch_id, "status": "FINALIZED"}

    class Documents:
        def process_batch(self, message_ids: list[str]) -> list[SimpleNamespace]:
            return [
                SimpleNamespace(message_id=item, status=SimpleNamespace(value="FAILED"))
                for item in message_ids
            ]

    class Bulk:
        last_epoch_id: str | None = None

        def process_batch(self, message_ids: list[str]) -> list[object]:
            raise AssertionError(f"Bulk Epoch must not run for {message_ids}")

    runner = CDECRWorkflowRunner(
        binding=RuntimeRegistryBinding(
            market="US",
            ticker="MU",
            runtime_scope="cdecr:US:MU",
            registry_path="runtime.sqlite3",
        ),
        registry=Registry(),  # type: ignore[arg-type]
        document_processor=Documents(),
        bulk_epoch_engine=Bulk(),
    )
    result = runner.run(["m2", "m1", "m2"])
    assert result.status == "FINALIZED_NOOP"
    assert result.message_ids == ["m2", "m1"]
    assert result.eligible_document_count == 0


def test_workflow_runner_returns_the_finalized_bulk_epoch() -> None:
    class Registry:
        def get_source(self, message_id: str) -> object | None:
            return object() if message_id == "m1" else None

        def get_bulk_epoch(self, epoch_id: str) -> dict[str, str]:
            return {"epoch_id": epoch_id, "status": "FINALIZED"}

    class Documents:
        def process_batch(self, message_ids: list[str]) -> list[SimpleNamespace]:
            return [
                SimpleNamespace(message_id=item, status=SimpleNamespace(value="SUCCEEDED"))
                for item in message_ids
            ]

    class Bulk:
        last_epoch_id: str | None = None

        def process_batch(self, message_ids: list[str]) -> list[object]:
            assert message_ids == ["m1"]
            self.last_epoch_id = "bulk-epoch:fixture"
            return []

    runner = CDECRWorkflowRunner(
        binding=RuntimeRegistryBinding(
            market="US",
            ticker="MU",
            runtime_scope="cdecr:US:MU",
            registry_path="runtime.sqlite3",
        ),
        registry=Registry(),  # type: ignore[arg-type]
        document_processor=Documents(),
        bulk_epoch_engine=Bulk(),
    )
    result = runner.run(["m1"])
    assert result.status == "FINALIZED"
    assert result.epoch_id == "bulk-epoch:fixture"
    assert result.eligible_document_count == 1


def test_mu_gold_frozen_snapshot_to_published_v1_without_model(tmp_path: Path) -> None:
    repository, service, workspace, runner, orchestrator = _foundation(tmp_path)
    gold = MUGoldContract.model_validate_json(
        (FIXTURE / "mu_gold.json").read_text(encoding="utf-8")
    )
    snapshot = _snapshot()
    batch, frozen_root, manifest = orchestrator.prepare(
        snapshot=snapshot,
        run_id="mu-event-library-foundation-v1",
        attempt_id="o2-attempt-1",
        mode="INITIALIZE",
    )
    assert len(batch.items) == 9
    assert frozen_root is not None and manifest is not None
    assert manifest.pending_delta_count == 9
    pending_wire = (frozen_root / "delta" / "pending_atomics.json").read_text(
        encoding="utf-8"
    )
    assert "runtime_atomic_id" not in pending_wire
    assert "mention" not in pending_wire.lower()
    assert "source" not in pending_wire.lower()
    assert (frozen_root / "known_event_index.md").read_text(encoding="utf-8") == ""
    assert (frozen_root / "delta" / "runtime_packages.json").is_file()
    assert (frozen_root / "delta" / "package_index.md").is_file()
    assert manifest.runtime_packages_path == "delta/runtime_packages.json"

    request = runner.build_worker_request(
        prepared=runner.prepare_attempt(
            run_id="mu-event-library-foundation-v1",
            attempt_id="o2-attempt-1",
            manifest=manifest,
        ),
        ticker="MU",
        cutoff_at=datetime(2026, 8, 24, tzinfo=UTC),
    )
    assert request.workflow_version == CODEX_EVENT_LIBRARY_WORKFLOW_VERSION
    assert request.research_lane is ResearchLane.EVENT_LIBRARY
    assert request.node is CodexEventLibraryNode.O2_MAINTAIN
    assert request.agent_role is CodexEventLibraryAgentRole.O2
    assert request.max_subagents == 0

    run_root = workspace.ensure_run("mu-event-library-foundation-v1")
    bundle_path = _copy_bundle(
        run_root / "attempts" / "o2-attempt-1" / "output" / "revision_bundle_gold",
        batch.batch_id,
    )
    result, outcome, exports = orchestrator.import_promoted_or_reviewed_bundle(
        run_id="mu-event-library-foundation-v1",
        bundle_path=bundle_path,
        export_dir=tmp_path / "exports",
    )
    assert outcome.status is ValidationStatus.PASS
    assert result.published_library_version == 1
    assert result.applied_event_count == gold.expectation.expected_event_count
    assert result.pending_delta_count == len(gold.expectation.expected_pending_delta_ids)
    assert result.dropped_delta_count == len(gold.expectation.expected_dropped_delta_ids)
    events = repository.published_events("MU")
    assert len(events) == gold.expectation.expected_event_count
    assert sum(len(event.facts) for event in events) == gold.expectation.expected_fact_count
    assert repository.active_fact_ids("MU") == {
        fact.fact_id for event in events for fact in event.facts
    }
    assert {event.event_id for event in events} == {"E1", "E2"}
    assert all(event.price_analysis is None for event in events)
    assert all("consumes_delta_ids" not in event.model_dump() for event in events)
    index = service.views.known_event_index("MU")
    assert len(index.splitlines()) == 2
    assert index.splitlines()[0].startswith("E2 | 2026-08-10 |")
    assert "event_type" not in index
    reference = service.views.reference_view("MU")
    assert reference.startswith("fields: event_id | event_time | precision | title\n\n")
    assert reference.count("\nevent_type:") == 2
    assert "ticker:" not in reference and "version:" not in reference
    assert "importance" not in reference
    assert "Fact subject_time: FY2026-Q3" in reference
    assert "Proposition: Micron reported FY2026 Q3 revenue" in reference
    assert "Fact subject_time: null" in reference
    assert "Proposition: Micron disclosed 16 strategic customer agreements" in reference
    assert exports["json"].is_file() and exports["markdown"].is_file()
    assert exports["reference_view_agent"].read_bytes() == exports[
        "reference_view_human"
    ].read_bytes()
    assert exports["known_event_index"].read_text(encoding="utf-8") == index

    loaded = RevisionBundleIO.load(bundle_path)
    assert all(
        "entities" not in fact.model_dump()
        for event in loaded.event_revisions
        for fact in event.facts
    )
    repeated, repeated_outcome = service.importer.import_and_publish(loaded)
    assert repeated_outcome.status is ValidationStatus.PASS
    assert repeated.published_library_version == 1
    assert repository.published_version("MU") == 1
    assert service.delta_compiler.compile(snapshot).batch_id == batch.batch_id

    incremental_batch = DeltaBatch(
        batch_id="delta:fact-suppression",
        ticker="MU",
        runtime_scope="cdecr:US:MU",
        source_snapshot_id="fact-suppression",
        source_epoch_id="epoch-fact-suppression",
        base_library_version=1,
        items=[
            DeltaItem(
                delta_id="D1",
                runtime_atomic_id="a10-new-detail",
                runtime_atomic_version=1,
                runtime_signature="new-detail-signature",
                proposition="Micron added a new detail to the earnings occurrence.",
                time="2026-06-24",
                assertion_state=CanonicalAssertionState.ACTUAL,
            )
        ],
    )
    repository.save_delta_batch(incremental_batch)
    event_one = repository.get_event("MU", "E1")
    assert event_one is not None
    revision_payload = event_one.model_dump(mode="json")
    revision_payload["facts"] = [
        {**fact.model_dump(mode="json"), "consumes_delta_ids": []}
        for fact in event_one.facts[:3]
    ] + [
        {
            "fact_id": "TF1",
            "proposition": "Micron added a new detail to the earnings occurrence.",
            "assertion_state": "ACTUAL",
            "subject_time": None,
            "consumes_delta_ids": ["D1"],
        }
    ]
    incremental_bundle = CanonicalRevisionBundle(
        run_id="mu-event-library-incremental-v2",
        ticker="MU",
        base_library_version=1,
        delta_batch_ids=[incremental_batch.batch_id],
        event_revisions=[CanonicalEventRevision.model_validate(revision_payload)],
    )
    incremental_result, _ = service.importer.import_and_publish(incremental_bundle)
    assert incremental_result.published_library_version == 2
    assert repository.fact_status("MU", "F4") == (
        CanonicalObjectStatus.SUPPRESSED,
        None,
    )
    assert "F4" not in repository.active_fact_ids("MU")


def test_validator_localizes_missing_delta_and_atomic_publish_rolls_back(tmp_path: Path) -> None:
    repository, service, workspace, _, orchestrator = _foundation(tmp_path)
    snapshot = _snapshot()
    batch, _, manifest = orchestrator.prepare(
        snapshot=snapshot,
        run_id="mu-partial",
        attempt_id="o2-attempt-1",
        mode="INITIALIZE",
    )
    assert manifest is not None
    run_root = workspace.ensure_run("mu-partial")
    bundle_path = _copy_bundle(
        run_root / "attempts" / "o2-attempt-1" / "output" / "partial_bundle",
        batch.batch_id,
    )
    event_path = bundle_path / "events" / "T2.json"
    event = json.loads(event_path.read_text(encoding="utf-8"))
    event["facts"][1]["consumes_delta_ids"] = []
    event_path.write_text(json.dumps(event, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    bundle = RevisionBundleIO.load(bundle_path)
    outcome = RevisionBundleValidator(repository).validate(bundle)
    assert outcome.status is ValidationStatus.PARTIAL
    assert outcome.normalized_bundle is not None
    pending = {
        item.delta_id
        for item in outcome.normalized_bundle.residual_delta_resolutions
        if item.resolution.value == "KEEP_PENDING"
    }
    assert pending == {"D7", "D8"}

    with sqlite3.connect(repository.path) as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_head_switch BEFORE INSERT ON library_heads
            BEGIN SELECT RAISE(ABORT, 'simulated head failure'); END
            """
        )
    with pytest.raises(sqlite3.IntegrityError, match="simulated head failure"):
        repository.publish_bundle(outcome.normalized_bundle, source_bundle=bundle)
    assert repository.published_version("MU") == 0
    assert repository.published_events("MU") == []
    with sqlite3.connect(repository.path) as connection:
        connection.execute("DROP TRIGGER fail_head_switch")
    published, published_outcome = service.importer.import_and_publish(bundle)
    repeated, repeated_outcome = service.importer.import_and_publish(bundle)
    assert published.published_library_version == 1
    assert repeated.published_library_version == 1
    assert published_outcome.status is ValidationStatus.PARTIAL
    assert repeated_outcome.status is ValidationStatus.PASS


def test_stale_base_is_a_hard_validation_failure(tmp_path: Path) -> None:
    repository, service, workspace, _, orchestrator = _foundation(tmp_path)
    batch, _, _ = orchestrator.prepare(
        snapshot=_snapshot(),
        run_id="mu-stale-base",
        attempt_id="o2-attempt-1",
        mode="INITIALIZE",
    )
    run_root = workspace.ensure_run("mu-stale-base")
    bundle_path = _copy_bundle(
        run_root / "attempts" / "o2-attempt-1" / "output" / "bundle",
        batch.batch_id,
    )
    bundle = RevisionBundleIO.load(bundle_path)
    result, _ = service.importer.import_and_publish(bundle)
    assert result.published_library_version == 1

    stale_batch = DeltaBatch(
        batch_id="delta:stale-second",
        ticker="MU",
        runtime_scope="cdecr:US:MU",
        source_snapshot_id="stale-second",
        source_epoch_id="epoch-stale-second",
        base_library_version=0,
        status=DeltaBatchStatus.PENDING,
        items=[
            DeltaItem(
                delta_id="D1",
                runtime_atomic_id="stale-atomic",
                runtime_atomic_version=1,
                runtime_signature="signature",
                proposition="A later fact.",
                time="2026-08-24",
                assertion_state=CanonicalAssertionState.ACTUAL,
            )
        ],
    )
    repository.save_delta_batch(stale_batch)
    stale = CanonicalRevisionBundle(
        run_id="different-run",
        ticker="MU",
        base_library_version=0,
        delta_batch_ids=[stale_batch.batch_id],
        residual_delta_resolutions=[
            ResidualDeltaResolution(
                delta_id="D1", resolution=DeltaResolution.KEEP_PENDING
            )
        ],
    )
    outcome = RevisionBundleValidator(repository).validate(stale)
    assert outcome.status is ValidationStatus.FAIL
    assert {item.code for item in outcome.issues} == {"STALE_BASE"}
