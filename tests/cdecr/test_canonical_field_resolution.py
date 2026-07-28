from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

from cdecr.canonical_field_resolution import (
    CanonicalFieldResolutionEngine,
    is_safe_deterministic_match,
)
from cdecr.contracts import (
    AccountingBasis,
    AssertionState,
    ComparisonBasis,
    EventFamily,
    EventMention,
    EventTime,
    EvidenceSpan,
    FinancialMetricFields,
    FinancialMetricProjection,
    Language,
    LocalPackageHint,
    MembershipRelation,
    Participant,
    ParticipantRole,
    Predicate,
    Quantity,
    QuantityRole,
    SourceMessage,
    SourceType,
    TimePrecision,
)
from cdecr.field_coreference import FieldCoreferenceResolver
from cdecr.field_coreference_contracts import FieldNamespace
from cdecr.identity_compiler import IdentityCompiler
from cdecr.kb_v2 import CATALOG_NAMES, V2KnowledgeBase
from cdecr.mention_finalization import MentionFinalizer
from cdecr.ports import (
    EmbeddingResult,
    StructuredModelRequest,
    StructuredModelResult,
)
from cdecr.registry import SQLiteCDECRRegistry


class Embeddings:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> EmbeddingResult:
        self.calls.append(list(texts))
        return EmbeddingResult(
            model="fake",
            dimensions=2,
            vectors=[[1.0, 0.0] for _ in texts],
            latency_ms=1,
        )


class NoModelCalls:
    def complete(self, request: StructuredModelRequest) -> object:
        raise AssertionError(f"unexpected model call: {request.user_prompt[:80]}")


class ChooseProductModel:
    def __init__(self) -> None:
        self.requests: list[StructuredModelRequest] = []

    def complete(self, request: StructuredModelRequest) -> StructuredModelResult:
        self.requests.append(request)
        payload = json.loads(request.user_prompt)
        target = next(
            item for item in payload["candidates"] if item["namespace"] == "object.product"
        )
        return StructuredModelResult(
            model="deepseek-v4-flash",
            payload={"decision": "LINK", "canonical_id": target["canonical_id"]},
            input_tokens=10,
            output_tokens=3,
            latency_ms=1,
        )


