from __future__ import annotations

import pytest
from pydantic import ValidationError

from doxagent.message_bus_v2.monitoring_terms import MonitoringTermsService, TickerMonitoringTerms
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.schema import SourceDefinition, SourceKind, UpdateActor
from doxagent.message_bus_v2.search_plan import build_query_plan
from doxagent.message_bus_v2.service import MessageBusV2Service


def _terms(
    *, expected_revision: int = 0, count: int = 3, traditional: bool = True
) -> TickerMonitoringTerms:
    concepts = [
        {
            "concept_id": f"concept-{number}",
            "expressions": {
                "en": value,
                "ko": value,
                **({"zh-Hant": value} if traditional else {}),
            },
        }
        for number, value in enumerate(["Micron", "memory", "HBM", "NAND"][:count])
    ]
    return TickerMonitoringTerms.model_validate(
        {
            "ticker": "MU",
            "expected_revision": expected_revision,
            "l1_concepts": concepts,
            "l2": {
                "en": {"groups": [{"id": "en", "any": [{"literal": "Micron"}]}]},
                "zh-Hant": {"groups": [{"id": "zh", "any": [{"literal": "美光"}]}]},
                "ko": {"groups": [{"id": "ko", "any": [{"literal": "마이크론"}]}]},
            },
            "definition": {"relevant": "Memory chips", "irrelevant": "Unrelated products"},
        }
    )


def test_terms_revision_language_and_search_plans(tmp_path) -> None:
    repository = MessageBusV2Repository(tmp_path / "bus.sqlite3")
    MessageBusV2Service(repository).bootstrap()
    service = MonitoringTermsService(repository)
    assert service.required_languages() == {"en", "zh-Hant", "ko"}
    with pytest.raises(ValueError, match="missing L1 language"):
        service.apply(_terms(traditional=False), actor="test")
    with pytest.raises(ValidationError):
        _terms(count=4)
    revision = service.apply(_terms(), actor="test")
    assert revision == 1
    with pytest.raises(ValueError, match="revision conflict"):
        service.apply(_terms(), actor="test")
    found = service.get("MU")
    assert found is not None
    reuters = repository.get_source("reuters_site_search")
    google = repository.get_source("google_news_search_rss")
    assert reuters is not None and google is not None
    assert len(build_query_plan(reuters, found[1], found[0]).queries) == 3
    assert len(build_query_plan(google, found[1], found[0]).queries) == 1


def test_site_language_is_resolved_once_when_source_is_registered(tmp_path) -> None:
    repository = MessageBusV2Repository(tmp_path / "bus.sqlite3")
    service = MessageBusV2Service(
        repository, site_language_resolver=lambda site_id: "ko" if site_id == "kr-news" else None
    )
    source = SourceDefinition(
        source_id="korean_industry",
        display_name="Korean industry",
        kind=SourceKind.CRAWLER,
        adapter_ref="builtin:ctee_semiconductor",
        site_id="kr-news",
        scheduler_group="industry",
    )
    saved = service.register_source(source)
    assert saved.content_language == "ko"
    assert repository.get_source(source.source_id).content_language == "ko"


def test_new_search_subscription_requires_submitted_terms(tmp_path) -> None:
    repository = MessageBusV2Repository(tmp_path / "bus.sqlite3")
    service = MessageBusV2Service(repository)
    service.bootstrap()
    with pytest.raises(ValueError, match="monitoring terms required"):
        service.configure_binding(
            ticker="MU", source_id="reuters_site_search", actor=UpdateActor.USER
        )
    MonitoringTermsService(repository).apply(_terms(), actor="test")
    binding = service.configure_binding(
        ticker="MU", source_id="reuters_site_search", actor=UpdateActor.USER
    )
    assert binding.ticker == "MU"
