import json
from urllib.parse import quote

from doxagent.message_bus_v2.reuters_sources import is_reuters_search_request, native_search_rows
from doxagent.site_strategy.schema import AccessRequest
from doxagent.site_strategy.verification import reuters_listing_rows


def test_blank_query_shell_never_proves_a_successful_micron_search():
    url='https://www.reuters.com/site-search/?query=Micron&offset=0'
    request=AccessRequest(operation_id='shell',url=url,purpose='CRAWLER',mode='BROWSER',
        recipe_ref='builtin:reuters_search@1',recipe_parameters={'query':'Micron','offset':0})
    for heading,expected in [('No search results match the term “”.',None),
                             ('No search results match the term “Intel”.',None),
                             ('No search results match the term “Micron”.',[])]:
        assert reuters_listing_rows('<main><h2>'+heading+'</h2></main>',request,url)==expected


def test_only_actual_requested_query_and_offset_are_listing_transport():
    endpoint='https://www.reuters.com/pf/api/v3/content/fetch/articles-by-search-v2'
    def url(keyword='Micron',offset=20):return endpoint+'?query='+quote(json.dumps({'keyword':keyword,'offset':offset,'size':20}))
    assert is_reuters_search_request(url(),'Micron',20)
    assert not is_reuters_search_request(url('Intel'),'Micron',20)
    assert not is_reuters_search_request(url(offset=0),'Micron',20)
    assert not is_reuters_search_request(url().replace('www.reuters.com','ads.example.com'),'Micron',20)
    assert not is_reuters_search_request(endpoint+'?query=invalid','Micron',20)


def test_native_results_preserve_publication_time_and_reject_non_articles():
    import pytest
    payload={'result':{'articles':[{'canonical_url':'/technology/micron-memory-2026-11-03/',
        'title':'Micron announces new memory products','published_time':'2026-11-03T17:00:00Z','description':'HBM update'}]}}
    rows=native_search_rows(payload)
    assert rows[0]['date']=='2026-11-03T17:00:00Z'
    assert rows[0]['date_basis']=='publisher_timestamp'
    assert native_search_rows({'result':{'articles':[]}})==[]
    with pytest.raises(ValueError):native_search_rows({'result':{}})
    payload['result']['articles'][0]['canonical_url']='https://ads.example.com/foo-2026-11-03/'
    with pytest.raises(ValueError):native_search_rows(payload)
