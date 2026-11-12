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
