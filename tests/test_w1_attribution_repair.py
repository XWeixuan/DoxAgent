import json

import pytest
from pydantic import ValidationError

from cdecr.model_boundary import bailian_strict_wire_schema
from doxagent.persistent_runtime_v2.schema import W1FactAttribution, W1NoveltyResult
from doxagent.persistent_runtime_v2.transport import (
    RuntimeResponsesRequest,
    normalize_w1_attributions,
)


def test_empty_and_provisional_ids_and_wire_pattern():
    assert W1FactAttribution(event_id="E82", fact_ids=[]).fact_ids == []
    assert W1FactAttribution(event_id="E82", fact_ids=["provisional_2026-09-15"]).fact_ids
    assert W1FactAttribution(event_id="E1", fact_ids=["f3"]).fact_ids == ["F3"]
    with pytest.raises(ValidationError):
        W1FactAttribution(event_id="E1", fact_ids=["proposition"])
    wire = bailian_strict_wire_schema(W1NoveltyResult.model_json_schema())
    facts = wire["properties"]["fact_attributions"]["anyOf"][0]["items"]["properties"]["fact_ids"]
    assert "minItems" not in facts
    assert "pattern" in facts["items"]


@pytest.mark.parametrize("label", ["E82_candidate", "proposition", "F12"])
def test_isolation_only_for_loaded_provisional(label):
    request = RuntimeResponsesRequest(
        instructions="",
        output_model=W1NoveltyResult,
        schema_name="w1_novelty_result",
        payload={
            "event_details": {
                "provisional_events": [{"provisional_event_id": "E82"}],
                "canonical_events": [{"event_id": "E1"}],
            }
        },
    )
    raw = json.dumps(
        {
            "result": "OLD",
            "confidence": "normal",
            "reference_ids": ["E82"],
            "reason": "Existing provisional covers the message",
            "fact_attributions": [{"event_id": "E82", "fact_ids": [label]}],
        }
    )
    text, warnings = normalize_w1_attributions(raw, request)
    assert W1NoveltyResult.model_validate_json(text).fact_attributions[0].fact_ids == []
    assert warnings == ["PROVISIONAL_FACT_ATTRIBUTION_ISOLATED:E82"]
    bad = raw.replace('"E82"', '"E1"')
    unchanged, warnings = normalize_w1_attributions(bad, request)
    assert json.loads(unchanged) == json.loads(bad)
    assert not warnings
    malformed = '{"reason":"a"b"}'
    assert normalize_w1_attributions(malformed, request) == (malformed, [])