def _catalog(tmp_path: Path) -> V2KnowledgeBase:
    root = tmp_path / "v2"
    root.mkdir()
    payloads: dict[str, list[dict[str, object]]] = {name: [] for name in CATALOG_NAMES}
    payloads["companies"] = [
        {
            "id": "COMPANY_MU",
            "name": "Micron Technology",
            "ticker": "MU",
            "aliases": ["Micron", "Micron Technology Inc"],
        },
        {
            "id": "COMPANY_ACME",
            "name": "Acme",
            "ticker": "ACME",
            "aliases": ["Acme Corporation"],
        },
        {
            "id": "COMPANY_OTHER",
            "name": "Other Company",
            "ticker": "OTHER",
            "aliases": [],
        },
    ]
    payloads["concepts"] = [
        {
            "id": "PREDICATE_REPORT_METRIC",
            "name": "report metric",
            "kind": "PREDICATE",
            "aliases": ["report_financial_metric"],
        }
    ]
    payloads["metrics"] = [{"id": "REVENUE", "name": "Revenue", "aliases": ["revenue"]}]
    payloads["fiscal_periods"] = [
        {
            "id": "COMPANY_MU_FY2026_Q4",
            "company_id": "COMPANY_MU",
            "start": "2026-05-29",
            "end": "2026-08-27",
            "name": "COMPANY_MU_FY2026_Q4",
            "aliases": ["FY2026-Q4"],
        },
        {
            "id": "COMPANY_OTHER_FY2026_Q4",
            "company_id": "COMPANY_OTHER",
            "start": "2026-07-01",
            "end": "2026-09-30",
            "name": "COMPANY_OTHER_FY2026_Q4",
            "aliases": ["FY2026 Q4", "Q4 FY2026"],
        },
    ]
    payloads["named_objects"] = [
        {
            "id": "PRODUCT_ACME",
            "name": "Acme",
            "kind": "PRODUCT",
            "owner_id": "COMPANY_OTHER",
            "aliases": ["Acme product"],
        }
    ]
    payloads["artifacts"] = [
        {
            "id": "ARTIFACT_MU_Q4_2026",
            "name": "Micron FY2026 Q4 earnings release",
            "kind": "EARNINGS_RELEASE",
            "aliases": ["Micron Q4 release"],
        }
    ]
    payloads["attributes"] = [
        {"key": "asset", "aliases": [], "target": "NAMED_OBJECT", "use": "HARD"}
    ]
    for name, payload in payloads.items():
        (root / f"{name}.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
    return V2KnowledgeBase(root)


def _source() -> SourceMessage:
    return SourceMessage(
        message_id="MSG-1",
        source_type=SourceType.NEWS,
        title="Micron Technology (MU) reports results",
        text="Micron Technology Inc reported revenue of $8.1 billion.",
        published_at=datetime(2026, 6, 25, 12, tzinfo=UTC),
        source_name="Wire",
        url="https://example.test/1",
        ticker_hints=["MU"],
        language=Language.EN,
    )


def _mention(mention_id: str, company: str) -> EventMention:
    source = _source()
    evidence = "Micron Technology Inc reported revenue of $8.1 billion."
    return EventMention(
        mention_id=mention_id,
        message_id=source.message_id,
        evidence_spans=[
            EvidenceSpan(field="text", start_char=0, end_char=len(evidence), text=evidence)
        ],
        canonical_proposition="Micron reported quarterly revenue",
        event_family=EventFamily.FINANCIAL_PERFORMANCE,
        predicate=Predicate(raw="reported", normalized="report_financial_metric"),
        participants=[Participant(surface=company, role=ParticipantRole.SUBJECT, entity_id=None)],
        locations=[],
        time=EventTime(precision=TimePrecision.QUARTER, reference_period_id="FY2026-Q4"),
        assertion_state=AssertionState.ACTUAL,
        quantities=[Quantity(metric_id="REVENUE", value=8.1, unit="USD", raw_text="$8.1 billion")],
        open_attributes=[],
        schema_projection=FinancialMetricProjection(
            fields=FinancialMetricFields(
                issuer_id="COMPANY_MU",
                period_id="FY2026-Q4",
                metric_id="REVENUE",
                value=8.1,
                unit="USD",
                comparison_basis=ComparisonBasis.ABSOLUTE,
                accounting_basis=AccountingBasis.GAAP,
            )
        ),
        local_package_hint=LocalPackageHint(
            anchor="Micron Q4 release",
            relation_to_anchor=MembershipRelation.DISCLOSED_IN,
        ),
    )


def _engine(
    tmp_path: Path,
) -> tuple[SQLiteCDECRRegistry, V2KnowledgeBase, CanonicalFieldResolutionEngine]:
    registry = SQLiteCDECRRegistry(tmp_path / "cdecr.sqlite3")
    registry.initialize()
    kb = _catalog(tmp_path)
    resolver = FieldCoreferenceResolver(
        registry=registry,
        embedding_client=Embeddings(),
        model_client=NoModelCalls(),  # type: ignore[arg-type]
    )
    return (
        registry,
        kb,
        CanonicalFieldResolutionEngine(
            registry=registry, knowledge_base=kb, field_resolver=resolver
        ),
    )


def test_n5_keeps_object_fields_immutable_and_only_normalizes_scalars() -> None:
    mention = _mention("M-1", "Micron Technology Inc")
    finalized, decisions = MentionFinalizer().finalize(mention)
    assert finalized.participants == mention.participants
    assert finalized.time == mention.time
    assert finalized.schema_projection == mention.schema_projection
    assert finalized.quantities[0].metric_id == "REVENUE"
    assert finalized.quantities[0].value == 8_100_000_000
    assert decisions[0].field_path == "quantities.0"


def test_n5_uses_shared_trillion_and_ratio_units() -> None:
    base = _mention("M-UNITS", "Micron")
    trillion = base.quantities[0].model_copy(
        update={"value": 1.2, "unit": "USD", "raw_text": "1.2 trillion USD"}
    )
    basis_points = base.quantities[0].model_copy(
        update={"value": 25, "unit": "bps", "raw_text": "25 basis points"}
    )
    multiple = base.quantities[0].model_copy(
        update={"value": 4, "unit": "times", "raw_text": "4 times"}
    )
    finalized, _ = MentionFinalizer().finalize(
        base.model_copy(update={"quantities": [trillion, basis_points, multiple]})
    )
    assert finalized.quantities[0].value == 1_200_000_000_000
    assert finalized.quantities[0].unit == "USD"
    assert finalized.quantities[1].unit == "BASIS_POINT"
    assert finalized.quantities[2].unit == "MULTIPLE"


def test_n55_groups_aliases_links_v2_kb_and_n6_uses_only_links(tmp_path: Path) -> None:
    registry, kb, engine = _engine(tmp_path)
    source = _source()
    mentions = [_mention("M-1", "Micron Technology Inc"), _mention("M-2", "Micron")]
    for mention in mentions:
        registry.save_source(source, fingerprint="a" * 64)
        registry.save_mention(mention)
    before = [mention.model_dump(mode="json") for mention in mentions]
    summary = engine.resolve_document(source, mentions)
    assert summary.unresolved_count == 0
    links = [registry.get_field_link(mention.mention_id, "participants[0]") for mention in mentions]
    assert all(link is not None for link in links)
    assert links[0].registry_id == links[1].registry_id  # type: ignore[union-attr]
    participant_entry = registry.resolve_field_registry_entry(links[0].registry_id)  # type: ignore[union-attr]
    assert participant_entry is not None
    assert participant_entry.external_id == "COMPANY_MU"
    compiled = IdentityCompiler(registry=registry, catalog_hash=kb.catalog_hash).compile(
        mentions[0]
    )
    assert compiled.identity_profile is not None
    assert compiled.identity_profile.fields.issuer_id == "COMPANY_MU"  # type: ignore[union-attr]
    assert compiled.identity_profile.fields.period_id == "COMPANY_MU_FY2026_Q4"  # type: ignore[union-attr]
    assert compiled.primary_metric_id == "REVENUE"
    assert compiled.primary_metric_field_path == "quantities[0].metric_id"
    assert compiled.primary_metric_trust_reason == "CORE_ONTOLOGY_EXACT"
    assert compiled.principal_company_ids == ["COMPANY_MU"]
    assert [mention.model_dump(mode="json") for mention in mentions] == before


def test_package_hint_is_deferred_until_n11(tmp_path: Path) -> None:
    registry, _, engine = _engine(tmp_path)
    source = _source()
    mention = _mention("M-1", "Micron")
    registry.save_source(source, fingerprint="b" * 64)
    registry.save_mention(mention)
    engine.resolve_document(source, [mention])
    assert registry.get_field_link(mention.mention_id, "local_package_hint.anchor") is None
    engine.resolve_package_hints(source, [mention])
    link = registry.get_field_link(mention.mention_id, "local_package_hint.anchor")
    assert link is not None
    entry = registry.resolve_field_registry_entry(link.registry_id)
    assert entry is not None
    assert entry.namespace is FieldNamespace.ARTIFACT_EARNINGS_RELEASE
    assert entry.external_id == "ARTIFACT_MU_Q4_2026"


def test_identity_discriminant_ignores_comparison_quantity(tmp_path: Path) -> None:
    registry, kb, engine = _engine(tmp_path)
    source = _source()
    mention = _mention("M-COMPARISON", "Micron")
    primary = mention.quantities[0].model_copy(
        update={"role": QuantityRole.PRIMARY}
    )
    comparison = Quantity(
        metric_id="EPS",
        value=1.5,
        unit="USD",
        raw_text="consensus EPS of $1.50",
        role=QuantityRole.COMPARISON,
    )
    mention = mention.model_copy(update={"quantities": [primary, comparison]})
    registry.save_source(source, fingerprint="c" * 64)
    registry.save_mention(mention)
    engine.resolve_document(source, [mention])
    compiled = IdentityCompiler(
        registry=registry, catalog_hash=kb.catalog_hash
    ).compile(mention)
    assert compiled.primary_metric_id == "REVENUE"
    assert compiled.primary_metric_field_path == "quantities[0].metric_id"


def test_routed_occurrences_exposes_n55_inventory_without_package_hint(
    tmp_path: Path,
) -> None:
    _, _, engine = _engine(tmp_path)
    source = _source()
    mention = _mention("M-1", "Micron")
    occurrences = engine.routed_occurrences(source, [mention])
    paths = [item.field_path for item in occurrences]
    assert "predicate.normalized" in paths
    assert "participants[0]" in paths
    assert "quantities[0].metric_id" in paths
    assert "time.reference_period_id" in paths
    assert "schema_projection.fields.period_id" in paths
    assert "local_package_hint.anchor" not in paths


def test_non_fiscal_horizon_is_not_sent_to_fiscal_resolver(tmp_path: Path) -> None:
    _, _, engine = _engine(tmp_path)
    mention = _mention("M-HORIZON", "Micron")
    projection = mention.schema_projection
    assert isinstance(projection, FinancialMetricProjection)
    mention = mention.model_copy(
        update={
            "time": mention.time.model_copy(
                update={"reference_period_id": "rolling 90 days"}
            ),
            "schema_projection": projection.model_copy(
                update={
                    "fields": projection.fields.model_copy(
                        update={"period_id": "through 2030"}
                    )
                }
            ),
        }
    )
    paths = {
        item.field_path for item in engine.routed_occurrences(_source(), [mention])
    }
    assert "time.reference_period_id" not in paths
    assert "schema_projection.fields.period_id" not in paths


def test_locations_remain_place_only_and_nasdaq_uses_local_participant_context(
    tmp_path: Path,
) -> None:
    _, _, engine = _engine(tmp_path)
    location_mention = _mention("M-LOCATION", "Micron").model_copy(
        update={"locations": ["Acme"]}
    )
    location = next(
        item
        for item in engine.routed_occurrences(_source(), [location_mention])
        if item.field_path == "locations[0]"
    )
    assert location.catalog == "places"
    assert location.value.namespace is FieldNamespace.PLACE

    nasdaq = _mention("M-NASDAQ", "Nasdaq").model_copy(
        update={
            "canonical_proposition": "The Nasdaq Composite closed higher",
            "predicate": Predicate(raw="closed higher", normalized="move_index"),
        }
    )
    participant = next(
        item
        for item in engine.routed_occurrences(_source(), [nasdaq])
        if item.field_path == "participants[0]"
    )
    assert participant.catalog == "instruments"
    assert participant.value.namespace is FieldNamespace.PARTICIPANT_INSTRUMENT


def test_fiscal_parser_is_issuer_scoped_and_never_uses_embeddings(
    tmp_path: Path,
) -> None:
    registry = SQLiteCDECRRegistry(tmp_path / "fiscal.sqlite3")
    registry.initialize()
    kb = _catalog(tmp_path)
    embeddings = Embeddings()
    resolver = FieldCoreferenceResolver(
        registry=registry,
        embedding_client=embeddings,
        model_client=NoModelCalls(),  # type: ignore[arg-type]
        catalog_hash=kb.catalog_hash,
    )
    engine = CanonicalFieldResolutionEngine(
        registry=registry, knowledge_base=kb, field_resolver=resolver
    )
    source = _source()
    mention = _mention("M-FISCAL", "Micron")
    registry.save_source(source, fingerprint="f" * 64)
    registry.save_mention(mention)
    summary = engine.resolve_document(source, [mention])
    assert summary.unresolved_count == 0
    period_link = registry.get_field_link(mention.mention_id, "time.reference_period_id")
    assert period_link is not None
    entry = registry.resolve_field_registry_entry(period_link.registry_id)
    assert entry is not None
    assert entry.external_id == "COMPANY_MU_FY2026_Q4"
    assert embeddings.calls == []

    direct, candidates, identifiable = kb.fiscal_candidates(
        "Q4 FY2026",
        company_id="COMPANY_OTHER",
        published_date=date(2026, 7, 15),
    )
    assert identifiable
    assert direct is not None
    assert direct.external_id == "COMPANY_OTHER_FY2026_Q4"
    assert {item.company_id for item in candidates} == {"COMPANY_OTHER"}


def test_participant_unknown_uses_typed_multi_catalog_candidates(
    tmp_path: Path,
) -> None:
    registry = SQLiteCDECRRegistry(tmp_path / "unknown.sqlite3")
    registry.initialize()
    kb = _catalog(tmp_path)
    model = ChooseProductModel()
    resolver = FieldCoreferenceResolver(
        registry=registry,
        embedding_client=Embeddings(),
        model_client=model,
        catalog_hash=kb.catalog_hash,
    )
    engine = CanonicalFieldResolutionEngine(
        registry=registry, knowledge_base=kb, field_resolver=resolver
    )
    source = _source()
    mention = _mention("M-UNKNOWN", "Acme")
    registry.save_source(source, fingerprint="u" * 64)
    registry.save_mention(mention)
    engine.resolve_document(source, [mention])
    link = registry.get_field_link(mention.mention_id, "participants[0]")
    assert link is not None
    entry = registry.resolve_field_registry_entry(link.registry_id)
    assert entry is not None
    assert entry.namespace is FieldNamespace.OBJECT_PRODUCT
    assert entry.external_id == "PRODUCT_ACME"
    request = json.loads(model.requests[0].user_prompt)
    assert {candidate["namespace"] for candidate in request["candidates"]} == {
        "participant.company",
        "object.product",
    }


def test_safe_deterministic_match_rejects_alias_and_cross_catalog_collision(
    tmp_path: Path,
) -> None:
    kb = _catalog(tmp_path)
    source = _source()
    micron = kb.lookup("companies", "Micron")[0]
    safe, reason = is_safe_deterministic_match(
        "Micron",
        micron,
        catalog="companies",
        source=source,
        cross_catalog_collision=False,
    )
    assert not safe
    assert reason == "CANDIDATE_REQUIRED"
    safe, reason = is_safe_deterministic_match(
        "COMPANY_MU",
        micron,
        catalog="companies",
        source=source,
        cross_catalog_collision=False,
    )
    assert safe
    assert reason == "EXPLICIT_ID"
    safe, reason = is_safe_deterministic_match(
        "Micron Technology",
        micron,
        catalog="companies",
        source=source,
        cross_catalog_collision=True,
    )
    assert not safe
    assert reason == "CANDIDATE_REQUIRED"


def test_default_resolution_policy_redirects_duplicate_metrics_and_adds_core_queries() -> None:
    kb = V2KnowledgeBase()
    assert kb.metric_redirect("ADJUSTED_EPS") == "EPS_NON_GAAP"
    assert kb.metric_redirect("ADJUSTED_NET_INCOME") == "NET_INCOME_NON_GAAP"
    assert kb.lookup("metrics", "trading volume")[0].external_id == "TRADING_VOLUME"
    assert kb.lookup("metrics", "closing price")[0].external_id == "CLOSING_PRICE"
    assert kb.lookup("metrics", "deal count")[0].external_id == "DEAL_COUNT"
    assert kb.lookup("metrics", "revenue growth")[0].external_id == "REVENUE_GROWTH"
    override = kb.participant_route_override("UBS")
    assert override is not None
    assert override.catalog == "institutions"
    assert override.external_id == "INSTITUTION_UBS"
