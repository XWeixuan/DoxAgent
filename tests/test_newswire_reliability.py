import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from doxagent.content_enrichment.publishers import public_article_request
from doxagent.content_enrichment.quality import choose_candidate, inspect_html
from doxagent.content_enrichment.managed import SiteAccessTransport
from doxagent.site_strategy.schema import AccessResult
from tests.test_content_enrichment_pipeline import TITLE, BODY, PARAGRAPHS


@pytest.mark.parametrize('url,method', [
    ('https://www.businesswire.com/news/home/20261112795782/en/A-release/', 'POST'),
    ('https://businesswire.com/news/home/123/en/', 'POST'),
    ('https://www.businesswire.com/newsroom', 'GET'),
    ('https://businesswire.com.example/news/home/123/en/', 'GET'),
    ('https://www.prnewswire.com/news-releases/a-123.html', 'GET'),
    ('https://r.jina.ai/https://www.businesswire.com/news/home/123/en/', 'GET'),
])
def test_readonly_post_is_limited_to_businesswire_article(url, method):
    actual, target = public_article_request(url)
    assert actual == method
    assert target == url.rstrip('/') if method == 'POST' else target == url


async def test_managed_http_uses_original_public_post_not_reader_or_browser():
    response = AccessResult(request_id='one',operation_id='body',disposition='SUCCESS',
                            site_id='businesswire',runtime_key='businesswire',strategy_revision=1,
                            status_code=200,body='<h1>Release</h1>')
    client = SimpleNamespace(execute=AsyncMock(return_value=response))
    transport = SiteAccessTransport(client)
    await transport.fetch('https://www.businesswire.com/news/home/123/en/', [])
    request = client.execute.call_args.args[0]
    assert request.method == 'POST' and request.url.endswith('/123/en')
    assert request.mode.value == 'HTTP_PUBLIC' and not request.parameters


async def test_local_public_transport_uses_same_readonly_method():
    from doxagent.content_enrichment.transport import PublicTransport
    from doxagent.monitoring.media_enrichment import DomainFetchController
    response=SimpleNamespace(status_code=200,headers={'content-type':'text/html'},text='<h1>Release</h1>',
                             url='https://www.businesswire.com/news/home/123/en')
    session=SimpleNamespace(post=AsyncMock(return_value=response),get=AsyncMock(side_effect=AssertionError('wrong method')))
    result=await PublicTransport(session,DomainFetchController(),validate_urls=False).fetch(
        'https://www.businesswire.com/news/home/123/en/',[])
    assert result.status==200 and result.reason is None
    assert session.post.call_args.args[0].endswith('/123/en')
    session.get.assert_not_called()


def release(host, markup, outside=''):
    return inspect_html('<h1>'+TITLE+'</h1>'+markup+outside,
                        'https://www.'+host+'/news-release/test', TITLE)


def test_prn_mobile_jump_is_not_an_unexpanded_article():
    body=''.join('<p>'+p+'</p>' for p in PARAGRAPHS)
    info=release('prnewswire.com','<section class="release-body"><a href="#continue-jump">Continue reading</a>'
                 '<span id="continue-jump"></span>'+body+'</section>',
                 '<footer><button>Read more</button></footer>')
    candidate,outcome,_=choose_candidate(info,TITLE)
    assert outcome=='FULL' and candidate.text==BODY
    assert not info.expansion_required


@pytest.mark.parametrize('control',[
    '<a href="#continue-jump">Continue reading</a>',  # no in-document destination
    '<button>Read more</button>',
    '<p>Sign in to read</p>',
])
def test_prn_keeps_real_missing_content_or_permissions(control):
    info=release('prnewswire.com','<section class="release-body">'+
                 ''.join('<p>'+p+'</p>' for p in PARAGRAPHS)+control+'</section>')
    assert choose_candidate(info,TITLE)[1] != 'FULL'


@pytest.mark.parametrize('host,tag',[
    ('businesswire.com','div id="bw-release-story"'),
    ('newsfilecorp.com','article id="release"'),
])
def test_wire_article_region_excludes_recommendations(host,tag):
    content='<'+tag+'>'+''.join('<p>'+p+'</p>' for p in PARAGRAPHS)+'</'+tag.split()[0]+'>'
    info=release(host,content,'<article><p>Other stories</p><button>Read more</button></article>')
    candidate,outcome,_=choose_candidate(info,TITLE)
    assert outcome=='FULL' and candidate.text==BODY


async def test_explicit_newsfile_registry_policy_survives_massive_defaults():
    from doxagent.content_enrichment.pipeline import ArticlePipeline
    from doxagent.content_enrichment.transport import Observation
    from doxagent.monitoring.media_enrichment import MediaEnrichmentRecord,DomainFetchController
    from doxagent.site_strategy.seeds import seed_specs
    spec=next(s for s in seed_specs() if s.site_id=='newsfile')
    fetch=AsyncMock(side_effect=AssertionError('known failing HTTP/Reader selected'))
    browser=SimpleNamespace(site_managed=True,read=AsyncMock(return_value=(
        Observation('https://www.newsfilecorp.com/release/123/test',
                    '<h1>'+TITLE+'</h1><article id="release">'+''.join('<p>'+p+'</p>' for p in PARAGRAPHS)+'</article>',200),{})))
    pipeline=ArticlePipeline(SimpleNamespace(fetch=fetch,controller=DomainFetchController()),
                             site_client=SimpleNamespace(resolve=AsyncMock(return_value=SimpleNamespace(
                                 site_id=spec.site_id,body=spec.body,auth=spec.auth,strategy_revision=1,runtime_key='newsfile'))),browser=browser)
    result=await pipeline.extract(MediaEnrichmentRecord('one','one','tmx_newsfile_massive','MU',TITLE,None,
                                    'https://www.newsfilecorp.com/release/123/test'))
    assert result.succeeded and result.content==BODY
    assert browser.read.call_args.kwargs['parameters']['wait_selector']=='article#release p'
    fetch.assert_not_called()


@pytest.mark.parametrize('already_selected',[False,True])
async def test_oldest_delivery_executes_before_short_round_budget(tmp_path,already_selected):
    from doxagent.message_bus_v2.live import LiveDeliveryWorker,LiveQueue
    from tests.test_content_enrichment_hub import _setup
    repo,bus,_,_=_setup(tmp_path);bus.live_queue=LiveQueue(bus)
    worker=LiveDeliveryWorker(bus);worker.configuration_checked_at=time.monotonic()
    worker.delivery_round=7
    new={'article_key':'new','ticker':'MU','binding_id':'new-binding','foreground':1}
    old={'article_key':'old','ticker':'INTC','binding_id':'old-binding','foreground':int(already_selected)}
    batch=[new,old] if already_selected else [new]
    worker.repository.deliveries=lambda state='PENDING',**kw: [] if state=='L2_DUE' else [old] if kw.get('old_only') else batch
    worker._prepare_batch=lambda rows:[(r,None,None,None,None) for r in rows]
    worker.process=AsyncMock()
    assert await worker.run_available(max_seconds=0)==1
    assert worker.process.call_args.args[0]['article_key']=='old'
    worker.process.reset_mock()
    assert await worker.run_available(max_seconds=0)==1
    assert worker.process.call_args.args[0]['article_key']=='new'
