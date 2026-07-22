from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cdecr.config import CDECRSettings
from cdecr.data import DoxAtlasRawMediaReader, build_manifest
from cdecr.models import ModelTier, probe_models
from cdecr.ports import SourceQuery


@pytest.mark.cdecr_real_db
@pytest.mark.real_db
def test_real_mu_baseline_matches_tracked_manifest() -> None:
    if os.getenv("CDECR_RUN_REAL_DB_TESTS") != "1":
        pytest.skip("set CDECR_RUN_REAL_DB_TESTS=1 to read the real DoxAtlas database")
    settings = CDECRSettings()
    url, key = settings.require_supabase()
    query = SourceQuery(
        market="US",
        ticker="MU",
        start_at=datetime(2026, 6, 25, tzinfo=UTC),
        end_at=datetime(2026, 6, 26, tzinfo=UTC),
        limit=200,
        min_text_chars=200,
    )
    with DoxAtlasRawMediaReader(
        supabase_url=url,
        publishable_key=key,
        timeout_seconds=settings.http_timeout_seconds,
        page_size=200,
    ) as reader:
        batch = reader.read(query)
    assert len(batch.accepted) >= 100
    assert len({record.message.source_name for record in batch.accepted}) >= 10
    manifest_path = (
        Path(__file__).resolve().parents[2]
        / "dev_plan"
        / "CDECR"
        / "baselines"
        / "mu_2026-06-25_manifest.json"
    )
    expected = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert build_manifest(batch) == expected


@pytest.mark.cdecr_real_models
@pytest.mark.real_api
def test_real_m1_to_m4_probes() -> None:
    if os.getenv("CDECR_RUN_REAL_MODEL_TESTS") != "1":
        pytest.skip("set CDECR_RUN_REAL_MODEL_TESTS=1 to consume DashScope quota")
    settings = CDECRSettings()
    results = probe_models(
        api_key=settings.require_dashscope(),
        base_url=settings.dashscope_base_url,
        tiers=list(ModelTier),
        model_names={
            ModelTier.M1: settings.model_m1,
            ModelTier.M2: settings.model_m2,
            ModelTier.M3: settings.model_m3,
            ModelTier.M4: settings.model_m4,
        },
        dimensions=1024,
        timeout_seconds=settings.model_timeout_seconds,
        fallback_api_keys=settings.dashscope_fallback_api_keys(),
    )
    assert [result["tier"] for result in results] == ["m1", "m2", "m3", "m4"]
    assert results[0]["dimensions"] == 1024
