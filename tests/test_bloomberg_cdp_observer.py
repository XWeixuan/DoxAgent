import importlib.util
import json
from pathlib import Path

spec=importlib.util.spec_from_file_location('bloomberg_cdp_observer',Path(__file__).parents[1]/'scripts/ops/observe_bloomberg_latest_cdp.py')
observer=importlib.util.module_from_spec(spec)
spec.loader.exec_module(observer)


def story(identifier='TOCNC6T9NJLT00'):
    return {'id':identifier,'publishedAt':'2026-11-11T19:29:06.220Z','headline':'新闻 café','url':'https://www.bloomberg.com/news/articles/2026-11-11/sample'}


def test_only_official_listing_paths_are_classified():
    assert observer.route('https://www.bloomberg.com/artemis/oscillator/stream')=='page_sse'
    assert observer.route('https://www.bloomberg.com/artemis/oscillator/poll/1')=='page_snapshot'
    assert observer.route('https://www.bloomberg.com/lineup-next/api/stories?limit=25')=='page_stories'
    for url in ['https://www.bloomberg.com/artemis/sentry','https://www.bloomberg.com/account/signin','https://attacker.example/lineup-next/api/stories']:
        assert observer.route(url) is None


def test_observation_extracts_public_news_fields_and_excludes_auth_material():
    rows=list(observer.articles({'data':{'name':'LATEST_STORIES','data':[story()]},'token':'private'}))
    assert len(rows)==1 and rows[0]['id']==story()['id']
    assert 'private' not in json.dumps(rows)
    assert not list(observer.articles({'id':'private','publishedAt':'2026-11-11T19:00:00Z'}))
    assert not list(observer.articles({**story(),'publishedAt':'2026-11-11T19:00:00'}))
    assert not list(observer.articles({**story(),'publishedAt':123}))


def test_fetch_sse_handles_arbitrary_chunk_boundaries_unicode_and_ping():
    payload='event: STORIES_SESSION\r\ndata: '+json.dumps({'providerName':'STORIES_SESSION','data':{'name':'LATEST_STORIES','data':[story()]}},ensure_ascii=False)+'\r\n\r\n'
    raw=(': comment\n\nevent: ping\ndata: {}\n\n'+payload).encode()
    decoder=observer.SSE();rows=[]
    for value in raw:
        rows.extend(decoder.feed(bytes([value])))
    assert rows==list(observer.articles(story()))
    assert not decoder.feed(b'event: ping\ndata: {}\n\n')


def test_native_json_snapshots_and_fulfilled_batch_resolve_same_ids():
    snapshot=[{'topicsData':{'LATEST_STORIES':{'data':[story()]}}}]
    expanded=[{'status':'fulfilled','value':story()}]
    assert list(observer.articles(snapshot))==list(observer.articles(expanded))


def test_other_sse_topics_and_authorization_payload_cannot_be_news():
    decoder=observer.SSE()
    for value in [{"token":"private"},{"providerName":"OTHER","data":{"name":"LATEST_STORIES","data":[story()]}},{"providerName":"STORIES_SESSION","data":{"name":"OTHER","data":[story()]}}]:
        assert decoder.feed(("data: "+json.dumps(value)+"\n\n").encode())==[]
