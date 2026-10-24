import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from doxagent.content_enrichment.service import ContentEnrichmentHub
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.schema import (
    DefaultMonitoringProfile,
    PollContext,
    TickerSourceBinding,
    UpdateActor,
)
from doxagent.message_bus_v2.sec_edgar import ITEMS, initial_source
from doxagent.message_bus_v2.sec_edgar.adapter import SecEdgarAdapter, filing_root
from doxagent.message_bus_v2.sec_edgar.body import SecBodyReader
from doxagent.message_bus_v2.sec_edgar.documents import (
    Document,
    archive_url,
    document_manifest,
    extract_document,
    merge_documents,
)
from doxagent.message_bus_v2.sec_edgar.http import SecHttp, SecHttpError
from doxagent.message_bus_v2.service import MessageBusV2Service
from doxagent.monitoring.media_enrichment import MediaEnrichmentRecord

FIXTURES = Path(__file__).parent / "fixtures" / "sec_edgar"
NOW = datetime.now(UTC)
ACC = "0001819994-26-000067"
BASE = filing_root("1819994", ACC)


@asynccontextmanager
async def permit():
    yield


def submissions(forms=None, items=None):
    forms = forms or ["8-K"]
    size = len(forms)
    return {
        "cik": "1819994",
        "name": "Rocket Lab",
        "tickers": ["RKLB"],
        "filings": {
            "recent": {
                "accessionNumber": [f"0001819994-26-{67 + i:06d}" for i in range(size)],
                "form": forms,
                "acceptanceDateTime": [(NOW - timedelta(seconds=90)).isoformat()] * size,
                "primaryDocument": ["main.htm"] * size,
                "items": items if items is not None else ["1.01,9.01"] * size,
            }
        },
    }


def context(cp=None, ticker="RKLB"):
    source = initial_source()
    binding = TickerSourceBinding(
        binding_id=ticker + ":sec_edgar",
        ticker=ticker,
        source_id=source.source_id,
        source_parameters={"cik": "1819994"},
    )
    return PollContext(
        ticker=ticker,
        source=source,
        binding=binding,
        requested_at=NOW,
        checkpoint=cp or {},
        request_permit=permit,
    )


class FakeHttp:
    def __init__(self, values):
        self.values, self.calls = values, []

    async def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        value = self.values(url) if callable(self.values) else self.values[url]
        if isinstance(value, Exception):
            raise value
        return value if isinstance(value, tuple) else (200, {}, value)

    async def close(self):
        pass


@pytest.mark.parametrize("item", ITEMS)
async def test_each_requested_item_is_selected(item):
    fake = FakeHttp(lambda _: json.dumps(submissions(items=[item + ",9.01"])).encode())
    result = await SecEdgarAdapter(fake).poll(context())
    assert result.messages[0].metadata["sec_filing"]["selected_items"] == [item]
    assert len(fake.calls) == 1  # no main file or exhibit HTTP during normal HEAD


async def test_scope_revisions_timestamp_and_no_summary_body():
    forms = ["8-K", "8-K/A", "6-K", "6-K/A", "424B5", "10-Q", "8-K", "424B2"]
    data = submissions(forms, ["2.02", "1.01", "", "", "", "", "7.01", ""])
    r = await SecEdgarAdapter(FakeHttp(lambda _: json.dumps(data).encode())).poll(context())
    assert [m.metadata["sec_filing"]["form"] for m in r.messages] == forms[1:5]
    assert len({m.external_id for m in r.messages}) == 4
    assert all(m.body is None and m.publication_time_basis == "EXACT" for m in r.messages)
    assert all(m.published_at == NOW - timedelta(seconds=90) for m in r.messages)


async def test_missing_items_are_inspected_and_unknown_is_not_silently_dropped():
    data = submissions(items=[""])
    fake = FakeHttp(
        lambda url: (
            json.dumps(data).encode()
            if "submissions" in url
            else b"<p>Item 4.02 Non-Reliance</p><p>Financial statements cannot be relied on.</p>"
        )
    )
    r = await SecEdgarAdapter(fake).poll(context())
    assert r.messages[0].metadata["sec_filing"]["selected_items"] == ["4.02"]
    fake = FakeHttp(
        lambda url: json.dumps(data).encode() if "submissions" in url else b"<p>Unknown</p>"
    )
    with pytest.raises(ValueError, match="items_unresolved"):
        await SecEdgarAdapter(fake).poll(context())


