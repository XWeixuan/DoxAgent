import json,hashlib,inspect
from doxagent.settings import DoxAgentSettings
from doxagent.persistent_runtime_v2.future_model_profiles import configured_rounds
from doxagent.persistent_runtime_v2 import openai_platform,service,future_model_profiles
from doxagent.persistent_runtime_v2.transport import RuntimeResponsesRequest
from doxagent.persistent_runtime_v2.schema import W1Round1Result
from doxagent.workflows.codex_document3.schema_v21 import PolicyV3
from doxagent.workflows.codex_document3.consumer_contracts import PolicyDetailSnapshotV3
settings=configured_rounds(DoxAgentSettings(_env_file=None,persistent_runtime_v2_api_profile='openai_luna_all_high'))
assert settings['W1:R3']['reasoning_effort']=='medium' and settings['W1:R3']['service_tier']=='default'
assert settings['W2:R3']['reasoning_effort']=='high' and settings['W2:R3']['service_tier']=='fast'
r=RuntimeResponsesRequest(instructions='fixed',payload={'published_known_event_index':'E1 | 2026-11-17 | fixed\n','today_provisional_facts':''.join(f'E{i+2} | UNKNOWN | fact {i}\n' for i in range(1373)),'source_message':{'title':'offline'}},output_model=W1Round1Result,schema_name='w1_round1_result',model='gpt-6-luna',provider='openai',provider_endpoint=openai_platform.ENDPOINT,cache_ttl='12h',provisional_cache_batch_size=5,cache_context_keys=('published_known_event_index',),metadata={'lane':'W1','round':'R1'})
body,_=openai_platform.build_request(r)
assert sum('prompt_cache_breakpoint' in b for m in body['input'] for b in m['content'])==276
p=PolicyV3(policy_id='P1',title='scope',transmission='economic',match_scope='preserve',ref=[],activation_conditions=[dict(condition_id='C1',decision='LONG',trigger_layer='MIDDLE',criterion='reality',calibration=dict(reference_state='before',trigger_boundary='after'))])
v=service._w2_detail_business_payload(PolicyDetailSnapshotV3(ticker='MU',policy_set_version=1,requested_policy_ids=['P1'],policies=[p],missing_policy_ids=[]))['policies'][0]
assert v['match_scope']=='preserve' and 'transmission' not in v and 'trigger_layer' not in v['activation_conditions'][0]
assert p.transmission=='economic'
print(json.dumps({'status':'OK','model_rounds':settings,'source_hashes':{m.__name__:hashlib.sha256(inspect.getsource(m).encode()).hexdigest() for m in [openai_platform,service,future_model_profiles]}}))
