from datetime import timedelta

import pytest

from doxagent.content_enrichment.rechecks import enqueue_due
from doxagent.content_enrichment.schema import EnrichmentJobStatus
from doxagent.message_bus_v2.body_results import ArticleBodyResults
from doxagent.message_bus_v2.schema import utc_now
from doxagent.settings import DoxAgentSettings
from tests.test_article_body_results import message
from tests.test_content_enrichment_hub import _setup, _message
from tests.test_cross_source_live_dedup import pipeline as _pipeline_fixture
from tests.test_cross_source_live_dedup import capture, drain, input_message, publications


@pytest.fixture
def pipeline(tmp_path):
    return _pipeline_fixture.__wrapped__(tmp_path)


def test_setting_is_off_by_default_and_can_be_enabled(monkeypatch):
    monkeypatch.delenv('DOXAGENT_CONTENT_ENRICHMENT_RECHECK_ENABLED', raising=False)
    assert not DoxAgentSettings(_env_file=None).content_enrichment_recheck_enabled
    monkeypatch.setenv('DOXAGENT_CONTENT_ENRICHMENT_RECHECK_ENABLED', 'true')
    assert DoxAgentSettings(_env_file=None).content_enrichment_recheck_enabled


async def test_off_keeps_full_and_stops_timer_and_listing_reentry_but_allows_update(pipeline):
    repo, bus, extractor = pipeline
    original = input_message(800001, 'https://www.reuters.com/technology/micron-investment/')
    capture(pipeline, 'yahoo_finance_news', original)
    await drain(pipeline)
    live = bus.live_queue.repository
    with repo.transaction() as db:
        db.execute("UPDATE live_articles SET next_check_at=?,checked_at=?",
            ((utc_now()-timedelta(minutes=1)).isoformat(), (utc_now()-timedelta(hours=1)).isoformat()))
        before = dict(db.execute('SELECT * FROM live_articles').fetchone())
    published = publications(pipeline)
    assert not repo.body_recheck_enabled and live.enqueue_checks() == 0
    capture(pipeline, 'yahoo_finance_news', original)
    await drain(pipeline)
    after = live.get(before['article_key'])
    assert after['state'] == 'READY' and after['result_ref'] == before['result_ref']
    assert after['revision'] == before['revision'] and publications(pipeline) == published
    assert len(extractor.calls) == 1
    # Existing explicit material-change handling must still wake the article.
    capture(pipeline, 'yahoo_finance_news', original.model_copy(update={'summary': 'Investment doubled.'}))
    assert live.get(before['article_key'])['work_kind'] == 'content_update'
    assert live.claim(limit=1)


@pytest.mark.parametrize('state', ['FETCH_DUE', 'FETCHING'])
async def test_off_skips_queued_and_expired_live_recheck_without_erasing_result(pipeline, state):
    repo, bus, _ = pipeline
    capture(pipeline, 'yahoo_finance_news', input_message(800002, 'https://www.reuters.com/technology/micron/'))
    await drain(pipeline)
    live = bus.live_queue.repository
    with repo.transaction() as db:
        old = dict(db.execute('SELECT * FROM live_articles').fetchone())
        db.execute("UPDATE live_articles SET state=?,work_kind='recheck',due_at=?,lease_until=?",
            (state, (utc_now()-timedelta(minutes=1)).isoformat(), (utc_now()-timedelta(seconds=1)).isoformat()))
    assert live.claim(limit=4, background=True) == []
    assert live.claim(limit=4) == []  # Includes expired-lease recovery.
    assert live.get(old['article_key'])['result_ref'] == old['result_ref']
    repo.body_recheck_enabled = True
    assert live.claim(limit=1, background=True)  # Paused work is reversible.


async def test_initial_recovery_still_enqueues_and_claims_when_checks_off(pipeline):
    repo, bus, extractor = pipeline
    extractor.blocked = True
    capture(pipeline, 'yahoo_finance_news', input_message(800003, 'https://www.reuters.com/technology/recovery/'))
    await drain(pipeline)
    with repo.transaction() as db:
        db.execute("UPDATE live_articles SET state='READY',work_kind='new_article',"
            "last_error='site_recovery_pending',next_check_at=?", ((utc_now()-timedelta(seconds=1)).isoformat(),))
    live = bus.live_queue.repository
    assert live.enqueue_checks() == 1
    claims = live.claim(limit=1, background=True)
    assert len(claims) == 1 and live.get(claims[0][0])['work_kind'] == 'capability_resume'


@pytest.mark.parametrize('expired', [False, True])
def test_off_stops_legacy_pump_and_claim_including_expired_owners(tmp_path, expired):
    repo, bus, source, binding = _setup(tmp_path)
    job, _ = bus.enqueue_enrichment(source=source, binding=binding, message=_message(800004),
        bootstrap=False, poll_run_id='switch')
    now = utc_now()
    repo.requeue_enrichment_job(job.model_copy(update={'work_kind': 'recheck',
        'status': EnrichmentJobStatus.QUEUED,
        'not_before': now-timedelta(seconds=1),
        'lease_expires_at': None}))
    if expired:
        repo.body_recheck_enabled = True
        assert len(repo.claim_enrichment_jobs(limit=1, now=now)) == 1
        repo.body_recheck_enabled = False
        now += timedelta(seconds=61)
    assert enqueue_due(repo) == 0
    assert repo.claim_enrichment_jobs(limit=4, now=now, allow_rechecks=True) == []
    assert next(j for j in repo.list_enrichment_jobs() if j.job_id == job.job_id).status.value == ('running' if expired else 'queued')
    repo.body_recheck_enabled = True
    assert len(repo.claim_enrichment_jobs(limit=1, now=now, allow_rechecks=True)) == 1


def test_off_reuses_expired_full_only_when_material_and_access_scope_still_match(tmp_path):
    from types import SimpleNamespace
    from doxagent.message_bus_v2.repository import MessageBusV2Repository

    repo = MessageBusV2Repository(tmp_path / 'body.db')
    results = ArticleBodyResults(repo)
    original, now = message(), utc_now()
    row, kind = results.schedule(original, 'source', now=now)
    assert kind == 'new_article'
    results.acquire(row['fetch_key'], 'first', now=now)
    results.finish(row['fetch_key'], 'first', original,
        SimpleNamespace(succeeded=True, content='Complete investment article.',
            diagnostics={'outcome': 'FULL'}, final_url=original.url), now=now)
    old = results.get(row['fetch_key'])
    cached, kind = results.schedule(original, 'source', now=now+timedelta(hours=2))
    assert kind == 'reuse' and cached['acquired_at'] == old['acquired_at']
    assert results.schedule(original.model_copy(update={'summary': 'New details.'}),
        'source', now=now+timedelta(hours=2))[1] == 'content_update'
    private = original.model_copy(update={'metadata': {**original.metadata, 'body_access_scope': 'private:other'}})
    assert results.schedule(private, 'source', now=now+timedelta(hours=2))[1] == 'new_article'
