from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from cdecr.coreference_rules import (
    singleton_atomic_event,
)
from cdecr.registry import SCHEMA_VERSION, SQLiteCDECRRegistry
from tests.cdecr.test_registry import mention, package, source


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
        ):
            connection.execute(
                f"ALTER TABLE {table} ADD COLUMN confidence REAL NOT NULL DEFAULT 0.5"
            )
        connection.execute("PRAGMA user_version=4")
        connection.commit()

    registry.initialize()
    assert registry.pragma_state()["user_version"] == SCHEMA_VERSION
    restored = registry.get_mention("MENTION-1")
    assert restored is not None and restored.source_claim is None
    assert "extraction_confidence" not in restored.model_dump()
    with sqlite3.connect(registry.path) as connection:
        for table in (
            "dream_candidates",
            "atomic_assignment_decisions",
            "package_assignment_decisions",
        ):
            columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
            assert "confidence" not in columns




def test_rebuild_derived_state_preserves_inputs_and_field_state(tmp_path: Path) -> None:
    registry = SQLiteCDECRRegistry(tmp_path / "rebuild.sqlite3")
    registry.initialize()
    registry.save_source(source(), fingerprint="a" * 64)
    value = mention()
    registry.save_mention(value)
    event = singleton_atomic_event(value)
    registry.save_atomic_event(event)
    current_package = package()
    registry.save_package(current_package)

    deleted = registry.rebuild_derived_state()

    assert deleted["atomic_events"] == 1
    assert deleted["packages"] == 1
    assert registry.get_source(value.message_id) is not None
    assert registry.get_mention(value.mention_id) == value
    assert registry.list_current_atomic_events() == []
    assert registry.list_current_packages() == []
    with sqlite3.connect(registry.path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert "hold_queue" not in tables
