import copy,json
from dataclasses import replace
import pytest
from doxagent.settings import DoxAgentSettings
from doxagent.persistent_runtime_v2.future_model_profiles import configured_rounds
from doxagent.persistent_runtime_v2.openrouter import RuntimeRecallOpenRouterClient,HAIKU_MODEL
from doxagent.persistent_runtime_v2.openai_platform import build_request
from doxagent.persistent_runtime_v2.schema import W1FactExtractionResult,W2NovelFactAlignmentResultV2,W1NoveltyResult
from doxagent.persistent_runtime_v2.transport import RuntimeResponsesError,RuntimeResponsesResult
from tests.test_openai_luna_rounds import req
from tests.test_w2_r1_openrouter import bailian

def client():
    return RuntimeRecallOpenRouterClient(bailian(),api_key='offline',openai_api_key='offline',
        round_overrides=configured_rounds(DoxAgentSettings(_env_file=None,persistent_runtime_v2_api_profile='openai_luna_all_high')))

def test_only_r3_changes_from_current_profile():
    old=configured_rounds(DoxAgentSettings(_env_file=None,persistent_runtime_v2_api_profile='openai_luna_r1_r2_high'))
    c=client()
    try:
        new=c.model_settings()
        for lane in ['W1','W2']:
            for rnd in ['R1','R2']:assert new['rounds'][lane+':'+rnd]==old[lane+':'+rnd]
            r=new['rounds'][lane+':R3'];f=new['case_fallback'][lane+':R3']
            assert (r['model'],r['provider'],r['reasoning_effort'],r['service_tier'],r['cache_ttl'])==('gpt-6-luna','openai','high','fast','30m')
            assert (f['model'],f['provider_endpoint'],f['reasoning_effort'],f['cache_ttl'])==(HAIKU_MODEL,'google-vertex/global','high','1h')
    finally:c.close()

@pytest.mark.parametrize('model,schema,lane',[(W1FactExtractionResult,'w1_fact_extraction_result','W1'),(W2NovelFactAlignmentResultV2,'w2_novel_fact_alignment_result','W2')])
def test_r3_parent_format_fixed_prefix_and_native_schema(model,schema,lane):
    original=copy.deepcopy(model.model_json_schema())
    r=replace(req(),output_model=model,schema_name=schema,replay_parent=('frozen R2 input','{"result":"NEW"}'),
        metadata={**req().metadata,'round':'R3','lane':lane})
    a,af=build_request(r)
    b,bf=build_request(replace(r,replay_parent=('other R2 input','{"result":"OLD"}'),instructions='fixed prompt\n\n# Previous Attempt Correction\nrepair'))
    assert a['input'][2]=={'role':'assistant','content':[{'type':'output_text','text':'{"result":"NEW"}'}]}
    assert a['input'][0]==b['input'][0] and af==bf and a['prompt_cache_key']==b['prompt_cache_key']
    assert all('prompt_cache_breakpoint' not in v for m in a['input'][1:] for v in m['content'])
    assert a['prompt_cache_options']=={'mode':'explicit','ttl':'30m'}
    if lane=='W2':assert a['text']['format']['schema']['properties']['condition_results']['items']['properties']['condition_ids']['items']['pattern']==r'^[^/]+/[^/]+$'
    assert original==model.model_json_schema()

@pytest.mark.parametrize('lane',['W1','W2'])
@pytest.mark.parametrize('failure',[None,'provider_timeout','provider_rate_limited','structured_output_validation_failed','semantic_output_validation_failed'])
@pytest.mark.parametrize('crash',[False,True])
def test_r3_orchestrator_replays_parent_and_durable_fallback(tmp_path,lane,failure,crash,monkeypatch):
    if not failure and crash:pytest.skip('No fallback transition to interrupt')
    from doxagent.persistent_runtime_v2.journal import RuntimeJournal
    from doxagent.persistent_runtime_v2.repository import SQLitePersistentRuntimeV2Repository
    from doxagent.persistent_runtime_v2.service import PersistentRuntimeV2Service
    from tests.test_persistent_runtime_v2 import _FakeKnownEvents,_FakePolicies
    from tests.test_runtime_policy_direction_usage import fixture_case
    primary=client();calls=[]
    class Responses:
        model=primary.model
        model_settings=primary.model_settings
        def complete(self,r):
            calls.append(r)
            assert r.replay_parent==('frozen parent input','{"result":"NEW"}')
            assert r.previous_response_id is None and not r.inherit_context
            if failure and r.provider=='openai':raise RuntimeResponsesError(failure,'fixture',retryable=False)
            return RuntimeResponsesResult(value=W1NoveltyResult(result='NEW',confidence='normal',reason='ok'),
                response_id='success',latency_ms=1,input_tokens=10,output_tokens=5,reasoning_tokens=2,cached_input_tokens=3)
    j=RuntimeJournal(tmp_path/'runtime.db');s=PersistentRuntimeV2Service(repository=SQLitePersistentRuntimeV2Repository(j.path),journal=j,
        responses=Responses(),known_events=_FakeKnownEvents(),policies=_FakePolicies(),dispatch_effects=False,max_retry_attempts=0)
    case,_=fixture_case();case.frozen_inputs['models']=primary.model_settings();s.repository.save_case(case)
    monkeypatch.setattr(s,'_parent_replay',lambda *args:('frozen parent input','{"result":"NEW"}'))
    kw=dict(case=case,lane=lane,round_name='R3',round_prompt='',payload={'frozen':'R3 facts'},output_model=W1NoveltyResult,
        schema_name='w1_novelty_result',inherit_context=True,previous_response_id='old-provider-r2-id')
    try:
        if crash:
            original=j.set
            def interrupt(namespace,*args,**kwargs):
                if namespace=='round_provider_fallback':raise KeyboardInterrupt('before fallback binding')
                return original(namespace,*args,**kwargs)
            monkeypatch.setattr(j,'set',interrupt)
            with pytest.raises(KeyboardInterrupt):s._call_with_retry(**kw)
            monkeypatch.setattr(j,'set',original)
        s._call_with_retry(**kw);s._call_with_retry(**kw)
        assert [r.provider for r in calls]==(['openai','openrouter'] if failure else ['openai'])
        assert all(t.context_mode=='REPLAY_PARENT' for t in s.repository.list_turns(case.case_id))
    finally:s.close();primary.close()
