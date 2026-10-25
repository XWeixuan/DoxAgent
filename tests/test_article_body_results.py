from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from doxagent.message_bus_v2.body_results import ArticleBodyResults, article_key
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.schema import RawMessageInput


def message(**updates):
    return RawMessageInput(
        title="Company announces investment",
        url="https://publisher.test/news/investment",
        published_at=datetime.now(UTC),
        summary="Company invests in a new plant.",
        raw_payload={},
        metadata={"identity_evidence": {"url_kind": "article"}},
        **updates,
    )


def test_body_reuse_is_not_a_publication_receipt_and_failed_old_owner_cannot_erase_full(tmp_path):
    results = ArticleBodyResults(MessageBusV2Repository(tmp_path / "bus.db"))
    original = message()
    row, _ = results.schedule(original, "source_a")
    key = row["fetch_key"]
    assert key == article_key(original, "source_b")
    assert results.acquire(key, "execution-1")
    assert not results.acquire(key, "execution-2")
    results.finish(
        key,
        "execution-1",
        original,
        SimpleNamespace(
            succeeded=True,
            content="Complete article.",
            diagnostics={"outcome": "SHORT_FULL"},
            final_url=original.url,
        ),
    )
    assert results.reusable(results.get(key), original)
    changed_date = original.model_copy(
        update={"published_at": original.published_at + timedelta(minutes=1)}
    )
    assert results.schedule(changed_date, "source_b")[1] == "reuse"
    results.finish(
        key,
        "execution-1",
        original,
        SimpleNamespace(succeeded=False, reason="timeout", diagnostics={}),
    )
    assert results.get(key)["content"] == "Complete article."
    assert results.get(key)["execution_id"] == "execution-1"


def test_update_hint_and_due_recheck_have_one_durable_sequence(tmp_path):
    results = ArticleBodyResults(MessageBusV2Repository(tmp_path / "bus.db"))
    original, now = message(), datetime.now(UTC)
    row, _ = results.schedule(original, "source", now=now)
    key = row["fetch_key"]
    results.acquire(key, "first", now=now)
    results.finish(
        key,
        "first",
        original,
        SimpleNamespace(
            succeeded=True, content="Body.", diagnostics={"outcome": "FULL"}, final_url=original.url
        ),
        now=now,
    )
    assert results.schedule(original, "source", now=now + timedelta(minutes=29))[1] == "reuse"
    checked, kind = results.schedule(original, "source", now=now + timedelta(minutes=30))
    assert kind == "recheck" and checked["check_seq"] == 2
    assert (
        results.schedule(original, "source", now=now + timedelta(minutes=31))[0]["check_seq"] == 2
    )
    updated = original.model_copy(update={"summary": "Company doubles the investment."})
    assert results.schedule(updated, "source", now=now + timedelta(minutes=31))[0]["check_seq"] == 3
    private = original.model_copy(
        update={"metadata": {**original.metadata, "body_access_scope": "account:other"}}
    )
    assert article_key(private, "source") != key


def test_unchanged_revalidation_preserves_original_acquisition_time(tmp_path):
    results = ArticleBodyResults(MessageBusV2Repository(tmp_path / "bus.db"))
    original, now = message(), datetime.now(UTC)
    row, _ = results.schedule(original, "source", now=now)
    full = SimpleNamespace(
        succeeded=True,
        content="Unchanged full article.",
        diagnostics={"outcome": "FULL"},
        final_url=original.url,
    )
    assert results.acquire(row["fetch_key"], "first", now=now)
    first = results.finish(row["fetch_key"], "first", original, full, now=now)
    later = now + timedelta(minutes=31)
    assert results.acquire(row["fetch_key"], "second", now=later)
    second = results.finish(row["fetch_key"], "second", original, full, now=later)
    assert second["acquired_at"] == first["acquired_at"]
    assert second["checked_at"] == later.isoformat()


