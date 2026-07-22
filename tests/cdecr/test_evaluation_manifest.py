from __future__ import annotations

import json
from pathlib import Path

import pytest

from cdecr.evaluation import EvaluationManifestRow, select_mu_evaluation_rows

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "dev_plan" / "CDECR" / "baselines" / "mu_2026-06-25_step2_eval_manifest.json"
SNAPSHOT = ROOT / ".tmp" / "cdecr" / "baselines" / "us_mu_2026-06-25.jsonl"


def test_tracked_evaluation_manifest_contains_no_news_body_and_has_24_valid_rows() -> None:
    raw = MANIFEST.read_text(encoding="utf-8")
    payload = json.loads(raw)
    assert len(payload["rows"]) == 24
    rows = [EvaluationManifestRow.model_validate(item) for item in payload["rows"]]
    assert sum(item.length_bucket == "long" for item in rows) == 4
    assert sum(item.length_bucket == "medium" for item in rows) == 10
    assert sum(item.length_bucket == "short" for item in rows) == 10
    assert len({item.source_name for item in rows}) >= 8
    assert all(
        "title" not in item and "text" not in item and "url" not in item for item in payload["rows"]
    )


def test_manifest_matches_deterministic_local_baseline_selection() -> None:
    if not SNAPSHOT.exists():
        pytest.skip("ignored local full-text baseline is not present")
    source_rows = [json.loads(line) for line in SNAPSHOT.read_text(encoding="utf-8").splitlines()]
    selected_ids = {str(item["source_row_id"]) for item in select_mu_evaluation_rows(source_rows)}
    manifest_ids = {
        str(item["source_row_id"])
        for item in json.loads(MANIFEST.read_text(encoding="utf-8"))["rows"]
    }
    assert selected_ids == manifest_ids
