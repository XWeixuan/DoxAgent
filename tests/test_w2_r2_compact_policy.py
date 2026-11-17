import copy

from doxagent.persistent_runtime_v2.service import _w2_detail_business_payload
from doxagent.workflows.codex_document3.consumer_contracts import PolicyDetailSnapshotV3
from tests.test_runtime_policy_direction_usage import policy


def test_policy_projection_omits_only_unused_fields_and_preserves_related_scope():
    p = policy()
    snapshot = PolicyDetailSnapshotV3(ticker='MU', policy_set_version=1,
        requested_policy_ids=['P1'], policies=[p], missing_policy_ids=[])
    original = copy.deepcopy(snapshot.model_dump(mode='json'))
    data = _w2_detail_business_payload(snapshot)
    projected = data['policies'][0]
    expected = copy.deepcopy(p.model_dump(mode='json'))
    for key in ['ref', 'transmission']:
        expected.pop(key, None)
    for condition in expected['activation_conditions']:
        condition.pop('trigger_layer', None)
    assert projected == expected
    assert projected['match_scope'] == p.match_scope
    assert projected['policy_id'] == p.policy_id and projected['title'] == p.title
    for source, condition in zip(p.activation_conditions, projected['activation_conditions']):
        assert condition['condition_id'] == source.condition_id
        assert condition['criterion'] == source.criterion
        assert condition['calibration'] == source.calibration.model_dump(mode='json')
        assert condition['decision'] == source.decision
    assert snapshot.model_dump(mode='json') == original


def test_historical_frozen_r2_input_is_consumed_verbatim(tmp_path):
    from doxagent.persistent_runtime_v2.journal import RuntimeJournal,digest
    from doxagent.persistent_runtime_v2.repository import SQLitePersistentRuntimeV2Repository
    from doxagent.persistent_runtime_v2.schema import W1NoveltyResult
    from doxagent.persistent_runtime_v2.service import PersistentRuntimeV2Service
    from doxagent.persistent_runtime_v2.transport import RuntimeResponsesResult
    from tests.test_persistent_runtime_v2 import _FakeKnownEvents,_FakePolicies
    from tests.test_runtime_policy_direction_usage import fixture_case
    from tests.test_openai_luna_r3 import client
    primary=client();calls=[]
    class Responses:
        model=primary.model
        model_settings=primary.model_settings
        def complete(self,r):
            calls.append(r)
            return RuntimeResponsesResult(value=W1NoveltyResult(result='NEW',confidence='normal',reason='ok'),
                response_id='frozen',latency_ms=1,input_tokens=10,output_tokens=2,
                reasoning_tokens=1,cached_input_tokens=0)
    j=RuntimeJournal(tmp_path/'runtime.db')
    s=PersistentRuntimeV2Service(repository=SQLitePersistentRuntimeV2Repository(j.path),journal=j,
        responses=Responses(),known_events=_FakeKnownEvents(),policies=_FakePolicies(),dispatch_effects=False)
    case,_=fixture_case();case.frozen_inputs['models']=primary.model_settings();s.repository.save_case(case)
    old={'policy_details':{'policies':[policy().model_dump(mode='json')]}}
    j.set('round_inputs',case.case_id+':W2:R2',dict(payload=old,instructions='old frozen instructions',
        previous_response_id=None,inherit_context=False,cache_context_keys=['policy_details'],
        schema_hash=digest(W1NoveltyResult.model_json_schema())))
    try:
        s._call_with_retry(case=case,lane='W2',round_name='R2',round_prompt='',
            payload={'policy_details':{'policies':[]}},output_model=W1NoveltyResult,schema_name='fixture')
        assert calls[0].payload == old and calls[0].instructions == 'old frozen instructions'
    finally:s.close();primary.close()