async def test_checkpoint_suppresses_repeats_but_not_new_accession_and_304():
    data = submissions()
    fake = FakeHttp(lambda _: (200, {"etag": "version1"}, json.dumps(data).encode()))
    adapter = SecEdgarAdapter(fake)
    first = await adapter.poll(context())
    second = await adapter.poll(context(first.next_checkpoint))
    assert len(first.messages) == 1 and not second.messages
    adapter.recent.clear()
    fake.values = lambda _: (304, {}, b"")
    third = await adapter.poll(context(first.next_checkpoint))
    assert not third.messages and fake.calls[-1][1]["headers"] == {"If-None-Match": "version1"}
    fake.values = lambda _: json.dumps(submissions(["8-K", "8-K/A"])).encode()
    fourth = await adapter.poll(context(first.next_checkpoint))
    assert len(fourth.messages) == 1 and fourth.messages[0].external_id.endswith("000068")


async def test_cik_identity_class_mapping_and_no_substring_fallback():
    mapping = {"0": {"ticker": "BRK-B", "cik_str": 1067983}}
    adapter = SecEdgarAdapter(FakeHttp(lambda _: json.dumps(mapping).encode()))
    assert await adapter.cik_for("BRK.B") == "0001067983"
    with pytest.raises(ValueError, match="unresolved"):
        await adapter.cik_for("BRK")
    data = submissions()
    data["cik"] = "1"
    with pytest.raises(ValueError, match="cik_mismatch"):
        await SecEdgarAdapter(FakeHttp(lambda _: json.dumps(data).encode())).poll(context())


@pytest.mark.parametrize(
    "key,form,primary",
    [("rklb", "8-K", "rklb-20261014.htm"), ("nbis", "6-K", "tm2628078d1_6k.htm")],
)
def test_real_official_index_includes_primary_and_economic_exhibit(key, form, primary):
    data = json.loads((FIXTURES / (key + ".json")).read_text())
    acc = data["filings"]["recent"]["accessionNumber"][0]
    base = filing_root(data["cik"], acc)
    docs, _ = document_manifest(
        (FIXTURES / (key + "-index.html")).read_bytes(), base, primary, form
    )
    assert docs[0].name == primary
    assert any(d.kind == "EX-99.1" for d in docs)
    assert all(d.url.startswith(base) for d in docs)


def test_tables_inline_xbrl_unicode_and_no_approximate_term_dedup():
    raw = b"""<?xml version="1.0" encoding="UTF-8"?><html><body><!--comment-->
    <ix:hidden>SECRET XBRL TEMPLATE</ix:hidden><p style="display:none">hidden</p>
    <p>The note bears <ix:nonFraction>7.5%</ix:nonFraction> interest; maturity 2031.</p>
    <table><tr><th>Principal</th><th>Conversion price</th></tr>
    <tr><td>$250,000,000</td><td>$15.25</td></tr></table>
    <p>100</p><p>Prepayment premium 103%.</p></body></html>"""
    text = extract_document(raw, "main.htm")
    assert "SECRET" not in text and "hidden" not in text
    assert "7.5%" in text and "$250,000,000 | $15.25" in text and "100" in text
    prose = "The issuer must maintain liquidity of $20 million. " * 5
    changed = prose.replace("$20", "$21")
    doc = Document(BASE + "main.htm", "main.htm", "EX-10.1", "Credit agreement")
    combined, removed = merge_documents([(doc, prose), (doc, prose + "\n" + changed)])
    assert removed == 1 and "$20" in combined and "$21" in combined


def test_scanned_pdf_does_not_count_as_full():
    from io import BytesIO

    from pypdf import PdfWriter

    stream = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(stream)
    with pytest.raises(ValueError, match="requires_ocr"):
        extract_document(stream.getvalue(), "terms.pdf")


