from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from cdecr.bulk_epoch.embedding import EmbeddingBatchExecutor, EmbeddingWorkItem
from cdecr.bulk_epoch.stage_runtime import StageReadSnapshot
from cdecr.bulk_epoch.task_ledger import BulkTaskLedger
from cdecr.bulk_epoch.writer import BulkWriter
from cdecr.canonical_field_resolution import (
    CanonicalFieldResolutionEngine,
    FieldOccurrence,
)
from cdecr.coreference_rules import (
    package_seed_for_event,
    singleton_atomic_event,
    singleton_package,
)
from cdecr.field_coreference_contracts import (
    CanonicalFieldRegistryEntry,
    FieldCoreferenceHints,
    FieldCoreferenceInput,
    FieldNamespace,
)
from cdecr.identity_compiler import IdentityCompiler
from cdecr.package_engine import PackageProfileCompiler
from cdecr.ports import DecisionAuditRecord, EmbeddingResult
from cdecr.registry import ImmutableRecordConflict, SQLiteCDECRRegistry
from tests.cdecr.test_cross_document import add, metric_mention, source


def registry(path: Path) -> SQLiteCDECRRegistry:
    value = SQLiteCDECRRegistry(path)
    value.initialize()
    return value


def test_batch_audit_is_idempotent_and_does_not_hide_conflict(tmp_path: Path) -> None:
    value = registry(tmp_path / "audit.sqlite3")
    records = [
        DecisionAuditRecord(
            audit_id=f"audit-{index}",
            decision_type="TEST",
            subject_id=str(index),
            payload={"value": index},
        )
        for index in range(7)
    ]
    first = value.append_decision_audits(records, chunk_size=3)
    second = value.append_decision_audits(records, chunk_size=3)
    conflict = value.append_decision_audits(
        [records[0].model_copy(update={"payload": {"value": 999}})]
    )
    assert first == {
        "inserted": 7,
        "reused": 0,
        "conflicted": 0,
        "degraded": 0,
        "transactions": 3,
        "retries": 0,
    }
    assert second["reused"] == 7
    assert conflict["conflicted"] == 1
    with pytest.raises(ImmutableRecordConflict):
        value.append_decision_audit(
            records[0].model_copy(update={"payload": {"value": -1}})
        )


def test_batch_task_ledger_preserves_attempt_and_stage_times(tmp_path: Path) -> None:
    value = registry(tmp_path / "tasks.sqlite3")
    value.start_bulk_epoch(
        epoch_id="epoch",
        manifest_hash="manifest",
        orchestrator_version="test",
        message_ids=[],
    )
    ledger = BulkTaskLedger(registry=value, epoch_id="epoch")
    base = [
        {
            "stage": "N12_A",
            "task_id": f"event-{index}",
            "input_hash": f"input-{index}",
            "snapshot_hash": "snapshot",
        }
        for index in range(4)
    ]
    ledger.start_many(base)
    ledger.start_many(base[:1])
    ledger.finish_many([{**item, "decision_ref": {"ok": True}} for item in base])
    rows = value.list_bulk_epoch_tasks("epoch", stage="N12_A")
    assert [row["status"] for row in rows] == ["SUCCEEDED"] * 4
    assert rows[0]["attempt_count"] == 2
    assert [row["attempt_count"] for row in rows[1:]] == [1, 1, 1]


def test_package_anchor_create_merges_concurrent_parent_aliases(tmp_path: Path) -> None:
    value = registry(tmp_path / "anchor-race.sqlite3")
    entries = [
        CanonicalFieldRegistryEntry(
            id="field:shared-parent",
            namespace=FieldNamespace.PACKAGE_ANCHOR,
            canonical_text=text,
            aliases=[text],
        )
        for text in (
            "Micron FY2026 Q3 results",
            "Micron FY2026 Q3 earnings report",
        )
    ]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(value.create_field_registry_entry, entries))
    stored = value.get_field_registry_entry("field:shared-parent")
    assert stored is not None
    assert sorted(results) == [False, True]
    assert set(stored.aliases) == {
        "Micron FY2026 Q3 results",
        "Micron FY2026 Q3 earnings report",
    }


