from types import SimpleNamespace

from doxagent.site_strategy.governance_models import SourceAccessScope
from doxagent.site_strategy.recovery import verify_capture
from doxagent.site_strategy.runtime import RuntimeResponse
from doxagent.site_strategy.schema import AccessRequest


URL='https://www.reuters.com/site-search/?query=Micron&offset=0'
HTML='''<html><main><a href="/technology/chip-memory-story-2026-11-20/">Micron memory news article example</a></main></html>'''


def case():
    service=SimpleNamespace(resolve=lambda _:SimpleNamespace(site_id='reuters'))
    scope=SourceAccessScope(scope_id='source:reuters_site_search', source_id='reuters_site_search',
        owner_site_id='reuters',primary_identity_id='main')
    request=AccessRequest(operation_id='actual-query',url=URL,purpose='CRAWLER',mode='BROWSER',
        recipe_ref='builtin:reuters_search@1',recipe_parameters={'query':'Micron','offset':0})
    return service,scope,request.model_dump(mode='json')


def test_ssr_links_in_retained_document_do_not_establish_search_api_readiness():
    service,scope,context=case()
    report=verify_capture(service,scope,context,RuntimeResponse(0,URL,{},body=HTML,
        assessment={'kind':'NONE'}),observed=True)
    assert report.challenge != 'CLEARED' and report.crawler != 'READY'
    assert report.reason == 'reuters_search_requires_business_probe'


def test_real_recipe_result_keeps_normal_business_recovery():
    service,scope,context=case()
    report=verify_capture(service,scope,context,RuntimeResponse(200,URL,{},body=HTML,
        assessment={'kind':'NONE'},recipe_result={'rows':[{'title':'Micron news'}]}))
    assert report.challenge == 'CLEARED' and report.crawler == 'READY'


def test_reuters_section_url_cannot_preempt_real_article_verification():
    from doxagent.site_strategy.verification import body_verification_url
    _,scope,_=case()
    article='https://www.reuters.com/world/us/micron-memory-story-2026-11-20/'
    assert body_verification_url(scope,'https://www.reuters.com/world/',article) == article
    assert body_verification_url(scope,'https://www.reuters.com/world/') is None
