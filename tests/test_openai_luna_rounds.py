import copy
import json
from dataclasses import replace
from decimal import Decimal

import httpx
import pytest

from doxagent.settings import DoxAgentSettings
from doxagent.persistent_runtime_v2.future_model_profiles import configured_rounds
from doxagent.persistent_runtime_v2.openrouter import RuntimeRecallOpenRouterClient, SAIL_MODEL, HAIKU_MODEL
from doxagent.persistent_runtime_v2.openai_platform import OpenAIPlatformClient, build_request, ENDPOINT
from doxagent.persistent_runtime_v2.schema import W1NoveltyResult, W2PolicyAssessmentResultV2
from doxagent.persistent_runtime_v2.transport import RuntimeResponsesError, RuntimeResponsesResult
from doxagent.persistent_runtime_v2.case_cache import VERSION
from doxagent.v2_read.pricing import normalize, price
from tests.test_w2_r1_openrouter import bailian, request


def configured():
    return RuntimeRecallOpenRouterClient(bailian(), api_key='offline', openai_api_key='offline',
        round_overrides=configured_rounds(DoxAgentSettings(_env_file=None,persistent_runtime_v2_api_profile='openai_luna_r2_high')))


def req():
    return replace(request(),model='gpt-6-luna',provider='openai',provider_endpoint=ENDPOINT,
        instructions='fixed prompt',payload={'source_message':{'title':'one'}},cache_context_keys=(),
        output_model=W1NoveltyResult,schema_name='w1_novelty_result',cache_ttl='30m',
        metadata={'lane':'W1','round':'R2','case_cache_layout':VERSION,'service_tier':'fast'})


def stream(output=None,status='completed',tier='fast'):
    text=json.dumps(output or {'result':'NEW','confidence':'normal','reference_ids':[],
        'fact_attributions':[],'reason':'test'})
    events=[{'type':'response.output_text.delta','delta':text},{'type':'response.completed',
        'response':{'id':'resp-native','model':'gpt-6-luna','status':status,'service_tier':tier,
        'usage':{'input_tokens':2000,'output_tokens':100,'input_tokens_details':{
            'cached_tokens':1000,'cache_write_tokens':500,'cache_write_tokens_details':{
                'ttl_30m_tokens':500,'ttl_12h_tokens':0}},'output_tokens_details':{'reasoning_tokens':60}}}}]
    return httpx.Response(200,headers={'content-type':'text/event-stream','x-request-id':'native-test'},
        content=''.join('data: '+json.dumps(e)+'\n\n' for e in events))


def test_manifest_only_changes_r2_and_retains_haiku_single_fallback():
    c=configured()
    try:
        settings=c.model_settings()
        old=configured_rounds(DoxAgentSettings(_env_file=None,persistent_runtime_v2_api_profile='openrouter_sail_high'))
        for lane in ['W1','W2']:
            r2=settings['rounds'][lane+':R2']
            assert r2['model']=='gpt-6-luna' and r2['provider']=='openai'
            assert r2['reasoning_effort']=='high' and r2['service_tier']=='fast' and r2['cache_ttl']=='30m'
            assert settings['rounds'][lane+':R3']==old[lane+':R3']
            assert settings['case_fallback'][lane+':R2']['model']==HAIKU_MODEL
            assert settings['case_fallback'][lane+':R2']['cache_ttl']=='1h'
            assert SAIL_MODEL not in json.dumps(settings['case_fallback'])
    finally:c.close()


@pytest.mark.parametrize('model',[W1NoveltyResult,W2PolicyAssessmentResultV2])
def test_native_wire_schema_fixed_prefix_and_dynamic_retry_tail(model):
    local=copy.deepcopy(model.model_json_schema())
    a=replace(req(),output_model=model)
    b=replace(a,instructions='fixed prompt\n\n# Previous Attempt Correction\ncorrection',payload={'source_message':{'title':'two'}})
    x,fx=build_request(a);y,fy=build_request(b)
    assert x['input'][0]==y['input'][0] and fx==fy and x['prompt_cache_key']==y['prompt_cache_key']
    assert 'correction' not in x['input'][0]['content'][0]['text']
    assert 'correction' in y['input'][-1]['content'][0]['text']
    assert x['input'][0]['content'][0]['prompt_cache_breakpoint']=={'mode':'explicit','ttl':'30m'}
    assert not any('prompt_cache_breakpoint' in v for v in x['input'][-1]['content'])
    assert x['store'] is False and x['max_output_tokens']==65536 and x['service_tier']=='fast'
    def check(v):
        if isinstance(v,dict):
            if 'properties' in v:assert set(v['required'])==set(v['properties'])
            for item in v.values():check(item)
        elif isinstance(v,list):
            for item in v:check(item)
    check(x['text']['format']['schema']);assert local==model.model_json_schema()


