import copy,json
from types import SimpleNamespace

import pytest

from doxagent.persistent_runtime_v2.journal import digest
from doxagent.persistent_runtime_v2.router import route_runtime_case
from doxagent.persistent_runtime_v2.schema import (
    RuntimeRouteDecision,RuntimeSideEffect,RuntimeFactCandidate,
    W1FactExtractionResult,W1NoveltyResult,W2PolicyAssessmentResultV2,
    W2NovelFactAlignmentResultV2,strict_json_schema,
)
from doxagent.persistent_runtime_v2.w3_contracts import (
    W3_V6_CONTRACT,W3OperationsResultV6,parse_w3_operations,OPERATIONS_PHASE,
)
from tests.test_w3_v5_execution import setup_case


def fact():
    return RuntimeFactCandidate(proposition='Production started.',assertion_state='ACTUAL')


def v6(tmp_path, *, candidates=None, action='NO_TRADE', hit=False):
    service,j,agent,case,clock=setup_case(tmp_path,action,hit)
    case.w3_contract_version=W3_V6_CONTRACT;service.repository.save_case(case)
    run=agent.run_phase
    def operations(**kw):
        result,job=run(**kw)
        if kw['phase']==OPERATIONS_PHASE:
            return W3OperationsResultV6(pin_actions=[],watch_actions=[],delta_candidates=candidates or []),job
        return result,job
    agent.run_phase=operations
    return service,j,agent,case,clock


def test_empty_fact_results_are_valid_but_missing_or_malformed_results_are_not():
    assert W1FactExtractionResult(candidates=[]).candidates==[]
    for fields in [{},{'candidates':None},{'candidates':[{}]}]:
        with pytest.raises(ValueError):W1FactExtractionResult.model_validate(fields)
    schema=strict_json_schema(W3OperationsResultV6.model_json_schema())
    assert set(schema['required'])=={'pin_actions','watch_actions','delta_candidates'}
    assert set(schema['properties'])==set(schema['required'])
    good=dict(pin_actions=[],watch_actions=[],delta_candidates=[])
    assert parse_w3_operations(good,version=W3_V6_CONTRACT)[0].delta_candidates==[]
    for fields in [dict(pin_actions=[],watch_actions=[]),{**good,'delta_candidates':None},
                   {**good,'delta_candidates':[{}]},{**good,'novelty':{}},
                   {**good,'reference_ids':[]},{**good,'policy':{}}]:
        with pytest.raises(ValueError):parse_w3_operations(fields,version=W3_V6_CONTRACT)
    # Reading a stored old result remains supported; missing fields on a fresh
    # submission are only accepted under its actual frozen legacy schema.
    assert W3OperationsResultV6.model_validate(dict(pin_actions=[],watch_actions=[])).delta_candidates==[]
    assert parse_w3_operations(dict(pin_actions=[],watch_actions=[]),version=W3_V6_CONTRACT,
                               allow_legacy_pin_watch=True)[0].delta_candidates==[]


@pytest.mark.asyncio
async def test_legacy_two_field_worker_result_requires_its_frozen_schema(tmp_path):
    from doxagent.persistent_runtime_v2.journal import RuntimeJournal
    from doxagent.persistent_runtime_v2.w3 import CodexW3AgentRunner,W3Error
    from tests.test_persistent_runtime_w3 import _Worker,_Workspace,_Context
    from tests.test_runtime_policy_direction_usage import fixture_case
    case,_=fixture_case();case.w3_contract_version=W3_V6_CONTRACT
    case.frozen_inputs['w3_contracts']={'w3_operations_schema':{'properties':{'pin_actions':{},'watch_actions':{}}}}
    j=RuntimeJournal(tmp_path/'legacy.db');worker=_Worker(None)
    worker.reject_output=lambda reason:None
    runner=CodexW3AgentRunner(worker=worker,workspace=_Workspace(),context_provider=_Context(),
        prompt_root=tmp_path,journal=j,timeout_seconds=30)
    job=SimpleNamespace(status='succeeded',final_response=json.dumps(dict(pin_actions=[],watch_actions=[])),
        thread_id='existing',job_id='legacy-result')
    try:
        result,_=await runner._validate_phase_job(case,OPERATIONS_PHASE,job,None,worker)
        assert result.delta_candidates==[]
        case.frozen_inputs['w3_contracts']['w3_operations_schema']['properties']['delta_candidates']={}
        with pytest.raises(W3Error,match='delta_candidates'):
            await runner._validate_phase_job(case,OPERATIONS_PHASE,job,None,worker)
    finally:runner.close()


