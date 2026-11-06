"""Explicit opt-in manifests. No factory imports these defaults automatically."""


def vertex_recall_rounds(*, w1_timeout=120, w2_timeout=180, provisional_batch_size=5):
    return {
        f"{lane}:R1": {
            "model": "claude-haiku-5-5",
            "provider": "vertex",
            "transport": "vertex_anthropic",
            "provider_endpoint": "global",
            "cache_ttl": "1h",
            "reasoning_effort": "high",
            "timeout_seconds": w1_timeout if lane == "W1" else w2_timeout,
            **({"provisional_cache_batch_size": provisional_batch_size} if lane == "W1" else {}),
        }
        for lane in ("W1", "W2")
    }


def bailian_glm_rounds(workspace_endpoint):
    if not workspace_endpoint.startswith("https://") or not (
        workspace_endpoint == "https://dashscope.aliyuncs.com/compatible-mode/v1"
        or workspace_endpoint.endswith(".cn-beijing.maas.aliyuncs.com/compatible-mode/v1")
    ):
        raise ValueError("GLM requires a Beijing Model Studio compatible endpoint")
    return {
        f"{lane}:{rnd}": {
            "model": "ZHIPU/GLM-5.3-FlashX",
            "provider": "bailian",
            "transport": "chat_completions_json_object",
            "provider_endpoint": workspace_endpoint,
            "reasoning_effort": "high",
            "timeout_seconds": 120,
        }
        for lane in ("W1", "W2")
        for rnd in ("R2", "R3")
    }


def configured_rounds(settings):
    profile = settings.persistent_runtime_v2_api_profile
    result = {}
    if profile in {"openrouter_haiku", "openrouter_haiku_high", "openrouter_sail_high", "openai_luna_r2_high"}:
        from .case_output_contract import VERSION
        from .case_cache import VERSION as CACHE_VERSION

        result.update({
            f"{lane}:{rnd}": dict(
                model="anthropic/claude-haiku-5.5",
                provider="openrouter",
                transport="chat_completions_json_schema",
                provider_endpoint="google-vertex/global",
                reasoning_effort="xhigh" if profile == "openrouter_haiku" else "high",
                timeout_seconds=300,
                cache_ttl="1h",
                output_contract=VERSION,
                case_cache_layout=CACHE_VERSION,
            )
            for lane in ("W1", "W2") for rnd in ("R2", "R3")
        })
        if profile in {"openrouter_sail_high", "openai_luna_r2_high"}:
            for config in result.values():
                config.update(
                    model="deepseek/deepseek-v4.1-flash-1017",
                    provider_endpoint="sail-research/fp4",
                    cache_ttl=None,  # Sail uses automatic prefix caching, not Anthropic TTLs.
                )
        if profile == "openai_luna_r2_high":
            for lane in ("W1", "W2"):
                result[f"{lane}:R2"].update(model="gpt-6-luna", provider="openai",
                    provider_endpoint="https://api.openai.com/v1/responses",
                    transport="responses_json_schema", reasoning_effort="high",
                    service_tier="fast", cache_ttl="30m")
    if profile in {"vertex_recall", "vertex_glm"}:
        if not settings.vertex_project:
            raise ValueError("Vertex profile requires DOXAGENT_VERTEX_PROJECT")
        result.update(
            vertex_recall_rounds(
                w1_timeout=getattr(settings, "persistent_runtime_v2_timeout_seconds", 120),
                w2_timeout=getattr(settings, "persistent_runtime_v2_w2_r1_timeout_seconds", 180),
                provisional_batch_size=getattr(
                    settings, "persistent_runtime_v2_w1_provisional_cache_batch_size", 5
                ),
            )
        )
    if profile in {"bailian_glm", "vertex_glm"}:
        result.update(
            bailian_glm_rounds(settings.bailian_glm_endpoint or settings.dashscope_chat_base_url)
        )
    if profile == "bailian_deepseek":
        result.update(
            {
                f"{lane}:{rnd}": dict(
                    model="deepseek-v4.1-flash-1017",
                    provider="bailian",
                    transport="chat_completions_json_object",
                    provider_endpoint=settings.dashscope_chat_base_url,
                    reasoning_effort="medium",
                    timeout_seconds=120,
                )
                for lane in ("W1", "W2")
                for rnd in ("R2", "R3")
            }
        )
    return result
