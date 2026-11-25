import json
from datetime import UTC, datetime
from io import BytesIO
from types import SimpleNamespace

import pytest
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

from doxagent.message_bus_v2.fintech_sources import SOURCES, initial_sources, listing_recipes
from doxagent.message_bus_v2.fintech_parsers import parse_listing
from doxagent.message_bus_v2.fintech_body import filing_body
from doxagent.message_bus_v2.schema import RawMessageInput
from doxagent.message_bus_v2.technology_sources import Capture
from doxagent.message_bus_v2.monitoring_terms import MonitoringTermsService
from doxagent.monitoring.media_enrichment import MediaEnrichmentRecord
from scripts.ops.configure_fintech_sources import configure
from tests.fixtures.message_bus_v2 import _bus
from tests.test_message_bus_monitoring_terms import _terms

NOW = datetime(2026,11,25,tzinfo=UTC)


def parse(source,raw,index=0):
    return parse_listing(listing_recipes()[source][index],raw,NOW)


def test_sources_group_columns_and_share_current_cadence():
    sources=initial_sources()
    assert len(sources)==14 and len({s.source_id for s in sources})==14
    assert all(s.default_polling_config.target_interval_seconds==20 for s in sources)
    assert len(listing_recipes()['coindesk_crypto'])==3
    assert len(listing_recipes()['decrypt_crypto'])==4
    assert len(listing_recipes()['cointelegraph_crypto'])==3
    assert all(s.adapter_ref=='builtin:technology_public@1' for s in sources)
    assert all(s.distribution_policy.require_complete_body for s in sources)
    assert next(s for s in sources if s.source_id=='cftc_trading_rules').acquisition_mode.value=='by_ticker'


def test_cftc_filters_actual_rows_even_if_endpoint_ignores_requested_org():
    def row(org,key):
        return f'<tr><td>{org}</td><td>New market maker agreement</td><td>11/23/2026</td><td>Certified</td><td>11/24/2026</td><td>Terms apply</td><td><a href="/IndustryOversight/IndustryFilings/TradingOrganizationRules/{key}">2</a></td></tr>'
    raw='<table>'+row('NODAL',1)+row('COIN',2)+row('KEX',3)+'</table>'
    coin=parse('cftc_trading_rules',raw)
    kalshi=parse('cftc_trading_rules',raw,1)
    assert len(coin)==len(kalshi)==1
    assert coin[0].url.endswith('/2') and coin[0].raw_payload['organization']=='COIN'
    assert kalshi[0].url.endswith('/3') and kalshi[0].raw_payload['organization']=='KEX'
    assert coin[0].raw_payload['attachment_count']==2
    assert coin[0].publication_time_basis=='DATE'


def test_occ_enforces_all_five_types_without_alerts():
    raw=json.dumps({'NewsItems':[dict(NewsType=t,NewsTitle='OCC announcement '+str(t),
        Url='https://www.occ.gov/news-issuances/news-releases/2026/'+str(t)+'.html',PubDate='2026-11-24T16:00:00Z')
        for t in range(7)]})
    rows=parse('occ_newsroom',raw)
    assert {r.title for r in rows}=={'OCC announcement '+str(t) for t in [0,1,3,4,5]}
    assert all(r.publication_time_basis=='EXACT' for r in rows)


def test_defiant_recent_articles_excludes_old_video_and_other_topics():
    raw='<section><h2>Videos</h2><a href="/news/tradfi-and-fintech/old-video"><h3>Old video</h3></a></section>'
    raw+='<section><h2>Recent articles</h2><a href="/converge/tradfi-and-fintech/new-settlement"><h3>New settlement</h3></a><a href="/news/markets/bitcoin-price"><h3>Unrequested topic</h3></a></section>'
    rows=parse('thedefiant_fintech_regulation',raw)
    assert len(rows)==1 and rows[0].title=='New settlement'
    assert rows[0].publication_time_basis=='UNKNOWN_FIRST_SEEN'


