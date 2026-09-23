"""Deterministic L1 search rendering; no provider or browser I/O."""

from __future__ import annotations

from pydantic import Field

from .monitoring_terms import TickerMonitoringTerms
from .schema import BusModel, SearchPolicy, SourceDefinition, canonical_json, sha256_text


class SearchQuery(BusModel):
    concept_ids: list[str]
    query: str
    query_key: str


class QueryPlan(BusModel):
    ticker: str
    source_id: str
    language: str
    terms_revision: int
    queries: list[SearchQuery] = Field(min_length=1, max_length=3)


def build_query_plan(
    source: SourceDefinition, terms: TickerMonitoringTerms, revision: int
) -> QueryPlan:
    language = source.content_language or "en"
    if any(language not in item.expressions for item in terms.l1_concepts):
        raise ValueError(f"CONFIG_INCOMPLETE: missing L1 language {language}")
    policy = source.search_policy or SearchPolicy()
    rendered = [(item.concept_id, _quote(item.expressions[language])) for item in terms.l1_concepts]
    groups = [rendered] if policy.mode == "or" else [[item] for item in rendered]
    queries = []
    for group in groups:
        ids = [item[0] for item in group]
        query = " OR ".join(item[1] for item in group)
        if len(group) > 1:
            query = f"({query})"
        key = sha256_text(canonical_json([source.source_id, terms.ticker, revision, ids, query]))[
            :24
        ]
        queries.append(SearchQuery(concept_ids=ids, query=query, query_key=key))
    return QueryPlan(
        ticker=terms.ticker,
        source_id=source.source_id,
        language=language,
        terms_revision=revision,
        queries=queries,
    )


def _quote(value: str) -> str:
    escaped = value.replace('"', '\\"')
    return f'"{escaped}"' if " " in value or '"' in value else escaped