def test_native_receipt_cache_and_fast_cost_components():
    client=OpenAIPlatformClient('offline',client=httpx.Client(transport=httpx.MockTransport(lambda r:stream())))
    try:r=client.complete(req())
    finally:client.close()
    assert (r.input_tokens,r.output_tokens,r.cached_input_tokens,r.cache_write_input_tokens,r.reasoning_tokens)==(2000,100,1000,500,60)
    assert r.provider_usage['service_tier']=='fast' and r.provider_usage['request_id']=='native-test'
    usage=normalize('openai',dict(input_tokens=r.input_tokens,output_tokens=r.output_tokens,
        cached_input_tokens=r.cached_input_tokens,cache_write_input_tokens=r.cache_write_input_tokens,
        provider_usage=r.provider_usage),ttl='30m')
    usage['provider_usage']=r.provider_usage
    assert usage['input_tokens_uncached']==500
    priced=price('gpt-6-luna','openai',None,usage,endpoint=ENDPOINT)
    assert priced['total_cost_usd']==Decimal('0.000345') and priced['cost_basis']=='ESTIMATED'
    usage['provider_usage']['service_tier']='default'
    assert price('gpt-6-luna','openai',None,usage,endpoint=ENDPOINT)['total_cost_usd']==Decimal('0.0001725')
    usage['provider_usage']['service_tier']=None
    assert price('gpt-6-luna','openai',None,usage,endpoint=ENDPOINT)['total_cost_usd'] is None


@pytest.mark.parametrize('status,api_code,error',[(401,'invalid_api_key','provider_auth_failed'),
    (429,'rate_limit_exceeded','provider_rate_limited'),(429,'insufficient_quota','provider_balance_unavailable'),
    (500,'server_error','provider_request_failed')])
def test_native_errors_are_consumed_by_existing_fallback(status,api_code,error):
    client=OpenAIPlatformClient('offline',client=httpx.Client(transport=httpx.MockTransport(
        lambda r:httpx.Response(status,json={'error':{'code':api_code}},headers={'retry-after':'2'}))))
    try:
        with pytest.raises(RuntimeResponsesError) as e:client.complete(req())
        assert e.value.code==error and e.value.retry_after_seconds==2
    finally:client.close()


def test_invalid_business_shape_keeps_paid_usage():
    client=OpenAIPlatformClient('offline',client=httpx.Client(transport=httpx.MockTransport(lambda r:stream(
        {'policy_hit':False,'policy_related':True,'policy_ids':[],'hit_condition_ids':[],
         'related_policy_ids':[],'confidence':'normal','reason':'bad'}))))
    try:
        with pytest.raises(RuntimeResponsesError) as error:client.complete(replace(req(),output_model=W2PolicyAssessmentResultV2))
        assert error.value.code=='structured_output_validation_failed'
        assert error.value.usage['output_tokens']==100 and error.value.usage['provider_usage']['service_tier']=='fast'
    finally:client.close()


@pytest.mark.parametrize('lane',['W1','W2'])
@pytest.mark.parametrize('failure',['provider_timeout','provider_auth_failed','provider_rate_limited','semantic_output_validation_failed'])
@pytest.mark.parametrize('crash',[False,True])
def test_durable_openai_to_haiku_fallback_does_not_repeat_native(tmp_path,monkeypatch,lane,failure,crash):
    from doxagent.persistent_runtime_v2.journal import RuntimeJournal
    from doxagent.persistent_runtime_v2.repository import SQLitePersistentRuntimeV2Repository
    from doxagent.persistent_runtime_v2.service import PersistentRuntimeV2Service
    from tests.test_persistent_runtime_v2 import _FakeKnownEvents,_FakePolicies
    from tests.test_runtime_policy_direction_usage import fixture_case
    primary=configured();calls=[]
    class Responses:
        model=primary.model
        model_settings=primary.model_settings
        def complete(self,r):
            calls.append(r)
            if r.provider=='openai':raise RuntimeResponsesError(failure,'failed',retryable=False)
            return RuntimeResponsesResult(value=W1NoveltyResult(result='NEW',confidence='normal',reason='ok'),
                response_id='haiku-response',latency_ms=1,input_tokens=10,output_tokens=5,reasoning_tokens=2,cached_input_tokens=3)
    journal=RuntimeJournal(tmp_path/'runtime.db')
    service=PersistentRuntimeV2Service(repository=SQLitePersistentRuntimeV2Repository(journal.path),journal=journal,
        responses=Responses(),known_events=_FakeKnownEvents(),policies=_FakePolicies(),dispatch_effects=False,max_retry_attempts=0)
    case,_=fixture_case();case.frozen_inputs['models']=primary.model_settings();service.repository.save_case(case)
    kw=dict(case=case,lane=lane,round_name='R2',round_prompt='',payload={'frozen':'facts'},output_model=W1NoveltyResult,schema_name='w1_novelty_result')
    try:
        if crash:
            original=journal.set
            def interrupt(namespace,*args,**kwargs):
                if namespace=='round_provider_fallback':raise KeyboardInterrupt('crash before fallback binding')
                return original(namespace,*args,**kwargs)
            monkeypatch.setattr(journal,'set',interrupt)
            with pytest.raises(KeyboardInterrupt):service._call_with_retry(**kw)
            monkeypatch.setattr(journal,'set',original)
        service._call_with_retry(**kw);service._call_with_retry(**kw)
        assert [r.provider for r in calls]==['openai','openrouter']
        assert [t.model for t in service.repository.list_turns(case.case_id)]==['gpt-6-luna',HAIKU_MODEL]
        assert calls[0].payload==calls[1].payload and calls[0].instructions==calls[1].instructions
        assert calls[0].metadata['service_tier']=='fast' and 'service_tier' not in calls[1].metadata
    finally:service.close();primary.close()
