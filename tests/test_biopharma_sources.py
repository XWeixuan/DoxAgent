import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from contextlib import asynccontextmanager

import pytest
from pydantic import ValidationError

from doxagent.message_bus_v2.biopharma_parsers import parse_listing
from doxagent.message_bus_v2.biopharma_sources import TICKERS, initial_sources, listing_recipes
from doxagent.message_bus_v2.collection_plane.config import Configuration
from doxagent.message_bus_v2.corporate_ir.catalog import initial_endpoints
from doxagent.message_bus_v2.corporate_ir.parsers import parse_listing as parse_ir
from doxagent.message_bus_v2.public_listing import ListingAdapter
from doxagent.message_bus_v2.schema import PollResult, SourceActivationWindow, TickerSourceBinding
from doxagent.message_bus_v2.technology_sources import Capture
from scripts.ops.configure_biopharma_sources import configure
from tests.fixtures.message_bus_v2 import _bus

FIXTURES = Path(__file__).parent / "fixtures" / "biopharma"
NOW = datetime(2026, 11, 26, 0, tzinfo=UTC)


def parse(source, name, index=0):
    return parse_listing(
        listing_recipes()[source][index], (FIXTURES / name).read_text(encoding="utf8"), NOW
    )


def test_et_window_inclusive_endpoints_and_dst():
    window = SourceActivationWindow(start_date="2026-12-03", end_date="2026-12-09")
    assert not window.contains(datetime(2026, 12, 3, 4, 59, 59, tzinfo=UTC))
    assert window.contains(datetime(2026, 12, 3, 5, tzinfo=UTC))
    assert window.contains(datetime(2026, 12, 10, 4, 59, 59, tzinfo=UTC))
    assert not window.contains(datetime(2026, 12, 10, 5, tzinfo=UTC))
    summer = SourceActivationWindow(start_date="2026-07-01", end_date="2026-07-01")
    assert summer.contains(datetime(2026, 7, 1, 4, tzinfo=UTC))
    assert not summer.contains(datetime(2026, 7, 2, 4, tzinfo=UTC))
    with pytest.raises(ValidationError):
        SourceActivationWindow(start_date="2026-12-09", end_date="2026-12-03")
    with pytest.raises(ValidationError):
        SourceActivationWindow(
            start_date="2026-12-09", end_date="2026-12-09", timezone="invalid/zone"
        )


def test_date_window_controls_real_collection_even_when_market_is_closed():
    source = next(s for s in initial_sources() if s.source_id == "ash_cullinan")
    binding = TickerSourceBinding(
        binding_id="CGEM:" + source.source_id, ticker="CGEM", source_id=source.source_id
    )
    config = Configuration(
        sources={source.source_id: source}, bindings={binding.binding_id: binding}, realtime=False
    )
    assert config.allowed(binding, datetime(2026, 12, 6, 16, tzinfo=UTC))
    assert not config.allowed(binding, NOW)
    assert not config.allowed(binding, datetime(2026, 12, 10, 5, tzinfo=UTC))
    config.pauses = {"CGEM": "all"}
    assert not config.allowed(binding, datetime(2026, 12, 6, 16, tzinfo=UTC))


def test_only_two_sources_have_dates_and_entries_stay_grouped():
    sources = initial_sources()
    assert len(sources) == 9
    assert {s.source_id for s in sources if s.activation_window} == {
        "ash_cullinan",
        "aes_abstract_search",
    }
    assert all(s.default_polling_config.target_interval_seconds == 20 for s in sources)
    assert len(listing_recipes()["fda_release"]) == 3
    assert len(listing_recipes()["akeso_press_releases"]) == 2
    assert len(listing_recipes()["stat_biotech_trials"]) == 2
    assert all("activation_window" not in s.model_dump() for s in sources)


