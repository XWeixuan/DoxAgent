"""Compact Document 1 context for downstream Document 2 tasks."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from doxagent.models import GlobalResearchDocument, ResearchSection

FreshnessLabel = Literal["recent_30d", "background", "unknown"]


class ContextPackModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ClaimDigest(ContextPackModel):
    claim_id: str
    text: str
    source_section: str
    category: str
    freshness: FreshnessLabel


class MarketTraceDigest(ContextPackModel):
    summary: str


class Document1KnownGap(ContextPackModel):
    gap_id: str
    description: str
    source_section: str
    severity: Literal["info", "warning"] = "info"


class Document1ContextPack(ContextPackModel):
    ticker: str
    generated_from_document_id: str
    window_days: int = 30
    research_window: str = "recent_30d_with_long_cycle_background"
    recent_company_facts: list[ClaimDigest] = Field(default_factory=list)
    recent_industry_macro_market_drivers: list[ClaimDigest] = Field(default_factory=list)
    market_trace: MarketTraceDigest | None = None
    catalysts: list[ClaimDigest] = Field(default_factory=list)
    risks: list[ClaimDigest] = Field(default_factory=list)
    key_variables: list[ClaimDigest] = Field(default_factory=list)
    known_gaps: list[Document1KnownGap] = Field(default_factory=list)
    stale_background_facts: list[ClaimDigest] = Field(default_factory=list)
    compaction: dict[str, Any] = Field(default_factory=dict)


def build_document1_context_pack(
    document: GlobalResearchDocument,
    *,
    window_days: int = 30,
    max_claim_chars: int = 360,
) -> Document1ContextPack:
    """Build the compact Document1ContextPack from a stable GlobalResearchDocument."""

    section_specs: tuple[tuple[str, str, str], ...] = (
        ("fundamental_report", "company_fact", "recent_company_facts"),
        ("industry_report", "industry_driver", "recent_industry_macro_market_drivers"),
        ("macro_report", "macro_driver", "recent_industry_macro_market_drivers"),
        ("market_trace_report", "market_driver", "recent_industry_macro_market_drivers"),
        ("market_narrative_report", "market_narrative", "recent_industry_macro_market_drivers"),
    )
    recent_company_facts: list[ClaimDigest] = []
    recent_drivers: list[ClaimDigest] = []
    catalysts: list[ClaimDigest] = []
    risks: list[ClaimDigest] = []
    key_variables: list[ClaimDigest] = []
    stale_background_facts: list[ClaimDigest] = []
    known_gaps: list[Document1KnownGap] = []

    for section_key, category, bucket_name in section_specs:
        section = getattr(document, section_key, None)
        if not isinstance(section, ResearchSection):
            continue
        claim = _claim_from_section(
            document.ticker,
            section_key,
            section,
            category=category,
            max_claim_chars=max_claim_chars,
        )
        if claim.freshness == "background":
            stale_background_facts.append(claim)
        elif bucket_name == "recent_company_facts":
            recent_company_facts.append(claim)
        else:
            recent_drivers.append(claim)

        if claim.freshness != "background" and _looks_like_catalyst(claim.text):
            catalysts.append(
                claim.model_copy(
                    update={"claim_id": f"{claim.claim_id}:catalyst", "category": "catalyst"}
                )
            )
        if _looks_like_risk(claim.text):
            risks.append(
                claim.model_copy(
                    update={"claim_id": f"{claim.claim_id}:risk", "category": "risk"}
                )
            )
        if _looks_like_key_variable(claim.text):
            key_variables.append(
                claim.model_copy(
                    update={
                        "claim_id": f"{claim.claim_id}:key_variable",
                        "category": "key_variable",
                    }
                )
            )
        known_gaps.extend(_known_gaps_from_section(section_key, section))

    market_trace = None
    if isinstance(document.market_trace_report, ResearchSection):
        market_trace = MarketTraceDigest(
            summary=_compact_text(document.market_trace_report.summary, max_claim_chars),
        )

    return Document1ContextPack(
        ticker=document.ticker,
        generated_from_document_id=document.document_id,
        window_days=window_days,
        recent_company_facts=recent_company_facts,
        recent_industry_macro_market_drivers=recent_drivers,
        market_trace=market_trace,
        catalysts=catalysts,
        risks=risks,
        key_variables=key_variables,
        known_gaps=known_gaps,
        stale_background_facts=stale_background_facts,
        compaction={
            "mode": "document1_context_pack",
            "primary_window_days": window_days,
            "omitted_full_text": True,
            "max_claim_chars": max_claim_chars,
            "source_sections": [
                key
                for key, _category, _bucket in section_specs
                if isinstance(getattr(document, key, None), ResearchSection)
            ],
        },
    )


def _claim_from_section(
    ticker: str,
    section_key: str,
    section: ResearchSection,
    *,
    category: str,
    max_claim_chars: int,
) -> ClaimDigest:
    text = _compact_text(section.summary or section.text, max_claim_chars)
    freshness = _freshness_from_text(text)
    return ClaimDigest(
        claim_id=f"{section_key}:summary",
        text=text or f"{ticker} {section_key} summary unavailable.",
        source_section=section_key,
        category=category,
        freshness=freshness,
    )


def _document_sections(document: GlobalResearchDocument) -> list[ResearchSection]:
    sections = [
        document.fundamental_report,
        document.industry_report,
        document.macro_report,
        document.market_trace_report,
    ]
    if document.market_narrative_report is not None:
        sections.append(document.market_narrative_report)
    return sections


def _known_gaps_from_section(
    section_key: str,
    section: ResearchSection,
) -> list[Document1KnownGap]:
    gaps: list[Document1KnownGap] = []
    text = f"{section.summary} {section.text}".lower()
    if any(marker in text for marker in ("gap", "unknown", "unavailable", "missing")):
        gaps.append(
            Document1KnownGap(
                gap_id=f"{section_key}:declared_gap",
                description=_compact_text(section.summary, 240),
                source_section=section_key,
            )
        )
    return gaps


def _freshness_from_text(text: str) -> FreshnessLabel:
    lowered = text.lower()
    background_markers = (
        "background",
        "longer-cycle",
        "longer cycle",
        "historical",
        "old fact",
        "older fact",
        "not fresh",
        "not a fresh catalyst",
        "one-year",
        "half-year",
    )
    if any(marker in lowered for marker in background_markers):
        return "background"
    if re.search(r"\b20(1[0-9]|2[0-4])\b", lowered):
        return "background"
    return "recent_30d"


def _looks_like_catalyst(text: str) -> bool:
    lowered = text.lower()
    return any(
        marker in lowered
        for marker in (
            "catalyst",
            "driver",
            "guidance",
            "earnings",
            "demand",
            "capex",
            "price",
        )
    )


def _looks_like_key_variable(text: str) -> bool:
    lowered = text.lower()
    return any(
        marker in lowered
        for marker in (
            "variable",
            "margin",
            "revenue",
            "growth",
            "capex",
            "demand",
            "supply",
            "inventory",
            "price",
            "volume",
            "rate",
            "yield",
        )
    )


def _looks_like_risk(text: str) -> bool:
    lowered = text.lower()
    return any(
        marker in lowered
        for marker in (
            "risk",
            "uncertain",
            "uncertainty",
            "pressure",
            "weaker",
            "gap",
            "missing",
            "unknown",
        )
    )


def _compact_text(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "..."
