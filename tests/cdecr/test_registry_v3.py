from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from cdecr.contracts import (
    AtomicAction,
    ExternalRelationType,
    PackageAction,
    PackageAssignmentRelation,
    PackageExternalRelation,
)
from cdecr.coreference_rules import (
    package_seed_for_event,
    singleton_atomic_event,
    singleton_package,
)
from cdecr.cross_document_contracts import (
    AtomicAssignmentRecord,
    CrossDocumentResult,
    CrossDocumentStatus,
    HoldKind,
    HoldRecord,
    PackageAssignmentRecord,
)
from cdecr.registry import SQLiteCDECRRegistry
from tests.cdecr.test_registry import mention, source


def test_v4_to_v5_migration_removes_confidence_columns_and_upgrades_mentions(
    tmp_path: Path,
) -> None:
    registry = SQLiteCDECRRegistry(tmp_path / "v4.sqlite3")
    registry.initialize()
    registry.save_source(source(), fingerprint="a" * 64)
    registry.save_mention(mention())
    with sqlite3.connect(registry.path) as connection:
        row = connection.execute(
            "SELECT payload_json FROM event_mentions WHERE mention_id = 'MENTION-1'"
        ).fetchone()
        payload = json.loads(row[0])
        payload.pop("source_claim", None)
        payload["extraction_confidence"] = 0.91
        connection.execute(
            "UPDATE event_mentions SET payload_json = ? WHERE mention_id = 'MENTION-1'",
            (json.dumps(payload),),
        )
        for table in (
            "dream_candidates",
            "atomic_assignment_decisions",
            "package_assignment_decisions",
            "package_merge_decisions",
        ):
            connection.execute(
                f"ALTER TABLE {table} ADD COLUMN confidence REAL NOT NULL DEFAULT 0.5"
            )
        connection.execute("PRAGMA user_version=4")
        connection.commit()

    registry.initialize()
    assert registry.pragma_state()["user_version"] == 5
    restored = registry.get_mention("MENTION-1")
    assert restored is not None and restored.source_claim is None
    assert "extraction_confidence" not in restored.model_dump()
    with sqlite3.connect(registry.path) as connection:
        for table in (
            "dream_candidates",
            "atomic_assignment_decisions",
            "package_assignment_decisions",
            "package_merge_decisions",
        ):
            columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
            assert "confidence" not in columns


def test_v3_cross_document_audit_restart_and_recall(tmp_path: Path) -> None:
    registry = SQLiteCDECRRegistry(tmp_path / "v3.sqlite3")
    registry.initialize()
    registry.save_source(source(), fingerprint="a" * 64)
    value = mention()
    registry.save_mention(value)
    event = singleton_atomic_event(value)
    registry.save_atomic_event(event)
    package = singleton_package(event, package_seed_for_event(event, [value]))
    registry.save_package(package)
    run_id = "RUN-CROSS-1"
    assert registry.start_cross_document_run(
        run_id=run_id,
        processing_key="key-1",
        message_id=value.message_id,
        engine_version="v1",
        prompt_version="v1",
        model_config={"m2": "fake"},
    )
    atomic_record = AtomicAssignmentRecord(
        assignment_id="AA-1",
        run_id=run_id,
        mention_id=value.mention_id,
        resulting_event_id=event.event_id,
        action=AtomicAction.CREATE_NEW,
        reason="NO_CANDIDATE",
        hard_conflicts=[],
        identity_conflicts=[],
    )
    package_record = PackageAssignmentRecord(
        assignment_id="PA-1",
        run_id=run_id,
        event_id=event.event_id,
        resulting_package_id=package.package_id,
        action=PackageAction.CREATE_NEW_PACKAGE,
        relation=PackageAssignmentRelation.NOT_RELATED,
        reason="NO_CANDIDATE",
    )
    hold = HoldRecord(
        hold_id="HOLD-1",
        run_id=run_id,
        kind=HoldKind.PACKAGE_CORRECTION,
        subject_id=package.package_id,
        candidate_ids=[],
        reason_codes=["REVIEW"],
        payload={},
    )
    assert registry.save_atomic_assignment(atomic_record)
    assert registry.save_package_assignment(package_record)
    assert registry.save_hold(hold)
    assert registry.save_package_external_relation(
        PackageExternalRelation(
            relation_id="PR-1",
            source_event_id=event.event_id,
            target_package_id=package.package_id,
            relation=ExternalRelationType.RELATED_TO,
        )
    )
    result = CrossDocumentResult(
        run_id=run_id,
        processing_key="key-1",
        message_id=value.message_id,
        status=CrossDocumentStatus.SUCCEEDED,
        atomic_events=[event],
        packages=[package],
        atomic_assignments=[atomic_record],
        package_assignments=[package_record],
        hold_ids=[hold.hold_id],
        model_calls=[],
        candidate_counts={},
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
    )
    assert registry.complete_cross_document_run(result)

    restarted = SQLiteCDECRRegistry(registry.path)
    restarted.initialize()
    assert restarted.pragma_state()["user_version"] == 5
    assert restarted.get_completed_cross_document_result("key-1") == result
    assert restarted.list_open_holds() == [hold]
    recalled = restarted.recall_atomic_event_ids(
        entity_ids=["COMPANY_MU"],
        event_family=value.event_family.value,
        normalized_predicate=value.predicate.normalized,
        schema_type="OPEN",
        reference_period_id=None,
        event_start=value.time.event_start.isoformat() if value.time.event_start else None,
        event_end=None,
        source_fingerprint="a" * 64,
    )
    assert event.event_id in recalled
    packages = restarted.recall_package_ids(
        package_kind=package.package_kind.value,
        package_family=package.package_family.value,
        anchor_entities=package.anchor_entities,
        anchor_artifact_id=package.anchor_artifact_id,
        anchor_period_id=package.anchor_period_id,
        time_start=None,
        time_end=None,
    )
    assert package.package_id in packages
