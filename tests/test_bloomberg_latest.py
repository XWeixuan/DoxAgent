import copy
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from doxagent.message_bus_v2.bloomberg_latest import parse_stories, poll_latest, story_hint
from doxagent.message_bus_v2.newswire_sources import NewswireAdapter, endpoint_units, initial_sources
from doxagent.message_bus_v2.poll_trace import REQUESTS
from doxagent.message_bus_v2.schema import SharedPollContext

NOW = datetime(2026, 11, 11, 19, 6, tzinfo=UTC)
FIXTURE = Path(__file__).parent / "fixtures/bloomberg_latest/stories.json"


def source():
    return next(s for s in initial_sources() if s.source_id == "bloomberg_massive")


def items():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@asynccontextmanager
async def permit():
    yield


def context(**kwargs):
    return SharedPollContext(run_id="latest-test", source=source(), acquisition_unit="TEST:WIRE:latest",
                             requested_at=NOW, request_permit=permit, **kwargs)


def response(data, *, status=200, age=0):
    return httpx.Response(status, json=data, headers={"Age": str(age), "Cache-Control": "public, max-age=120"},
                          request=httpx.Request("GET", "https://www.bloomberg.com/lineup-next/api/stories"))


def adapter_for(*responses):
    replies = iter(responses)
    async def request(ctx, url, recipe, headers):
        result = next(replies)
        if isinstance(result, Exception):
            raise result
        events = REQUESTS.get()
        if events is not None:
            events.append(dict(physical_request_id=str(len(events)), status_code=result.status_code,
                               role="listing", proof_basis="TRANSPORT_SEND", response_received_at=NOW.isoformat()))
        return result, "direct"
    return SimpleNamespace(listing_request=AsyncMock(side_effect=request))


def test_real_frontend_json_preserves_native_identity_times_and_no_fake_body():
    rows = parse_stories(FIXTURE.read_text(encoding="utf-8"), source())
    assert len(rows) == 25
    assert rows[0].external_id == "TOCQ4GR24U8M00"
    assert rows[0].source_item_key == rows[0].external_id
    assert rows[0].published_at == datetime(2026, 11, 11, 19, 0, 16, 977000, tzinfo=UTC)
    assert all(r.url.startswith("https://www.bloomberg.com/") and r.body is None and r.summary is None for r in rows)
    assert all(r.publication_time_basis == "EXACT" for r in rows)


@pytest.mark.parametrize("bad", [{"appId": "challenge"}, {"latest": {"items": []}}, [None]])
def test_success_status_with_challenge_or_wrong_schema_is_not_a_valid_list(bad):
    with pytest.raises(ValueError):
        parse_stories(json.dumps(bad), source())


@pytest.mark.parametrize("patch", [
    {"id": "not-provider-id"}, {"headline": ""}, {"url": "https://evil.example/news/articles/phish"},
    {"url": "/latest"}, {"publishedAt": "2026-11-11T19:00:00"}, {"updatedAt": "2026-11-11T19:00:00"},
])
def test_bad_article_does_not_silently_reduce_coverage(patch):
    data=items()[:1];data[0].update(patch)
    with pytest.raises(ValueError):
        parse_stories(json.dumps(data), source())


async def test_shared_poll_reads_every_card_keeps_late_items_and_deduplicates_updates():
    data=items()
    a=adapter_for(response(data), response(data), response([{**data[0], "headline": "Important updated headline"}, *data[1:]]))
    first=await poll_latest(a, context(checkpoint={"seen":{data[-1]['id']:"prior-title"}}))
    assert len(first.messages)==25  # Includes old/late candidates; admission decides freshness.
    assert min(r.published_at for r in first.messages) < NOW-timedelta(minutes=30)
    second=await poll_latest(a, context(checkpoint=first.next_checkpoint))
    assert not second.messages and not second.failures
    third=await poll_latest(a, context(checkpoint=second.next_checkpoint))
    assert len(third.messages)==1 and third.messages[0].title=="Important updated headline"
    assert third.messages[0].published_at==first.messages[0].published_at
    args=a.listing_request.call_args_list[0].args
    assert "types=ARTICLE%2CFEATURE%2CINTERACTIVE%2CLETTER%2CEXPLAINERS" in args[1]
    assert "&_=" in args[1] and args[2]=={"method":"GET","routes":["direct"]}
    assert args[3]["Referer"]=="https://www.bloomberg.com/latest"


