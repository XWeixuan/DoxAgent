import json
import random
import string
from doxagent.v2_read.native_content import NativeContent,MARKER


def test_phase_mutations_share_unchanged_evidence_and_projection_is_selective(tmp_path,monkeypatch):
    randomizer=random.Random(17)
    evidence=''.join(randomizer.choices(string.ascii_letters+string.digits,k=240_000))
    value={'case_id':'exact','frozen_inputs':{'index':{'large':evidence},'provisional':[{'facts':evidence}],
        'projection':{'policies':[{'policy_id':'P1','activation_conditions':[{'condition_id':'C1','decision':'LONG','criterion':evidence}]}]},
        'w3_execution_state':{'phase':'first'}}}
    codec=NativeContent(tmp_path/'runtime.db')
    first=json.loads(codec.encode(value,split=('frozen_inputs',)))
    value['frozen_inputs']['w3_execution_state']['phase']='second'
    second=json.loads(codec.encode(value,split=('frozen_inputs',)))
    assert first['frozen_inputs']['projection']==second['frozen_inputs']['projection']
    assert codec.decode(second)==value
    forbidden={second['frozen_inputs'][name][MARKER] for name in ('index','provisional')}
    original=codec.files.read_all
    def read(identity):
        assert identity not in forbidden,'serving decoded unused evidence'
        return original(identity)
    monkeypatch.setattr(codec.files,'read_all',read)
    thin=codec.projection_case(json.dumps(second))
    assert thin['frozen_inputs']=={'w3_execution_state':{'phase':'second'},'projection':{'policies':[{'policy_id':'P1','activation_conditions':[{'condition_id':'C1','decision':'LONG'}]}]}}
