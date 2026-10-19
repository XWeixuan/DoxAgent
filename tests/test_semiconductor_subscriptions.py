import importlib.util
from pathlib import Path

import pytest

from doxagent.message_bus_v2.monitoring_terms import MonitoringTermsService, TickerMonitoringTerms
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.schema import UpdateActor
from doxagent.message_bus_v2.service import MessageBusV2Service

spec = importlib.util.spec_from_file_location(
    "semiconductor_subscriptions",
    Path(__file__).parents[1] / "scripts/ops/subscribe_semiconductor_sources.py",
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_subscription_dry_run_apply_idempotency_and_terms_preservation(tmp_path):
    repo = MessageBusV2Repository(tmp_path / "bus.db")
    service = MessageBusV2Service(repo)
    service.bootstrap()
    terms = MonitoringTermsService(repo)
    for ticker in ("NBIS", "COHR"):
        service.start_ticker(ticker)
        terms.apply(
            TickerMonitoringTerms.model_validate(
                {
                    "ticker": ticker,
                    "expected_revision": 0,
                    "l1_concepts": [
                        {
                            "concept_id": "company",
                            "expressions": {lang: ticker for lang in ("en", "ko", "zh-Hant")},
                        }
                    ],
                    "l2": {
                        lang: {"groups": [{"id": "company", "any": [{"literal": ticker}]}]}
                        for lang in ("en", "ko", "zh-Hant")
                    },
                    "definition": {"relevant": "company news", "irrelevant": "unrelated news"},
                }
            ),
            actor="test",
        )
    disabled = "ctee_semiconductor"
    service.update_source(disabled, {"enabled": False}, actor=UpdateActor.USER)
    dry = module.subscribe(service, ["NBIS", "COHR"])
    assert not repo.get_source(disabled).enabled
    assert disabled in dry["sources_to_enable"]
    first = module.subscribe(service, ["NBIS", "COHR"], apply=True)
    assert len(first["bindings"]) == 20
    assert all(b["enabled"] and b["interval_seconds"] == 60 for b in first["bindings"])
    again = module.subscribe(service, ["NBIS", "COHR"], apply=True)
    assert all(b["action"] == "unchanged" for b in again["bindings"])
    assert again["terms_revisions"] == {"NBIS": 1, "COHR": 1}


def test_missing_terms_does_not_partially_apply(tmp_path):
    repo = MessageBusV2Repository(tmp_path / "bus.db")
    service = MessageBusV2Service(repo)
    service.bootstrap()
    before = repo.list_bindings(ticker="NBIS")
    with pytest.raises(ValueError, match="monitoring terms"):
        module.subscribe(service, ["NBIS", "COHR"], apply=True)
    assert repo.list_bindings(ticker="NBIS") == before
