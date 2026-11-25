import copy
from types import SimpleNamespace
from doxagent.persistent_runtime_v2.w3_v6 import trade_input, operations_input, without_w2
from doxagent.settings import DoxAgentSettings


def test_clean_projection_does_not_mutate_business_or_validate_hidden_policies():
    case = SimpleNamespace(frozen_inputs={}, w2_final=None)
    payload = dict(ticker="MU", message={"title":"original Policy news"}, w1={"reason":"W1"},
        w2={"reason":"UPSTREAM_SECRET", "policy_hit":True},
        policies=[{"title":"SECRET_POLICY"}], alignment={"error":"SECRET"},
        additional_tags=["W2_LOW_CONFIDENCE","RECENT_TRADE","BODY_MISSING"],
        contexts=[{"name":"D1"},{"name":"D2"},{"name":"D3"}])
    original = copy.deepcopy(payload)
    result = trade_input(case,payload,[],hide_w2=True)
    assert payload == original
    assert not {"w2","policies","alignment"} & result.keys()
    assert result["additional_tags"] == ["BODY_MISSING"]
    assert result["w1"] == payload["w1"] and result["message"] == payload["message"]
    assert result["contexts"] == [{"name":"D1"},{"name":"D2"}]
    ops = operations_input(case,dict(submitted_decision={},output_disposition="NO_TRADE",
        watch={"items":[{"watch_id":"W1","policy_id":"P1"}]},pins={}),hide_w2=True)
    assert ops["watch"]["items"] == [{"watch_id":"W1"}]
    assert "policy_targets" not in ops


def test_nested_history_and_switch():
    data = {"recent_trade":[{"w2_result":{"reason":"secret"},"w3_decision":{"policy":{}},"direction":"LONG"}]}
    assert without_w2(data) == {"recent_trade":[{"w3_decision":{},"direction":"LONG"}]}
    assert DoxAgentSettings(DOXAGENT_W3_HIDE_W2_CONTEXT=True).w3_hide_w2_context is True
    assert DoxAgentSettings(DOXAGENT_W3_HIDE_W2_CONTEXT=False).w3_hide_w2_context is False