def test_field_epoch_localizes_failure_and_skips_completed_task_on_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = registry(tmp_path / "field-resume.sqlite3")
    document = source("MSG-1")
    mention = metric_mention("MSG-1")
    add(value, document, mention)
    engine = CanonicalFieldResolutionEngine(
        registry=value,
        knowledge_base=cast(Any, SimpleNamespace(catalog_hash="test")),
        field_resolver=cast(
            Any,
            SimpleNamespace(
                begin_epoch_snapshot=lambda: None,
                end_epoch_snapshot=lambda: None,
            ),
        ),
    )
    occurrences = [
        FieldOccurrence(
            mention_id=mention.mention_id,
            field_path=f"open_attributes[{index}]",
            value=FieldCoreferenceInput(
                namespace=FieldNamespace.OBJECT_PROJECT,
                raw_value=raw,
                local_context="context",
                hints=FieldCoreferenceHints(),
            ),
            catalog="",
        )
        for index, raw in enumerate(("good project", "bad project"))
    ]
    monkeypatch.setattr(engine, "prime_participant_documents", lambda _documents: None)
    monkeypatch.setattr(
        engine,
        "routed_occurrences",
        lambda _source, _mentions: occurrences,
    )
    monkeypatch.setattr(
        engine,
        "package_hint_occurrences",
        lambda _source, _mentions, *, run_id=None: [],
    )
    fail_bad = True
    calls: list[str] = []

    def resolve_group(
        _source: object, group: list[FieldOccurrence], *, run_id: str | None
    ) -> tuple[int, int, int]:
        del run_id
        nonlocal fail_bad
        raw = group[0].value.raw_value
        calls.append(raw)
        if raw == "bad project" and fail_bad:
            raise ImmutableRecordConflict("simulated local conflict")
        return 1, 0, 1

    monkeypatch.setattr(engine, "_resolve_groups", resolve_group)
    transitions: list[tuple[str, str, str | None]] = []
    first = engine.resolve_epoch(
        [(document, [mention])],
        run_id="run",
        max_workers=2,
        task_hook=lambda task_id, status, error: transitions.append(
            (task_id, status, error)
        ),
    )
    assert first.group_count == 2
    assert first.failed_group_count == 1
    assert any(status == "FAILED" for _, status, _ in transitions)

    good_task_id = engine._epoch_field_task_key(occurrences[0])
    fail_bad = False
    calls.clear()
    resumed = engine.resolve_epoch(
        [(document, [mention])],
        run_id="run",
        max_workers=2,
        completed_task_ids={good_task_id},
    )
    assert calls == ["bad project"]
    assert resumed.failed_group_count == 0
    assert resumed.skipped_group_count == 1


def test_stage_snapshot_and_chunk_apply_match_single_writes(tmp_path: Path) -> None:
    batch = registry(tmp_path / "batch.sqlite3")
    single = registry(tmp_path / "single.sqlite3")
    mention = metric_mention("MSG-1")
    for value in (batch, single):
        add(value, source("MSG-1"), mention)
    profile = IdentityCompiler(registry=single, catalog_hash="test").compile(
        mention
    ).identity_profile
    assert profile is not None
    event = singleton_atomic_event(mention, identity_profile=profile)
    single.save_atomic_event(event)
    batch_result = batch.save_atomic_stage_batch([{"event": event}])
    assert batch_result["degraded"] == 0
    assert batch.get_current_atomic_event(event.event_id) == single.get_current_atomic_event(
        event.event_id
    )
    seed = package_seed_for_event(event, [mention])
    package = singleton_package(event, seed)
    single.save_package(package)
    package_result = batch.save_package_stage_batch([{"package": package}])
    assert package_result["degraded"] == 0
    assert batch.get_current_package(package.package_id) == single.get_current_package(
        package.package_id
    )
    snapshot = StageReadSnapshot.load(batch)
    assert snapshot.mentions_by_id[mention.mention_id] == mention
    assert snapshot.events_by_id[event.event_id] == event
    assert snapshot.packages_by_id[package.package_id] == package
    compiler = PackageProfileCompiler(batch)
    assert compiler.compile(
        package,
        [event],
        seed=seed,
        force_version=1,
        read_snapshot=snapshot,
    ) == compiler.compile(package, [event], seed=seed, force_version=1)


class _EmbeddingModels:
    def embed(self, texts: list[str], *, stage: str) -> EmbeddingResult:
        assert stage == "runtime-test"
        return EmbeddingResult(
            model="embed-test",
            dimensions=2,
            vectors=[[float(text.removeprefix("text-")), 1.0] for text in texts],
            latency_ms=1,
        )


def test_embedding_batch_executor_preserves_owner_mapping(tmp_path: Path) -> None:
    value = registry(tmp_path / "embedding.sqlite3")
    items = [
        EmbeddingWorkItem(
            owner_id=f"owner-{index:03d}",
            text=f"text-{index}",
            input_hash=hashlib.sha256(f"text-{index}".encode()).hexdigest(),
        )
        for index in reversed(range(130))
    ]
    vectors, telemetry = EmbeddingBatchExecutor().run(
        items=items,
        models=_EmbeddingModels(),
        registry=value,
        owner_kind="test",
        stage="runtime-test",
    )
    assert telemetry.batch_sizes == (64, 64, 2)
    assert telemetry.failed_owner_ids == ()
    assert vectors["owner-000"] == [0.0, 1.0]
    assert vectors["owner-129"] == [129.0, 1.0]
    stored = value.list_latest_embeddings(owner_kind="test", model="embed-test", limit=200)
    assert [item.owner_id for item in stored] == sorted(vectors)


def test_bulk_writer_submit_is_non_blocking_and_barrier_waits() -> None:
    with BulkWriter() as writer:
        futures = [writer.submit(lambda value=value: value * 2) for value in range(10)]
        writer.barrier()
    assert [future.result() for future in futures] == [value * 2 for value in range(10)]