@pytest.mark.parametrize("ticker", ["XENE", "RGNX", "PRAX", "CGEM"])
def test_captured_nir_company_cards_keep_title_and_day_precision(ticker):
    spec = next(s for s in initial_endpoints() if s.owner_ticker == ticker)
    rows, _ = parse_ir(spec, (FIXTURES / (ticker.lower() + "-ir.html")).read_text(encoding="utf8"))
    assert rows and all(r.publication_time_basis == "DATE" and len(r.title) > 30 for r in rows)
    assert all("Read Full Press Release" not in r.title for r in rows)


def test_summit_uses_native_q4_time_and_own_domain():
    spec = next(s for s in initial_endpoints() if s.owner_ticker == "SMMT")
    rows, _ = parse_ir(spec, (FIXTURES / "smmt-ir.json").read_text())
    assert len(rows) == 2
    assert all(
        r.url.startswith("https://smmttx.com/") and r.publication_time_basis == "EXACT"
        for r in rows
    )
    assert spec.parser_parameters["http_route"] == "us-standard-5"


def test_hkex_rechecks_company_and_category_and_uses_hong_kong_timezone():
    raw = (FIXTURES / "hkex.html").read_text()
    rows = parse_listing(listing_recipes()["hkex_akeso"][0], raw, NOW)
    assert len(rows) == 2 and rows[0].published_at == datetime(2026, 11, 25, 14, 1, tzinfo=UTC)
    assert all(r.url.endswith(".pdf") and r.raw_payload["stock_code"] == "09926" for r in rows)
    wrong = raw.replace("09926", "00001")
    with pytest.raises(ValueError):
        parse_listing(listing_recipes()["hkex_akeso"][0], wrong, NOW)
    wrong = raw.replace("Announcements and Notices", "Circulars")
    with pytest.raises(ValueError):
        parse_listing(listing_recipes()["hkex_akeso"][0], wrong, NOW)


def test_ash_preserves_paper_pid_and_does_not_invent_publication():
    rows = parse("ash_cullinan", "ash.html")
    assert len(rows) == 2 and rows[0].url != rows[1].url
    assert all(
        "?pid=" in r.url
        and r.external_id == r.url
        and r.raw_url == r.url
        and r.publication_time_basis == "UNKNOWN_FIRST_SEEN"
        for r in rows
    )


def test_aes_and_calendar_do_not_use_future_meeting_dates_as_publication():
    aes = parse("aes_abstract_search", "aes.json")
    calendar = parse("fda_release", "fda-calendar.json", 2)
    assert aes and calendar
    assert all(r.publication_time_basis == "UNKNOWN_FIRST_SEEN" for r in [*aes, *calendar])
    assert aes[0].raw_payload["presentation_date"] != aes[0].published_at.isoformat()


def test_calendar_archive_is_not_new_news_but_recent_amendments_survive():
    from doxagent.message_bus_v2.biopharma_parsers import calendar_entry_current

    raw = json.loads((FIXTURES / "fda-calendar.json").read_text())
    archived, upcoming = raw
    assert not calendar_entry_current(archived, NOW)
    assert calendar_entry_current(upcoming, NOW)
    amended = {**archived, "changed": '<time datetime="2026-11-25T12:00:00-05:00">Updated</time>'}
    assert calendar_entry_current(amended, NOW)
    rows = parse_listing(listing_recipes()["fda_release"][2], json.dumps([archived]), NOW)
    assert rows == []  # Valid empty current scope, not a failed HTTP list.
    assert len(parse_listing(listing_recipes()["fda_release"][2], json.dumps([amended]), NOW)) == 1
    # An undated fresh notice is retained; a known-old undated archive is excluded.
    assert calendar_entry_current({"changed": amended["changed"]}, NOW)
    assert not calendar_entry_current({"changed": archived["changed"]}, NOW)