def test_teaser_and_tracking_changes_do_not_force_recompletion_but_publisher_update_does():
    data=items()[:1];first=parse_stories(json.dumps(data),source())[0]
    data[0].update(summary="new teaser",url=data[0]['url']+"?srnd=latest")
    same=parse_stories(json.dumps(data),source())[0]
    assert same.url==first.url and story_hint(same)==story_hint(first)
    data[0]['updatedAt']="2026-11-11T19:06:00Z"
    changed=parse_stories(json.dumps(data),source())[0]
    assert story_hint(changed)!=story_hint(first)
    assert changed.published_at==first.published_at


async def test_burst_paginates_until_overlap_instead_of_using_maximum_timestamp():
    data=items();second=copy.deepcopy(data)
    for n,row in enumerate(second):
        row.update(id=f"SECONDARY{n:05d}",publishedAt="2026-11-11T19:04:00Z")
    cp={"seen": {second[-1]['id']:story_hint(parse_stories(json.dumps(second[-1:]),source())[0])}}
    a=adapter_for(response(data),response(second))
    result=await poll_latest(a,context(checkpoint=cp))
    assert not result.failures and a.listing_request.await_count==2
    assert len(result.messages)==49 and result.acquisition_metadata['feed_items']==50
    assert 'pageNumber=2' in a.listing_request.call_args_list[1].args[1]


@pytest.mark.parametrize("bad", [response({},status=403), response(items(),age=90), response({"appId":"challenge"}),response([])])
async def test_failure_never_invokes_rss_or_advances_checkpoint(bad):
    cp={"seen":{"retained":"prior-hint"}}
    a=adapter_for(bad);a.poll_feed=AsyncMock(side_effect=AssertionError("RSS must not run"))
    result=await poll_latest(a,context(checkpoint=cp))
    assert result.failures[0].error_code=='BLOOMBERG_LATEST_FAILED'
    assert result.next_checkpoint==cp and not result.window_done
    assert not result.acquisition_metadata['validated_request_ids']
    a.poll_feed.assert_not_called()


async def test_partial_page_failure_preserves_first_page_and_retries_from_old_checkpoint():
    a=adapter_for(response(items()),TimeoutError("page two"));cp={"seen":{}}
    result=await poll_latest(a,context(checkpoint=cp))
    assert len(result.messages)==25 and result.failures and result.next_checkpoint==cp


async def test_ignored_pagination_cannot_be_reported_healthy():
    a=adapter_for(response(items()),response(items()))
    result=await poll_latest(a,context())
    assert 'pagination_ignored' in result.failures[0].error_message


async def test_empty_later_page_is_a_valid_end_without_discarding_head():
    result=await poll_latest(adapter_for(response(items()),response([])),context())
    assert not result.failures and len(result.messages)==25


async def test_only_parsed_fresh_transport_is_listing_proof():
    tok=REQUESTS.set([])
    try:
        good=await poll_latest(adapter_for(response(items()[:2])),context())
        assert good.acquisition_metadata['validated_request_ids']==['0']
        bad=await poll_latest(adapter_for(response({"captcha":True})),context())
        assert bad.failures and not bad.acquisition_metadata['validated_request_ids']
        assert REQUESTS.get()[0]['list_validated'] and not REQUESTS.get()[1].get('list_validated')
    finally:REQUESTS.reset(tok)


async def test_legacy_source_config_still_cannot_poll_rss_after_upgrade():
    a=NewswireAdapter();a.poll_feed=AsyncMock(side_effect=AssertionError("legacy RSS"))
    a.listing_request=adapter_for(response(items()[:2])).listing_request
    old=source().model_copy(update={"default_parameters":{"endpoints":[{"endpoint_id":"news","format":"rss","url":"https://www.bloomberg.com/feeds/news.rss"}]}})
    assert endpoint_units(old)==['TEST:WIRE:latest']
    result=await a.poll_shared(context().model_copy(update={"source":old}))
    assert len(result.messages)==2 and not result.failures
    a.poll_feed.assert_not_called()
    with pytest.raises(ValueError,match='does not use RSS'):
        await NewswireAdapter().poll_feed(context())


