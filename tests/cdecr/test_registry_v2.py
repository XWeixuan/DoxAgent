from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cdecr.contracts import Language, SourceMessage, SourceType
from cdecr.preprocessing import preprocess_source
from cdecr.registry import ImmutableRecordConflict, SQLiteCDECRRegistry
from cdecr.single_document_contracts import (
    DreamCandidate,
    EvidenceLocator,
    GrounderOutput,
)


def source() -> SourceMessage:
    return SourceMessage(
        message_id="MSG-1",
        source_type=SourceType.NEWS,
        title="Micron update",
        text="Micron raised guidance after strong demand.",
        published_at=datetime(2026, 6, 25, 12, tzinfo=UTC),
        source_name="Wire",
        url="https://example.test/1",
        ticker_hints=["MU"],
        language=Language.EN,
    )


def test_explicit_v1_to_v5_migration_preserves_source(tmp_path: Path) -> None:
    path = tmp_path / "v1.sqlite3"
    payload = source().model_dump_json()
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE source_messages (
                message_id TEXT PRIMARY KEY,
                fingerprint TEXT NOT NULL,
                published_at TEXT NOT NULL,
                source_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            PRAGMA user_version=1;
            """
        )
        connection.execute(
            """
            INSERT INTO source_messages(
                message_id, fingerprint, published_at, source_type, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "MSG-1",
                "a" * 64,
                source().published_at.isoformat(),
                "NEWS",
                payload,
                datetime.now(UTC).isoformat(),
            ),
        )
        connection.commit()
    registry = SQLiteCDECRRegistry(path)
    registry.initialize()
    assert registry.pragma_state()["user_version"] == 5
    assert registry.get_source("MSG-1") == source()
    with sqlite3.connect(path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert {
        "document_processing_runs",
        "preprocessed_documents",
        "dream_candidates",
        "judge_decisions",
        "normalization_decisions",
        "document_run_mentions",
    }.issubset(tables)


def test_preprocessing_records_are_immutable_and_run_failure_survives_restart(
    tmp_path: Path,
) -> None:
    registry = SQLiteCDECRRegistry(tmp_path / "cdecr.sqlite3")
    registry.initialize()
    registry.save_source(source(), fingerprint="a" * 64)
    registry.start_document_run(
        run_id="RUN-1",
        processing_key="KEY-1",
        message_id="MSG-1",
        pipeline_version="p1",
        prompt_version="r1",
        catalog_version="c1",
        model_config={"m2": "fake"},
    )
    result = preprocess_source(source())
    assert registry.save_preprocessing_result("RUN-1", result)
    assert not registry.save_preprocessing_result("RUN-1", result)
    changed = result.model_copy(
        update={
            "document": result.document.model_copy(
                update={"removed_span_count": result.document.removed_span_count + 1}
            )
        }
    )
    with pytest.raises(ImmutableRecordConflict):
        registry.save_preprocessing_result("RUN-1", changed)
    registry.fail_document_run("RUN-1", error_code="test_failure")
    restarted = SQLiteCDECRRegistry(registry.path)
    restarted.initialize()
    with sqlite3.connect(registry.path) as connection:
        row = connection.execute(
            "SELECT status, error_code FROM document_processing_runs WHERE run_id='RUN-1'"
        ).fetchone()
    assert row == ("FAILED", "test_failure")


def test_failed_run_dreamer_candidates_can_resume_by_processing_key(
    tmp_path: Path,
) -> None:
    registry = SQLiteCDECRRegistry(tmp_path / "cdecr.sqlite3")
    registry.initialize()
    registry.save_source(source(), fingerprint="a" * 64)
    registry.start_document_run(
        run_id="RUN-1",
        processing_key="KEY-1",
        message_id="MSG-1",
        pipeline_version="p1",
        prompt_version="r1",
        catalog_version="c1",
        model_config={"m2": "fake"},
    )
    candidate = DreamCandidate(
        candidate_id="candidate:1",
        statement="Micron raised guidance.",
        evidence_locations=[
            EvidenceLocator(
                segment_id="text:0",
                start_char=0,
                end_char=22,
                text="Micron raised guidance",
            )
        ],
    )
    assert registry.save_dream_candidates("RUN-1", [candidate]) == 1
    registry.fail_document_run("RUN-1", error_code="grounder_failed")
    assert registry.get_latest_dream_candidates_for_processing_key("KEY-1") == [candidate]


def test_grounder_batch_checkpoint_survives_failed_run(tmp_path: Path) -> None:
    registry = SQLiteCDECRRegistry(tmp_path / "cdecr.sqlite3")
    registry.initialize()
    registry.save_source(source(), fingerprint="a" * 64)
    registry.start_document_run(
        run_id="RUN-1",
        processing_key="KEY-1",
        message_id="MSG-1",
        pipeline_version="p1",
        prompt_version="r1",
        catalog_version="c1",
        model_config={"m3": "fake"},
    )
    output = GrounderOutput(drafts=[], issue_flags=["none"])
    assert registry.save_grounder_batch(
        run_id="RUN-1",
        processing_key="KEY-1",
        batch_key="BATCH-1",
        output=output,
    )
    registry.fail_document_run("RUN-1", error_code="later_batch_failed")
    assert registry.get_grounder_batch(
        processing_key="KEY-1", batch_key="BATCH-1"
    ) == output


def test_model_call_v2_audit_columns_are_append_only(tmp_path: Path) -> None:
    registry = SQLiteCDECRRegistry(tmp_path / "cdecr.sqlite3")
    registry.initialize()
    kwargs = {
        "model_call_id": "CALL-1",
        "run_id": None,
        "tier": "m2",
        "model": "deepseek-v4-flash",
        "status": "SUCCEEDED",
        "input_tokens": 10,
        "output_tokens": 2,
        "latency_ms": 5,
        "error_code": None,
        "metadata": {"candidate_count": 1},
        "stage": "dreamer",
        "prompt_version": "v1",
        "schema_hash": "b" * 64,
        "input_hash": "c" * 64,
    }
    assert registry.record_model_call(**kwargs)  # type: ignore[arg-type]
    assert not registry.record_model_call(**kwargs)  # type: ignore[arg-type]
    with sqlite3.connect(registry.path) as connection:
        row = connection.execute(
            """
            SELECT stage, prompt_version, schema_hash, input_hash, metadata_json
            FROM model_calls WHERE model_call_id='CALL-1'
            """
        ).fetchone()
    assert row[:4] == ("dreamer", "v1", "b" * 64, "c" * 64)
    assert json.loads(row[4]) == {"candidate_count": 1}