async def test_old_calendar_backlog_is_rejected_before_model_or_publication(monkeypatch):
    from doxagent.message_bus_v2.live import LiveDeliveryWorker

    raw = (FIXTURES / "fda-calendar.json").read_text()
    earlier = datetime(2026, 10, 1, tzinfo=UTC)
    message = next(
        m
        for m in parse_listing(listing_recipes()["fda_release"][2], raw, earlier)
        if "October 30" in m.title
    )
    worker = LiveDeliveryWorker.__new__(LiveDeliveryWorker)
    worker._decision = AsyncMock()
    monkeypatch.setattr("doxagent.message_bus_v2.live.utc_now", lambda: NOW)
    source = SimpleNamespace(source_id="fda_release")
    binding = SimpleNamespace(enabled=True)
    row = {"terms_revision": 1}
    await worker.process(row, (message, source, binding, None))
    worker._decision.assert_awaited_once_with(
        None, row, "ADMISSION_REJECTED", error="ARCHIVED_FDA_CALENDAR"
    )


async def test_configuration_repair_finishes_batched_tail_without_another_edit(tmp_path):
    import time
    from doxagent.message_bus_v2.live import LiveQueue, LiveDeliveryWorker

    repo, bus = _bus(tmp_path / "bus.db")
    bus.live_queue = LiveQueue(bus)
    worker = LiveDeliveryWorker(bus)
    worker.advanced_reported_at = time.monotonic()

    async def read(operation, *args, **kwargs):
        return 7 if operation.__name__ == "_configuration_revision" else []

    bus.storage.read = read
    bus.storage.call = AsyncMock(side_effect=[2048, 1])
    for _ in range(3):
        worker.configuration_checked_at = 0
        await worker.run_available()
    assert bus.storage.call.await_count == 2
    assert worker.configuration_revision == 7 and not worker.configuration_repair_pending
    repo.close()


def test_akeso_two_publishers_and_stat_paid_flags():
    assert len(parse("akeso_press_releases", "akeso.html")) == 2
    assert len(parse("akeso_press_releases", "akeso-pr.html", 1)) == 2
    assert all(
        r.raw_payload["publisher_subscription"] for r in parse("stat_biotech_trials", "stat.html")
    )


def test_fierce_native_node_time_and_sponsored_filter():
    raw = json.loads((FIXTURES / "fierce.json").read_text())
    rows = parse_listing(listing_recipes()["fiercebiotech_biotech"][0], json.dumps(raw), NOW)
    assert len(rows) == 1 and rows[0].publication_time_basis == "EXACT"
    raw["data"][0]["attributes"]["is_sponsored"] = True
    with pytest.raises(ValueError):
        parse_listing(listing_recipes()["fiercebiotech_biotech"][0], json.dumps(raw), NOW)


@pytest.mark.asyncio
async def test_fierce_public_api_body_respects_meter_gate_and_article_identity():
    from doxagent.message_bus_v2.biopharma_body import fierce_body
    from doxagent.message_bus_v2.biopharma_sources import SOURCES
    from doxagent.monitoring.media_enrichment import MediaEnrichmentRecord

    raw = json.loads((FIXTURES / "fierce.json").read_text())["data"][0]
    raw["attributes"]["body"] = {
        "value": "".join(
            f"<p>Cohort {i}: Trial results preserved a 12.5% response estimate and safety data. "
            "The contract specifies the complete milestone payment. "
            "The next analysis will measure duration of response.</p>"
            for i in range(6)
        )
    }
    raw["attributes"]["field_enable_meter_gate"] = False
    m = parse_listing(
        listing_recipes()["fiercebiotech_biotech"][0], json.dumps({"data": [raw]}), NOW
    )[0]
    record = MediaEnrichmentRecord(
        "test", "test", "fiercebiotech_biotech", "XENE", m.title, "", m.url
    )
    fetch = AsyncMock(side_effect=lambda *a, **k: Capture(200, a[0], json.dumps({"data": raw}), {}))
    reader = SimpleNamespace(
        http=SimpleNamespace(fetch=fetch),
        _fierce_public_header=(datetime.now(UTC), "public-client-value"),
    )
    job = SimpleNamespace(message=m)
    result = await fierce_body(reader, SOURCES["fiercebiotech_biotech"], job, record)
    assert result.diagnostics["outcome"] == "FULL" and result.content.count("12.5%") == 6
    raw["attributes"]["field_enable_meter_gate"] = True
    result = await fierce_body(reader, SOURCES["fiercebiotech_biotech"], job, record)
    assert result.diagnostics["outcome"] == "UNAVAILABLE" and result.content is None
    raw["attributes"]["path"]["alias"] = "/biotech/different-article"
    with pytest.raises(ValueError, match="identity_mismatch"):
        await fierce_body(reader, SOURCES["fiercebiotech_biotech"], job, record)


