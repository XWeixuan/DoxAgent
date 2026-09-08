import hashlib
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from doxagent.api_v2.dto import SCHEMA, model, validate


def test_generated_schema_is_current_and_closed():
    source = Path("dev_plan/workflow_v2/api_contract/doxagent-v2-api.types.ts").read_bytes()
    assert hashlib.sha256(source).hexdigest() == SCHEMA["source_sha256"]
    Draft202012Validator.check_schema(SCHEMA)
    value = {"ticker": "MU", "monitor_mode": "MESSAGE_MONITORING", "initialization": "REUSE_ACTIVE"}
    assert model("StartTickerRequest").model_validate(value).model_dump() == value
    with pytest.raises(ValueError):
        validate("StartTickerRequest", {**value, "account": "secret"})
    with pytest.raises(ValueError):
        validate("Count", True)
    with pytest.raises(ValueError):
        validate("Instant", "2026-09-07T08:00:00+00:00")


def test_generic_and_indexed_schemas_preserve_constraints():
    with pytest.raises(ValueError):
        validate("BindingPatch", {"streaming": {"buffer": {"unrecognized": 1}}})
    validate("BindingPatch", {"streaming": {"buffer": {}}})
