import importlib.util
from pathlib import Path
import pytest

spec=importlib.util.spec_from_file_location('bloomberg_refresh_probe',Path(__file__).parents[1]/'scripts/ops/bloomberg_latest_refresh_probe.py')
probe=importlib.util.module_from_spec(spec);spec.loader.exec_module(probe)


def item(identifier='TNOK1JKK3NY800',published='2026-11-12T17:06:39.238Z'):
    return {'id':identifier,'publishedAt':published,'headline':'A meaningful headline','url':'/news/articles/2026-11-12/sample?tracking=x'}


def test_complete_unsorted_page_retains_old_and_new_items_and_exact_publication_times():
    old=item(published='2020-01-01T00:00:00Z');new=item('TOD2JQT9NJLT00')
    rows=probe.metadata_rows([old,new])
    assert [r['id'] for r in rows]==[old['id'],new['id']]
    assert rows[1]['publishedAt']=='2026-11-12T17:06:39.238Z'
    assert rows[1]['url']=='https://www.bloomberg.com/news/articles/2026-11-12/sample'


def test_authorization_pages_and_foreign_urls_cannot_be_listing_evidence():
    for url in ['https://evil.example/news/articles/x','javascript:alert(1)','https://www.bloomberg.com/account/signin','https://user:secret@www.bloomberg.com/news/articles/x']:
        assert probe.canonical(url) is None
    assert probe.endpoint('https://www.bloomberg.com/artemis/sentry') is None
    assert probe.endpoint('https://www.bloomberg.com/artemis/oscillator/stream') is None
    assert probe.endpoint('https://evil.example/lineup-next/api/stories?pageNumber=1') is None
    with pytest.raises(ValueError):probe.metadata_rows({'message':'challenge'})


def test_primary_full_list_distinguished_from_expansion_and_second_page():
    assert probe.endpoint('https://www.bloomberg.com/lineup-next/api/stories?limit=25&pageNumber=1')=='list_page_1'
    assert probe.endpoint('https://www.bloomberg.com/lineup-next/api/stories?pageNumber=2')=='list_page_2'
    assert probe.endpoint('https://www.bloomberg.com/lineup-next/api/stories?id=TNOK1JKK3NY800')=='expanded_story'
    assert probe.endpoint('https://www.bloomberg.com/lineup-next/api/stories/TNOK1JKK3NY800')=='expanded_story'


def test_missing_timezone_cannot_produce_fake_exact_latency():
    with pytest.raises(ValueError,match='timezone_missing'):
        probe.metadata_rows([item(published='2026-11-12T17:06:39')])


def test_failed_expansion_is_not_substituted_for_valid_full_list():
    assert probe.metadata_rows([{'status':'rejected','reason':'subscription'}])==[]
    assert probe.metadata_rows([{'status':'fulfilled','value':item()}])[0]['id']==item()['id']