def test_decrypt_uses_article_query_not_unrelated_hydrated_recommendations():
    a=dict(__typename='NewsArticleEntity',id=42,slug='new-regulated-product',title='New regulated product',publishedAt='2026-11-24T22:36:02',excerpt='Details')
    state={'props':{'pageProps':{'dehydratedState':{'queries':[
        {'queryKey':['ArticlePreviews',{}],'state':{'data':{'pages':[{'articles':{'data':[a]}}]}}},
        {'queryKey':['OtherWidget',{}],'state':{'data':{'articles':[dict(a,id=99,title='Other query')]}}},
    ]}}}}
    rows=parse('decrypt_crypto','<script id="__NEXT_DATA__">'+json.dumps(state)+'</script>')
    assert len(rows)==1 and rows[0].url=='https://decrypt.co/42/new-regulated-product'
    assert rows[0].published_at.isoformat()=='2026-11-24T22:36:02+00:00'


def nuxt(value):
    # Encode the same reference-table contract used by the real public frontend.
    items=[]
    def ref(value):
        idx=len(items);items.append(None)
        items[idx]={k:ref(v) for k,v in value.items()} if isinstance(value,dict) else [ref(v) for v in value] if isinstance(value,list) else value
        return idx
    ref(value)
    return '<script id="__NUXT_DATA__">'+json.dumps(items)+'</script>'


def test_cointelegraph_drops_cached_articles_absent_from_requested_tag_cards():
    a=dict(slug='tag-story',title='Tag story',publishedAt='2026-11-24T20:48:27Z',status='published')
    b=dict(a,slug='cached-unrelated',title='Unrelated cached article')
    raw=nuxt({'state':{'$surql':{'1':{'data':json.dumps({'Articles':{'docs':[a,b]}})}}}})
    raw+='<article data-card-href="/news/tag-story"><h2>Tag story</h2></article>'
    rows=parse('cointelegraph_crypto',raw)
    assert len(rows)==1 and rows[0].title=='Tag story'


def test_rg_only_picks_prediction_market_links_and_keeps_unknown_precision():
    raw='<a href="/news/gambling-industry/unrelated"><h3>Sports court news</h3></a>'
    raw+='<a href="/news/prediction-markets/new-rule"><h3>Prediction markets new rule</h3></a>'
    rows=parse('rg_prediction_markets',raw)
    assert len(rows)==1 and rows[0].publication_time_basis=='UNKNOWN_FIRST_SEEN'


def pdf(text=None):
    writer=PdfWriter();page=writer.add_blank_page(width=612,height=792)
    if text:
        font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
        page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
        stream=DecodedStreamObject();stream.set_data(('BT /F1 12 Tf 10 700 Td ('+text+') Tj ET').encode())
        page[NameObject('/Contents')]=writer._add_object(stream)
    out=BytesIO();writer.write(out);return out.getvalue()


@pytest.mark.asyncio
async def test_cftc_loads_all_documents_and_preserves_financial_terms():
    url='https://www.cftc.gov/IndustryOversight/IndustryFilings/TradingOrganizationRules/123'
    message=RawMessageInput(title='CFTC COIN new agreement',url=url,raw_payload=dict(
        organization='COIN',description='New market maker agreement',receipt_date='11/23/2026',
        status='Certified',status_date='11/24/2026',remarks='',attachment_count=2))
    contents=[pdf('The market maker agreement pays $40 million and expires December 2027.'),pdf('The fee schedule sets a 2.5 percent rebate and requires $5 million collateral.')]
    detail='<h1>Designated Contract Market Rules: 123</h1>Organization: COIN<table><tr><td><a href="/files/terms.pdf">Terms</a></td><td><a href="/files/fees.pdf">Fees</a></td></tr></table>'
    async def fetch(url,spec,**kw):
        if url==message.url:return Capture(200,url,detail,{})
        return Capture(200,url,'',{'content-type':'application/pdf'},contents[0 if url.endswith('terms.pdf') else 1])
    result=await filing_body(SimpleNamespace(fetch=fetch),SOURCES['cftc_trading_rules'],SimpleNamespace(message=message),
        MediaEnrichmentRecord('id','id','cftc_trading_rules','COIN',message.title,'',url))
    assert result.succeeded and result.diagnostics['outcome']=='FULL'
    assert '$40 million' in result.content and '2.5 percent' in result.content and '$5 million' in result.content
    assert len(result.diagnostics['attachments'])==2
    contents[1]=pdf()
    failed=await filing_body(SimpleNamespace(fetch=fetch),SOURCES['cftc_trading_rules'],SimpleNamespace(message=message),result.record)
    assert not failed.succeeded and failed.reason=='cftc_attachment_ocr_required'
    assert failed.diagnostics['outcome']=='PARTIAL'


