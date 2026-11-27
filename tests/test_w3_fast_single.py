from types import SimpleNamespace
import pytest
from doxagent.persistent_runtime_v2.w3_consensus import freeze_mode
from doxagent.persistent_runtime_v2.w3_models import freeze_case_profile, preparation_profile
from tests.test_w3_v5_execution import setup_case


@pytest.mark.parametrize("source",["sec_edgar","corporate_ir_press_releases","other"])
@pytest.mark.parametrize("global_mode",["single","dual_consensus"])
def test_first_w3_registration_freezes_model_then_mode(tmp_path,source,global_mode):
    s,j,a,original,_=setup_case(tmp_path)
    try:
        a._fast_source_ids=frozenset(["sec_edgar","corporate_ir_press_releases"])
        j.set("w3_trade_config","mode",global_mode)
        case=original.model_copy(deep=True)
        case.case_id="new-case"
        case.source.source_id=source
        case.source.source_message_id="new-message"
        for k in ("w3_trade_mode","w3_execution_profile","w3_model_route"):
            case.frozen_inputs.pop(k,None)
        s._enqueue_route_effects(case,"")
        saved=s.repository.get_case(case.case_id)
        fast=source!="other"
        assert saved.frozen_inputs["w3_trade_mode"] == ("single" if fast else global_mode)
        assert saved.frozen_inputs["w3_execution_profile"]["model"] == ("gpt-6.2-sol" if fast else "gpt-6-luna")
        assert preparation_profile(saved)["model"] == "gpt-6-luna"
        assert len([e for e in s.repository.list_effects(saved.case_id) if str(e.effect_type)=="route_to_w3"])==1
        j.set("w3_trade_config","mode","single" if global_mode=="dual_consensus" else "dual_consensus")
        a._fast_source_ids=frozenset()
        s._enqueue_route_effects(saved,"")
        assert s.repository.get_case(saved.case_id).frozen_inputs == saved.frozen_inputs
    finally:s.close()


def test_old_fast_dual_is_not_downgraded():
    case=SimpleNamespace(frozen_inputs={"w3_trade_mode":"dual_consensus","w3_model_route":{"route":"source_fast"}})
    assert freeze_mode(case,None)=="dual_consensus"


@pytest.mark.parametrize("saved_model",["gpt-6-luna","gpt-6.2-sol"])
def test_historical_model_without_source_marker_is_not_reclassified(tmp_path,saved_model):
    s,j,a,c,_=setup_case(tmp_path)
    try:
        c.case_id="new-history"
        c.source.source_id="sec_edgar"
        c.frozen_inputs.pop("w3_trade_mode",None)
        c.frozen_inputs["w3_execution_profile"]={"model":saved_model,"effort":"max","provider":None}
        freeze_case_profile(c,fast_source_ids=["sec_edgar"])
        j.set("w3_trade_config","mode","dual_consensus")
        assert "w3_model_route" not in c.frozen_inputs
        assert freeze_mode(c,j)=="dual_consensus"
    finally:s.close()


def test_fast_and_ordinary_same_ticker_use_independent_existing_paths(tmp_path):
    from doxagent.persistent_runtime_v2.w3_contracts import TRADE_PHASE, OPERATIONS_PHASE
    from doxagent.persistent_runtime_v2.w3_threads import W3ThreadRegistry
    s,j,a,original,_=setup_case(tmp_path)
    try:
        a._fast_source_ids=frozenset(["sec_edgar"])
        j.set("w3_trade_config","mode","dual_consensus")
        calls=[]
        original_run=a.run_phase
        def run(**kw):
            calls.append((kw["case"].case_id,kw["phase"],kw["record"]["thread_id"]))
            return original_run(**kw)
        a.run_phase=run
        def pair(**kw):
            for label,record in kw["records"].items():
                result,job=run(case=kw["case"],record=record,phase=TRADE_PHASE,payload=kw["payload"],context=kw["context"],research=kw["research"])
                kw["save"](label,result,job)
        a.run_trade_pair=pair
        for name,source in [("fast","sec_edgar"),("normal","other")]:
            c=original.model_copy(deep=True)
            c.case_id=name
            c.source.source_message_id=name
            c.source.source_id=source
            for k in ("w3_trade_mode","w3_execution_profile","w3_model_route"):
                c.frozen_inputs.pop(k,None)
            s._enqueue_route_effects(c,"")
        assert s.process_pending_effects(limit=20)==3
        trades=[v for v in calls if v[1]==TRADE_PHASE]
        assert len([v for v in trades if v[0]=="fast"])==1
        assert len([v for v in trades if v[0]=="normal"])==2
        r=W3ThreadRegistry(j)
        assert r.stage("fast",TRADE_PHASE)["status"]=="COMMITTED"
        assert r.stage("fast",TRADE_PHASE+":A") is None
        assert r.stage("fast",TRADE_PHASE+":B") is None
        assert r.stage("normal",TRADE_PHASE+":A")["result_json"] is not None
        assert r.stage("normal",TRADE_PHASE+":B")["result_json"] is not None
        assert s.process_pending_effects(limit=20)==3
        for name in ("fast","normal"):
            operations=[v for v in calls if v[0]==name and v[1]==OPERATIONS_PHASE]
            assert len(operations)==1
            assert operations[0][2]==r.stage(name,TRADE_PHASE)["thread_id"]
        assert not j.values("trade_intents")
    finally:s.close()
