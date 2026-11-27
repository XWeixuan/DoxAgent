import pytest
from doxagent.persistent_runtime_v2.w3_models import freeze_case_profile, execution_profile, preparation_profile
from tests.test_runtime_policy_direction_usage import fixture_case
from doxagent.settings import DoxAgentSettings


@pytest.mark.parametrize("source,fast", [("sec_edgar",True),("corporate_ir_press_releases",True),("reuters_site_search",False),("other_press_releases",False),("SEC EDGAR",False)])
def test_source_identity_and_freezing(source,fast):
    case,_=fixture_case()
    case.source.source_id=source
    ids=DoxAgentSettings().w3_fast_source_ids.split(",")
    p=freeze_case_profile(case,fast_source_ids=ids)
    assert p["model"] == ("gpt-6.2-sol" if fast else "gpt-6-luna")
    assert p["effort"] == ("high" if fast else "max")
    assert preparation_profile(case) == dict(model="gpt-6-luna",effort="max",provider=None)
    assert execution_profile(case,phase="post_decision_operations") == preparation_profile(case)
    case.source.source_id="other"
    assert freeze_case_profile(case,fast_source_ids=[]) == p


def test_config_extension_disable_and_historical_profile():
    c,_=fixture_case(); c.source.source_id="custom"
    assert freeze_case_profile(c,fast_source_ids=["custom"])["model"] == "gpt-6.2-sol"
    c,_=fixture_case(); c.source.source_id="sec_edgar"
    assert freeze_case_profile(c,fast_source_ids=[])["model"] == "gpt-6-luna"
    assert freeze_case_profile(c,fast_source_ids=["sec_edgar"])["model"] == "gpt-6-luna"
