from __future__ import annotations

import json
import shutil
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from cdecr.contracts import Language, SourceMessage, SourceType
from doxagent.cdecr_integration.contracts import RuntimeNovelMessageBatch
from doxagent.cdecr_integration.coordinator import TickerCDECRPipelineCoordinator
from doxagent.cdecr_integration.novel_batch import validate_runtime_novel_batch
from doxagent.event_library.bundle_io import RevisionBundleIO
from doxagent.event_library.contracts import FrozenRuntimeSnapshot
from doxagent.event_library.provider import PublishedEventLibraryReader
from doxagent.event_library.quality import compile_quality_report
from doxagent.event_library.repository import EventLibraryRepository
from doxagent.event_library.service import EventLibraryService
from doxagent.monitoring.schema import SourceType as RuntimeSourceType
from doxagent.persistent_runtime.schema import (
    RuntimeSourceMessage,
    W1Confidence,
    W1NoveltyLabel,
    W1Result,
)
from doxagent.persistent_runtime.workers import EventLibraryAwareW1Worker
from doxagent.workflows.codex_document2.inputs import OptionalInput, PublishedEventLibraryProvider
from doxagent.workflows.codex_document2.orchestrator import CodexDocument2Orchestrator
from doxagent.workflows.codex_document2.schema import InputAvailability, ShellRunState

FIXTURE = Path("tests/fixtures/event_library/mu_v1")


def _publish_mu(root: Path) -> EventLibraryRepository:
    repository = EventLibraryRepository(root / "US" / "MU" / "event_library.sqlite3")
    service = EventLibraryService(repository)
    snapshot = FrozenRuntimeSnapshot.model_validate_json(
        (FIXTURE / "runtime_snapshot.json").read_text(encoding="utf-8")
    )
    batch = service.delta_compiler.compile(snapshot)
    bundle_path = root / "bundle"
    shutil.copytree(FIXTURE / "revision_bundle", bundle_path)
    manifest_path = bundle_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["delta_batch_ids"] = [batch.batch_id]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    service.importer.import_and_publish(RevisionBundleIO.load(bundle_path))
    return repository


def test_runtime_novel_batch_is_strict_and_validates_complete_sources() -> None:
    with pytest.raises(ValidationError, match="unique"):
        RuntimeNovelMessageBatch(
            batch_id="b1",
            market="US",
            ticker="MU",
            trading_date=date(2026, 8, 24),
            new_non_social_message_refs=["m1", "m1"],
            source_snapshot_or_lookup_ref="fixture:s1",
        )
    with pytest.raises(ValidationError):
        RuntimeNovelMessageBatch(
            batch_id="b-running",
            market="US",
            ticker="MU",
            trading_date=date(2026, 8, 24),
            status="RUNNING",  # type: ignore[arg-type]
            new_non_social_message_refs=["m1"],
            source_snapshot_or_lookup_ref="fixture:s1",
        )

    source = SourceMessage(
        message_id="m1",
        source_type=SourceType.NEWS,
        title="Micron update",
        text="Micron published a complete material update.",
        published_at=datetime(2026, 8, 24, tzinfo=UTC),
        source_name="fixture",
        url="https://example.test/m1",
        ticker_hints=["MU"],
        language=Language.EN,
    )

    class Registry:
        def get_source(self, message_id: str) -> SourceMessage | None:
            return source if message_id == "m1" else None

    batch = RuntimeNovelMessageBatch(
        batch_id="b1",
        market="us",
        ticker="mu",
        trading_date=date(2026, 8, 24),
        new_non_social_message_refs=["m1"],
        source_snapshot_or_lookup_ref="fixture:s1",
    )
    assert validate_runtime_novel_batch(batch, registry=Registry()) == ["m1"]  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="unknown"):
        validate_runtime_novel_batch(
            batch.model_copy(update={"new_non_social_message_refs": ["missing"]}),
            registry=Registry(),  # type: ignore[arg-type]
        )


def test_published_reader_w1_index_to_detail_and_quality_metrics(tmp_path: Path) -> None:
    repository = _publish_mu(tmp_path / "library")
    reader = PublishedEventLibraryReader(tmp_path / "library", market="US")
    read_only_repository = reader._repository("MU")
    assert read_only_repository is not None and read_only_repository.read_only
    with pytest.raises(PermissionError, match="read-only"):
        with read_only_repository._write():
            pass
    index = reader.known_index("MU")
    assert index is not None and index.version == 1
    assert set(line.split(" | ", 1)[0] for line in index.known_event_index.splitlines()) == {
        "E1",
        "E2",
    }
    details = reader.event_details("MU", ["E1"], version=1)
    assert details is not None and details.events[0].event_id == "E1"
    assert len(details.events[0].facts) == 4

    class Delegate:
        def __init__(self) -> None:
            self.contexts: list[dict[str, object]] = []

        def classify(
            self, message: RuntimeSourceMessage, context: dict[str, object]
        ) -> W1Result:
            del message
            self.contexts.append(context)
            if len(self.contexts) == 1:
                return W1Result(
                    is_new=False,
                    novelty_label=W1NoveltyLabel.KNOWN_EVENT_RECAP,
                    matched_known_event_ids=["E1"],
                    confidence=W1Confidence.LOW,
                    reasoning="Need full detail.",
                )
            return W1Result(
                is_new=True,
                novelty_label=W1NoveltyLabel.MATERIAL_UPDATE,
                matched_known_event_ids=["E1"],
                confidence=W1Confidence.HIGH,
                reasoning="Detail confirms a material update.",
            )

    delegate = Delegate()
    worker = EventLibraryAwareW1Worker(delegate, reader)
    result = worker.classify(
        RuntimeSourceMessage(
            source_message_id="runtime-m1",
            ticker="MU",
            source_type=RuntimeSourceType.MEDIA,
            source_id="fixture",
            title="Update",
            body="New detail",
        ),
        {},
    )
    assert result.novelty_label is W1NoveltyLabel.MATERIAL_UPDATE
    first = delegate.contexts[0]["canonical_event_library"]
    second = delegate.contexts[1]["canonical_event_library"]
    assert isinstance(first, dict) and first["mode"] == "KNOWN_INDEX"
    assert isinstance(second, dict) and second["mode"] == "EVENT_DETAILS"
    assert "known_event_index" not in second
    assert len(second["events"][0]["facts"]) == 4  # type: ignore[index]

    report = compile_quality_report(repository, ticker="MU")
    assert report.known_index_event_coverage == 1
    assert report.event_detail_fact_coverage == 1
    assert report.reference_important_event_recall == 1
    assert report.reference_duplicate_event_mentions == 0
    assert report.delta_total == 9
    assert report.delta_resolution_rate + report.pending_delta_ratio == 1


