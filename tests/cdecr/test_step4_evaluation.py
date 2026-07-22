from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cdecr.contracts import Language, SourceMessage, SourceType
from cdecr.step4_evaluation import (
    M4ReviewOutput,
    load_step4_corpus,
    step4_evaluation_lock,
)


def _source() -> SourceMessage:
    return SourceMessage(
        message_id="doxatlas:raw_media:row-1",
        source_type=SourceType.NEWS,
        title="Micron update",
        text="x" * 250,
        published_at=datetime(2026, 6, 25, 12, tzinfo=UTC),
        source_name="Wire",
        url="https://example.test/row-1",
        ticker_hints=["MU"],
        language=Language.EN,
    )


def test_step4_corpus_requires_safe_manifest_to_match_ignored_snapshot(
    tmp_path: Path,
) -> None:
    source = _source()
    snapshot = tmp_path / "snapshot.jsonl"
    snapshot.write_text(
        json.dumps(
            {
                "source_row_id": "row-1",
                "document_fingerprint": "a" * 64,
                "message": source.model_dump(mode="json"),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "manifest_version": "test-v1",
                "rows": [
                    {
                        "source_row_id": "row-1",
                        "document_fingerprint": "a" * 64,
                        "length_bucket": "short",
                        "text_chars": 250,
                        "source_name": "Wire",
                        "expected_event_families": ["FINANCIAL_PERFORMANCE"],
                        "review_status": "PENDING",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    version, corpus = load_step4_corpus(snapshot, manifest, limit=1)
    assert version == "test-v1"
    assert corpus[0][1] == source

    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["rows"][0]["document_fingerprint"] = "b" * 64
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        load_step4_corpus(snapshot, manifest, limit=1)


def test_step4_evaluation_rejects_a_concurrent_process(tmp_path: Path) -> None:
    registry = tmp_path / "evaluation.sqlite3"
    with step4_evaluation_lock(registry):
        with pytest.raises(RuntimeError, match="another CDECR"):
            with step4_evaluation_lock(registry):
                pass


def test_m4_review_output_contract_excludes_schema_projections() -> None:
    serialized = json.dumps(M4ReviewOutput.model_json_schema(), sort_keys=True)
    assert "schema_projection" not in serialized
    assert "FinancialMetricProjection" not in serialized
    assert "GuidanceProjection" not in serialized
    assert "AnalystActionProjection" not in serialized