def index_html():
    return b"""<table summary="Document Format Files">
    <tr><td>1</td><td>Current report</td><td><a href="main.htm">main.htm</a></td><td>8-K</td></tr>
    <tr><td>2</td><td>Credit facility</td><td><a href="terms.htm">terms.htm</a></td>
    <td>EX-10.1</td></tr>
    <tr><td>3</td><td>Consent</td><td><a href="consent.htm">consent.htm</a></td>
    <td>EX-23.1</td></tr>
    </table>"""


def body_http(fail=False):
    return FakeHttp(
        lambda url: (
            index_html()
            if url.endswith("-index.html")
            else (
                SecHttpError("sec_http_503", status=503)
                if fail
                else (
                    b"<p>Principal $250 million; interest 7.5%; maturity 2031; "
                    b"covenant: liquidity $20 million.</p>"
                )
            )
            if url.endswith("terms.htm")
            else b"<p>Item 1.01 Credit facility</p><p>A new secured facility closed today.</p>"
        )
    )


async def make_message():
    return (
        await SecEdgarAdapter(FakeHttp(lambda _: json.dumps(submissions()).encode())).poll(
            context()
        )
    ).messages[0]


async def test_bundle_failure_is_partial_not_full_and_does_not_fetch_consents():
    msg = await make_message()
    record = MediaEnrichmentRecord(
        standard_message_id="x",
        raw_message_id="x",
        source_id="sec_edgar",
        ticker="RKLB",
        url=msg.url,
        title=msg.title,
        body=msg.summary,
    )
    job = SimpleNamespace(message=msg)
    http = body_http(True)
    result = await SecBodyReader(http).extract(job, record)
    assert not result.succeeded and result.diagnostics["outcome"] == "PARTIAL"
    assert result.http_status == 503 and ContentEnrichmentHub._retryable(result)
    assert result.diagnostics["document_errors"][0]["name"] == "terms.htm"
    assert not any("consent" in url for url, _ in http.calls)
    assert result.diagnostics["documents_extracted"] == 1


async def test_existing_queue_to_raw_standard_stream_and_repeated_filing(tmp_path):
    repo = MessageBusV2Repository(tmp_path / "bus.db")
    bus = MessageBusV2Service(repo, enrichment_queue_enabled=True)
    bus.bootstrap()
    bus.start_ticker("RKLB")
    source = bus.require_source("sec_edgar")
    binding = repo.get_binding("RKLB:sec_edgar")
    msg = await make_message()
    job, created = bus.enqueue_enrichment(
        source=source, binding=binding, message=msg, bootstrap=False, poll_run_id="integration"
    )
    assert created and job
    hub = ContentEnrichmentHub(repo, bus)
    hub.sec_body_reader = SecBodyReader(body_http())
    await hub.run_once()
    raw = repo.list_raw(ticker="RKLB")
    standard = repo.list_standard(ticker="RKLB")
    assert len(raw) == len(standard) == 1
    assert "Principal $250 million" in standard[0].body and "7.5%" in standard[0].body
    assert standard[0].metadata["media_enrichment"]["outcome"] == "SHORT_FULL"
    with repo._connect() as db:
        assert (
            db.execute("SELECT count(*) FROM stream_items WHERE ticker='RKLB'").fetchone()[0] == 1
        )
    await bus.accept_message(source=source, binding=binding, message=msg, bootstrap=False)
    assert len(repo.list_raw(ticker="RKLB")) == 1
    await hub.close()
    repo.close()


async def test_incomplete_economic_bundle_never_publishes_a_generic_summary(tmp_path):
    repo = MessageBusV2Repository(tmp_path / "bus.db")
    bus = MessageBusV2Service(repo, enrichment_queue_enabled=True)
    bus.bootstrap()
    bus.start_ticker("RKLB")
    msg = await make_message()
    bus.enqueue_enrichment(
        source=bus.require_source("sec_edgar"),
        binding=repo.get_binding("RKLB:sec_edgar"),
        message=msg,
        bootstrap=False,
        poll_run_id="failed-bundle",
    )
    hub = ContentEnrichmentHub(repo, bus, retry_delay_seconds=0)
    hub.sec_body_reader = SecBodyReader(body_http(True))
    await hub.run_once()
    await hub.run_once()
    assert not repo.list_raw(ticker="RKLB")
    assert not repo.list_standard(ticker="RKLB")
    with repo._connect() as db:
        assert (
            db.execute(
                "SELECT count(*) FROM article_body_results WHERE status='FAILED'"
            ).fetchone()[0]
            == 1
        )
    await hub.close()
    repo.close()


