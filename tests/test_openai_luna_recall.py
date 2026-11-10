import json
from dataclasses import replace

import pytest

from doxagent.settings import DoxAgentSettings
from doxagent.persistent_runtime_v2.future_model_profiles import configured_rounds
from doxagent.persistent_runtime_v2.openai_platform import build_request, ENDPOINT
from doxagent.persistent_runtime_v2.openrouter import RuntimeRecallOpenRouterClient, HAIKU_MODEL
from doxagent.persistent_runtime_v2.transport import RuntimeResponsesError, RuntimeResponsesResult
from tests.test_w1_r1_cache import request as w1_request
from tests.test_w2_r1_openrouter import request as w2_request, bailian


def request(lane, count=9):
    original = w1_request(count) if lane == 'W1' else w2_request()
    return replace(original, model='gpt-6-luna', provider='openai', provider_endpoint=ENDPOINT,
                   cache_ttl='12h', metadata={**original.metadata, 'service_tier':'fast'})


def client():
    return RuntimeRecallOpenRouterClient(bailian(), api_key='offline', openai_api_key='offline',
        round_overrides=configured_rounds(DoxAgentSettings(_env_file=None,
            persistent_runtime_v2_api_profile='openai_luna_r1_r2_high')))


def test_opt_in_only_changes_recall_and_freezes_haiku_fallback():
    c = client()
    try:
        new = c.model_settings()
        old = configured_rounds(DoxAgentSettings(_env_file=None,persistent_runtime_v2_api_profile='openai_luna_r2_high'))
        for lane in ['W1','W2']:
            assert all(new['rounds'][lane+':'+rnd] == old[lane+':'+rnd] for rnd in ['R2','R3'])
            r = new['rounds'][lane+':R1'];f = new['recall_fallback'][lane+':R1']
            assert (r['model'],r['provider'],r['cache_ttl'],r['service_tier'],r['reasoning_effort']) == ('gpt-6-luna','openai','12h','fast','high')
            assert (f['model'],f['provider'],f['cache_ttl'],f['reasoning_effort']) == (HAIKU_MODEL,'openrouter','1h','high')
            assert f.get('service_tier') is None and f['timeout_seconds'] == r['timeout_seconds']
        assert new['recall_fallback']['W1:R1']['provisional_cache_batch_size'] == new['rounds']['W1:R1']['provisional_cache_batch_size']
    finally:c.close()


@pytest.mark.parametrize('lane',['W1','W2'])
def test_explicit_long_cache_keeps_dynamic_news_and_repair_outside_prefix(lane):
    a = request(lane);x,fx = build_request(a)
    changed = {**a.payload,'source_message':{'title':'different news'}}
    y,fy = build_request(replace(a,payload=changed,instructions=a.instructions+'\n\n# Previous Attempt Correction\nrepair'))
    assert fx == fy and x['prompt_cache_key'] == y['prompt_cache_key']
    assert x['input'][0] == y['input'][0] and x['input'][0]['role'] == 'developer'
    assert x['input'][1]['content'][:-1] == y['input'][1]['content'][:-1]
    assert 'repair' not in json.dumps(y['input'][0]) and 'repair' in y['input'][-1]['content'][-1]['text']
    points=[b['prompt_cache_breakpoint'] for m in x['input'] for b in m['content'] if 'prompt_cache_breakpoint' in b]
    assert 2 <= len(points) <= 4 and all(p == {'mode':'explicit','ttl':'12h'} for p in points)
    assert 'prompt_cache_breakpoint' not in x['input'][-1]['content'][-1]
    assert x['prompt_cache_options'] == {'mode':'explicit','ttl':'12h'}
    assert x['reasoning'] == {'effort':'high'} and x['service_tier'] == 'fast' and x['store'] is False
    assert x['max_output_tokens'] == 65536 and len(x['prompt_cache_key']) <= 64


def test_provisional_promotion_preserves_base_key_and_prior_block_text():
    a,fa=build_request(request('W1',9));b,fb=build_request(request('W1',12))
    assert fa != fb and a['prompt_cache_key'] == b['prompt_cache_key']
    assert a['input'][0] == b['input'][0]
    for x,y in zip(a['input'][1]['content'][:-1],b['input'][1]['content'][:-1]):
        assert x['text'] == y['text']
    long,_=build_request(request('W1',200))
    assert sum('prompt_cache_breakpoint' in b for m in long['input'] for b in m['content']) == 4
    joined=''.join(b['text'] for b in long['input'][1]['content'])
    for row in request('W1',200).payload['today_provisional_facts'].splitlines():assert joined.count(row+'\n') == 1


