from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest

from doxagent.message_bus_v2.deduplication import article_url, invalid_page, stable_fingerprint
from doxagent.message_bus_v2.ibkr_news import _published
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.schema import RawMessageInput
from doxagent.message_bus_v2.service import MessageBusV2Service
from doxagent.persistent_runtime_v2.schema import SourceMessageEnvelope

NOW = datetime(2026, 9, 15, 12, tzinfo=UTC)
BODY = (
    "Micron announced a memory manufacturing investment of 15 billion dollars in Idaho. "
    "The board approved the project and expects construction to begin in October. "
    "The company said customer demand for advanced memory has grown across its major markets. "
    "Management confirmed that capacity will increase in stages rather than all at once. "
) * 4


@pytest.fixture
def bus(tmp_path, monkeypatch):
    for module in ("service", "schema", "repository"):
        monkeypatch.setattr("doxagent.message_bus_v2." + module + ".utc_now", lambda: NOW)
    repo = MessageBusV2Repository(tmp_path / "bus.sqlite3")
    svc = MessageBusV2Service(repo)
    svc.bootstrap()
    svc.start_ticker("MU")
    return repo, svc


def message(id="1", body=BODY, summary=None, url="https://example.test/article/1", **updates):
    data = dict(
        external_id=id,
        title="Micron announces Idaho manufacturing investment",
        body=body,
        summary=summary,
        source="Reuters",
        url=url,
        published_at=NOW,
        raw_payload={"id": id, "body": body, "summary": summary},
        metadata={"identity_evidence": {"url_kind": "article"}},
    )
    data.update(updates)
    return RawMessageInput(**data)


async def accept(bus, msg, source="benzinga_news", **kw):
    repo, svc = bus
    return await svc.accept_message(
        source=svc.require_source(source),
        binding=repo.get_binding("MU:" + source),
        message=msg,
        bootstrap=False,
        collected_at=NOW,
        **kw,
    )


async def test_parallel_id_url_and_full_cross_source(bus):
    repo, _ = bus
    first = await accept(bus, message())
    assert (await accept(bus, message(id="different"))).decision.value == "duplicate"
    assert (
        await accept(
            bus, message(id="other", url="https://other.test/story"), source="finnhub_company_news"
        )
    ).decision.value == "duplicate"
    assert repo.latest_stream_offset("MU") == 1
    raw = repo.list_raw(ticker="MU")[0]
    assert {s["source_id"] for s in raw.metadata["message_sources"]} == {
        "benzinga_news",
        "finnhub_company_news",
    }
    with repo._connect() as c:
        assert c.execute("select count(*) from message_observations").fetchone()[0] == 3
    assert raw.raw_message_id == first.raw_message_id


async def test_same_title_different_body_and_reused_id_split(bus):
    repo, _ = bus
    await accept(bus, message(body="Original short bulletin."))
    distinct = await accept(
        bus, message(body="A different bulletin.", url="https://example.test/article/2")
    )
    assert distinct.decision.value == "revision"  # Legacy source revision counter retained.
    assert (
        len(
            {
                r.metadata["message_version"]["logical_message_id"]
                for r in repo.list_raw(ticker="MU")
            }
        )
        == 2
    )
    await accept(
        bus, message(id="3", body="Different facts again.", url="https://other.test/article/3")
    )
    assert repo.latest_stream_offset("MU") == 3


async def test_full_body_empty_tick_never_downgrades(bus):
    repo, _ = bus
    await accept(bus, message())
    assert (await accept(bus, message(body=None, summary=None))).decision.value == "duplicate"
    assert repo.list_raw(ticker="MU")[0].body == BODY.strip()
    assert repo.latest_stream_offset("MU") == 1


async def test_supplement_and_live_update_have_linked_runtime_input(bus):
    repo, _ = bus
    first = await accept(bus, message(body=None, summary="Micron plans a new factory."))
    supplement = await accept(bus, message())
    changed = await accept(bus, message(body=BODY.replace("15 billion", "20 billion")))
    assert repo.latest_stream_offset("MU") == 3
    items = repo.read_stream("MU", after_offset=0, limit=10)
    envelopes = [SourceMessageEnvelope.from_stream_item(i) for i in items]
    assert len({e.logical_message_id for e in envelopes}) == 1
    assert [e.business_version for e in envelopes] == [1, 2, 3]
    assert envelopes[1].update_kind == "CONTENT_SUPPLEMENT"
    assert envelopes[1].previous_raw_message_id == first.raw_message_id
    assert envelopes[2].previous_raw_message_id == supplement.raw_message_id
    assert changed.stream_item_ids


