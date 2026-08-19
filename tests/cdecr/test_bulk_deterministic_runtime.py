from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from cdecr.atomic_exact_recall import ExactCosineRecallIndex
from cdecr.bulk_epoch.atomic_late_stage import _merge_source_mentions_for_single_save
from cdecr.bulk_epoch.embedding import EmbeddingBatchExecutor, EmbeddingWorkItem
from cdecr.bulk_epoch.engine import _recover_atomic_late_state
from cdecr.bulk_epoch.task_ledger import BulkTaskLedger
from cdecr.bulk_epoch.writer import BulkWriter
from cdecr.canonical_field_resolution import (
    CanonicalFieldResolutionEngine,
    FieldOccurrence,
)
from cdecr.contracts import AtomicAction
from cdecr.coreference_rules import add_mention_to_atomic, singleton_atomic_event
from cdecr.cross_document import (
    CrossDocumentPipelineError,
    _active_atomic_target,
    _cosine,
    _pack_mentions_by_candidate_overlap,
    is_content_repairable,
)
from cdecr.cross_document_contracts import AtomicAssignmentRecord
from cdecr.field_coreference_contracts import (
    FieldCoreferenceHints,
    FieldCoreferenceInput,
    FieldNamespace,
)
from cdecr.ports import DecisionAuditRecord, EmbeddingResult
from cdecr.registry import ImmutableRecordConflict, SQLiteCDECRRegistry
from tests.cdecr.test_cross_document import add, metric_mention, source


def registry(path: Path) -> SQLiteCDECRRegistry:
    value = SQLiteCDECRRegistry(path)
    value.initialize()
    return value


def test_n9_overlap_packing_keeps_every_mention_and_candidate_order() -> None:
    mentions = [metric_mention(f"MSG-{index}") for index in range(1, 6)]
    candidate_ids = {
        mentions[0].mention_id: ["A", "B"],
        mentions[1].mention_id: ["X"],
        mentions[2].mention_id: ["B", "C"],
        mentions[3].mention_id: ["X", "Y"],
        mentions[4].mention_id: ["C", "D"],
    }
    candidates = {
        mention_id: [
            SimpleNamespace(event=SimpleNamespace(event_id=event_id))
            for event_id in event_ids
        ]
        for mention_id, event_ids in candidate_ids.items()
    }
    batches = _pack_mentions_by_candidate_overlap(
        mentions,
        cast(Any, candidates),
    )
    packed_mentions = [mention for batch in batches for mention in batch]
    assert sorted(item.mention_id for item in packed_mentions) == sorted(
        item.mention_id for item in mentions
    )
    assert max(map(len, batches)) == 3
    assert mentions[2] in batches[0]
    assert {
        mention_id: [item.event.event_id for item in candidates[mention_id]]
        for mention_id in candidates
    } == candidate_ids


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


def test_provider_failures_are_not_content_repairable() -> None:
    assert is_content_repairable(ValueError("bad shape"))
    assert is_content_repairable(
        CrossDocumentPipelineError("package", "structured_output_invalid")
    )
    for code in ("provider_arrearage", "timeout", "rate_limit_429", "transport_error"):
        assert not is_content_repairable(CrossDocumentPipelineError("package", code))


def test_exact_cosine_matrix_matches_scalar_and_has_stable_order() -> None:
    vectors = {"B": [0.0, 1.0], "A": [1.0, 0.0], "ZERO": [0.0, 0.0]}
    index = ExactCosineRecallIndex(vectors)
    scores = index.scores([0.6, 0.8])
    assert abs(scores["A"] - _cosine([0.6, 0.8], vectors["A"])) < 1e-12
    assert abs(scores["B"] - _cosine([0.6, 0.8], vectors["B"])) < 1e-12
    assert scores["ZERO"] == 0.0
    assert sorted(scores, key=lambda event_id: (-scores[event_id], event_id)) == [
        "B",
        "A",
        "ZERO",
    ]


def test_description_task_batch_uses_one_transaction(tmp_path: Path) -> None:
    value = registry(tmp_path / "description-tasks.sqlite3")
    rows = [
        {
            "registry_scope_id": "scope",
            "batch_id": "batch",
            "mcp_id": f"MCP-{index:06d}",
            "input_hash": f"hash-{index}",
            "status": "SUCCEEDED",
            "payload": {"compressed_description": str(index)},
        }
        for index in range(100)
    ]
    telemetry = value.save_package_registry_description_tasks_v3(rows, chunk_size=256)
    assert telemetry == {"rows": 100, "transactions": 1}
    assert len(
        value.list_package_registry_description_tasks_v3(
            registry_scope_id="scope", batch_id="batch"
        )
    ) == 100


def test_atomic_late_multi_mention_source_advances_target_version_once(tmp_path: Path) -> None:
    value = registry(tmp_path / "atomic-late-version.sqlite3")
    target_mention = metric_mention("TARGET", metric="REVENUE")
    source_first = metric_mention("SOURCE-A", metric="REVENUE")
    source_second = metric_mention("SOURCE-B", metric="REVENUE")
    for message_id, mention in (
        ("TARGET", target_mention),
        ("SOURCE-A", source_first),
        ("SOURCE-B", source_second),
    ):
        add(value, source(message_id), mention)
    target = singleton_atomic_event(target_mention)
    source_event = add_mention_to_atomic(
        singleton_atomic_event(source_first),
        source_second,
        known_mentions=[source_first, source_second],
        claim_conflict=False,
        identity_differences=[],
    )
    value.save_atomic_event(target)

    merged = _merge_source_mentions_for_single_save(
        source=source_event,
        target=target,
        registry=value,
        known_mentions=[target_mention, source_first, source_second],
    )
    value.save_atomic_event(merged)

    assert merged.version == target.version + 1
    assert merged.mention_ids == [
        target_mention.mention_id,
        source_first.mention_id,
        source_second.mention_id,
    ]
    assert value.get_current_atomic_event(target.event_id) == merged