def test_configuration_is_idempotent_and_preserves_body_terms_targets_and_isolation(tmp_path):
    from scripts.ops.configure_bloomberg_latest import configure
    from tests.test_content_enrichment_hub import _setup
    from doxagent.message_bus_v2.schema import SourceKind
    repo,bus,_,_=_setup(tmp_path);s=source()
    old=s.model_copy(update={"kind":SourceKind.API,"default_parameters":{"endpoints":[{"url":"https://www.bloomberg.com/feeds/news.rss","format":"rss","endpoint_id":"news"}],"retained_setting":True}})
    repo.save_source(old)
    receipt=configure(bus);updated=repo.get_source(s.source_id)
    assert receipt['action']=='updated' and configure(bus)['action']=='unchanged'
    assert updated.delivery_mode=='test' and updated.distribution_policy==s.distribution_policy
    assert updated.default_parameters['retained_setting'] is True
    assert updated.default_parameters['discovery_mode']=='bloomberg_latest_http'
    assert all(e['format']=='json' for e in updated.default_parameters['endpoints'])
    assert updated.default_polling_config==old.default_polling_config
    with repo._connect() as db:
        assert db.execute('SELECT count(*) FROM ticker_source_bindings WHERE source_id=?',(s.source_id,)).fetchone()[0]==0


def test_native_id_preserves_legacy_url_owner_and_article_once_across_revised_slug(tmp_path):
    from tests.test_content_enrichment_hub import _setup
    from doxagent.message_bus_v2.live import LiveQueue
    from doxagent.message_bus_v2.schema import utc_now
    repo,bus,_,_=_setup(tmp_path);s=source();repo.save_source(s)
    live=LiveQueue(bus).repository
    row=parse_stories(json.dumps(items()[:1]),s)[0].model_copy(update={'published_at':utc_now()})
    # Legacy discoveries carried URL identity and no native id.
    legacy=row.model_copy(update={'external_id':None,'source_item_key':row.url,'metadata':{'language':'en','identity_evidence':{'id_kind':'canonical_url','url_kind':'article'}}})
    live.accept(live.prepare_input(s,[],[legacy],observed_at=utc_now().isoformat(),unit='WIRE:latest'))
    with repo._connect() as db:
        db.execute("UPDATE live_articles SET state='READY',outcome='FULL',body_digest='retained-body' WHERE source_id=?",(s.source_id,))
    live.accept(live.prepare_input(s,[],[row],observed_at=utc_now().isoformat(),unit='WIRE:latest'))
    revised=row.model_copy(update={'title':row.title+' Updated','url':row.url+'-updated'})
    live.accept(live.prepare_input(s,[],[revised],observed_at=utc_now().isoformat(),unit='WIRE:latest'))
    with repo._connect() as db:
        assert db.execute('SELECT count(*) FROM live_articles WHERE source_id=?',(s.source_id,)).fetchone()[0]==1
        owner=db.execute('SELECT input_ref,input_revision FROM live_articles WHERE source_id=?',(s.source_id,)).fetchone()
        assert owner['input_revision']>=2
        assert db.execute("SELECT count(*) FROM live_origins WHERE unit='bloomberg-id'").fetchone()[0]==1
    assert repo.read_only(live.load,owner['input_ref'])['title'].endswith(' Updated')


def test_first_native_observation_cannot_swallow_a_changed_legacy_headline(tmp_path):
    from tests.test_content_enrichment_hub import _setup
    from doxagent.message_bus_v2.live import LiveQueue
    from doxagent.message_bus_v2.schema import utc_now
    repo,bus,_,_=_setup(tmp_path);s=source();repo.save_source(s);live=LiveQueue(bus).repository
    row=parse_stories(json.dumps(items()[:1]),s)[0].model_copy(update={'published_at':utc_now()})
    old=row.model_copy(update={'title':'An earlier headline','external_id':None,'source_item_key':row.url,
                              'metadata':{'language':'en','identity_evidence':{'id_kind':'canonical_url','url_kind':'article'}}})
    live.accept(live.prepare_input(s,[],[old],observed_at=utc_now().isoformat(),unit='WIRE:news'))
    with repo._connect() as db:
        db.execute("UPDATE live_articles SET state='READY',outcome='FULL',body_digest='retained-body' WHERE source_id=?",(s.source_id,))
    live.accept(live.prepare_input(s,[],[row],observed_at=utc_now().isoformat(),unit='WIRE:latest'))
    with repo._connect() as db:
        current=db.execute('SELECT input_ref,input_revision FROM live_articles WHERE source_id=?',(s.source_id,)).fetchone()
        assert current['input_revision']==2
    assert repo.read_only(live.load,current['input_ref'])['title']==row.title
