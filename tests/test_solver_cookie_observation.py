"""Local synthetic CDP events only; no CAPTCHA interactions or website traffic."""
import json
from doxagent.site_strategy.solver_forensics import WireEvidence, cookie_summary


def start(trace,key='api',url='https://www.reuters.com/pf/api/v3/content/fetch/articles-by-search-v2'):
    trace.event({'method':'Network.requestWillBeSent','params':{'requestId':key,'request':{'url':url,'headers':{}}}})


def extra(trace,*,cookie=False,key='api'):
    trace.event({'method':'Network.requestWillBeSentExtraInfo','params':{
        'requestId':key,'headers':{'Cookie':'datadome=PRIVATE_VALUE'} if cookie else {},
        'associatedCookies':[{'cookie':{'name':'datadome','value':'PRIVATE_VALUE'},'blockedReasons':[]}] if cookie else []}})


def test_missing_event_and_observed_cookie_absence_remain_distinct():
    unknown=WireEvidence('audit');start(unknown)
    absent=WireEvidence('audit');extra(absent);start(absent)
    assert unknown.result()[0]['request_cookies']==absent.result()[0]['request_cookies']==[]
    assert unknown.result()[0]['cookie_evidence']=='UNAVAILABLE'
    assert absent.result()[0]['cookie_evidence']=='EXTRA_INFO'


def test_event_order_does_not_change_cookie_delivery_proof():
    first=WireEvidence('audit');extra(first,cookie=True);start(first)
    last=WireEvidence('audit');start(last);extra(last,cookie=True)
    assert first.result()==last.result()
    assert first.result()[0]['cookie_evidence']=='EXTRA_INFO'
    assert len(first.result()[0]['request_cookies'])==1
    assert 'PRIVATE' not in json.dumps(first.result())


def test_unrelated_early_events_release_the_small_buffer():
    trace=WireEvidence('audit')
    for i in range(20):
        key='ad-'+str(i);extra(trace,key=key);start(trace,key,'https://ads.example.test/pixel')
    assert not trace.extras and not trace.result()
    extra(trace);start(trace)
    assert trace.result()[0]['cookie_evidence']=='EXTRA_INFO'
    for i in range(30):extra(trace,key=str(i))
    assert len(trace.extras)<=8


def test_cookie_metadata_retains_lifetime_without_disclosing_value():
    row=cookie_summary([{'name':'datadome','value':'PRIVATE_VALUE','sameSite':'Lax','expires':2000000000}],'audit')[0]
    assert row['value_length']==13 and row['same_site']=='Lax' and row['expires']==2000000000
    assert 'PRIVATE' not in json.dumps(row)


def test_late_extra_for_ignored_requests_does_not_pin_the_buffer():
    trace=WireEvidence('audit')
    for i in range(20):
        key='ad-'+str(i)
        start(trace,key,'https://ads.example.test/pixel')
        extra(trace,key=key)
        trace.event({'method':'Network.loadingFinished','params':{'requestId':key}})
    assert not trace.extras
    extra(trace,cookie=True);start(trace)
    assert trace.result()[0]['cookie_evidence']=='EXTRA_INFO'


def test_memory_cache_before_response_is_not_mistaken_for_network_acceptance():
    trace=WireEvidence('audit');start(trace)
    trace.event({'method':'Network.requestServedFromCache','params':{'requestId':'api'}})
    trace.event({'method':'Network.responseReceived','params':{'requestId':'api','response':{
        'url':'https://www.reuters.com/site-search/','status':200,'fromDiskCache':False}}})
    assert trace.result()[0]['transport']['from_cache'] is True


def test_memory_cache_after_response_is_not_lost():
    trace=WireEvidence('audit');start(trace)
    trace.event({'method':'Network.responseReceived','params':{'requestId':'api','response':{
        'url':'https://www.reuters.com/site-search/','status':200}}})
    trace.event({'method':'Network.requestServedFromCache','params':{'requestId':'api'}})
    assert trace.result()[0]['transport']['from_cache'] is True


def test_prefetched_response_is_not_new_website_acceptance():
    trace=WireEvidence('audit');start(trace)
    trace.event({'method':'Network.responseReceived','params':{'requestId':'api','response':{
        'url':'https://www.reuters.com/site-search/','status':200,'fromPrefetchCache':True}}})
    assert trace.result()[0]['transport']['from_cache'] is True
