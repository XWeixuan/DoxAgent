from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest

from cdecr.config import CDECRSettings
from cdecr.contracts import SourceMessage
from cdecr.evaluation import GoldDocument, evaluate_results, select_mu_evaluation_rows
from cdecr.models import (
    DashScopeEmbeddingClient,
    DashScopeStructuredModelClient,
    ModelTier,
    probe_models,
)
from cdecr.registry import SQLiteCDECRRegistry
from cdecr.single_document import SingleDocumentProcessor
from cdecr.single_document_contracts import ProcessingStatus

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = ROOT / ".tmp" / "cdecr" / "baselines" / "us_mu_2026-06-25.jsonl"
GOLD = ROOT / ".tmp" / "cdecr" / "evaluation" / "mu_2026-06-25_step2_gold.json"


@pytest.mark.cdecr_real_models
@pytest.mark.cdecr_real_step2
def test_real_step2_mu_24_quality_and_idempotence(tmp_path: Path) -> None:
    if os.getenv("CDECR_RUN_REAL_STEP2_EVALS") != "1":
        pytest.skip("set CDECR_RUN_REAL_STEP2_EVALS=1 to consume M1-M4 quota")
    settings = CDECRSettings()
    api_key = settings.require_dashscope()
    model_names = {
        ModelTier.M1: settings.model_m1,
        ModelTier.M2: settings.model_m2,
        ModelTier.M3: settings.model_m3,
        ModelTier.M4: settings.model_m4,
    }
    # Fail before the expensive 24-document run if any provider tier is unavailable.
    probes = probe_models(
        api_key=api_key,
        base_url=settings.dashscope_base_url,
        tiers=[ModelTier.M1, ModelTier.M2, ModelTier.M3, ModelTier.M4],
        model_names=model_names,
        dimensions=settings.embedding_dimensions,
        timeout_seconds=settings.model_timeout_seconds,
        fallback_api_keys=settings.dashscope_fallback_api_keys(),
    )
    assert len(probes) == 4
    assert SNAPSHOT.exists(), "ignored full-text MU baseline is required"
    assert GOLD.exists(), "M4-assisted and human-reviewed gold file is required"

    rows = [json.loads(line) for line in SNAPSHOT.read_text(encoding="utf-8").splitlines()]
    selected = select_mu_evaluation_rows(rows)
    gold_documents = [
        GoldDocument.model_validate(item)
        for item in json.loads(GOLD.read_text(encoding="utf-8"))["documents"]
    ]
    assert len(gold_documents) == 24

    registry = SQLiteCDECRRegistry(tmp_path / "step2-eval.sqlite3")
    registry.initialize()
    sources: list[SourceMessage] = []
    for row in selected:
        source = SourceMessage.model_validate(row["message"])
        registry.save_source(source, fingerprint=str(row["document_fingerprint"]))
        sources.append(source)
    embedding = DashScopeEmbeddingClient(
        api_key=api_key,
        base_url=settings.dashscope_base_url,
        model=settings.model_m1,
        dimensions=settings.embedding_dimensions,
        timeout_seconds=settings.model_timeout_seconds,
        fallback_api_keys=settings.dashscope_fallback_api_keys(),
    )
    m2 = DashScopeStructuredModelClient(
        tier=ModelTier.M2,
        api_key=api_key,
        base_url=settings.dashscope_base_url,
        model=settings.model_m2,
        timeout_seconds=settings.model_timeout_seconds,
        fallback_api_keys=settings.dashscope_fallback_api_keys(),
    )
    m3 = DashScopeStructuredModelClient(
        tier=ModelTier.M3,
        api_key=api_key,
        base_url=settings.dashscope_base_url,
        model=settings.model_m3,
        timeout_seconds=settings.model_timeout_seconds,
        fallback_api_keys=settings.dashscope_fallback_api_keys(),
    )
    m4 = DashScopeStructuredModelClient(
        tier=ModelTier.M4,
        api_key=api_key,
        base_url=settings.dashscope_base_url,
        model=settings.model_m4,
        timeout_seconds=settings.model_timeout_seconds,
        fallback_api_keys=settings.dashscope_fallback_api_keys(),
    )
    processor = SingleDocumentProcessor(
        registry=registry,
        embedding_client=embedding,
        m2_client=m2,
        m3_client=m3,
        m4_client=m4,
        model_m1=settings.model_m1,
        model_m2=settings.model_m2,
        model_m3=settings.model_m3,
        model_m4=settings.model_m4,
    )
    results = processor.process_batch([source.message_id for source in sources])
    assert all(result.status is ProcessingStatus.SUCCEEDED for result in results)
    for result, source in zip(results, sources, strict=True):
        for mention in result.mentions:
            mention.validate_evidence(source)

    metrics = evaluate_results(results, gold_documents)
    assert metrics.document_count == 24
    assert metrics.completed_count == 24
    assert metrics.valid_schema_rate == 1.0
    assert metrics.valid_evidence_rate == 1.0
    assert metrics.event_recall >= 0.90
    assert metrics.mention_precision >= 0.90
    # schema_projection is explicitly deferred by the current user-approved contract.
    assert metrics.projection_core_accuracy is None

    with sqlite3.connect(registry.path) as connection:
        calls_before = connection.execute("SELECT COUNT(*) FROM model_calls").fetchone()[0]
        mentions_before = connection.execute("SELECT COUNT(*) FROM event_mentions").fetchone()[0]
    rerun = processor.process_batch([source.message_id for source in sources])
    assert all(result.reused and not result.model_calls for result in rerun)
    with sqlite3.connect(registry.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM model_calls").fetchone()[0] == calls_before
        assert (
            connection.execute("SELECT COUNT(*) FROM event_mentions").fetchone()[0]
            == mentions_before
        )