@pytest.mark.parametrize('novelty,w1_conf,w2_conf',[
    ('NEW','normal','normal'),('NEW','low','normal'),('NEW','normal','low'),
    ('OLD','low','normal'),('OLD','normal','low'),
])
def test_every_w3_route_has_exactly_one_fact_compiler(novelty,w1_conf,w2_conf):
    w1=W1NoveltyResult(result=novelty,confidence=w1_conf,reference_ids=['E1'] if novelty=='OLD' else [],reason='facts')
    w2=W2PolicyAssessmentResultV2(policy_hit=False,policy_related=False,related_policy_ids=[],confidence=w2_conf,reason='none')
    route=route_runtime_case(w1,w2,w3_contract_version=W3_V6_CONTRACT)
    assert route.primary_route.value=='W3' and route.side_effects==[RuntimeSideEffect.ROUTE_TO_W3]


def test_non_w3_related_archive_retains_r3():
    w1=W1NoveltyResult(result='NEW',confidence='normal',reason='new')
    w2=W2PolicyAssessmentResultV2(policy_hit=False,policy_related=True,related_policy_ids=['P1'],confidence='normal',reason='related')
    align=W2NovelFactAlignmentResultV2(condition_results=[],related_policy_results=[dict(policy_id='P1',is_new=False)])
    route=route_runtime_case(w1,w2,align,w3_contract_version=W3_V6_CONTRACT)
    assert route.primary_route.value=='ARCHIVE' and RuntimeSideEffect.EMIT_DELTA in route.side_effects


def test_watch_or_stale_side_effects_cannot_dispatch_a_second_compiler(tmp_path,monkeypatch):
    s,_,_,case,_=v6(tmp_path)
    case.route=RuntimeRouteDecision(primary_route='W3',side_effects=[RuntimeSideEffect.ROUTE_TO_W3,RuntimeSideEffect.EMIT_DELTA],reason='POLICY_WATCH_FOLLOWUP')
    s.repository.save_case(case)
    monkeypatch.setattr(s,'_call_with_retry',lambda **kw:pytest.fail('W3 Case must not call W1R3'))
    try:
        s._enqueue_route_effects(case,'parent')
        assert all(e.effect_type is not RuntimeSideEffect.EMIT_DELTA for e in s.repository.list_effects(case.case_id))
        s._execute_r3(case,SimpleNamespace(payload={}))
        case.w1_extraction=W1FactExtractionResult(candidates=[])
        s._execute_r3(case,SimpleNamespace(payload={}))
        assert not s.repository.list_provisional(case.ticker,case.trading_date)
    finally:s.close()


@pytest.mark.parametrize('action',['NO_TRADE','LONG'])
@pytest.mark.parametrize('novelty',['NEW','OLD'])
def test_operations_compiles_after_trade_without_revising_it_and_only_once(tmp_path,action,novelty):
    s,j,agent,case,_=v6(tmp_path,candidates=[fact()],action=action)
    if novelty=='OLD':
        case.w1_final=W1NoveltyResult(result='OLD',confidence='low',reference_ids=['E1'],reason='uncertain')
        j.set('round_inputs',case.case_id+':W1:R2',{'payload':{'event_details':{
            'canonical_events':[{'event_id':'E1','known_event_summary':'prior event','facts':[]}],
            'provisional_events':[],
        }}})
        s.repository.save_case(case)
    try:
        assert s.process_pending_effects()==1
        prior=s.repository.get_case(case.case_id);trade=copy.deepcopy(prior.w3_trade_decision)
        intents=copy.deepcopy(j.values('trade_intents'))
        assert prior.status.value=='COMPLETED' and prior.w3_operations_status=='PENDING'
        assert not s.repository.list_provisional(case.ticker,case.trading_date)
        assert s.process_pending_effects()==1
        saved=s.repository.get_case(case.case_id)
        assert saved.w3_trade_decision==trade and saved.w1_final==case.w1_final and saved.w2_final==case.w2_final
        assert j.values('trade_intents')==intents
        rows=s.repository.list_provisional(case.ticker,case.trading_date)
        assert len(rows)==1 and rows[0].originating_node=='W3' and rows[0].case_id==case.case_id
        s._v4_execution().operations(saved)
        assert len(s.repository.list_provisional(case.ticker,case.trading_date))==1
        assert sum(call[1]==OPERATIONS_PHASE for call in agent.calls)==1
    finally:s.close()


