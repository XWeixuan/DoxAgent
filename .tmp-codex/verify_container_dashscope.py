from __future__ import annotations

from doxagent.settings import DoxAgentSettings
import doxagent.agents.runner as runner_mod


def fp(value: str | None) -> str:
    if value and len(value) > 8:
        return value[:4] + "..." + value[-4:]
    return "<set>" if value else "<empty>"

settings = DoxAgentSettings()
fallbacks = settings.dashscope_fallback_api_keys()
print("container_DASHSCOPE_API_KEY=" + fp(settings.dashscope_api_key))
print("container_fallbacks=" + ",".join(fp(item) for item in fallbacks))
print("container_fallback_count=" + str(len(fallbacks)))
assert settings.dashscope_api_key
assert len(fallbacks) == 2

sdk_clients: list[dict[str, str]] = []

class FakeAsyncOpenAI:
    def __init__(self, *, api_key: str, base_url: str) -> None:
        sdk_clients.append({"api_key": api_key, "base_url": base_url})

runner_mod.AsyncOpenAI = FakeAsyncOpenAI
runner_mod.wrap_provider_client = lambda _provider, client, **_kwargs: client
agent_runner = runner_mod.default_real_agent_runner(settings=settings)
print("runner_sdk_client_count=" + str(len(sdk_clients)))
print("runner_fallback_client_count=" + str(len(agent_runner.model_gateway.fallbacks)))
assert len(sdk_clients) == 3
assert len(agent_runner.model_gateway.fallbacks) == 2
