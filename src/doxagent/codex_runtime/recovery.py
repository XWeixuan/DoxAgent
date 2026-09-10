"""Mechanical ingestion helpers. Canonical business models remain strict."""

import json
from typing import Any

from pydantic import BaseModel, ValidationError


def bounded_text(value: object, limit: int = 3000) -> str:
    text = str(value)
    encoded = text.encode("utf-8")
    return text if len(encoded) <= limit else encoded[: limit - 3].decode("utf-8", "ignore") + "..."


def json_value(text: str) -> Any:
    value = text.lstrip("\ufeff").strip()
    if value.startswith("```") and value.endswith("```"):
        if "\n" not in value:
            raise ValueError("fenced JSON has no body")
        value = value.split("\n", 1)[1].rsplit("```", 1)[0]
    return json.loads(value)


def failure_details(error: Exception) -> dict[str, Any]:
    code = str(getattr(error, "code", type(error).__name__))
    transient = bool(getattr(error, "retryable", False)) or isinstance(
        error, (TimeoutError, ConnectionError)
    )
    return {
        "code": code,
        "manual_resume_required": code == "WORKER_INFRA_RECOVERY_EXHAUSTED",
        "scope": getattr(error, "scope", "node"),
        "retryable": transient,
        "summary": bounded_text(error),
    }


def ingest_model(model: type[BaseModel], payload: Any) -> BaseModel:
    """Discard only forbidden extension fields; never invent missing business data."""
    import copy

    value = copy.deepcopy(payload)
    for _ in range(3):
        try:
            return model.model_validate(value)
        except ValidationError as exc:
            extras = [e for e in exc.errors() if e["type"] == "extra_forbidden"]
            if not extras:
                raise
            for error in extras:
                target = value
                try:
                    for key in error["loc"][:-1]:
                        target = target[key]
                    target.pop(error["loc"][-1], None)
                except (KeyError, IndexError, TypeError, AttributeError):
                    raise exc from None
    return model.model_validate(value)