def test_fact_insert_and_operations_commit_roll_back_together(tmp_path,monkeypatch):
    s,j,_,case,_=v6(tmp_path,candidates=[fact()],action='LONG')
    try:
        s.process_pending_effects();x=s._v4_execution();commit=x.registry.commit_stage
        def fail(*a,**kw):raise RuntimeError('injected commit failure')
        monkeypatch.setattr(x.registry,'commit_stage',fail)
        with pytest.raises(RuntimeError,match='injected'):x.operations(s.repository.get_case(case.case_id))
        assert not s.repository.list_provisional(case.ticker,case.trading_date)
        assert j.get('w3_operations_applied',case.case_id) is None
        assert s.repository.get_case(case.case_id).w3_trade_decision['decision']=='LONG'
        monkeypatch.setattr(x.registry,'commit_stage',commit)
        x.operations(s.repository.get_case(case.case_id))
        assert len(s.repository.list_provisional(case.ticker,case.trading_date))==1
    finally:s.close()


def test_empty_operations_needs_no_event_lookup_and_leaves_trade_complete(tmp_path,monkeypatch):
    s,_,_,case,_=v6(tmp_path)
    monkeypatch.setattr(s,'_known_for_case',lambda *a:pytest.fail('empty facts do not need Event maximum'))
    try:
        s.process_pending_effects();s.process_pending_effects()
        saved=s.repository.get_case(case.case_id)
        assert saved.status.value=='COMPLETED' and saved.w3_operations_status=='COMMITTED'
        assert saved.w3_operations_result['delta_candidates']==[]
        assert not s.repository.list_provisional(case.ticker,case.trading_date)
    finally:s.close()


def test_old_successful_w1_compilation_is_not_written_again_by_operations(tmp_path):
    s,j,_,case,_=v6(tmp_path,candidates=[fact()])
    case.w1_extraction=W1FactExtractionResult(candidates=[fact()]);s.repository.save_case(case)
    s.repository.allocate_provisional(ticker=case.ticker,trading_date=case.trading_date,
        source_message_id=case.source.source_message_id,candidate_index=0,candidate=fact(),
        published_max_event_numeric_id=1,case_id=case.case_id,originating_node='W1_R3')
    try:
        s.process_pending_effects();s.process_pending_effects()
        rows=s.repository.list_provisional(case.ticker,case.trading_date)
        assert len(rows)==1 and rows[0].originating_node=='W1_R3'
        assert s.repository.get_case(case.case_id).w3_operations_status=='COMMITTED'
    finally:s.close()


def test_old_two_field_operations_keeps_its_pending_r3_owner(tmp_path,monkeypatch):
    s,_,_,case,_=v6(tmp_path,candidates=[fact()])
    case.frozen_inputs['w3_contracts']={'w3_operations_schema':{'properties':{'pin_actions':{},'watch_actions':{}}}}
    case.route.side_effects.append(RuntimeSideEffect.EMIT_DELTA)
    s.repository.save_case(case)
    s._enqueue_route_effects(case,'old-parent')
    effects=s.repository.list_effects(case.case_id)
    assert next(e for e in effects if e.effect_type is RuntimeSideEffect.EMIT_DELTA).payload['auxiliary_to_w3']
    monkeypatch.setattr(s,'_call_with_retry',lambda **kw:(W1FactExtractionResult(candidates=[fact()]),'old-r3'))
    try:
        s._v4_execution().trade(case)
        s._v4_execution().operations(s.repository.get_case(case.case_id))
        assert not s.repository.list_provisional(case.ticker,case.trading_date)
        s._execute_r3(s.repository.get_case(case.case_id),SimpleNamespace(payload={}))
        rows=s.repository.list_provisional(case.ticker,case.trading_date)
        assert len(rows)==1 and rows[0].originating_node=='W1_R3'
    finally:s.close()


def test_facts_keep_existing_pin_and_watch_actions(tmp_path):
    s,j,agent,case,_=v6(tmp_path,hit=True);run=agent.run_phase
    def mixed(**kw):
        result,job=run(**kw)
        if kw['phase']==OPERATIONS_PHASE:
            result=W3OperationsResultV6(delta_candidates=[fact()],
                pin_actions=[dict(action='SET',pin_id=None,reason='retain baseline')],
                watch_actions=[dict(action='SET',policy_id='P1',reason='follow final disclosure')])
        return result,job
    agent.run_phase=mixed
    try:
        s.process_pending_effects();s.process_pending_effects()
        saved=s.repository.get_case(case.case_id)
        assert saved.w3_operations_status=='COMMITTED'
        assert len(s.repository.list_provisional(case.ticker,case.trading_date))==1
        actions=j.get('w3_operations_applied',case.case_id)
        assert actions['watch'] and actions['pins']
    finally:s.close()