def test_configuration_is_candidate_safe_and_first_terms_seed_x_once(tmp_path):
    repo,bus=_bus(tmp_path/'bus.db')
    before={b.binding_id:b.model_dump() for b in repo.list_bindings()}
    one=configure(bus)
    assert len(one['bindings'])==16 and repo.get_ticker_state('COIN') is None
    assert MonitoringTermsService(repo).get('COIN') is None
    assert all(repo.get_binding(k).model_dump()==v for k,v in before.items())
    versions={b.binding_id:b.version for b in repo.list_bindings(ticker='COIN')}
    configure(bus)
    assert {b.binding_id:b.version for b in repo.list_bindings(ticker='COIN')}==versions
    from doxagent.message_bus_v2.monitoring_terms import TickerMonitoringTerms
    terms=MonitoringTermsService(repo)
    value=TickerMonitoringTerms.model_validate({**_terms().model_dump(),'ticker':'COIN','expected_revision':0,'x_accounts':[]})
    terms.apply(value,actor='test')
    rev,saved=terms.get('COIN')
    assert saved.x_accounts==['faryarshirzad','coinbase']
    terms.apply(saved.model_copy(update={'expected_revision':rev,'x_accounts':[]}),actor='test')
    assert terms.get('COIN')[1].x_accounts==[]


def test_candidate_admission_keeps_preconfigured_sources_without_starting_coin(tmp_path):
    from doxagent.ticker_initialization.configuration import CandidateConfiguration
    repo,bus=_bus(tmp_path/'bus.db');configure(bus)
    expected={b.source_id for b in repo.list_bindings(ticker='COIN')}
    candidate=CandidateConfiguration(repo.path,'init-coin-test','COIN')
    candidate.prepare()
    from doxagent.message_bus_v2.service import MessageBusV2Service
    configure(MessageBusV2Service(candidate.candidate))
    candidate.install()
    assert expected <= {b.source_id for b in repo.list_bindings(ticker='COIN')}
    assert repo.get_ticker_state('COIN') is None


def test_public_rg_author_expand_does_not_block_article_but_paid_schema_still_does():
    from doxagent.message_bus_v2.fintech_body import inspect_article
    from doxagent.content_enrichment.quality import choose_candidate
    url='https://rg.org/news/prediction-markets/new-rule';title='Prediction markets announce rule changes'
    body=''.join('<p>The exchange rule changes require $5 million collateral and preserve customer settlement rights. Section '+str(n)+'.</p>' for n in range(12))
    raw='<html><head><link rel="canonical" href="'+url+'"><script type="application/ld+json">'+json.dumps({'@type':'NewsArticle','headline':title,'isAccessibleForFree':True})+'</script></head><body><h1>'+title+'</h1><article>'+body+'</article><aside>Author interests<button>Show More</button></aside></body></html>'
    info=inspect_article(SOURCES['rg_prediction_markets'],raw,url,title)
    candidate,outcome,_=choose_candidate(info,title)
    assert candidate and outcome=='FULL' and '$5 million' in candidate.text
    assert 'Author interests' not in candidate.text
    protected=inspect_article(SOURCES['rg_prediction_markets'],raw.replace('true','false'),url,title)
    assert choose_candidate(protected,title)[1]!='FULL'


def test_ledger_pro_preview_is_never_full_even_when_over_generic_length_threshold():
    from doxagent.message_bus_v2.fintech_body import inspect_article
    from doxagent.content_enrichment.quality import choose_candidate
    title='Bank expands tokenized money market fund';url='https://www.ledgerinsights.com/tokenized-fund/'
    raw='<h1>'+title+'</h1><article><div class="entry-content">'+'<p>Public preview describes a tokenized fund with $100 million assets and institutional investors.</p>'*14+'<p>Article continues …</p><p>Want the full story? Pro subscribers get complete articles.</p></div></article>'
    info=inspect_article(SOURCES['ledgerinsights_news'],raw,url,title)
    candidate,outcome,reason=choose_candidate(info,title)
    assert candidate is None and outcome=='UNAVAILABLE' and reason=='subscription_required'


