"""Exercise growing-prefix reuse, rather than identical-request cache hits."""
import copy
from dataclasses import replace

import pytest

from doxagent.persistent_runtime_v2.openai_platform import build_request
from doxagent.persistent_runtime_v2.openrouter import build_w1_r1_cached_request
from doxagent.persistent_runtime_v2.schema import W1FactExtractionResult
from tests.test_openai_luna_recall import request
from tests.test_openai_luna_rounds import req


def boundaries(body):
    prefix = []
    endpoints = []
    for message in body['input']:
        prefix.append(('role', message['role']))
        for block in message['content']:
            prefix.append((block['type'], block['text']))
            if 'prompt_cache_breakpoint' in block:
                endpoints.append(tuple(prefix))
    return endpoints


def w1(count):
    return replace(request('W1', count), provisional_cache_batch_size=5)


@pytest.mark.parametrize('before,after', [(4,5), (9,10), (99,105), (1339,1345),
                                         (1363,1372), (300,400), (500,501)])
def test_last_written_completed_prefix_stays_eligible_after_promotion(before, after):
    old, _ = build_request(w1(before)); new, _ = build_request(w1(after))
    written = boundaries(old)
    incoming = boundaries(new)
    eligible = incoming[:2] + incoming[-50:]
    assert written[-1] in eligible
    assert old['prompt_cache_key'] == new['prompt_cache_key']
    assert all('prompt_cache_breakpoint' not in b for b in [new['input'][-1]['content'][-1]])


def test_known_prefix_is_eligible_even_after_more_than_fifty_chunks_arrive_at_once():
    old, _ = build_request(w1(0)); new, _ = build_request(w1(1000))
    incoming = boundaries(new)
    assert boundaries(old)[-1] in incoming[:2] + incoming[-50:]


def test_native_layout_keeps_every_fact_once_and_does_not_change_haiku_layout():
    r = w1(1373)
    original = copy.deepcopy(r.payload)
    haiku, fingerprint = build_w1_r1_cached_request(r)
    body, native_fingerprint = build_request(r)
    expected = ''.join(b['text'] for b in haiku['messages'][1]['content'])
    actual = ''.join(b['text'] for b in body['input'][1]['content'])
    assert expected == actual
    assert r.payload == original and fingerprint == native_fingerprint
    assert len(boundaries(body)) == 2 + 1373//5
    assert sum('cache_control' in b for m in haiku['messages'] for b in m['content']) == 4
    after, _ = build_w1_r1_cached_request(r)
    assert after == haiku


@pytest.mark.parametrize('lane,rnd', [('W1','R2'), ('W2','R2'), ('W1','R3'), ('W2','R3')])
def test_other_rounds_only_write_shared_instructions_not_case_specific_history(lane, rnd):
    r = replace(req(), metadata={**req().metadata, 'lane':lane, 'round':rnd},
                replay_parent=('private R2 input', '{"result":"NEW"}') if rnd=='R3' else None)
    a, fingerprint = build_request(r)
    b, changed = build_request(replace(r, payload={'private':'different case'},
        instructions=r.instructions+'\n\n# Previous Attempt Correction\nrepair'))
    assert boundaries(a) == boundaries(b) and fingerprint == changed
    assert len(boundaries(a)) == 1
    assert all('prompt_cache_breakpoint' not in x for m in a['input'][1:] for x in m['content'])


def test_fact_compilation_default_tier_is_explicit_not_native_fast_default():
    r = replace(req(), output_model=W1FactExtractionResult, schema_name='w1_fact_extraction_result',
                reasoning_effort='medium', metadata={**req().metadata, 'round':'R3', 'service_tier':'default'})
    body, _ = build_request(r)
    assert body['reasoning'] == {'effort':'medium'} and body['service_tier'] == 'default'