@pytest.mark.parametrize('change',[{'cache_ttl':'1h'},{'inherit_context':True},{'replay_parent':('a','b')}])
def test_invalid_recall_contract_never_calls_provider(change):
    with pytest.raises(RuntimeResponsesError,match='OpenAI|Recall'):
        build_request(replace(request('W1'),**change))


def test_twelve_hour_receipt_uses_total_write_rate_and_returned_tier():
    import httpx
    from decimal import Decimal
    from doxagent.persistent_runtime_v2.openai_platform import OpenAIPlatformClient
    from doxagent.v2_read.pricing import normalize,price
    events=[{'type':'response.output_text.delta','delta':'{"event_ids":[]}'},
        {'type':'response.completed','response':{'id':'resp-recall','status':'completed','model':'gpt-6-luna',
         'service_tier':'fast','usage':{'input_tokens':2000,'output_tokens':100,
         'input_tokens_details':{'cached_tokens':1000,'cache_write_tokens':500,
          'cache_write_tokens_details':{'ttl_30m_tokens':0,'ttl_12h_tokens':500}},
         'output_tokens_details':{'reasoning_tokens':60}}}}]
    c=OpenAIPlatformClient('offline',client=httpx.Client(transport=httpx.MockTransport(lambda r:
        httpx.Response(200,content=''.join('data: '+json.dumps(e)+'\n\n' for e in events)))))
    try:r=c.complete(request('W1'))
    finally:c.close()
    raw=dict(input_tokens=r.input_tokens,output_tokens=r.output_tokens,cached_input_tokens=r.cached_input_tokens,
             cache_write_input_tokens=r.cache_write_input_tokens,provider_usage=r.provider_usage)
    usage=normalize('openai',raw,ttl='12h');usage['provider_usage']=r.provider_usage
    assert r.provider_usage['cache_ttl']=='12h' and r.cache_write_input_tokens==500
    assert price('gpt-6-luna','openai',None,usage,endpoint=ENDPOINT)['total_cost_usd']==Decimal('0.00047')


@pytest.mark.parametrize('lane',['W1','W2'])
@pytest.mark.parametrize('crash',[False,True])
@pytest.mark.parametrize('failure',['provider_timeout','provider_auth_failed','provider_rate_limited','provider_request_failed','provider_stream_failed','incomplete_structured_output'])
def test_recall_fallback_is_durable_and_keeps_inputs(tmp_path,lane,crash,failure,monkeypatch):
    from doxagent.persistent_runtime_v2.journal import RuntimeJournal
    from doxagent.persistent_runtime_v2.repository import SQLitePersistentRuntimeV2Repository
    from doxagent.persistent_runtime_v2.schema import W1Round1Result,W2Round1RecallResult
    from doxagent.persistent_runtime_v2.service import PersistentRuntimeV2Service
    from tests.test_persistent_runtime_v2 import _FakeKnownEvents,_FakePolicies
    from tests.test_runtime_policy_direction_usage import fixture_case
    primary=client();calls=[]
    model=W1Round1Result if lane=='W1' else W2Round1RecallResult
    class Responses:
        model=primary.model
        model_settings=primary.model_settings
        recall_fallback_settings=primary.recall_fallback_settings
        def complete(self,r):
            calls.append(r)
            if r.provider=='openai':raise RuntimeResponsesError(failure,'unavailable',retryable=False,usage={'input_tokens':123,'output_tokens':7})
            return RuntimeResponsesResult(value=model(),response_id='haiku-success',latency_ms=1,
                input_tokens=10,output_tokens=5,reasoning_tokens=2,cached_input_tokens=3)
    journal=RuntimeJournal(tmp_path/'runtime.db')
    service=PersistentRuntimeV2Service(repository=SQLitePersistentRuntimeV2Repository(journal.path),journal=journal,
        responses=Responses(),known_events=_FakeKnownEvents(),policies=_FakePolicies(),dispatch_effects=False,max_retry_attempts=0)
    case,_=fixture_case();case.frozen_inputs['models']=primary.model_settings();service.repository.save_case(case)
    kw=dict(case=case,lane=lane,round_name='R1',round_prompt='',payload={'frozen':'business facts'},output_model=model,
            schema_name='w1_round1_result' if lane=='W1' else 'w2_round1_recall_result')
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
        assert [r.provider for r in calls] == ['openai','openrouter']
        assert calls[0].payload == calls[1].payload and calls[0].instructions == calls[1].instructions
        turns=service.repository.list_turns(case.case_id)
        assert [t.model for t in turns] == ['gpt-6-luna',HAIKU_MODEL] and turns[0].input_tokens == 123
        assert calls[0].cache_ttl == '12h' and calls[1].cache_ttl == '1h'
        assert 'service_tier' not in calls[1].metadata
    finally:service.close();primary.close()