@pytest.mark.asyncio
async def test_occ_native_pdf_preserves_full_text_and_rejects_image_only_pages():
    from doxagent.message_bus_v2.fintech_body import document_body
    url='https://www.occ.gov/news-issuances/speeches/2026/speech.pdf'
    record=MediaEnrichmentRecord('id','id','occ_newsroom','COIN','Comptroller speech','',url)
    data=pdf('Capital standards require $40 million in eligible bank collateral.')
    async def fetch(target,spec,**kwargs):
        assert target==url and kwargs['binary']
        return Capture(200,url,'',{'content-type':'application/pdf'},data)
    result=await document_body(SimpleNamespace(fetch=fetch),SOURCES['occ_newsroom'],record)
    assert result.succeeded and '$40 million' in result.content
    data=pdf()
    result=await document_body(SimpleNamespace(fetch=fetch),SOURCES['occ_newsroom'],record)
    assert not result.succeeded and result.reason=='public_document_ocr_required'


def test_stdlib_transport_honors_post_payload_and_keeps_pdf_bytes(monkeypatch):
    from email.message import Message
    from doxagent.message_bus_v2.corporate_ir.http import PublicHttp
    headers=Message();headers['Content-Type']='application/pdf'
    class Response(BytesIO):
        status=200
        url='https://example.com/query'
    response=Response(b'%PDF-raw');response.headers=headers
    calls=[]
    def open_request(request,**kwargs):
        calls.append(request)
        return response
    import urllib.request
    monkeypatch.setattr(urllib.request,'build_opener',lambda *a:SimpleNamespace(open=open_request))
    result=PublicHttp._stdlib_read(response.url,{},method='POST',data=b'{"filter":"COIN"}',binary=True)
    assert calls[0].method=='POST' and calls[0].data==b'{"filter":"COIN"}'
    assert result.raw_bytes==b'%PDF-raw' and result.body==''


@pytest.mark.asyncio
async def test_coinbase_cms_native_cutoff_and_listing_cache_guard_are_not_body_policy(monkeypatch):
    from doxagent.message_bus_v2.corporate_ir.catalog import initial_endpoints
    from doxagent.message_bus_v2.corporate_ir.http import PublicHttp
    from doxagent.message_bus_v2.corporate_ir.parsers import parse_listing as parse_ir
    from urllib.parse import parse_qs,urlsplit
    from unittest.mock import AsyncMock
    spec=next(s for s in initial_endpoints() if s.endpoint_id=='coin.blog')
    raw=json.dumps({'items':[{'sys':{'id':'official'},'fields':{
        'title':'Coinbase announces new product','slug':'/blog/new-product','publicationDate':'2026-11-24T08:00-08:00'}}]})
    async def fetch(url,*a,**kw):
        if urlsplit(url).hostname=='contentful.coinbase.com':
            query=parse_qs(urlsplit(url).query)
            assert 'fields.publicationDate[lte]' in query and 'sys.updatedAt[lte]' not in query
            assert query['order']==['-fields.publicationDate']
        return Capture(200,url,raw,{'age':'900'})
    http=PublicHttp();http.routed_http=SimpleNamespace(fetch=fetch,close=AsyncMock())
    try:
        with pytest.raises(ValueError,match='cache_age'):await http.fetch(spec)
        # BODY is not constrained by the discovery list's CDN age threshold.
        assert (await http.fetch(spec,url='https://www.coinbase.com/blog/new-product')).status==200
        rows,_=parse_ir(spec,raw)
        assert len(rows)==1 and rows[0].publication_time_basis=='DATE'
    finally:await http.close()


@pytest.mark.asyncio
async def test_fintech_ticker_catchup_uses_existing_listing_instead_of_rss_only_delegate():
    from doxagent.message_bus_v2.public_listing import ListingAdapter
    from doxagent.message_bus_v2.schema import PollResult
    from unittest.mock import AsyncMock
    source=next(s for s in initial_sources() if s.source_id=='cftc_trading_rules')
    adapter=ListingAdapter(SimpleNamespace(poll=AsyncMock(side_effect=AssertionError('RSS delegate has no ticker sweep'))),None)
    adapter._poll=AsyncMock(return_value=PollResult(window_done=True,window_coverage='PARTIAL'))
    result=await adapter.poll(SimpleNamespace(source=source,window_cutoff=NOW))
    assert result.window_done and result.window_coverage=='PARTIAL'
    adapter._poll.assert_awaited_once()