def test_verified_redirect_alias_reuses_large_body_without_crossing_paid_scope(tmp_path):
    results = ArticleBodyResults(MessageBusV2Repository(tmp_path / "bus.db"))
    original = message().model_copy(update={"url": "https://finnhub.io/api/news?id=1"})
    row, _ = results.schedule(original, "source_a")
    assert results.acquire(row["fetch_key"], "redirect")
    content = "Verified complete article. " * 1000
    results.finish(
        row["fetch_key"],
        "redirect",
        original,
        SimpleNamespace(
            succeeded=True,
            content=content,
            diagnostics={"outcome": "FULL"},
            final_url="https://publisher.test/news/1",
        ),
    )
    target = original.model_copy(update={"url": "https://publisher.test/news/1"})
    alias, kind = results.schedule(target, "source_b")
    assert alias["fetch_key"] == row["fetch_key"] and kind == "reuse"
    assert alias["content"] == content
    assert results.acquire(row["fetch_key"], "paid")
    results.finish(
        row["fetch_key"],
        "paid",
        original,
        SimpleNamespace(
            succeeded=True,
            content=content,
            diagnostics={"outcome": "FULL"},
            final_url="https://www.reuters.com/news/1",
        ),
    )
    other, kind = results.schedule(original, "source_b")
    assert other["fetch_key"] != row["fetch_key"] and kind == "new_article"
    assert results.schedule(original, "source_a")[0]["fetch_key"] == row["fetch_key"]


def test_published_identity_links_syndicated_url_without_resetting_recheck_or_new_cohort(tmp_path):
    results = ArticleBodyResults(MessageBusV2Repository(tmp_path / 'bus.db'))
    original, now = message(), datetime.now(UTC)
    row, _ = results.schedule(original, 'source', now=now)
    key = row['fetch_key']
    assert results.acquire(key, 'original', now=now)
    results.finish(key, 'original', original, SimpleNamespace(succeeded=True, content='Full verified body.',
        diagnostics={'outcome':'FULL'},final_url=original.url), now=now)
    proof = SimpleNamespace(metadata={'article_body_result_key':key},first_seen_at=now,raw_message_id='published')
    syndicated = original.model_copy(update={'url':'https://syndication.test/articles/different-path'})
    linked, kind = results.schedule(syndicated,'source',now=now+timedelta(minutes=10),known_article=proof)
    assert linked['fetch_key'] == key and kind == 'reuse'
    due, kind = results.schedule(syndicated,'source',now=now+timedelta(minutes=31),known_article=proof)
    assert due['fetch_key'] == key and kind == 'recheck'
    assert due['first_observed_at'] == now.isoformat()
    changed = syndicated.model_copy(update={'summary':'Company cancels its investment.'})
    updated, kind = results.schedule(changed,'source',now=now+timedelta(minutes=31),known_article=proof)
    assert kind == 'content_update' and updated['check_seq'] == due['check_seq']+1


def test_known_publication_cannot_cross_scope_or_language_and_is_not_new_article(tmp_path):
    results = ArticleBodyResults(MessageBusV2Repository(tmp_path / 'bus.db'))
    original, now = message(), datetime.now(UTC)
    row, _ = results.schedule(original,'source',now=now)
    key = row['fetch_key']; assert results.acquire(key,'original',now=now)
    results.finish(key,'original',original,SimpleNamespace(succeeded=True,content='Verified body.',
        diagnostics={'outcome':'FULL'},final_url=original.url),now=now)
    proof = SimpleNamespace(metadata={'article_body_result_key':key},first_seen_at=now-timedelta(hours=1),raw_message_id='old')
    for metadata in [{'body_access_scope':'account:other'}, {'language':'ko'}]:
        changed=original.model_copy(update={'url':'https://syndication.test/articles/other',
            'metadata':{**original.metadata,**metadata}})
        actual,kind=results.schedule(changed,'source',now=now,known_article=proof)
        assert actual['fetch_key'] != key and not actual.get('content')
        assert kind == 'content_update' and actual['first_observed_at'] == proof.first_seen_at.isoformat()
        assert actual['prior_publication_raw_id'] == 'old'


def test_published_article_without_full_keeps_material_update_foreground(tmp_path):
    results=ArticleBodyResults(MessageBusV2Repository(tmp_path/'bus.db'))
    original,now=message(),datetime.now(UTC)
    row,_=results.schedule(original,'source',now=now)
    proof=SimpleNamespace(metadata={'article_body_result_key':row['fetch_key']},first_seen_at=now-timedelta(days=1),raw_message_id='published-incomplete')
    assert results.schedule(original,'source',now=now,known_article=proof)[1]=='recheck'
    changed=original.model_copy(update={'summary':'The investment is cancelled after a material problem.'})
    updated,kind=results.schedule(changed,'source',now=now,known_article=proof)
    assert kind=='content_update' and updated['check_seq']>row['check_seq']
    assert updated['first_observed_at']==proof.first_seen_at.isoformat()
