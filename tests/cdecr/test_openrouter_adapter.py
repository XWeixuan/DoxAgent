from cdecr.config import CDECRSettings
from cdecr.models import ModelTier
from cdecr.openrouter import ENDPOINT, OpenRouterStructuredModelClient
from cdecr.ports import ResponsesModelRequest, StructuredModelRequest


def test_provider_pin_and_node_reasoning():
    s = CDECRSettings(
        _env_file=None,
        OPENROUTER_API_KEY="test",
        CDECR_PACKAGE_V3_MODEL="deepseek/deepseek-v4.1-flash-1017",
    )
    c = OpenRouterStructuredModelClient(settings=s, tier=ModelTier.M2)
    r = StructuredModelRequest(system_prompt="s", user_prompt="u", json_schema={"type": "object"})
    k = c._kwargs(r)
    assert k["extra_body"]["provider"] == {
        "only": [ENDPOINT],
        "allow_fallbacks": False,
        "require_parameters": True,
    }
    assert k["extra_body"]["reasoning"] == {"effort": "none"}
    p = OpenRouterStructuredModelClient(settings=s, tier=ModelTier.M3, package=True)
    for effort in ("low", "none"):
        r = ResponsesModelRequest(
            input=[{"role": "user", "content": "x"}],
            json_schema={"type": "object"},
            reasoning_effort=effort,
        )
        assert p._kwargs(r)["extra_body"]["reasoning"]["effort"] == effort
