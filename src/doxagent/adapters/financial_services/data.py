"""Data provider contracts and offline fixtures for industry research."""

from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from doxagent.models import NonEmptyStr


class FinancialServicesDataModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class IndustryResearchRequest(FinancialServicesDataModel):
    sector_or_theme: NonEmptyStr
    angle: NonEmptyStr
    universe: list[NonEmptyStr]
    market: NonEmptyStr = "US equities"
    geography: NonEmptyStr = "US"
    depth: NonEmptyStr = "primer"
    metadata: dict[str, Any] = Field(default_factory=dict)


class SourceRef(FinancialServicesDataModel):
    source_id: NonEmptyStr
    source_type: NonEmptyStr
    title: NonEmptyStr
    citation_scope: NonEmptyStr
    confidence: float = Field(ge=0.0, le=1.0)
    retrieval_metadata: dict[str, Any] = Field(default_factory=dict)


class SourcedClaim(FinancialServicesDataModel):
    claim: NonEmptyStr
    source_refs: list[NonEmptyStr] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class UnknownItem(FinancialServicesDataModel):
    field: NonEmptyStr
    reason: NonEmptyStr
    related_source_refs: list[NonEmptyStr] = Field(default_factory=list)


class PeerComp(FinancialServicesDataModel):
    company: NonEmptyStr
    ticker: NonEmptyStr
    operating_metrics: dict[NonEmptyStr, NonEmptyStr]
    valuation_multiples: dict[NonEmptyStr, NonEmptyStr]
    source_refs: list[NonEmptyStr]
    confidence: float = Field(ge=0.0, le=1.0)
    outlier_flags: list[NonEmptyStr] = Field(default_factory=list)


class IdeaCandidate(FinancialServicesDataModel):
    company: NonEmptyStr
    ticker: NonEmptyStr
    direction: NonEmptyStr
    thesis_hook: NonEmptyStr
    source_refs: list[NonEmptyStr]
    confidence: float = Field(ge=0.0, le=1.0)
    key_risks: list[NonEmptyStr] = Field(default_factory=list)
    next_steps: list[NonEmptyStr] = Field(default_factory=list)


class IndustryResearchFixtureData(FinancialServicesDataModel):
    source_refs: list[SourceRef]
    market_claims: list[SourcedClaim]
    growth_claims: list[SourcedClaim]
    structure_claims: list[SourcedClaim]
    value_chain: list[SourcedClaim]
    drivers: list[SourcedClaim]
    why_now: list[SourcedClaim]
    competitors: list[SourcedClaim]
    recent_moves: list[SourcedClaim]
    moat_assessments: list[SourcedClaim]
    peer_comps: list[PeerComp]
    ideas: list[IdeaCandidate]
    risks: list[SourcedClaim]
    catalysts: list[SourcedClaim]
    unknowns: list[UnknownItem]


class IndustryResearchDataProvider(Protocol):
    def load(self, request: IndustryResearchRequest) -> IndustryResearchFixtureData:
        """Return sourced data for the industry research workflow."""


