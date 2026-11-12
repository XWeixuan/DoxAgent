import pytest
from doxagent.content_enrichment.quality import inspect_html,choose_candidate
from tests.test_content_enrichment_pipeline import TITLE,BODY,PARAGRAPHS


@pytest.mark.parametrize('host',['www.investing.com','uk.investing.com','in.investing.com','ca.investing.com'])
def test_complete_article_references_do_not_become_cross_edition_originals(host):
    html='<h1>'+TITLE+'</h1><div id="article">'+''.join('<p>'+p+'</p>' for p in PARAGRAPHS)
    html+='<a href="https://www.investing.com/news/transcripts/another-company-123">Read more</a></div>'
    html+='<aside><button>Read more</button><a href="https://other.example/news/321">Read full article</a></aside>'
    info=inspect_html(html,'https://'+host+'/news/stock-market-news/this-story-456',TITLE,
                      strategy_parameters={'body_xpath':['//*[@id="article"]']})
    selected,outcome,_=choose_candidate(info,TITLE)
    assert not info.publisher_links and not info.expansion_required
    assert outcome=='FULL' and selected.text==BODY


def test_real_external_original_link_and_actual_subscription_wall_still_apply():
    html='<h1>'+TITLE+'</h1><div id="article"><p>Summary.</p>'
    html+='<a href="https://publisher.example/news/original">Read full article</a></div>'
    info=inspect_html(html,'https://www.investing.com/news/news-123',TITLE,
                      strategy_parameters={'body_xpath':['//*[@id="article"]']})
    assert info.publisher_links==['https://publisher.example/news/original']
    assert choose_candidate(info,TITLE)[0] is None
    html='<h1>'+TITLE+'</h1><div id="article"><p>'+BODY+'</p><p>Sign in to read</p></div>'
    info=inspect_html(html,'https://www.investing.com/news/news-123',TITLE,
                      strategy_parameters={'body_xpath':['//*[@id="article"]']})
    assert choose_candidate(info,TITLE)[2]=='login_required'


@pytest.mark.parametrize('site',['investing','investorshub'])
def test_access_classification_does_not_run_recall_body_extraction(site,monkeypatch,tmp_path):
    from doxagent.site_strategy.repository import SiteStrategyRepository
    from doxagent.site_strategy.service import SiteStrategyService
    from doxagent.site_strategy.seeds import bootstrap_seed
    from doxagent.site_strategy.runtime import _verification_document
    from doxagent.site_strategy.schema import AccessRequest
    repo=SiteStrategyRepository(tmp_path/'site.sqlite3')
    service=SiteStrategyService(repo,profile_root=tmp_path/'profiles');bootstrap_seed(repo,service)
    url=('https://www.investing.com/news/company-news/example-123' if site=='investing'
         else 'https://investorshub.advfn.com/stock-market/NASDAQ/MU/stock-news/123/example')
    def forbidden(*args,**kw):raise AssertionError('generic extraction on access-classification path')
    monkeypatch.setattr('doxagent.content_enrichment.quality._default_extractor',forbidden)
    request=AccessRequest(operation_id='classification',purpose='BODY',mode='BROWSER',url=url)
    try:
        assert not _verification_document('<article><h1>'+TITLE+'</h1><p>'+BODY+'</p></article>',
                                         url,200,{},service.resolve(url),request)
        assert _verification_document('<title>Verify you are human</title>',url,403,{},
                                      service.resolve(url),request)
    finally:repo.close()
