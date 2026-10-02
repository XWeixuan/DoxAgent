"""Execution aliases for the approved GPT-6 Codex migration."""

CODEX_MODEL_UPGRADES = {
    "gpt-5.6-luna": "gpt-6-luna",
    "gpt-5.6-sol": "gpt-6.1-sol",
    "gpt-6-sol": "gpt-6.1-sol",
}


def codex_execution_model(model: str | None, provider: str | None = None) -> str | None:
    """Resolve future OpenAI calls without rewriting frozen historical inputs."""
    if provider not in (None, "", "openai"):
        return model
    return CODEX_MODEL_UPGRADES.get(model, model)
