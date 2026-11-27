import json
from datetime import datetime, UTC
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from doxagent.message_bus_v2.securities_sources import (
    SOURCES,
    initial_sources,
    listing_recipes,
    subscription_ids,
)
from doxagent.message_bus_v2.securities_parsers import parse_listing
from doxagent.message_bus_v2.securities_body import portable_html, tzero_body, documents_body
from doxagent.message_bus_v2.technology_sources import Capture
from doxagent.message_bus_v2.schema import UpdateActor
from doxagent.monitoring.media_enrichment import MediaEnrichmentRecord
from scripts.ops.configure_securities_sources import configure
from tests.fixtures.message_bus_v2 import _bus

FIXTURES = Path(__file__).parent / "fixtures" / "securities"
NOW = datetime(2026, 11, 28, 4, tzinfo=UTC)


def parse(sid, index, name):
    return parse_listing(
        listing_recipes()[sid][index], (FIXTURES / name).read_text(encoding="utf-8"), NOW
    )


def test_grouping_cadence_modes_and_no_rss_or_browser():
    sources = {s.source_id: s for s in initial_sources()}
    assert len(sources) == 5 and sum(len(v) for v in listing_recipes().values()) == 9
    for s in sources.values():
        assert s.default_polling_config.target_interval_seconds == 20
        assert s.distribution_policy.require_complete_body
    assert sources["marketsmedia_exchanges"].acquisition_mode.value == "by_distribution"
    assert all(
        s.acquisition_mode.value == "by_ticker"
        for s in sources.values()
        if s.source_id != "marketsmedia_exchanges"
    )
    assert all(
        not r.rss_fallback and r.transport != "browser" and r.cache_diagnostic_only
        for rs in listing_recipes().values()
        for r in rs
    )


def test_crypto_repeated_faq_keeps_newest_date_and_groups_fact_sheet():
    rows = parse("sec_crypto_newsroom", 0, "sec_crypto.html")
    faq = next(m for m in rows if "FAQs - Application" in m.title)
    assert faq.published_at.date().isoformat() == "2026-11-02"
    assert faq.publication_time_basis == "DATE"
    assert any(m.raw_payload["attachment_urls"] for m in rows)
    assert not any(m.title == "Fact Sheet" for m in rows)


def test_sec_native_precision_and_missing_table_are_explicit():
    rows = parse("sec_crypto_newsroom", 1, "sec_press.html")
    assert rows[0].published_at == datetime(2026, 11, 25, 9, 10, 24, tzinfo=UTC)
    assert rows[0].publication_time_basis == "EXACT"
    with pytest.raises(ValueError, match="table_missing"):
        parse_listing(
            listing_recipes()["sec_crypto_newsroom"][0], "<main>Client Challenge</main>", NOW
        )


@pytest.mark.parametrize("index,name", [(0, "markets_exchanges.html"), (1, "markets_from.html")])
def test_markets_uses_requested_teasers_and_avoids_global_hero(index, name):
    rows = parse("marketsmedia_exchanges", index, name)
    assert len(rows) == 2 and all(m.publication_time_basis == "DATE" for m in rows)
    assert listing_recipes()["marketsmedia_exchanges"][index].method == "POST"


@pytest.mark.parametrize("index,name", [(0, "sifma_letters.json"), (1, "sifma_press.json")])
def test_sifma_graphql_taxonomy_and_gmt_are_preserved(index, name):
    rows = parse("sifma_advocacy_press", index, name)
    assert len(rows) == 2 and all(m.publication_time_basis == "EXACT" for m in rows)
    assert rows[0].published_at.hour == (20 if index == 0 else 12)
    raw = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    raw["data"]["filingTypes" if index == 0 else "newsTypes"]["nodes"][0]["slug"] = "wrong"
    with pytest.raises(ValueError, match="scope_changed"):
        parse_listing(listing_recipes()["sifma_advocacy_press"][index], json.dumps(raw), NOW)


def test_dtcc_only_press_releases_and_no_fake_midnight_precision():
    rows = parse("exchange_infrastructure_press", 1, "dtcc.json")
    assert len(rows) == 2 and all(m.publication_time_basis == "DATE" for m in rows)
    raw = json.loads((FIXTURES / "dtcc.json").read_text(encoding="utf-8"))
    raw["results"][0]["raw"]["news_content_type"] = ["Insights"]
    filtered = parse_listing(
        listing_recipes()["exchange_infrastructure_press"][1], json.dumps(raw), NOW
    )
    assert len(filtered) == 1