@pytest.mark.asyncio
async def test_document2_provider_is_published_read_only_and_cutoff_safe(
    tmp_path: Path,
) -> None:
    _publish_mu(tmp_path / "library")
    provider = PublishedEventLibraryProvider(
        PublishedEventLibraryReader(tmp_path / "library")
    )
    available = await provider.load(
        ticker="MU", as_of=datetime(2030, 1, 1, tzinfo=UTC)
    )
    assert available.status is InputAvailability.AVAILABLE
    assert available.metadata["read_only"] is True
    assert isinstance(available.payload, str)
    assert available.payload.startswith("fields: event_id | event_time | precision | title\n\n")
    assert available.metadata["contract_version"] == "reference-view-md-v4"
    assert available.metadata["content_type"] == "text/markdown; charset=utf-8"
    absent = await PublishedEventLibraryProvider(
        PublishedEventLibraryReader(tmp_path / "missing")
    ).load(ticker="MU", as_of=datetime(2030, 1, 1, tzinfo=UTC))
    assert absent.status is InputAvailability.ABSENT


def test_document2_injects_full_reference_payload_once_per_shell_thread() -> None:
    orchestrator = object.__new__(CodexDocument2Orchestrator)
    prepared = SimpleNamespace(
        event_library=OptionalInput(
            status=InputAvailability.AVAILABLE,
            payload={"ticker": "MU", "version": 2, "events": [{"event_id": "E1"}]},
            source_run_id="event-library:MU:v2",
            as_of=datetime(2026, 8, 24, tzinfo=UTC),
            metadata={"version": 2, "sha256": "abc", "read_only": True},
        )
    )
    state = ShellRunState(shell_id="shell-1", workspace_run_id="d2-shell-1")
    first = orchestrator._event_library_turn_context(prepared, state)  # type: ignore[arg-type]
    assert first["payload"] is not None
    state.event_library_injected = True
    later = orchestrator._event_library_turn_context(prepared, state)  # type: ignore[arg-type]
    assert "payload" not in later
    assert later["metadata"] == {"version": 2, "sha256": "abc", "read_only": True}
    assert later["payload_injected_earlier_in_thread"] is True


@pytest.mark.asyncio
async def test_empty_update_is_idempotent_noop_without_cdecr_or_o2(tmp_path: Path) -> None:
    calls = {"factory": 0, "run": 0, "o2": 0}

    class Registry:
        def get_source(self, message_id: str) -> None:
            raise AssertionError(message_id)

    class Runner:
        def run(self, message_ids: list[str]) -> None:
            calls["run"] += 1
            raise AssertionError(message_ids)

    def runtime_factory(binding: object) -> tuple[object, object]:
        del binding
        calls["factory"] += 1
        return Registry(), Runner()

    def o2_factory(service: object) -> object:
        del service
        calls["o2"] += 1
        raise AssertionError("O2 must not be constructed")

    coordinator = TickerCDECRPipelineCoordinator(
        registry_root=tmp_path / "runtime",
        state_root=tmp_path / "state",
        event_library_root=tmp_path / "library",
        providers=[],
        runtime_factory=runtime_factory,  # type: ignore[arg-type]
        o2_factory=o2_factory,  # type: ignore[arg-type]
    )
    batch = RuntimeNovelMessageBatch(
        batch_id="empty-2026-08-24",
        market="US",
        ticker="MU",
        trading_date=date(2026, 8, 24),
        new_non_social_message_refs=[],
        source_snapshot_or_lookup_ref="fixture:empty",
    )
    first = await coordinator.update(
        batch=batch, export_dir=tmp_path / "exports", run_o2=True
    )
    second = await coordinator.update(
        batch=batch, export_dir=tmp_path / "exports", run_o2=True
    )
    assert first.job.stage.value == "FINALIZED_NOOP"
    assert second.job.job_id == first.job.job_id
    assert coordinator.status(market="US", ticker="MU") == second.job
    assert calls == {"factory": 1, "run": 0, "o2": 0}