async def test_stable_input_noise_and_bounded_recheck(bus):
    repo, svc = bus
    msg = message(body=None, summary="Reliable provider summary.")
    await accept(bus, msg)
    noisy = msg.model_copy(
        update={"raw_payload": {"thumbnail": "changed", "transport": "different"}}
    )
    assert stable_fingerprint(noisy) == stable_fingerprint(msg)
    args = dict(
        source=svc.require_source("benzinga_news"),
        binding=repo.get_binding("MU:benzinga_news"),
        message=noisy,
        bootstrap=False,
        poll_run_id="poll",
    )
    assert not svc.enqueue_enrichment(**args, collected_at=NOW + timedelta(minutes=1))[1]
    assert svc.enqueue_enrichment(**args, collected_at=NOW + timedelta(minutes=31))[1]
    assert svc.enqueue_enrichment(
        **{**args, "message": noisy.model_copy(update={"summary": "New facts."})},
        collected_at=NOW + timedelta(minutes=2),
    )[1]


async def test_invalid_reuters_summary_and_yahoo_navigation(bus):
    repo, _ = bus
    bad = "Search results for “Micron”908 results 3 hours ago unrelated articles"
    await accept(bus, message(body=None, summary=bad))
    await accept(bus, message(body=None, summary=bad.replace("3 hours", "4 hours")))
    assert repo.latest_stream_offset("MU") == 1
    assert repo.list_raw(ticker="MU")[0].body == ""
    assert invalid_page("1. News • 3 hours ago A headline. 2. News • 4 hours ago Another.")


async def test_concurrent_workers_and_recovery_publish_once(bus, monkeypatch):
    repo, svc = bus

    def worker(id):
        return asyncio.run(
            accept(
                bus, message(id=id), source="benzinga_news" if id == "a" else "finnhub_company_news"
            )
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(worker, ["a", "b"]))
    assert repo.latest_stream_offset("MU") == 1
    assert len({r.raw_message_id for r in results}) == 1
    assert len(repo.list_raw(ticker="MU")[0].metadata["message_sources"]) == 2
    assert svc.retry_pending_raw() == 0


async def test_failed_publication_is_recoverable_not_a_receipt(bus, monkeypatch):
    repo, svc = bus
    finalize = repo.finalize_standard

    def fail(**kwargs):
        raise RuntimeError("simulated process interruption")

    monkeypatch.setattr(repo, "finalize_standard", fail)
    with pytest.raises(RuntimeError):
        await accept(bus, message())
    assert repo.latest_stream_offset("MU") == 0
    monkeypatch.setattr(repo, "finalize_standard", finalize)
    repeated = await accept(bus, message())
    assert repeated.stream_item_ids
    assert repo.latest_stream_offset("MU") == 1
    assert svc.retry_pending_raw() == 0


async def test_legacy_revisions_are_one_identity_without_rewriting_history(bus):
    repo, _ = bus
    first = await accept(bus, message(body="Earlier bulletin."))
    await accept(bus, message(body="Updated bulletin."))
    with repo.transaction() as c:
        for table in [
            "logical_message_versions",
            "message_identity_aliases",
            "message_observations",
        ]:
            c.execute("delete from " + table)
        for row in c.execute("select data_json from raw_messages").fetchall():
            import json

            payload = json.loads(row["data_json"])
            payload["metadata"].pop("content_evidence", None)
            payload["metadata"].pop("message_version", None)
            c.execute(
                "update raw_messages set data_json=? where raw_message_id=?",
                (json.dumps(payload), payload["raw_message_id"]),
            )
    assert (await accept(bus, message(body="Updated bulletin."))).decision.value == "duplicate"
    assert repo.latest_stream_offset("MU") == 2
    assert repo.get_raw(first.raw_message_id).content_hash
    with repo._connect() as c:
        assert (
            c.execute(
                "select count(distinct logical_message_id) from logical_message_versions"
            ).fetchone()[0]
            == 1
        )