def test_atomic_apply_resolves_redirect_before_using_touched_source(tmp_path: Path) -> None:
    value = registry(tmp_path / "atomic-redirect-target.sqlite3")
    source_mention = metric_mention("REDIRECT-SOURCE", metric="EPS")
    target_mention = metric_mention("REDIRECT-TARGET", metric="EPS")
    for message_id, mention in (
        ("REDIRECT-SOURCE", source_mention),
        ("REDIRECT-TARGET", target_mention),
    ):
        add(value, source(message_id), mention)
    source_event = singleton_atomic_event(source_mention)
    target_event = singleton_atomic_event(target_mention)
    value.save_atomic_event(source_event)
    value.save_atomic_event(target_event)
    value.start_cross_document_run(
        run_id="run",
        processing_key="redirect-test",
        message_id="REDIRECT-SOURCE",
        engine_version="test",
        prompt_version="test",
        model_config={},
    )
    value.save_atomic_redirect(
        source_event_id=source_event.event_id,
        target_event_id=target_event.event_id,
        run_id="run",
        reason="N9_SAME_SINGLETON_ABSORPTION",
    )

    root_id, resolved = _active_atomic_target(
        value, {source_event.event_id: source_event}, source_event.event_id
    )

    assert root_id == target_event.event_id
    assert resolved == target_event


def test_atomic_late_resume_recovers_persisted_assignments_without_replanning(
    tmp_path: Path,
) -> None:
    value = registry(tmp_path / "atomic-late-resume.sqlite3")
    first = metric_mention("RESUME-A", metric="REVENUE")
    second = metric_mention("RESUME-B", metric="EPS")
    for message_id, mention in (("RESUME-A", first), ("RESUME-B", second)):
        add(value, source(message_id), mention)
    value.start_cross_document_run(
        run_id="original-run",
        processing_key="original-processing-key",
        message_id="RESUME-A",
        engine_version="test",
        prompt_version="test",
        model_config={},
    )
    for mention in (first, second):
        event = singleton_atomic_event(mention)
        value.save_atomic_event(event)
        value.save_atomic_assignment(
            AtomicAssignmentRecord(
                assignment_id=f"assignment-{mention.mention_id}",
                run_id="original-run",
                mention_id=mention.mention_id,
                resulting_event_id=event.event_id,
                action=AtomicAction.CREATE_NEW,
                hard_conflicts=[],
                identity_differences=[],
                identity_processing_key="identity-key",
                assignment_policy_version="atomic-assignment-policy-v5-late-convergence",
                reason="N9_RELATED_CREATE_NEW",
            )
        )
    target_event = singleton_atomic_event(first)
    source_event = singleton_atomic_event(second)
    value.save_atomic_event(
        add_mention_to_atomic(
            target_event,
            second,
            known_mentions=[first, second],
            claim_conflict=False,
            identity_differences=[],
        )
    )
    value.save_atomic_redirect(
        source_event_id=source_event.event_id,
        target_event_id=target_event.event_id,
        run_id="original-run",
        reason="test-partial-late-merge",
    )

    events, assignments = _recover_atomic_late_state(value, [first, second])

    assert [event.event_id for event in events] == [target_event.event_id]
    assert set(events[0].mention_ids) == {first.mention_id, second.mention_id}
    assert {assignment.mention_id for assignment in assignments} == {
        first.mention_id,
        second.mention_id,
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
    assert telemetry.batch_sizes == (*((8,) * 16), 2)
    assert telemetry.configured_cap == 8
    assert telemetry.failed_owner_ids == ()
    assert vectors["owner-000"] == [0.0, 1.0]
    assert vectors["owner-129"] == [129.0, 1.0]
    stored = value.list_latest_embeddings(owner_kind="test", model="embed-test", limit=200)
    assert [item.owner_id for item in stored] == sorted(vectors)


def test_embedding_transport_failure_does_not_recursive_split(tmp_path: Path) -> None:
    class TimeoutModels:
        def __init__(self) -> None:
            self.calls: list[int] = []

        def embed(self, texts: list[str], *, stage: str) -> EmbeddingResult:
            del stage
            self.calls.append(len(texts))
            error = RuntimeError("timeout")
            error.code = "transport_timeout"  # type: ignore[attr-defined]
            raise error

    value = registry(tmp_path / "embedding-timeout.sqlite3")
    models = TimeoutModels()
    items = [
        EmbeddingWorkItem(owner_id=str(index), text=str(index), input_hash=str(index))
        for index in range(8)
    ]
    vectors, telemetry = EmbeddingBatchExecutor().run(
        items=items,
        models=models,
        registry=value,
        owner_kind="test",
        stage="runtime-test",
    )
    assert vectors == {}
    assert models.calls == [8]
    assert telemetry.provider_failure_count == 1
    assert telemetry.single_item_failure_count == 0


def test_bulk_writer_submit_is_non_blocking_and_barrier_waits() -> None:
    with BulkWriter() as writer:
        futures = [writer.submit(lambda value=value: value * 2) for value in range(10)]
        writer.barrier()
    assert [future.result() for future in futures] == [value * 2 for value in range(10)]