def test_aes_uses_normal_query_units_in_real_scheduler():
    from doxagent.message_bus_v2.scheduler import GlobalPollScheduler
    from doxagent.message_bus_v2.schema import PollState

    source = next(s for s in initial_sources() if s.source_id == "aes_abstract_search")
    binding = TickerSourceBinding(
        binding_id="XENE:" + source.source_id, ticker="XENE", source_id=source.source_id
    )
    scheduler = object.__new__(GlobalPollScheduler)
    scheduler._query_plans = {
        (source.source_id, "XENE"): {"queries": [{"query_key": "one", "query": "Xenon"}]}
    }
    state = PollState(binding_id=binding.binding_id, ticker="XENE", source_id=source.source_id)
    units = list(scheduler.work_units(source, binding, state, NOW, include_all=True))
    assert len(units) == 1 and units[0][1]["acquisition_unit"] == "QUERY:one"
    assert units[0][1]["query_plan_override"]["queries"][0]["query"] == "Xenon"


@pytest.mark.asyncio
async def test_relative_age_drift_does_not_create_repeated_listing_changes():
    source = next(s for s in initial_sources() if s.source_id == "endpoints_biopharma")
    recipe = listing_recipes()[source.source_id][0]
    adapter = ListingAdapter(SimpleNamespace(), SimpleNamespace())
    adapter._fetch = AsyncMock(
        return_value=Capture(200, recipe.url, (FIXTURES / "endpoints.html").read_text(), {})
    )

    @asynccontextmanager
    async def permit():
        yield

    ctx = SimpleNamespace(
        source=source, checkpoint={}, requested_at=NOW, request_permit=permit, ticker="XENE"
    )
    first = await adapter._one(ctx, recipe, shared=False)
    assert len(first.messages) == 2
    ctx.checkpoint = first.next_checkpoint
    ctx.requested_at = NOW + timedelta(seconds=20)
    second = await adapter._one(ctx, recipe, shared=False)
    assert not second.failures and not second.messages


@pytest.mark.asyncio
async def test_aes_uses_unified_queries_and_reads_all_result_pages():
    from doxagent.message_bus_v2.biopharma_http import poll_aes

    seen = []

    async def one(context, recipe, shared):
        seen.append(recipe.url)
        return PollResult(acquisition_metadata={"listing_total": 101})

    adapter = SimpleNamespace(_one=one)
    ctx = SimpleNamespace(query_plan={"queries": [{"query": "Xenon OR azetukalner"}]})
    await poll_aes(adapter, ctx)
    assert len(seen) == 2 and "page=2" in seen[1] and "azetukalner" in seen[0]
    with pytest.raises(ValueError):
        await poll_aes(adapter, SimpleNamespace(query_plan=None))


def test_portfolio_survives_initialization_defaults_without_starting_tickers(tmp_path):
    repo, bus = _bus(tmp_path / "bus.db")
    from doxagent.message_bus_v2.schema import UpdateActor

    for sid in (
        "access_newswire_massive",
        "businesswire_massive",
        "globenewswire_massive",
        "prnewswire_massive",
        "tmx_newsfile_massive",
    ):
        bus.update_source(sid, {"delivery_mode": "production"}, actor=UpdateActor.SYSTEM)
    first = configure(bus)
    assert {t: r["binding_count"] for t, r in first.items()} == {
        "XENE": 18,
        "RGNX": 18,
        "PRAX": 17,
        "CGEM": 19,
        "SMMT": 20,
    }
    for ticker in TICKERS:
        bus.materialize_default_bindings(ticker)
        actual = {b.source_id for b in repo.list_bindings(ticker=ticker)}
        assert actual == set(first[ticker]["approved"])
        assert "yahoo_finance_news" not in actual and "bloomberg_massive" not in actual
        assert ("reuters_site_search" in actual) == (ticker in {"RGNX", "SMMT"})
        assert repo.get_ticker_state(ticker) is None
        ir = repo.get_binding(ticker + ":corporate_ir_press_releases")
        assert ir.source_parameters["endpoint_ids"] == [ticker.lower() + ".press"]
    before = {b.binding_id: b.version for t in TICKERS for b in repo.list_bindings(ticker=t)}
    assert configure(bus) == first
    assert before == {
        b.binding_id: b.version for t in TICKERS for b in repo.list_bindings(ticker=t)
    }
    # Existing portfolios are not globally narrowed by this ticker-specific rule.
    bus.materialize_default_bindings("MU")
    assert repo.get_binding("MU:yahoo_finance_news") is not None


