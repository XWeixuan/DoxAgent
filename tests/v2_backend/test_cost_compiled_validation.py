import copy
import json
from pathlib import Path

import pytest

from doxagent.api_v2.cost_validation import compiled_cost_validator
from doxagent.api_v2.dto import SCHEMA, validate, validator


def test_compiled_cost_contract_matches_canonical_and_preserves_inputs():
    original = copy.deepcopy(SCHEMA)
    assert compiled_cost_validator() is not None
    path = Path(__file__).resolve().parents[2] / "frontend/v2/tests/browser/cost-audit.data.json"
    fixtures = json.loads(path.read_text(encoding="utf-8"))
    sample = fixtures["ALL:"]
    variants = list(fixtures.values())
    mutations = [
        lambda x: x.pop("scope"),
        lambda x: x.update(scope="INVALID"),
        lambda x: x.update(extra="forbidden"),
        lambda x: x.update(observed_through="2026-99-99T12:00:00Z"),
        lambda x: x["totals"].update(observed_attempts=True),
        lambda x: x["totals"].update(observed_attempts=-1),
        lambda x: x["totals"].update(observed_attempts=1.0),
        lambda x: x["rows"][0]["totals"]["input_tokens"]["current"].update(value="1\n"),
        lambda x: x["rows"][0]["totals"]["input_tokens"]["current"].update(value=None),
        lambda x: x["rows"][0]["totals"]["input_tokens"]["current"].update(state="INVALID"),
        lambda x: x["rows"][0]["totals"]["input_tokens"].update(previous=None),
        lambda x: x["rows"][0]["totals"]["input_tokens"]["current_coverage"].pop("known_count"),
    ]
    for mutation in mutations:
        candidate = copy.deepcopy(sample)
        mutation(candidate)
        variants.append(candidate)
    for value in variants:
        before = copy.deepcopy(value)
        expected = validator("CostReport").is_valid(value)
        if expected:
            assert validate("CostReport", value) is value
        else:
            with pytest.raises(ValueError):
                validate("CostReport", value)
        assert value == before
    assert SCHEMA == original


def test_newer_schema_keywords_retain_canonical_validation(monkeypatch):
    row = SCHEMA["$defs"]["CostReport"]
    monkeypatch.setitem(row, "dependentRequired", {"scope": ["new_required_field"]})
    compiled_cost_validator.cache_clear()
    try:
        assert compiled_cost_validator() is None
        path = (
            Path(__file__).resolve().parents[2] / "frontend/v2/tests/browser/cost-audit.data.json"
        )
        value = json.loads(path.read_text(encoding="utf-8"))["ALL:"]
        with pytest.raises(ValueError):
            validate("CostReport", value)
    finally:
        compiled_cost_validator.cache_clear()