class MockIndustryResearchDataProvider:
    """Offline fixture provider used until real DoxAtlas/CapIQ/FactSet adapters exist."""

    def load(self, request: IndustryResearchRequest) -> IndustryResearchFixtureData:
        source_refs = [
            SourceRef(
                source_id="mock-sector-primer",
                source_type="external_report",
                title=f"Mock sector primer for {request.sector_or_theme}",
                citation_scope="industry_overview",
                confidence=0.72,
                retrieval_metadata={
                    "mock_fixture": True,
                    "provider": "financial_services.fixture",
                    "sector_or_theme": request.sector_or_theme,
                },
            ),
            SourceRef(
                source_id="mock-capiq-comps",
                source_type="market_data",
                title=f"Mock peer comps for {request.sector_or_theme}",
                citation_scope="peer_comps",
                confidence=0.7,
                retrieval_metadata={
                    "mock_fixture": True,
                    "provider": "financial_services.fixture",
                    "market": request.market,
                },
            ),
            SourceRef(
                source_id="mock-company-filings",
                source_type="issuer_material",
                title="Mock issuer filings and investor presentations",
                citation_scope="competitive_landscape",
                confidence=0.68,
                retrieval_metadata={
                    "mock_fixture": True,
                    "provider": "financial_services.fixture",
                    "untrusted_docs_treated_as_data": True,
                },
            ),
        ]
        universe = request.universe[:5] or ["ALPHA", "BETA", "GAMMA"]
        comps = [
            PeerComp(
                company=f"{ticker} Corp",
                ticker=ticker,
                operating_metrics={
                    "revenue_growth": f"{12 + index * 3}% fixture",
                    "ebitda_margin": f"{20 + index * 2}% fixture",
                    "industry_metric": "capacity growth / utilization fixture",
                },
                valuation_multiples={
                    "ev_revenue": f"{4 + index}.0x fixture",
                    "ev_ebitda": f"{12 + index}.0x fixture",
                },
                source_refs=["mock-capiq-comps"],
                confidence=0.66,
                outlier_flags=["premium multiple"] if index == 0 else [],
            )
            for index, ticker in enumerate(universe[:5])
        ]
        ideas = [
            IdeaCandidate(
                company=comp.company,
                ticker=comp.ticker,
                direction="long",
                thesis_hook=(
                    f"Best liquid expression of {request.angle} within "
                    f"{request.sector_or_theme}."
                ),
                source_refs=["mock-sector-primer", "mock-capiq-comps"],
                confidence=0.62,
                key_risks=["Fixture data only; validate real estimates before action."],
                next_steps=["Run single-name fundamental model.", "Verify latest filings."],
            )
            for comp in comps[:3]
        ]
        return IndustryResearchFixtureData(
            source_refs=source_refs,
            market_claims=[
                SourcedClaim(
                    claim=(
                        f"{request.sector_or_theme} has a mock addressable market tied to "
                        f"{request.angle}; exact TAM requires real provider validation."
                    ),
                    source_refs=["mock-sector-primer"],
                    confidence=0.58,
                )
            ],
            growth_claims=[
                SourcedClaim(
                    claim=(
                        "Fixture growth is driven by utilization, capex cycle, and customer "
                        "demand."
                    ),
                    source_refs=["mock-sector-primer"],
                    confidence=0.6,
                )
            ],
            structure_claims=[
                SourcedClaim(
                    claim=(
                        "The fixture industry has scale leaders, specialized challengers, "
                        "and suppliers."
                    ),
                    source_refs=["mock-sector-primer", "mock-company-filings"],
                    confidence=0.64,
                )
            ],
            value_chain=[
                SourcedClaim(
                    claim=(
                        "Value accrues to scarce capacity, proprietary distribution, and "
                        "operating scale."
                    ),
                    source_refs=["mock-sector-primer"],
                    confidence=0.61,
                )
            ],
            drivers=[
                SourcedClaim(
                    claim=f"The primary fixture driver is the {request.angle} narrative.",
                    source_refs=["mock-sector-primer"],
                    confidence=0.6,
                )
            ],
            why_now=[
                SourcedClaim(
                    claim=(
                        "Why-now depends on capacity additions, funding costs, and customer "
                        "backlog."
                    ),
                    source_refs=["mock-sector-primer"],
                    confidence=0.57,
                )
            ],
            competitors=[
                SourcedClaim(
                    claim=f"{comp.company} is in the mock peer set for {request.sector_or_theme}.",
                    source_refs=["mock-capiq-comps", "mock-company-filings"],
                    confidence=comp.confidence,
                )
                for comp in comps
            ],
            recent_moves=[
                SourcedClaim(
                    claim=(
                        "Fixture recent moves include capacity expansion and partnership "
                        "announcements."
                    ),
                    source_refs=["mock-company-filings"],
                    confidence=0.55,
                )
            ],
            moat_assessments=[
                SourcedClaim(
                    claim=(
                        "Scale, customer relationships, and execution quality are the core "
                        "moat checks."
                    ),
                    source_refs=["mock-company-filings"],
                    confidence=0.57,
                )
            ],
            peer_comps=comps,
            ideas=ideas,
            risks=[
                SourcedClaim(
                    claim="Key risk: sourced market sizing is fixture-only and may overstate TAM.",
                    source_refs=["mock-sector-primer"],
                    confidence=0.9,
                ),
                SourcedClaim(
                    claim="Key risk: peer multiples use mock market data and must be refreshed.",
                    source_refs=["mock-capiq-comps"],
                    confidence=0.9,
                ),
            ],
            catalysts=[
                SourcedClaim(
                    claim="Potential catalyst: provider-verified demand acceleration.",
                    source_refs=["mock-sector-primer"],
                    confidence=0.55,
                ),
                SourcedClaim(
                    claim="Potential catalyst: multiple rerating after real comps refresh.",
                    source_refs=["mock-capiq-comps"],
                    confidence=0.55,
                ),
            ],
            unknowns=[
                UnknownItem(
                    field="market_size.tam",
                    reason="No real CapIQ/FactSet/DoxAtlas source is connected in Phase 8.",
                    related_source_refs=["mock-sector-primer"],
                ),
                UnknownItem(
                    field="peer_comps.metric_period",
                    reason="Fixture multiples do not represent a verified fiscal period.",
                    related_source_refs=["mock-capiq-comps"],
                ),
            ],
        )