def test_ash_body_checks_the_requested_pid_before_scoping_abstract():
    from doxagent.message_bus_v2.biopharma_body import inspect_ash
    from doxagent.content_enrichment.quality import choose_candidate

    title = "Phase 1b study of a bispecific antibody in autoimmune cytopenias"
    body = (
        "Methods: Adults received the study antibody with a planned escalation schedule. "
        "Results: Responses and safety findings were measured in the prespecified population. "
        "Conclusions: Additional follow-up will evaluate duration of benefit. "
    ) * 6
    raw = f'<form action="./paper-details.aspx?pid=123"><h1>Paper Details</h1><h2 class="session-title">3001 {title}</h2><div id="pnlPaperAbstract">{body}</div></form>'
    candidate, outcome, _ = choose_candidate(
        inspect_ash(raw, "https://onlineprogram.hematology.org/paper-details.aspx?pid=123", title),
        title,
    )
    assert (
        outcome == "FULL"
        and candidate.method == "ash_abstract_dom"
        and candidate.text.count("Methods:") == 6
    )
    candidate, outcome, _ = choose_candidate(
        inspect_ash(raw, "https://onlineprogram.hematology.org/paper-details.aspx?pid=999", title),
        title,
    )
    assert candidate is None and outcome == "UNAVAILABLE"


def test_massive_preparation_does_not_relax_user_or_running_ticker_terms_guard(tmp_path):
    from doxagent.message_bus_v2.schema import UpdateActor, TickerMonitoringState

    repo, bus = _bus(tmp_path / "bus.db")
    bus.update_source(
        "businesswire_massive", {"delivery_mode": "production"}, actor=UpdateActor.SYSTEM
    )
    with pytest.raises(ValueError, match="MASSIVE_TERMS_MISSING"):
        bus.configure_binding(
            ticker="XENE", source_id="businesswire_massive", actor=UpdateActor.USER
        )
    repo.save_ticker_state(TickerMonitoringState(ticker="XENE", profile_version=1))
    with pytest.raises(ValueError, match="MASSIVE_TERMS_MISSING"):
        bus.configure_binding(
            ticker="XENE", source_id="businesswire_massive", actor=UpdateActor.SYSTEM
        )


def test_verified_official_x_account_seed_is_first_revision_only(tmp_path):
    from tests.test_message_bus_monitoring_terms import _terms
    from doxagent.message_bus_v2.monitoring_terms import MonitoringTermsService
    from doxagent.message_bus_v2.schema import UpdateActor
    from doxagent.message_bus_v2.x_official import ACCOUNTS

    repo, bus = _bus(tmp_path / "bus.db")
    bus.configure_binding(ticker="XENE", source_id="x_official_posts", actor=UpdateActor.SYSTEM)
    terms = MonitoringTermsService(repo)
    terms.apply(_terms().model_copy(update={"ticker": "XENE"}), actor="test")
    revision, value = terms.get("XENE")
    assert value.x_accounts == [s.lower() for s in ACCOUNTS["XENE"]]
    terms.apply(
        value.model_copy(update={"expected_revision": revision, "x_accounts": []}), actor="test"
    )
    assert terms.get("XENE")[1].x_accounts == []