def test_safe_url_and_ibkr_fractional_date():
    assert (
        article_url("https://example.test/story?id=7&ref=article&mode=live&utm_source=x", "article")
        == "https://example.test/story?id=7&mode=live&ref=article"
    )
    assert article_url("https://www.interactivebrokers.com/en/trading/providers.php") is None
    assert _published("2026-08-17 14:29:00.0") == datetime(2026, 8, 17, 14, 29, tzinfo=UTC)


async def test_exact_text_republication_and_ticker_boundary(bus):
    repo, svc = bus
    await accept(bus, message())
    # Independent republishing days later is not a duplicate solely because text matches.
    from doxagent.message_bus_v2.deduplication import context_conflict

    old = repo.list_raw(ticker="MU")[0]
    assert context_conflict(
        old, old.model_copy(update={"published_at": NOW + timedelta(days=2)}), content_match=True
    )
    svc.start_ticker("NVDA")
    result = await svc.accept_message(
        source=svc.require_source("benzinga_news"),
        binding=repo.get_binding("NVDA:benzinga_news"),
        message=message(),
        bootstrap=False,
    )
    assert result.stream_item_ids
    assert repo.latest_stream_offset("NVDA") == 1


async def test_native_full_body_survives_failed_url_completion_and_channel_aliases(bus):
    repo, _ = bus
    meta = {
        "identity_evidence": {"url_kind": "generic"},
        "media_enrichment": {"succeeded": False, "outcome": "UNAVAILABLE", "reason": "http_404"},
    }
    generic = "https://www.interactivebrokers.com/en/trading/providers.php"
    await accept(bus, message(id="DJ-N:DJ-N$abc", url=generic, metadata=meta), source="ibkr_news")
    assert (
        await accept(
            bus, message(id="DJ-RTA:DJ-RTA$abc", url=generic, metadata=meta), source="ibkr_news"
        )
    ).decision.value == "duplicate"
    # Same suffix is NOT sufficient when the real text differs.
    await accept(
        bus,
        message(
            id="UNKNOWN:UNKNOWN$abc",
            url=generic,
            body=BODY.replace("15 billion", "25 billion"),
            metadata=meta,
        ),
        source="ibkr_news",
    )
    assert repo.latest_stream_offset("MU") == 2


async def test_publisher_short_correction_is_not_rejected_by_old_body_length(bus):
    repo, _ = bus
    await accept(bus, message())
    correction = message(
        body=None,
        summary="Correction: investment is 20 billion dollars, not 15 billion.",
        metadata={"identity_evidence": {"url_kind": "article", "provider_version": "corrected-2"}},
    )
    result = await accept(bus, correction)
    assert result.stream_item_ids
    updated = repo.get_raw(result.raw_message_id)
    assert updated.body.startswith("Correction: investment is 20 billion")
    assert updated.metadata["content_evidence"]["previous_article_body"]


async def test_cross_source_receipt_uses_own_input_and_url_pre_match(bus):
    repo, svc = bus
    first = message(summary="First source teaser.")
    await accept(bus, first)
    second = message(id="another", summary="Second source teaser.")
    await accept(bus, second, source="finnhub_company_news")
    # Exact full body merges these while each source keeps its own stable input receipt.
    assert repo.latest_stream_offset("MU") == 1
    assert not svc.enqueue_enrichment(
        source=svc.require_source("finnhub_company_news"),
        binding=repo.get_binding("MU:finnhub_company_news"),
        message=second,
        bootstrap=False,
        poll_run_id="repeat",
        collected_at=NOW + timedelta(minutes=1),
    )[1]
    # Same article URL is an independent pre-enrichment match despite a different ID.
    assert not svc.enqueue_enrichment(
        source=svc.require_source("benzinga_news"),
        binding=repo.get_binding("MU:benzinga_news"),
        message=first.model_copy(update={"external_id": "new-id"}),
        bootstrap=False,
        poll_run_id="repeat2",
        collected_at=NOW + timedelta(minutes=1),
    )[1]


async def test_cross_provider_headline_variant_but_trusted_editorial_change(bus):
    repo, _ = bus
    await accept(bus, message())
    variant = message(
        id="syndicated",
        url="https://other.test/article",
        title="Micron announces new Idaho manufacturing investment",
    )
    assert (await accept(bus, variant, source="finnhub_company_news")).decision.value == "duplicate"
    editorial = message(title="Micron announces revised Idaho manufacturing investment")
    assert (await accept(bus, editorial)).stream_item_ids
    assert repo.latest_stream_offset("MU") == 2