async def test_sec_runs_on_closed_day_but_respects_ticker_pause(tmp_path):
    from doxagent.persistent_runtime_v2.bus_orchestration import BusOrchestration
    from doxagent.persistent_runtime_v2.journal import RuntimeJournal

    now = datetime(2026, 10, 24, 14, tzinfo=UTC)  # Saturday
    repo = MessageBusV2Repository(tmp_path / "bus.db")
    bus = MessageBusV2Service(repo)
    bus.bootstrap()
    bus.start_ticker("RKLB")
    source = bus.require_source("sec_edgar")
    binding = repo.get_binding("RKLB:sec_edgar")
    journal = RuntimeJournal(tmp_path / "runtime.db", clock=lambda: now)
    calls = []

    async def poll(*args, **kwargs):
        calls.append(args)

    scheduler = SimpleNamespace(
        repository=repo,
        service=bus,
        _poll=poll,
        distribution_worker=None,
        _prepare_poll_cycle=lambda _: (
            [(source, binding)],
            [(source, binding)],
            {binding.binding_id: repo.get_poll_state(binding)},
        ),
    )
    owner = BusOrchestration(journal)
    await owner.run_once(scheduler)
    await asyncio.gather(*owner._inflight.values())
    assert len(calls) == 1
    journal.set("pause", "RKLB", "all")
    await owner.run_once(scheduler)
    assert len(calls) == 1
    await owner.close()
    repo.close()


def test_current_future_custom_profile_and_explicit_disabled_binding(tmp_path):
    repo = MessageBusV2Repository(tmp_path / "bus.db")
    bus = MessageBusV2Service(repo)
    bus.bootstrap()
    bus.start_ticker("MU")
    assert repo.get_binding("MU:sec_edgar").polling.target_interval_seconds == 30
    bus.save_default_profile(DefaultMonitoringProfile(profile_id="custom", entries=[]))
    bus.start_ticker("FUTURE", profile_id="custom")
    assert repo.get_binding("FUTURE:sec_edgar").enabled
    bus.update_binding("MU:sec_edgar", {"enabled": False}, actor=UpdateActor.USER)
    bus.bootstrap()
    bus.start_ticker("MU")
    assert not repo.get_binding("MU:sec_edgar").enabled
    assert len(repo.list_bindings(source_id="sec_edgar")) == 2
    repo.close()


async def test_official_rate_limit_is_shared_between_instances_and_body_lane_is_independent(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("SEC_USER_AGENT", "DoxAgent test ops@doxagent.test")
    one = SecHttp(rate_root=tmp_path)
    two = SecHttp(rate_root=tmp_path)
    body = SecHttp("body", rate_root=tmp_path)
    assert one._reserve() < 0.1
    assert 0.3 < two._reserve() < 0.6
    assert body._reserve() < 0.1  # BODY reservations never occupy the discovery lane
    one._cooldown(60)
    with pytest.raises(SecHttpError, match="cooldown"):
        body._reserve()
    await one.close()
    await two.close()
    await body.close()


async def test_429_stops_without_identity_or_route_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "DoxAgent test ops@doxagent.test")
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "90"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        reader = SecHttp(client=client, rate_root=tmp_path)
        with pytest.raises(SecHttpError) as error:
            await reader.get("https://www.sec.gov/files/company_tickers.json")
        assert error.value.retry_seconds == 90 and len(calls) == 1
        with pytest.raises(SecHttpError, match="cooldown"):
            await reader.get("https://data.sec.gov/submissions/CIK0001819994.json")
        assert len(calls) == 1


@pytest.mark.parametrize(
    "url", ["https://evil.test/a.htm", "../other.htm", "/Archives/edgar/data/1/a.htm"]
)
def test_document_urls_cannot_escape_own_filing(url):
    with pytest.raises(ValueError, match="outside_filing"):
        archive_url(url, BASE)
