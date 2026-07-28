from __future__ import annotations

from cdecr.config import CDECRSettings
from cdecr.cross_document import _compiled_active_hard_conflicts
from cdecr.cross_document_contracts import HardConflictCode
from cdecr.identity_compiler import CompiledMentionIdentity


def compiled(
    mention_id: str,
    *,
    metric: str | None = None,
    companies: list[str] | None = None,
) -> CompiledMentionIdentity:
    return CompiledMentionIdentity(
        mention_id=mention_id,
        missing_required_fields=[],
        field_links_hash="links",
        processing_key=f"key-{mention_id}",
        primary_metric_id=metric,
        primary_metric_field_path=(
            "quantities[0].metric_id" if metric is not None else None
        ),
        primary_metric_trust_reason=(
            "CORE_ONTOLOGY_EXACT" if metric is not None else None
        ),
        principal_company_ids=companies or [],
        principal_company_field_paths=(
            ["participants[0]"] if companies else []
        ),
        principal_company_trust_reason=(
            "CONTEXTUAL_COMPANY_EXACT" if companies else None
        ),
    )


def test_active_boundaries_block_different_trusted_primary_metrics_and_issuers() -> None:
    conflicts, audit = _compiled_active_hard_conflicts(
        compiled("M-IN", metric="REVENUE", companies=["COMPANY_MU"]),
        [compiled("M-OLD", metric="EPS", companies=["COMPANY_OTHER"])],
    )
    assert conflicts == [HardConflictCode.ISSUER, HardConflictCode.METRIC]
    assert audit["incoming_primary_metric_field_path"] == "quantities[0].metric_id"
    assert audit["incoming_primary_metric_trust_reason"] == "CORE_ONTOLOGY_EXACT"


def test_missing_or_non_primary_metric_is_neutral_not_a_hard_conflict() -> None:
    conflicts, _ = _compiled_active_hard_conflicts(
        compiled("M-IN"),
        [compiled("M-OLD", metric="EPS")],
    )
    assert conflicts == []


def test_runtime_settings_enable_trusted_hard_boundaries_by_default() -> None:
    settings = CDECRSettings(_env_file=None)

    assert settings.atomic_hard_cannot_link_mode == "enforce"