def test_non_w3_empty_r3_is_recorded_without_lookup_or_repair_loop(tmp_path,monkeypatch):
    s,j,_,case,_=v6(tmp_path)
    case.route=RuntimeRouteDecision(primary_route='ARCHIVE',side_effects=[RuntimeSideEffect.EMIT_DELTA],reason='NEW related false')
    s.repository.save_case(case);calls=[]
    def empty(**kw):calls.append(kw);return W1FactExtractionResult(candidates=[]),'r3-response'
    monkeypatch.setattr(s,'_call_with_retry',empty)
    monkeypatch.setattr(s,'_known_for_case',lambda *a:pytest.fail('empty facts need no Event lookup'))
    try:
        s._execute_r3(case,SimpleNamespace(payload={}))
        assert len(calls)==1 and s.repository.get_case(case.case_id).w1_extraction.candidates==[]
        assert not s.repository.list_provisional(case.ticker,case.trading_date)
    finally:s.close()


def test_closed_w1_phase_defers_compilation_until_w2_and_watch_choose_route(tmp_path,monkeypatch):
    from doxagent.persistent_runtime_v2.schema import W1Round1Result
    s,_,_,case,_=v6(tmp_path)
    case.runtime_mode='CLOSED';case.route=None
    case.frozen_inputs['index']=s.known_events.current_index(case.ticker).model_dump(mode='json')
    s.repository.save_case(case)
    monkeypatch.setattr(s,'_run_w1_hot',lambda *a:(W1Round1Result(event_ids=[]),case.w1_final,'parent'))
    monkeypatch.setattr(s,'_execute_r3',lambda *a:pytest.fail('cannot compile before final route'))
    monkeypatch.setattr(s,'_run_w2_hot',lambda *a:pytest.fail('W1 phase must remain independent'))
    try:
        s._resume_hot(case,phase='W1')
        assert s.repository.get_case(case.case_id).w1_extraction is None
    finally:s.close()


def test_legacy_r3_schema_hash_accepts_empty_output_without_repair(tmp_path,monkeypatch):
    from doxagent.persistent_runtime_v2.transport import RuntimeResponsesResult
    s,j,_,case,_=v6(tmp_path);calls=[]
    case.route=RuntimeRouteDecision(primary_route='ARCHIVE',side_effects=[RuntimeSideEffect.EMIT_DELTA],reason='new')
    schema=copy.deepcopy(W1FactExtractionResult.model_json_schema());schema['properties']['candidates']['minItems']=1
    j.set('round_inputs',case.case_id+':W1:R3',dict(payload={},instructions='frozen R3',
        previous_response_id=None,inherit_context=False,cache_context_keys=[],schema_hash=digest(schema)))
    def complete(req):
        calls.append(req)
        return RuntimeResponsesResult(value=W1FactExtractionResult(candidates=[]),response_id='empty',
            latency_ms=1,input_tokens=1,output_tokens=1,reasoning_tokens=0,cached_input_tokens=0)
    monkeypatch.setattr(s.responses,'complete',complete)
    try:
        s._execute_r3(case,SimpleNamespace(payload={}))
        assert len(calls)==1 and s.repository.get_case(case.case_id).w1_extraction.candidates==[]
    finally:s.close()


def test_operations_fact_failure_cannot_roll_back_committed_trade(tmp_path,monkeypatch):
    s,j,_,case,_=v6(tmp_path,candidates=[fact()],action='LONG')
    monkeypatch.setattr(s,'_known_for_case',lambda c:SimpleNamespace(max_event_numeric_id=lambda *a:None))
    try:
        s.process_pending_effects();before=copy.deepcopy(j.values('trade_intents'))
        s.process_pending_effects();saved=s.repository.get_case(case.case_id)
        assert saved.status.value=='COMPLETED' and saved.w3_trade_decision['decision']=='LONG'
        assert saved.w3_operations_status!='COMMITTED' and j.values('trade_intents')==before
        assert not s.repository.list_provisional(case.ticker,case.trading_date)
    finally:s.close()