def test_q4_urls_and_reusable_securitize_ir_ownership():
    from doxagent.message_bus_v2.corporate_ir.catalog import initial_endpoints
    from doxagent.message_bus_v2.corporate_ir.parsers import parse_listing as parse_ir
    from doxagent.message_bus_v2.corporate_ir.schema import endpoints
    from doxagent.message_bus_v2.schema import TickerSourceBinding

    rows = parse("exchange_infrastructure_press", 0, "ice-q4.json")
    assert rows[0].published_at.hour == 21 and rows[0].url.startswith("https://ir.theice.com/")
    spec = next(s for s in initial_endpoints() if s.owner_ticker == "SECZ")
    assert spec.parser_ref == "q4" and spec.target_interval_seconds == 20
    ir, _ = parse_ir(spec, (FIXTURES / "securitize-q4.json").read_text(encoding="utf-8"))
    assert (
        ir[0].url.startswith("https://investors.securitize.io/")
        and ir[0].publication_time_basis == "EXACT"
    )
    with pytest.raises(ValueError, match="does not belong"):
        endpoints(
            SimpleNamespace(default_parameters={"endpoints": [spec.model_dump(mode="json")]}),
            TickerSourceBinding(
                binding_id="COIN:corporate_ir_press_releases",
                ticker="COIN",
                source_id="corporate_ir_press_releases",
                source_parameters={"endpoint_ids": ["secz.press"]},
            ),
        )


def test_tzero_only_published_news_and_complete_portable_text():
    raw = json.loads((FIXTURES / "tzero.json").read_text(encoding="utf-8"))
    raw["result"][1]["_type"] = "insight"
    rows = parse_listing(listing_recipes()["tzero_press_releases"][0], json.dumps(raw), NOW)
    assert len(rows) == 1 and rows[0].publication_time_basis == "EXACT"
    assert (
        portable_html(
            [
                {"_type": "block", "children": [{"text": "Material financing terms."}]},
                {"_type": "table", "rows": [{"cells": ["Amount", "$50m"]}]},
            ]
        )
        == "<p>Material financing terms.</p><table><tr><td>Amount</td><td>$50m</td></tr></table>"
    )
    with pytest.raises(ValueError):
        portable_html([{"_type": "unknown_economic_data"}])


def test_approved_subscriptions_and_candidate_preconfiguration(tmp_path):
    repo, bus = _bus(tmp_path / "bus.sqlite3")
    for s in repo.list_sources():
        if s.acquisition_mode.value == "massive_distribution":
            bus.update_source(
                s.source_id, {"delivery_mode": "production"}, actor=UpdateActor.SYSTEM
            )
    result = configure(bus)
    secz = {b.source_id for b in repo.list_bindings(ticker="SECZ")}
    assert secz == subscription_ids(repo.list_sources(), "SECZ")
    assert {
        "theblock_crypto",
        "coindesk_crypto",
        "ledgerinsights_news",
        "unchained_crypto",
        "yahoo_finance_news",
        "reuters_site_search",
        "bloomberg_massive",
    } <= secz
    assert (
        not {"decrypt_crypto", "occ_newsroom", "cftc_trading_rules", "rg_prediction_markets"} & secz
    )
    assert {b.source_id for b in repo.list_bindings(ticker="COIN")} == {
        "sec_crypto_newsroom",
        "marketsmedia_exchanges",
        "sifma_advocacy_press",
    }
    assert not any(repo.get_ticker_state(t) for t in ["SECZ", "COIN"])
    assert result["SECZ"]["state"] == "NOT_STARTED"
    bus.materialize_default_bindings("SECZ")
    assert {b.source_id for b in repo.list_bindings(ticker="SECZ")} == secz
    assert configure(bus) == result


async def test_native_tzero_checks_identity_before_using_complete_body():
    message = parse("tzero_press_releases", 0, "tzero.json")[0]
    source = SOURCES["tzero_press_releases"]
    row = {
        "_id": "wrong",
        "_type": "news",
        "title": "unrelated",
        "slug": {"current": "other"},
        "body": [],
    }
    http = SimpleNamespace(
        fetch=AsyncMock(
            return_value=Capture(
                200, "https://jpd7ydtd.api.sanity.io", json.dumps({"result": row}), {}
            )
        )
    )
    record = MediaEnrichmentRecord(
        "r", "r", source.source_id, "SECZ", message.title, "", message.url
    )
    with pytest.raises(ValueError, match="identity_mismatch"):
        await tzero_body(http, source, SimpleNamespace(message=message), record)


async def test_document_failure_is_not_labeled_full():
    message = parse("sec_crypto_newsroom", 0, "sec_crypto.html")[0]
    page = (
        "<main><h1>"
        + message.title
        + '</h1><a href="/files/incoming.pdf">Incoming Letter</a></main>'
    )
    http = SimpleNamespace(
        fetch=AsyncMock(
            side_effect=[
                Capture(200, message.url, page, {}),
                Capture(503, "https://www.sec.gov/files/incoming.pdf", "", {}),
            ]
        )
    )
    record = MediaEnrichmentRecord(
        "r", "r", "sec_crypto_newsroom", "SECZ", message.title, "", message.url
    )
    result = await documents_body(
        http, SOURCES["sec_crypto_newsroom"], SimpleNamespace(message=message), record
    )
    assert result.diagnostics["outcome"] == "PARTIAL" and "503" in result.reason
